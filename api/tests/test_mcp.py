import base64
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastmcp import Client
from sqlalchemy import func, select

from app.database import SessionLocal
from app.mcp_server import mcp
from app.models import Change
from app.services import findings as findings_svc
from app.services import scans

from .conftest import PADDED_CIDR, make_host, make_section, make_subnet, store_cidr_as
from .test_checkin import (  # noqa: F401 (agent_api is a fixture)
    agent_api,
    jobs_for,
    make_agent,
    make_target,
    set_target,
    target_row,
)
from .test_findings import HOSTILE, MAC, OTHER_MAC, dismiss, key, observe
from app.ipnet import parse_ip
from .test_findings import findings as rest_findings

EXPECTED_TOOLS = {
    "list_sections",
    "list_subnets",
    "get_subnet",
    "get_free_ips",
    "list_hosts",
    "get_host",
    "create_host",
    "bulk_create_hosts",
    "allocate_next_ip",
    "update_host",
    "delete_host",
    "bulk_delete_hosts",
    "list_changes",
    "add_reserved_range",
    "delete_reserved_range",
    "list_device_types",
    "list_scan_findings",
    "request_scan",
}


async def test_all_tools_registered():
    async with Client(mcp) as mcp_client:
        tools = await mcp_client.list_tools()
        assert {t.name for t in tools} == EXPECTED_TOOLS
        # descriptions are Claude's documentation — they must exist
        assert all(t.description for t in tools)
        update_host = next(t for t in tools if t.name == "update_host")
        clear_fields = update_host.input_schema["properties"]["clear_fields"]
        allowed = clear_fields["anyOf"][0]["items"]["enum"]
        assert set(allowed) == {"hostname", "domain", "mac", "owner", "notes", "last_seen"}
        # the scan tools take exactly the arguments spec §5.8 lists
        listing = next(t for t in tools if t.name == "list_scan_findings").input_schema
        assert set(listing["properties"]) == {"subnet_id", "type", "cursor"}
        assert not listing.get("required")
        [types] = [a["enum"] for a in listing["properties"]["type"]["anyOf"] if "enum" in a]
        assert tuple(types) == findings_svc.FINDING_TYPES
        scan = next(t for t in tools if t.name == "request_scan").input_schema
        assert set(scan["properties"]) == {"subnet_id"}
        assert scan["required"] == ["subnet_id"]


async def test_mcp_end_to_end(client):
    section = await make_section(client, "Homelab")
    subnet = await make_subnet(client, section["id"], "10.0.0.0/29", gateway="10.0.0.1")

    async with Client(mcp) as mcp_client:
        sections = (await mcp_client.call_tool("list_sections", {})).data["sections"]
        assert sections[0]["name"] == "Homelab"

        allocated = (
            await mcp_client.call_tool(
                "allocate_next_ip", {"subnet_id": subnet["id"], "hostname": "pbs-01"}
            )
        ).data["host"]
        assert allocated["ip"] == "10.0.0.2"  # .1 is the gateway

        found = (await mcp_client.call_tool("list_hosts", {"q": "pbs"})).data["hosts"]
        assert found[0]["hostname"] == "pbs-01"

        detail = (await mcp_client.call_tool("get_subnet", {"subnet_id": subnet["id"]})).data[
            "subnet"
        ]
        assert detail["used"] == 1

    # MCP writes land in the audit log with source=mcp
    change = (await client.get("/api/v1/changes")).json()["data"][0]
    assert change["source"] == "mcp"
    assert change["entity_type"] == "host"


async def test_mcp_errors_are_meaningful(client):
    section = await make_section(client)
    subnet = await make_subnet(client, section["id"])
    async with Client(mcp) as mcp_client:
        result = await mcp_client.call_tool(
            "create_host",
            {"subnet_id": subnet["id"], "ip": "192.168.99.1"},
            raise_on_error=False,
        )
        assert result.is_error
        assert "not within" in result.content[0].text


async def test_mcp_update_host_can_clear_nullable_fields(client):
    section = await make_section(client)
    subnet = await make_subnet(client, section["id"], domain="home.arpa")
    host = (
        await client.post(
            "/api/v1/hosts",
            json={
                "subnet_id": subnet["id"],
                "ip": "10.0.0.10",
                "hostname": "server",
                "domain": "override.example.com",
                "notes": "remove me",
            },
        )
    ).json()["data"]

    async with Client(mcp) as mcp_client:
        updated = (
            await mcp_client.call_tool(
                "update_host",
                {"host_id": host["id"], "clear_fields": ["domain", "notes"]},
            )
        ).data["host"]

    assert updated["domain"] is None
    assert updated["notes"] is None
    assert updated["fqdn"] == "server.home.arpa"


