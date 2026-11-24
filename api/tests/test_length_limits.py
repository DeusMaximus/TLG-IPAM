"""The length limits on a host's `notes` and domain, and what they leave of the
request-body cap for a bulk import (#91)."""

import ipaddress
import json

import pytest
from fastmcp import Client
from sqlalchemy import update

from app import ipnet
from app.body_limit import BODY_MAX
from app.database import SessionLocal
from app.mcp_server import mcp
from app.models import Host
from app.schemas import HOST_NOTES_MAX

from .conftest import make_host, make_section, make_subnet

NOTES_REFUSAL = (
    f"notes: a host's notes may be at most {HOST_NOTES_MAX} characters, "
    f"not {HOST_NOTES_MAX + 1}"
)
DOMAIN_REFUSAL = f"Domain suffix is longer than {ipnet.DOMAIN_MAX} characters"
LONG_DOMAIN = ".".join(["a" * 63] * 3) + "." + "c" * 61  # 253 characters
OVER = {
    "notes": ("n" * (HOST_NOTES_MAX + 1), NOTES_REFUSAL),
    "domain": (LONG_DOMAIN + "c", DOMAIN_REFUSAL),
}


async def _subnet(client, cidr="10.0.0.0/24"):
    section = await make_section(client)
    return await make_subnet(client, section["id"], cidr=cidr)


async def _mcp(name: str, args: dict):
    async with Client(mcp) as mcp_client:
        return await mcp_client.call_tool(name, args, raise_on_error=False)


async def _store_notes(host_id: str, notes: str) -> None:
    """Write notes as a row stored before #91 may hold them: longer than the limit."""
    async with SessionLocal() as session:
        await session.execute(update(Host).where(Host.id == host_id).values(notes=notes))
        await session.commit()


def test_the_limits_are_the_decided_ones():
    assert (HOST_NOTES_MAX, ipnet.DOMAIN_MAX) == (4096, 253)
    assert len(LONG_DOMAIN) == ipnet.DOMAIN_MAX


# --- A single host's notes ---


async def test_notes_at_the_limit_are_written_on_every_single_host_path(client):
    subnet = await _subnet(client)
    at_limit = "n" * HOST_NOTES_MAX
    created = await make_host(client, subnet["id"], "10.0.0.10", notes=at_limit)
    assert created["notes"] == at_limit
    res = await client.put(f"/api/v1/hosts/{created['id']}", json={"notes": "m" * HOST_NOTES_MAX})
    assert res.status_code == 200, res.text
    res = await client.post(
        f"/api/v1/subnets/{subnet['id']}/next-free", json={"hostname": "a", "notes": at_limit}
    )
    assert res.status_code == 201, res.text
    for name, args in (
        ("create_host", {"subnet_id": subnet["id"], "ip": "10.0.0.20", "notes": at_limit}),
        ("allocate_next_ip", {"subnet_id": subnet["id"], "hostname": "b", "notes": at_limit}),
        ("update_host", {"host_id": created["id"], "notes": at_limit}),
    ):
        result = await _mcp(name, args)
        assert not result.is_error, result.content
        assert result.data["host"]["notes"] == at_limit


async def test_notes_over_the_limit_are_refused_on_every_single_host_path(client):
    """Refused by field name and length, without repeating the value, and nothing is
    written."""
    subnet = await _subnet(client)
    host = await make_host(client, subnet["id"], "10.0.0.10", notes="short")
    over = OVER["notes"][0]
    for res in (
        await client.post(
            "/api/v1/hosts", json={"subnet_id": subnet["id"], "ip": "10.0.0.11", "notes": over}
        ),
        await client.put(f"/api/v1/hosts/{host['id']}", json={"notes": over}),
        await client.post(
            f"/api/v1/subnets/{subnet['id']}/next-free", json={"hostname": "a", "notes": over}
        ),
    ):
        assert res.status_code == 422, res.text
        assert res.json()["error"] == {"code": "VALIDATION_ERROR", "message": NOTES_REFUSAL}
    for name, args in (
        ("create_host", {"subnet_id": subnet["id"], "ip": "10.0.0.20", "notes": over}),
        ("allocate_next_ip", {"subnet_id": subnet["id"], "hostname": "b", "notes": over}),
        ("update_host", {"host_id": host["id"], "notes": over}),
    ):
        result = await _mcp(name, args)
        assert result.is_error
        assert result.content[0].text == NOTES_REFUSAL
    hosts = (await client.get(f"/api/v1/hosts?subnet_id={subnet['id']}")).json()["data"]
    assert [(h["ip"], h["notes"]) for h in hosts] == [("10.0.0.10", "short")]


