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
from .test_auth import INITIALIZE, INITIALIZED, MCP_ACCEPT, OWNER_BEARER, running_app

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


# The longest a device type's name can be (`schemas.HostCreate.device_type`).
DEVICE_TYPE_MAX = 40
LONG_DEVICE_TYPE = "t" * DEVICE_TYPE_MAX
# A subnet with room for 1,000 addresses written out at their longest (15 characters).
FULL_ROWS_CIDR = "100.100.0.0/16"


def full_rows(count: int, *, notes: str | None, owner: str = "\U0001f600" * 120) -> list[dict]:
    """`count` import rows, the measured example of brief §4.3 (not a byte ceiling: see
    `test_a_row_has_no_largest_size`). Every field is at its longest character count
    and written in ASCII, but `owner` and `notes`, which are given: a 15-character
    address in `FULL_ROWS_CIDR` (a padded spelling is refused), hostname and domain of
    253 characters, a 17-character MAC as normalised, a device type of
    `DEVICE_TYPE_MAX`, status `reserved` (the longest of the four), `owner` as given
    (the default's characters are four bytes each in UTF-8) and `notes` as given, or
    no `notes` key when it is None."""
    addresses = [f"100.100.{c}.{d}" for c in range(100, 256) for d in range(100, 255)]
    rows = [
        {
            "ip": address,
            "hostname": LONG_DOMAIN,
            "domain": LONG_DOMAIN,
            "mac": f"aa:bb:cc:dd:{i >> 8:02x}:{i & 255:02x}",
            "device_type": LONG_DEVICE_TYPE,
            "status": "reserved",
            "owner": owner,
        }
        for i, address in enumerate(addresses[:count])
    ]
    assert len(rows) == count and all(len(row["ip"]) == 15 for row in rows)
    if notes is not None:
        for row in rows:
            row["notes"] = notes
    return rows


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
    """Every size brief §4.3 states for an import, as the UI encodes it (compact JSON,
    non-ASCII unescaped), for the rows `full_rows` builds: 1,000 rows without notes are
    1,151,011 bytes; with every other field at its maximum they fit with at most 233
    four-byte characters of notes each (1,295 ASCII characters with an ASCII owner);
    with notes at the limit they are 17,546,011 bytes, or 5,258,011 in ASCII (4,898,011
    with an ASCII owner as well); at most 119 such rows fit one request, 398 with ASCII
    notes and 428 with an ASCII owner too."""
    widest = "\U0001f600" * HOST_NOTES_MAX
    ascii_notes = "a" * HOST_NOTES_MAX
    assert len(encoded({"hosts": full_rows(1000, notes=None)})) == 1_151_011
    assert _longest_notes_that_fit(1000, "\U0001f600") == 233
    assert _longest_notes_that_fit(1000, "a", owner="a" * 120) == 1295
    assert len(encoded({"hosts": full_rows(1000, notes=widest)})) == 17_546_011
    assert len(encoded({"hosts": full_rows(1000, notes=ascii_notes)})) == 5_258_011
    ascii_all = full_rows(1000, notes=ascii_notes, owner="a" * 120)
    assert len(encoded({"hosts": ascii_all})) == 4_898_011
    for count, notes, owner in (
        (119, widest, {}),
        (398, ascii_notes, {}),
        (428, ascii_notes, {"owner": "a" * 120}),
    ):
        fits = encoded({"hosts": full_rows(count, notes=notes, **owner)})
        over = encoded({"hosts": full_rows(count + 1, notes=notes, **owner)})
        assert len(fits) <= BODY_MAX < len(over), count
    # a device type of the usual 5 characters, as the rows had before: 35 bytes a row less
    short_type = full_rows(1000, notes=None)
    for row in short_type:
        row["device_type"] = "other"
    assert len(encoded({"hosts": short_type})) == 1_151_011 - 35 * 1000