async def test_mcp_update_host_rejects_conflicting_set_and_clear(client):
    section = await make_section(client)
    subnet = await make_subnet(client, section["id"])
    host = (
        await client.post(
            "/api/v1/hosts", json={"subnet_id": subnet["id"], "ip": "10.0.0.10"}
        )
    ).json()["data"]

    async with Client(mcp) as mcp_client:
        result = await mcp_client.call_tool(
            "update_host",
            {"host_id": host["id"], "notes": "keep", "clear_fields": ["notes"]},
            raise_on_error=False,
        )

    assert result.is_error
    assert "cannot be both set and cleared" in result.content[0].text


# --- Scan tools (#17, spec §5.8) ---

# what list_scan_findings adds to each finding the REST listing returns
NAMES = ("subnet_name", "subnet_cidr", "host_name", "host_fqdn")


async def change_count() -> int:
    async with SessionLocal() as session:
        return await session.scalar(select(func.count(Change.id)))


async def call(name: str, args: dict):
    async with Client(mcp) as mcp_client:
        return await mcp_client.call_tool(name, args, raise_on_error=False)


async def scan_page(**args) -> dict:
    """One page of list_scan_findings, checked as every page must be."""
    result = await call("list_scan_findings", args)
    assert not result.is_error, result.content[0].text
    page = result.data
    assert set(page) == {"findings", "returned", "next_cursor", "note"}
    assert page["returned"] == len(page["findings"]) <= findings_svc.PAGE_SIZE
    # the warning travels with every page, not only the first
    assert page["note"] == findings_svc.NETWORK_REPORTED_NOTE
    # within the size budget, unless one finding alone is larger
    used = sum(findings_svc._encoded_size(f) + 1 for f in page["findings"])
    assert used <= findings_svc.PAGE_BUDGET or page["returned"] == 1
    # a page that says there is more is never empty
    if page["next_cursor"] is not None:
        assert page["returned"] >= 1
    return page


async def scan_pages(**args) -> list[list[dict]]:
    """Every page, following next_cursor to the end."""
    pages = []
    cursor = None
    while True:
        page = await scan_page(**args, **({"cursor": cursor} if cursor else {}))
        pages.append(page["findings"])
        cursor = page["next_cursor"]
        if cursor is None:
            return pages


async def scan_findings(**args) -> list[dict]:
    return [f for page in await scan_pages(**args) for f in page]


def position(f: dict) -> tuple:
    return (f["subnet_id"], f["ip"], f["type"])


async def test_list_scan_findings_names_each_finding_and_its_subnet(client):
    section = await make_section(client)
    lab = await make_subnet(client, section["id"], "10.0.0.0/24", name="lab", domain="home.arpa")
    iot = await make_subnet(client, section["id"], "10.0.1.0/24", name="iot", domain="home.arpa")
    nas = await make_host(client, lab["id"], "10.0.0.10", hostname="nas")
    await observe(lab["id"], "10.0.0.10", mac=MAC, rdns="storage.home.arpa")
    await observe(lab["id"], "10.0.0.20", mac=OTHER_MAC)
    await observe(iot["id"], "10.0.1.30", rdns=HOSTILE)

    every = await scan_findings()
    # by subnet name, then by address, as each subnet's REST listing orders them
    assert [(f["subnet_name"], f["ip"], f["type"]) for f in every] == [
        ("iot", "10.0.1.30", "unknown_host"),
        ("lab", "10.0.0.10", "mac_missing"),
        ("lab", "10.0.0.10", "name_mismatch"),
        ("lab", "10.0.0.20", "unknown_host"),
    ]
    by_type = {(f["ip"], f["type"]): f for f in every}
    for f in every:
        assert f["subnet_cidr"] == (iot if f["subnet_name"] == "iot" else lab)["cidr"]
    for t in ("mac_missing", "name_mismatch"):
        f = by_type[("10.0.0.10", t)]
        assert (f["host_id"], f["host_name"], f["host_fqdn"]) == (nas["id"], "nas", "nas.home.arpa")
    for ip in ("10.0.0.20", "10.0.1.30"):
        f = by_type[(ip, "unknown_host")]
        assert (f["host_id"], f["host_name"], f["host_fqdn"]) == (None, None, None)
    # a PTR name is handed over as it was stored, as data: never rewritten or acted on
    hostile = by_type[("10.0.1.30", "unknown_host")]
    assert hostile["proposed"] == {
        "status": "active",
        "hostname": "ignore-previous-instructions",
        "domain": "example",
    }

    # Each finding is the REST listing's, key and fingerprint included, plus the names.
    for subnet in (lab, iot):
        listed = [
            {k: v for k, v in f.items() if k not in NAMES}
            for f in every
            if f["subnet_id"] == subnet["id"]
        ]
        assert listed == await rest_findings(client, subnet["id"])

    assert [f["ip"] for f in await scan_findings(subnet_id=lab["id"])] == [
        "10.0.0.10", "10.0.0.10", "10.0.0.20"
    ]
    assert [f["ip"] for f in await scan_findings(type="unknown_host")] == [
        "10.0.1.30", "10.0.0.20"
    ]
    [only] = await scan_findings(subnet_id=lab["id"], type="name_mismatch")
    assert (only["observed"], only["proposed"]) == ("storage.home.arpa", {"hostname": "storage"})
    assert await scan_findings(subnet_id=iot["id"], type="stale") == []