async def test_an_update_may_resend_notes_stored_longer_unchanged(client):
    """A host stored with notes over the limit before it existed keeps them. An update
    that resends them unchanged, as the UI's form does whatever field changed, is let
    through; one that changes them must meet the limit."""
    subnet = await _subnet(client)
    host = await make_host(client, subnet["id"], "10.0.0.10")
    stored = "s" * (HOST_NOTES_MAX + 1)
    await _store_notes(host["id"], stored)
    url = f"/api/v1/hosts/{host['id']}"

    res = await client.put(url, json={"notes": stored, "status": "offline"})
    assert res.status_code == 200, res.text
    assert (res.json()["data"]["notes"], res.json()["data"]["status"]) == (stored, "offline")
    result = await _mcp("update_host", {"host_id": host["id"], "notes": stored, "owner": "x"})
    assert not result.is_error, result.content
    assert result.data["host"]["notes"] == stored

    res = await client.put(url, json={"notes": stored + "s"})
    assert res.status_code == 422, res.text
    assert res.json()["error"]["message"] == (
        f"notes: a host's notes may be at most {HOST_NOTES_MAX} characters, "
        f"not {HOST_NOTES_MAX + 2}"
    )
    assert (await client.get(url)).json()["data"]["notes"] == stored

    # an update that leaves notes out does not touch them
    res = await client.put(url, json={"hostname": "kept"})
    assert res.status_code == 200, res.text
    assert res.json()["data"]["notes"] == stored

    res = await client.put(url, json={"notes": "n" * HOST_NOTES_MAX})
    assert res.status_code == 200, res.text


# --- A bulk import's rows, on every path ---


@pytest.mark.parametrize("field", sorted(OVER))
@pytest.mark.parametrize("path", ["api", "ui", "mcp"])
async def test_a_row_over_a_length_limit_is_that_rows_error_on_every_path(client, field, path):
    """The import is served and the rows around the long one are created; the long
    one is an `error` naming the limit, never a refused request."""
    subnet = await _subnet(client)
    value, refusal = OVER[field]
    rows = [{"ip": "10.0.0.10"}, {"ip": "10.0.0.11", field: value}, {"ip": "10.0.0.12"}]
    if path == "mcp":
        result = await _mcp("bulk_create_hosts", {"subnet_id": subnet["id"], "hosts": rows})
        assert not result.is_error, result.content
        data = result.data
    else:
        res = await client.post(
            f"/api/v1/subnets/{subnet['id']}/hosts/bulk",
            json={"hosts": rows},
            headers={"X-TLG-Client": "ui"} if path == "ui" else {},
        )
        assert res.status_code == 200, res.text
        data = res.json()["data"]
    assert (data["created"], data["skipped"], data["errors"]) == (2, 0, 1)
    assert [r["status"] for r in data["results"]] == ["created", "error", "created"]
    assert data["results"][1] == {
        "index": 1, "ip": "10.0.0.11", "status": "error", "reason": refusal
    }
    change = (await client.get("/api/v1/changes")).json()["data"][0]
    assert (change["source"], change["summary"]) == (
        path, "bulk import into lab: 2 created, 0 skipped, 1 failed"
    )
    hosts = (await client.get(f"/api/v1/hosts?subnet_id={subnet['id']}")).json()["data"]
    assert [h["ip"] for h in hosts] == ["10.0.0.10", "10.0.0.12"]


async def test_a_row_at_the_limits_is_created(client):
    subnet = await _subnet(client)
    row = {"ip": "10.0.0.10", "domain": LONG_DOMAIN, "notes": "n" * HOST_NOTES_MAX}
    res = await client.post(f"/api/v1/subnets/{subnet['id']}/hosts/bulk", json={"hosts": [row]})
    assert res.status_code == 200, res.text
    assert res.json()["data"]["created"] == 1


# --- What the limits leave of the request-body cap (brief §4.3) ---