async def test_a_full_import_with_notes_at_the_limit_is_refused_whole(client):
    subnet = await _subnet(client, cidr=FULL_ROWS_CIDR)
    body = encoded({"hosts": full_rows(1000, notes="a" * HOST_NOTES_MAX)})
    res = await client.post(
        f"/api/v1/subnets/{subnet['id']}/hosts/bulk",
        content=body,
        headers={"Content-Type": "application/json"},
    )
    assert res.status_code == 413, res.text
    assert res.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"
    assert (await client.get(f"/api/v1/hosts?subnet_id={subnet['id']}")).json()["total"] == 0


# --- The example in `bulk_create_hosts`' docstring (brief §5.2) ---
#
# "400 records whose notes are 4,096 characters of plain ASCII text with no quotes,
# backslashes or line breaks fit", the other text fields ASCII too. It is judged as the
# worst client sends it, Python's `json.dumps`, which writes the separators with spaces.


def _tool_call(subnet_id: str, rows: list[dict]) -> bytes:
    call = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {
            "name": "bulk_create_hosts",
            "arguments": {"subnet_id": subnet_id, "hosts": rows},
        },
    }
    return json.dumps(call).encode()


def _sse_data(res) -> dict:
    if res.headers["content-type"].startswith("application/json"):
        return res.json()
    lines = [line for line in res.text.splitlines() if line.startswith("data:")]
    return json.loads(lines[-1][len("data:") :])


async def _post_to_mcp(anon_client, body: bytes):
    """`body` posted to the real `/mcp` route, behind the body cap, in a session."""
    headers = {**OWNER_BEARER, "Accept": MCP_ACCEPT}
    async with running_app():
        res = await anon_client.post("/mcp", json=INITIALIZE, headers=headers)
        assert res.status_code == 200, res.text
        headers["mcp-session-id"] = res.headers["mcp-session-id"]
        res = await anon_client.post("/mcp", json=INITIALIZED, headers=headers)
        assert res.status_code == 202, res.text
        return await anon_client.post(
            "/mcp", content=body, headers={**headers, "Content-Type": "application/json"}
        )


async def test_the_largest_call_the_docstring_permits_is_served_by_the_route(client, anon_client):
    """400 rows at every limit, notes 4,096 plain ASCII characters, every other text
    field ASCII: 1,965,771 bytes, under the 2,000,000 the docstring says to keep to
    and the cap, and the real route creates all 400 hosts."""
    created = await client.post("/api/v1/device-types", json={"name": LONG_DEVICE_TYPE})
    assert created.status_code == 201, created.text
    subnet = await _subnet(client, cidr=FULL_ROWS_CIDR)
    rows = full_rows(400, notes="a" * HOST_NOTES_MAX, owner="a" * 120)
    # plain ASCII in every field: JSON writes each value as itself, with no escape
    assert all(json.dumps(v) == f'"{v}"' for row in rows for v in row.values())
    body = _tool_call(subnet["id"], rows)
    assert len(body) == 1_965_771 < 2_000_000 < BODY_MAX
    res = await _post_to_mcp(anon_client, body)
    assert res.status_code == 200, res.text
    result = _sse_data(res)["result"]
    assert not result.get("isError"), result
    assert result["structuredContent"]["created"] == 400
    assert (await client.get(f"/api/v1/hosts?subnet_id={subnet['id']}")).json()["total"] == 400


async def test_the_same_call_with_a_non_ascii_owner_is_refused_by_the_route(client, anon_client):
    """The example holds for ASCII fields only: with an owner of 120 characters outside
    the BMP, which `json.dumps` writes as twelve bytes each, the same 400 rows are
    2,493,771 bytes (2,103,359 as the UI encodes them) and the real route answers `413`
    with nothing created."""
    subnet = await _subnet(client, cidr=FULL_ROWS_CIDR)
    rows = full_rows(400, notes="a" * HOST_NOTES_MAX)  # the default owner is non-BMP
    body = _tool_call(subnet["id"], rows)
    assert len(body) == 2_493_771 > BODY_MAX
    # and as the UI encodes it (compact, non-ASCII unescaped), still over
    assert len(encoded(json.loads(body))) == 2_103_359 > BODY_MAX
    res = await _post_to_mcp(anon_client, body)
    assert res.status_code == 413, res.text
    assert res.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"
    assert (await client.get(f"/api/v1/hosts?subnet_id={subnet['id']}")).json()["total"] == 0