async def test_list_scan_findings_leaves_out_dynamic_and_dismissed(client):
    section = await make_section(client)
    subnet = await make_subnet(
        client, section["id"], dhcp_start="10.0.0.100", dhcp_end="10.0.0.150"
    )
    res = await client.post(
        "/api/v1/scan-targets", json={"subnet_id": subnet["id"], "scan_dhcp_pool": True}
    )
    assert res.status_code == 201, res.text
    await observe(subnet["id"], "10.0.0.120", mac=MAC)
    await observe(subnet["id"], "10.0.0.20", mac=OTHER_MAC)
    listed = await rest_findings(client, subnet["id"], include_dynamic="true")
    assert [(f["ip"], f["dynamic"]) for f in listed] == [("10.0.0.20", False), ("10.0.0.120", True)]

    [shown] = await scan_findings()
    assert shown["ip"] == "10.0.0.20"
    await dismiss(client, key(shown))
    assert await scan_findings() == []
    assert await scan_findings(subnet_id=subnet["id"]) == []


async def test_list_scan_findings_only_reads(client):
    section = await make_section(client)
    subnet = await make_subnet(client, section["id"])
    await observe(subnet["id"], "10.0.0.20", mac=MAC, rdns=HOSTILE)
    before = await change_count()
    assert len(await scan_findings()) == 1
    assert len(await scan_findings(subnet_id=subnet["id"], type="unknown_host")) == 1
    assert await change_count() == before


async def test_list_scan_findings_refusals(client):
    unknown = await call("list_scan_findings", {"subnet_id": "nope"})
    assert unknown.is_error
    assert unknown.content[0].text == "Subnet nope not found"
    # FastMCP checks the type against the schema's enum before the tool runs
    bad_type = await call("list_scan_findings", {"type": "fdns_mismatch"})
    assert bad_type.is_error
    assert "fdns_mismatch" in bad_type.content[0].text
    # no subnets: no findings, not an error
    assert await scan_findings() == []


# --- Paging list_scan_findings (#79) ---

# Claude Code keeps at most this many characters of a tool's text result
# (code.claude.com/docs/en/mcp, "MCP output limits and warnings")
CLIENT_TEXT_MAX = 50_000


async def unknown_hosts(subnet_id, prefix, last_octets):
    for octet in last_octets:
        await observe(subnet_id, f"{prefix}.{octet}", mac=f"aa:bb:cc:00:00:{octet:02x}")


async def test_list_scan_findings_pages_cover_every_finding_once(client):
    """Acceptance: no page over the page size, a cursor while there is more, and the
    pages together are every finding exactly once, in the unpaged order."""
    assert findings_svc.PAGE_SIZE == 25  # the fixture's page boundaries assume it
    section = await make_section(client)
    alpha = await make_subnet(client, section["id"], "10.0.0.0/24", name="alpha")
    beta = await make_subnet(client, section["id"], "10.0.1.0/24", name="beta")
    gamma = await make_subnet(client, section["id"], "10.0.2.0/24", name="gamma")
    # alpha: 24 unknown hosts, then one host with two findings across the page boundary,
    # then 6 more; beta: a host and no finding; gamma: 18. 50 in all: two full pages.
    await unknown_hosts(alpha["id"], "10.0.0", range(10, 34))
    await make_host(client, alpha["id"], "10.0.0.40")
    await observe(alpha["id"], "10.0.0.40", mac=MAC, rdns="nas.example")
    await unknown_hosts(alpha["id"], "10.0.0", range(50, 56))
    await make_host(client, beta["id"], "10.0.1.10", hostname="quiet")
    await unknown_hosts(gamma["id"], "10.0.2", range(10, 28))

    pages = await scan_pages()
    assert [len(p) for p in pages] == [25, 25]  # full to the end: no empty last page
    assert (pages[0][-1]["ip"], pages[0][-1]["type"]) == ("10.0.0.40", "mac_missing")
    assert (pages[1][0]["ip"], pages[1][0]["type"]) == ("10.0.0.40", "name_missing")
    every = [f for p in pages for f in p]
    assert len({position(f) for f in every}) == len(every) == 50
    # the same findings, in the same order, as each subnet's REST listing
    for subnet in (alpha, beta, gamma):
        listed = [
            {k: v for k, v in f.items() if k not in NAMES}
            for f in every
            if f["subnet_id"] == subnet["id"]
        ]
        assert listed == await rest_findings(client, subnet["id"])
    assert [f["subnet_name"] for f in every] == ["alpha"] * 32 + ["gamma"] * 18

    # one more makes a third page of one
    await observe(gamma["id"], "10.0.2.200", mac=OTHER_MAC)
    assert [len(p) for p in await scan_pages()] == [25, 25, 1]
    # paging under a filter: the cursor is a position, so it carries across filters too
    assert [len(p) for p in await scan_pages(type="unknown_host")] == [25, 24]
    assert [len(p) for p in await scan_pages(subnet_id=alpha["id"])] == [25, 7]
    assert [f["ip"] for f in await scan_findings(subnet_id=gamma["id"])][-1] == "10.0.2.200"