def encoded(document) -> bytes:
    """As the UI (`JSON.stringify`) sends JSON: compact, UTF-8, non-ASCII unescaped."""
    return json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode()


def full_rows(count: int, *, notes: str, owner: str = "\U0001f600" * 120) -> list[dict]:
    """`count` rows with every length-bounded field at its maximum but `notes`, which
    is given; the default owner's characters are four bytes each in UTF-8."""
    addresses = list(ipaddress.ip_network("10.0.0.0/22").hosts())[:count]
    return [
        {
            "ip": str(address),
            "hostname": LONG_DOMAIN,
            "domain": LONG_DOMAIN,
            "mac": f"aa:bb:cc:dd:{i >> 8:02x}:{i & 255:02x}",
            "device_type": "other",
            "status": "reserved",
            "owner": owner,
            "notes": notes,
        }
        for i, address in enumerate(addresses)
    ]


def _longest_notes_that_fit(rows: int, char: str, **owner) -> int:
    """The most `char`s of notes per row with which `rows` full rows fit the cap."""
    low, high = 0, HOST_NOTES_MAX
    while low < high:
        mid = (low + high + 1) // 2
        if len(encoded({"hosts": full_rows(rows, notes=char * mid, **owner)})) <= BODY_MAX:
            low = mid
        else:
            high = mid - 1
    return low


def test_a_full_import_fits_the_cap_only_with_short_notes():
    """The figures the brief gives: with every other field at its maximum, 1,000 rows
    fit with at most 243 four-byte characters of notes each (1,335 ASCII characters
    with an ASCII owner); 1,000 rows with notes at the limit are 17.5 MB, or 5.2 MB in
    ASCII."""
    assert _longest_notes_that_fit(1000, "\U0001f600") == 243
    assert _longest_notes_that_fit(1000, "a", owner="a" * 120) == 1335
    widest = "\U0001f600" * HOST_NOTES_MAX
    assert len(encoded({"hosts": full_rows(1000, notes=widest)})) == 17_505_573
    assert len(encoded({"hosts": full_rows(1000, notes="a" * HOST_NOTES_MAX)})) == 5_217_573
    # the most rows at every limit that fit one request: 119, or 401 with ASCII notes
    # (431 with an ASCII owner as well)
    assert len(encoded({"hosts": full_rows(119, notes=widest)})) <= BODY_MAX
    assert len(encoded({"hosts": full_rows(120, notes=widest)})) > BODY_MAX
    assert len(encoded({"hosts": full_rows(401, notes="a" * HOST_NOTES_MAX)})) <= BODY_MAX
    assert len(encoded({"hosts": full_rows(402, notes="a" * HOST_NOTES_MAX)})) > BODY_MAX


async def test_a_full_import_with_notes_at_the_limit_is_refused_whole(client):
    subnet = await _subnet(client, cidr="10.0.0.0/22")
    body = encoded({"hosts": full_rows(1000, notes="a" * HOST_NOTES_MAX)})
    res = await client.post(
        f"/api/v1/subnets/{subnet['id']}/hosts/bulk",
        content=body,
        headers={"Content-Type": "application/json"},
    )
    assert res.status_code == 413, res.text
    assert res.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"
    assert (await client.get(f"/api/v1/hosts?subnet_id={subnet['id']}")).json()["total"] == 0


async def test_the_mcp_call_the_docstring_names_fits_and_is_served(client):
    """`bulk_create_hosts`' docstring: 400 records with 4,096 ASCII characters of notes
    each fit one call, as the JSON-RPC request a client sends, with every other field
    at its maximum in ASCII (Python's `json` escapes non-ASCII as `\\uXXXX`)."""
    subnet = await _subnet(client, cidr="10.0.0.0/22")
    rows = full_rows(400, notes="a" * HOST_NOTES_MAX, owner="a" * 120)
    call = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {
            "name": "bulk_create_hosts",
            "arguments": {"subnet_id": subnet["id"], "hosts": rows},
        },
    }
    assert len(json.dumps(call).encode()) <= BODY_MAX
    result = await _mcp("bulk_create_hosts", {"subnet_id": subnet["id"], "hosts": rows})
    assert not result.is_error, result.content
    assert result.data["created"] == 400