def test_notes_json_escapes_take_the_same_call_over_the_cap():
    """The byte count decides, not the characters: the same 400 rows with notes of
    characters JSON escapes (a quote, a backslash, a line break) do not fit."""
    for escaped in ('"', "\\", "\n"):
        rows = full_rows(400, notes=escaped * HOST_NOTES_MAX, owner="a" * 120)
        assert len(_tool_call("x" * 36, rows)) > BODY_MAX, repr(escaped)


@pytest.mark.parametrize("escaped", ['"', "\\", "\t"], ids=["quote", "backslash", "tab"])
def test_escaped_ascii_outside_the_notes_takes_the_same_call_over_the_cap(escaped):
    """The example's rows are free of every character JSON escapes in every field, not
    just the notes: the same 400 rows with a hostname and an owner of quotes,
    backslashes or tabs (two bytes each in JSON) are 2,114,971 bytes, over the cap,
    though every character is ASCII and none is a line break."""
    rows = full_rows(400, notes="a" * HOST_NOTES_MAX, owner=escaped * 120)
    for row in rows:
        row["hostname"] = escaped * 253
    assert len(_tool_call("x" * 36, rows)) == 2_114_971 > BODY_MAX


def test_the_limits_count_characters_so_a_unicode_row_is_larger_than_the_example():
    """Brief §4.3: a hostname and a device type accept characters outside ASCII. 1,000
    rows with the longest notes that fit the ASCII example (233 four-byte characters),
    a hostname of 253 four-byte characters and a device type of 40 are 2,973,011 bytes
    as the UI encodes them, over the cap."""
    wide = "\U0001f600"
    rows = full_rows(1000, notes=wide * 233)
    assert len(encoded({"hosts": rows})) <= BODY_MAX
    for row in rows:
        row["hostname"] = wide * 253
        row["device_type"] = wide * DEVICE_TYPE_MAX
    assert len(encoded({"hosts": rows})) == 2_973_011 > BODY_MAX


async def test_a_row_has_no_largest_size(client):
    """Brief §4.3: a MAC's punctuation, a domain's leading white space and trailing
    dots, and a `last_seen`'s extra fractional digits are accepted and dropped on
    normalising, however many there are, so no row is the largest one accepted; an
    IPv4 address in a padded spelling is refused, so 15 characters is its longest."""
    subnet = await _subnet(client, cidr="10.0.0.0/24")
    padding = 10_000
    padded = {
        "mac": "aa:bb:cc:dd:ee:ff" + ":" * padding,
        "domain": " " * padding + "example.com" + "." * padding,
        "last_seen": "2026-01-01T00:00:00." + "0" * padding + "1+00:00",
    }
    rows = [
        {"ip": "10.0.0.10", **padded},
        {"ip": " " * padding + "10.0.0.11"},
        {"ip": "10.0.0.12" + " " * padding},
    ]
    res = await client.post(f"/api/v1/subnets/{subnet['id']}/hosts/bulk", json={"hosts": rows})
    assert res.status_code == 200, res.text
    results = res.json()["data"]["results"]
    assert [r["status"] for r in results] == ["created", "error", "error"], results
    host = (await client.get(f"/api/v1/hosts?subnet_id={subnet['id']}")).json()["data"][0]
    assert (host["mac"], host["domain"]) == ("aa:bb:cc:dd:ee:ff", "example.com")