async def test_list_scan_findings_cursor_survives_resolving_earlier_findings(client):
    """An assistant resolving findings from one page skips none on the next: the cursor
    is where the page ended, not how many came before it."""
    section = await make_section(client)
    subnet = await make_subnet(client, section["id"])
    await unknown_hosts(subnet["id"], "10.0.0", range(10, 50))
    everything = [position(f) for f in await scan_findings()]
    assert len(everything) == 40

    first = await scan_page()
    for f in first["findings"][:5]:
        created = await call(
            "create_host", {"subnet_id": f["subnet_id"], "ip": f["ip"], "mac": f["proposed"]["mac"]}
        )
        assert not created.is_error, created.content[0].text
    # and a new finding before the cursor: a fresh listing shows it, this paging does not
    await observe(subnet["id"], "10.0.0.5", mac=OTHER_MAC)
    rest = await scan_pages(cursor=first["next_cursor"])
    seen = [position(f) for f in first["findings"]] + [position(f) for p in rest for f in p]
    assert seen == everything
    assert (await scan_page())["findings"][0]["ip"] == "10.0.0.5"


def _cursor(value) -> str:
    return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")


# a well-formed position: name, CIDR, id, address, type
GOOD_CURSOR = ["lab", "10.0.0.0/24", "id", 167772170, "stale"]
# per slot, values of the wrong type or outside the vocabulary
WRONG_IN_SLOT = {
    0: [1, None, True, [], {}],
    1: [1, None, True, [], {}],
    2: [1, None, True, [], {}],
    3: ["167772170", True, -1, 1.5, None, [], {}],
    4: [1, None, True, [], {}, ["stale"], "fdns_mismatch"],
}


def _wrong_slot_cursors() -> list:
    return [
        pytest.param(
            _cursor(GOOD_CURSOR[:slot] + [wrong] + GOOD_CURSOR[slot + 1 :]),
            id=f"slot{slot}-{json.dumps(wrong)}",
        )
        for slot, values in WRONG_IN_SLOT.items()
        for wrong in values
    ]


@pytest.mark.parametrize(
    "cursor",
    [
        "",
        "not a cursor",
        _cursor({}),
        _cursor("stale"),
        _cursor(GOOD_CURSOR[:4]),
        _cursor([*GOOD_CURSOR, "extra"]),
        # well-formed but longer than CURSOR_MAX: refused by its length alone
        _cursor(["x" * 1600, *GOOD_CURSOR[1:]]),
        *_wrong_slot_cursors(),
    ],
)
async def test_list_scan_findings_refuses_a_malformed_cursor(client, cursor):
    """Refused with the one fixed message, whatever is wrong and in whichever slot
    (Codex's briefed round on #83: a list or an object in the type's slot was raised as
    an internal error), never with an internal error's text."""
    section = await make_section(client)
    subnet = await make_subnet(client, section["id"])
    await observe(subnet["id"], "10.0.0.20", mac=MAC)
    result = await call("list_scan_findings", {"cursor": cursor})
    assert result.is_error
    assert result.content[0].text == findings_svc.CURSOR_REFUSED


async def test_a_well_formed_cursor_is_read_as_a_position(client):
    """Shape is all the check is: an unsigned cursor a client made up is a position in
    the order, and a cursor of the same shape as the over-long one, under the cap, is
    not refused."""
    assert len(_cursor(["x" * 1000, *GOOD_CURSOR[1:]])) <= findings_svc.CURSOR_MAX
    section = await make_section(client)
    subnet = await make_subnet(client, section["id"], name="lab")
    await observe(subnet["id"], "10.0.0.20", mac=MAC)
    await observe(subnet["id"], "10.0.0.30", mac=OTHER_MAC)
    made_up = ["lab", subnet["cidr"], subnet["id"], int(parse_ip("10.0.0.20")), "unknown_host"]
    page = await scan_page(cursor=_cursor(made_up))
    assert [f["ip"] for f in page["findings"]] == ["10.0.0.30"]
    assert (await scan_page(cursor=_cursor(["x" * 1000, *GOOD_CURSOR[1:]])))["findings"] == []


@pytest.mark.parametrize("name", ["😀" * 120, '"' * 120, "\\" * 120, "\x01" * 120])
def test_a_cursor_at_its_largest_round_trips(name):
    """Every cursor the encoder can make is one the decoder takes back: a subnet name at
    its 120-character maximum in the characters JSON encodes longest (#83, Codex)."""
    ip_int = 2**128 - 1  # an IPv6 address: the most digits
    cidr = "ffff:ffff:ffff:ffff:ffff:ffff:ffff:ffff/128"
    position = (name, cidr, "f" * 36, ip_int, findings_svc._TYPE_ORDER["name_mismatch"])
    cursor = findings_svc._encode_cursor(position)
    assert len(cursor) <= findings_svc.CURSOR_MAX
    assert findings_svc._decode_cursor(cursor) == position


async def test_paging_through_a_subnet_with_a_long_unicode_name(client):
    section = await make_section(client)
    subnet = await make_subnet(client, section["id"], name="😀" * 120)
    await unknown_hosts(subnet["id"], "10.0.0", range(10, 10 + findings_svc.PAGE_SIZE + 1))
    assert [len(p) for p in await scan_pages()] == [findings_svc.PAGE_SIZE, 1]


async def test_a_page_at_every_field_maximum_fits_a_client(client):
    """The page size holds against the largest finding the API can make: every name at
    its maximum length (the measurement behind `findings.PAGE_SIZE`)."""
    label = "a" * 63
    ptr = ".".join([label, label, label, "b" * 61])
    domain = ".".join([label, label, label, "c" * 61])
    assert len(ptr) == len(domain) == 253
    section = await make_section(client)
    subnet = await make_subnet(client, section["id"], name="s" * 120, domain=domain)
    for octet in range(10, 11 + findings_svc.PAGE_SIZE):
        ip = f"10.0.0.{octet}"
        await make_host(client, subnet["id"], ip, hostname="h" * 253)
        await observe(subnet["id"], ip, rdns=ptr)
    result = await call("list_scan_findings", {"type": "name_mismatch"})
    assert not result.is_error, result.content[0].text
    assert result.data["returned"] == findings_svc.PAGE_SIZE
    assert result.data["next_cursor"] is not None
    assert len(result.content[0].text) < CLIENT_TEXT_MAX


async def test_pages_order_by_name_then_cidr_then_id(client):
    """The order's three subnet keys each decide somewhere: name over CIDR (zulu has the
    lowest CIDR and comes last), CIDR over id (the two "same" subnets' ids run against
    their CIDRs), and id alone between "twin" subnets of one name and CIDR in two
    sections, where a page boundary falls between them."""
    assert findings_svc.PAGE_SIZE == 25  # the boundary below assumes it
    home = await make_section(client, "Home")
    away = await make_section(client, "Away")
    zulu = await make_subnet(client, home["id"], "10.0.0.0/24", name="zulu")
    high = await make_subnet(client, home["id"], "10.0.2.0/24", name="same")
    # the "same" subnet with the lower CIDR gets the higher id, so CIDR and id disagree
    while True:
        low = await make_subnet(client, home["id"], "10.0.1.0/24", name="same")
        if low["id"] > high["id"]:
            break
        assert (await client.delete(f"/api/v1/subnets/{low['id']}")).status_code == 204
    twins = sorted(
        [
            await make_subnet(client, home["id"], "10.0.3.0/24", name="twin"),
            await make_subnet(client, away["id"], "10.0.3.0/24", name="twin"),
        ],
        key=lambda s: s["id"],
    )
    counts = {low["id"]: 6, high["id"]: 6, twins[0]["id"]: 13, twins[1]["id"]: 13, zulu["id"]: 3}
    for subnet in (zulu, high, low, *twins):
        prefix = subnet["cidr"].rsplit(".", 1)[0]
        await unknown_hosts(subnet["id"], prefix, range(10, 10 + counts[subnet["id"]]))

    pages = await scan_pages()
    assert [len(p) for p in pages] == [25, 16]
    # the first page ends at the last finding of the first twin
    assert pages[0][-1]["subnet_id"] == twins[0]["id"]
    expected = [
        (subnet["id"], f"{subnet['cidr'].rsplit('.', 1)[0]}.{octet}")
        for subnet in (low, high, *twins, zulu)
        for octet in range(10, 10 + counts[subnet["id"]])
    ]
    assert [(f["subnet_id"], f["ip"]) for p in pages for f in p] == expected


async def test_paging_through_a_subnet_stored_with_a_long_spelling(client):
    """A subnet stored with a non-canonical CIDR before #97 is listed, and paged, by its
    canonical spelling: the cursor carries the CIDR, and the stored 4,300-character one
    made a cursor over `CURSOR_MAX`, so the listing refused the cursor it had issued."""
    section = await make_section(client)
    subnet = await make_subnet(client, section["id"], "192.168.240.0/20", name="lab")
    await store_cidr_as(subnet["id"], PADDED_CIDR)
    await unknown_hosts(subnet["id"], "192.168.240", range(10, 10 + findings_svc.PAGE_SIZE + 1))
    pages = await scan_pages()
    assert [len(p) for p in pages] == [findings_svc.PAGE_SIZE, 1]
    assert {f["subnet_cidr"] for p in pages for f in p} == {"192.168.240.0/20"}


async def test_pages_order_subnets_stored_with_other_spellings_by_their_canonical_cidr(client):
    """Subnets stored before #97 in other spellings are ordered, as the cursor places
    them, by the canonical spelling: two subnets named "same", a /24 stored as
    `10.0.0.0/255.255.255.0` and a /25 stored as `10.0.0.0/0.0.0.127`. Sorted by the
    stored strings, the /25 comes first ("0" before "2") while the cursor's canonical
    positions put the /24 first, and paging skips the /24's findings (the outside
    review of PR #113)."""
    assert findings_svc.PAGE_SIZE == 25  # the fixture's 52 findings cross two boundaries
    home = await make_section(client, "Home")
    away = await make_section(client, "Away")
    wide = await make_subnet(client, home["id"], "10.0.0.0/24", name="same")
    narrow = await make_subnet(client, away["id"], "10.0.0.0/25", name="same")
    await store_cidr_as(wide["id"], "10.0.0.0/255.255.255.0")
    await store_cidr_as(narrow["id"], "10.0.0.0/0.0.0.127")
    octets = range(10, 36)
    for subnet in (wide, narrow):
        await unknown_hosts(subnet["id"], "10.0.0", octets)

    pages = await scan_pages()
    # every finding, each once, the /24's before the /25's
    expected = [(s["id"], f"10.0.0.{octet}") for s in (wide, narrow) for octet in octets]
    assert [(f["subnet_id"], f["ip"]) for p in pages for f in p] == expected
    assert [len(p) for p in pages] == [25, 25, 2]
    assert {f["subnet_cidr"] for p in pages for f in p} == {"10.0.0.0/24", "10.0.0.0/25"}


# The review's accepted values (Codex's briefed round on #83), each through the REST API
# the owner uses: names JSON escapes six characters at a time, and a domain far longer
# than any finding the count was measured with.
ACCEPTED_LONG = {
    "control-character hostnames": ("s" * 120, "example.com", "\x01" * 253),
    "a 5,058-character domain": ("lab", ".".join(["d" * 63] * 79) + ".dd", "h" * 253),
    "control characters in subnet name and hostname": (
        "\x01" * 120,
        ".".join(["a" * 63] * 3) + "." + "c" * 61,
        "\x01" * 253,
    ),
}


@pytest.mark.parametrize("case", list(ACCEPTED_LONG))
async def test_pages_of_accepted_long_values_fit_a_client(client, case):
    name, domain, hostname = ACCEPTED_LONG[case]
    section = await make_section(client)
    subnet = await make_subnet(client, section["id"], name=name, domain=domain)
    for octet in range(10, 36):
        ip = f"10.0.0.{octet}"
        await make_host(client, subnet["id"], ip, hostname=hostname)
        await observe(subnet["id"], ip, rdns=HOSTILE)
    seen = []
    cursor = None
    while True:
        args = {"type": "name_mismatch", **({"cursor": cursor} if cursor else {})}
        result = await call("list_scan_findings", args)
        assert not result.is_error, result.content[0].text
        assert len(result.content[0].text) < CLIENT_TEXT_MAX
        assert len(result.content[0].text.encode()) < CLIENT_TEXT_MAX
        seen += [f["ip"] for f in result.data["findings"]]
        cursor = result.data["next_cursor"]
        if cursor is None:
            break
    assert seen == [f"10.0.0.{octet}" for octet in range(10, 36)]


def long_domain(labels: int) -> str:
    return ".".join(f"{i:03d}" + "d" * 60 for i in range(labels))


async def test_pages_of_long_domains_stay_inside_the_budget(client):
    """Codex on #83: a domain suffix has no length limit, so 25 findings could pass a
    client's limit. The size budget cuts the page short; paging still sees each finding
    once, and every page's text stays under what Claude Code keeps."""
    domain = long_domain(32)  # 32 labels of 63: 2,047 characters, as Codex measured
    section = await make_section(client)
    subnet = await make_subnet(client, section["id"], name="lab", domain=domain)
    for octet in range(10, 40):
        ip = f"10.0.0.{octet}"
        await make_host(client, subnet["id"], ip, hostname=f"host-{octet}")
        await observe(subnet["id"], ip, mac=MAC)
    seen = []
    cursor = None
    while True:
        result = await call("list_scan_findings", {"cursor": cursor} if cursor else {})
        assert not result.is_error, result.content[0].text
        assert len(result.content[0].text) < CLIENT_TEXT_MAX
        page = await scan_page(**({"cursor": cursor} if cursor else {}))
        assert page["returned"] < findings_svc.PAGE_SIZE  # cut short by size, not count
        seen += [position(f) for f in page["findings"]]
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert len(seen) == len(set(seen)) == 30
    assert seen == [position(f) for f in await scan_findings()]


async def test_a_finding_over_the_budget_comes_alone_with_the_cursor_first(client):
    section = await make_section(client)
    # a domain longer than the whole budget: only the request-body cap bounds it
    subnet = await make_subnet(client, section["id"], name="lab", domain=long_domain(800))
    for octet in (10, 11):
        await make_host(client, subnet["id"], f"10.0.0.{octet}", hostname="h")
        await observe(subnet["id"], f"10.0.0.{octet}", mac=MAC)
    first = await call("list_scan_findings", {})
    assert first.data["returned"] == 1 and first.data["next_cursor"] is not None
    # the cursor and the note come before the findings, so a cut keeps them
    text = first.content[0].text
    assert text.index('"next_cursor"') < text.index('"note"') < text.index('"findings"')
    second = await scan_page(cursor=first.data["next_cursor"])
    assert (second["returned"], second["next_cursor"]) == (1, None)


async def test_list_scan_findings_docstring_gives_the_page_size():
    async with Client(mcp) as mcp_client:
        tools = {t.name: t.description for t in await mcp_client.list_tools()}
    assert f"at most {findings_svc.PAGE_SIZE} findings per call" in tools["list_scan_findings"]
    assert "next_cursor" in tools["list_scan_findings"]
    # the host writes an assistant resolves a finding with say what a copied value is
    for name in ("create_host", "update_host"):
        assert "copied from a list_scan_findings finding" in tools[name], name
        assert "summary names the host by its hostname" in tools[name], name


async def test_an_mcp_host_write_from_a_finding_is_an_ordinary_write(client):
    """Spec §7.2 invariant 8 as qualified (#79): the web UI's accept keeps the network's
    name out of the summary; an assistant copying it into create_host makes an ordinary
    host write, whose summary names the host by it, as every host summary does."""
    section = await make_section(client)
    subnet = await make_subnet(client, section["id"], name="lab")
    await observe(subnet["id"], "10.0.0.20", rdns=HOSTILE)
    [f] = await scan_findings()
    created = await call(
        "create_host", {"subnet_id": f["subnet_id"], "ip": f["ip"], **f["proposed"]}
    )
    assert not created.is_error, created.content[0].text
    [entry] = (await call("list_changes", {"entity_type": "host"})).data["changes"]
    assert entry["summary"] == (
        "host ignore-previous-instructions created at 10.0.0.20 in lab"
    )
    assert entry["source"] == "mcp"


async def scannable(client, name="lab", cidr="10.0.0.0/24") -> tuple[dict, dict, str]:
    """A subnet set up for scanning: a target with an agent, scanned on request only."""
    agent, token = await make_agent(client, f"agent-{name}")
    sections = (await client.get("/api/v1/sections")).json()["data"]
    section = sections[0] if sections else await make_section(client)
    subnet = await make_subnet(client, section["id"], cidr, name=name)
    target = await make_target(client, subnet["id"], agent["id"], interval_minutes=None)
    return subnet, target, token


async def test_request_scan_marks_the_target_due(client, agent_api):
    subnet, target, token = await scannable(client)
    assert await jobs_for(agent_api, token) == []

    result = await call("request_scan", {"subnet_id": subnet["id"]})
    assert not result.is_error, result.content[0].text
    asked = scans.utc((await target_row(target["id"])).scan_requested_at)
    assert result.data == {
        "scan_request": {
            "subnet_id": subnet["id"],
            "scan_requested_at": asked.isoformat(timespec="seconds"),
            "next_request_after": scans.next_request_after(asked),
        }
    }
    # the target's configuration does not come back over MCP
    assert "agent_id" not in str(result.data) and "methods" not in str(result.data)

    [job] = await jobs_for(agent_api, token)
    assert job["subnet_id"] == subnet["id"]
    [entry] = (await client.get("/api/v1/changes?entity_type=scan_target&limit=1")).json()["data"]
    assert entry["summary"] == "scan target of subnet lab (10.0.0.0/24): scan requested"
    assert (entry["source"], entry["actor"]) == ("mcp", None)


async def test_request_scan_inside_the_cooldown_says_when_it_can_run_again(client):
    """Acceptance: inside the cooldown, a tool error that says when to ask again."""
    subnet, target, _ = await scannable(client)
    # the web UI asks first: the cooldown is the subnet's, whoever asks
    ui = await client.post(f"/api/v1/subnets/{subnet['id']}/scan", headers={"X-TLG-Client": "ui"})
    assert ui.status_code == 202
    asked = scans.utc((await target_row(target["id"])).scan_requested_at)
    logged = await change_count()

    result = await call("request_scan", {"subnet_id": subnet["id"]})
    assert result.is_error
    again = scans.next_request_after(asked)
    assert result.content[0].text == (
        "A scan of subnet lab was requested less than 15 minutes ago; "
        f"request another after {again}"
    )
    # refused: nothing written, and the cooldown is not restarted
    assert scans.utc((await target_row(target["id"])).scan_requested_at) == asked
    assert await change_count() == logged

    # and the other way round: a request over MCP holds off the web UI and MCP alike
    other, _, _ = await scannable(client, "iot", "10.0.1.0/24")
    first = await call("request_scan", {"subnet_id": other["id"]})
    assert not first.is_error, first.content[0].text
    again = first.data["scan_request"]["next_request_after"]
    second = await call("request_scan", {"subnet_id": other["id"]})
    assert second.is_error
    assert second.content[0].text.endswith(f"request another after {again}")
    res = await client.post(f"/api/v1/subnets/{other['id']}/scan")
    assert res.status_code == 429
    assert res.json()["error"]["code"] == "COOLDOWN"


async def test_next_request_after_is_never_inside_the_cooldown(client):
    """The time the tool gives is rounded up, so asking then is never refused."""
    asked = datetime(2026, 11, 16, 12, 0, 0, 400000, tzinfo=timezone.utc)
    assert scans.next_request_after(asked) == "2026-11-16T12:15:01+00:00"
    assert scans.next_request_after(asked.replace(microsecond=0)) == "2026-11-16T12:15:00+00:00"
    # through the service: a request at the time given passes the cooldown's own test
    subnet, target, _ = await scannable(client)
    # inside the cooldown by 2.5 seconds, at a time with a fraction of a second
    asked = (datetime.now(timezone.utc) - timedelta(minutes=15, seconds=-2)).replace(
        microsecond=500000
    )
    await set_target(target["id"], scan_requested_at=asked)
    refused = await call("request_scan", {"subnet_id": subnet["id"]})
    assert refused.is_error
    at = datetime.fromisoformat(refused.content[0].text.rsplit(" ", 1)[1])
    assert at >= asked + scans.REQUEST_COOLDOWN
    assert at - (asked + scans.REQUEST_COOLDOWN) < timedelta(seconds=1)


@pytest.mark.parametrize(
    ("setup", "message"),
    [
        ("none", "Subnet lab has no scan target: set up scanning first"),
        ("disabled", "Scanning of subnet lab is disabled"),
        ("unassigned", "Subnet lab has no agent assigned to scan it"),
    ],
)
async def test_request_scan_needs_a_configured_target(client, setup, message):
    agent, _ = await make_agent(client)
    section = await make_section(client)
    subnet = await make_subnet(client, section["id"])
    if setup == "disabled":
        await make_target(client, subnet["id"], agent["id"], enabled=False)
    elif setup == "unassigned":
        await make_target(client, subnet["id"], None)
    logged = await change_count()
    result = await call("request_scan", {"subnet_id": subnet["id"]})
    assert result.is_error
    assert result.content[0].text == message
    assert await change_count() == logged
    unknown = await call("request_scan", {"subnet_id": "nope"})
    assert unknown.is_error and unknown.content[0].text == "Subnet nope not found"
