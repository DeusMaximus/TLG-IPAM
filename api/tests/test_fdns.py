"""The forward-DNS check on the server (#20; v0.2 spec §5.2, §5.4, §5.5, §5.7, §7.2
invariant 5): the names a job sends, what ingest keeps of the answers, and the
`fdns_mismatch` finding. Jobs come from real check-ins (`test_checkin` helpers)."""

import ipaddress
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import create_async_engine

from app.agent_protocol import FDNS_ADDRESSES_MAX
from app.database import SessionLocal, init_models
from app.models import Base, Change, FdnsResult, Host, ScanTarget
from app.services import fdns, findings as findings_svc

from .conftest import make_host
from .test_checkin import (  # noqa: F401 (agent_api is a fixture)
    agent_api,
    jobs_for,
    lab,
    make_agent,
    make_target,
    set_target,
    target_row,
)
from .test_findings import accept, answer, dismiss, findings, key
from .test_results import accepted, change_count, obs, post_results

ALL = ["icmp", "arp", "rdns", "fdns"]


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def rows() -> dict[str, FdnsResult]:
    async with SessionLocal() as session:
        return {r.host_id: r for r in await session.scalars(select(FdnsResult))}


async def named(client, agent_api, subnet=None, methods=ALL, **target):
    """An agent, the lab subnet under home.arpa (unless `subnet` says otherwise), and its
    target issued `fdns`."""
    agent, token = await make_agent(client)
    made = await lab(client, **(subnet if subnet is not None else {"domain": "home.arpa"}))
    tgt = await make_target(client, made["id"], agent["id"], methods=methods, **target)
    return agent, token, made, tgt


async def next_job(agent_api, token, target_id):
    """The target made due again and its next job."""
    await set_target(target_id, last_scan_at=_now() - timedelta(days=1))
    [job] = await jobs_for(agent_api, token)
    return job


# --- What a job sends (invariant 5) ---


async def test_a_job_names_only_in_scope_hosts_whose_fqdn_is_under_the_subnets_domain(client, agent_api):
    """#20's first acceptance line: a host whose FQDN is outside the subnet's domain is
    never sent, whether by its own domain override or a lookalike suffix, nor one
    whose FQDN is no DNS hostname, nor one outside the scan scope (an excluded range,
    the unscanned DHCP pool). Names go lowercased, in address order, and the lease
    keeps what was sent."""
    agent, token = await make_agent(client)
    subnet = await lab(client, domain="home.arpa", dhcp_start="10.0.0.100", dhcp_end="10.0.0.150")
    res = await client.post(
        f"/api/v1/subnets/{subnet['id']}/reserved",
        json={"start_ip": "10.0.0.200", "end_ip": "10.0.0.210", "scan_excluded": True},
    )
    assert res.status_code == 201, res.text
    sent = {}
    for ip, fields in {
        "10.0.0.10": {"hostname": "nas"},
        "10.0.0.11": {"hostname": "Printer"},
        "10.0.0.12": {"hostname": "cam", "domain": "iot.home.arpa"},
        "10.0.0.13": {"hostname": "nested.name"},
        "10.0.0.20": {"hostname": "outside", "domain": "example.com"},
        "10.0.0.21": {"hostname": "lookalike", "domain": "evilhome.arpa"},
        "10.0.0.22": {"hostname": "x", "domain": "home.arpa.example.com"},
        "10.0.0.23": {"hostname": "not a name"},
        "10.0.0.24": {"hostname": "under_score"},
        "10.0.0.25": {},
        "10.0.0.26": {"hostname": "a" * 250},  # its FQDN would be over 253
        "10.0.0.120": {"hostname": "leased"},  # in the pool, which is not scanned
        "10.0.0.205": {"hostname": "excluded"},
    }.items():
        host = await make_host(client, subnet["id"], ip, **fields)
        sent[ip] = host["id"]
    target = await make_target(client, subnet["id"], agent["id"], methods=["icmp", "fdns"])
    [job] = await jobs_for(agent_api, token)
    expected = [
        {"host_id": sent["10.0.0.10"], "fqdn": "nas.home.arpa"},
        {"host_id": sent["10.0.0.11"], "fqdn": "printer.home.arpa"},
        {"host_id": sent["10.0.0.12"], "fqdn": "cam.iot.home.arpa"},
        {"host_id": sent["10.0.0.13"], "fqdn": "nested.name.home.arpa"},
    ]
    assert job["methods"] == ["icmp", "fdns"] and job["fdns"] == expected
    row = await target_row(target["id"])
    assert row.job_fdns == [[e["host_id"], e["fqdn"]] for e in expected]


@pytest.mark.parametrize("subnet", [{}, {"domain": "home.arpa"}])
async def test_a_job_names_no_host_without_a_domain_or_without_fdns(client, agent_api, subnet):
    agent, token = await make_agent(client)
    made = await lab(client, **subnet)
    await make_host(client, made["id"], "10.0.0.10", hostname="nas", domain="example.com")
    methods = ["icmp", "fdns"] if not subnet else ["icmp", "rdns"]
    target = await make_target(client, made["id"], agent["id"], methods=methods)
    [job] = await jobs_for(agent_api, token)
    assert job["fdns"] == []
    assert (await target_row(target["id"])).job_fdns == ([] if "fdns" in methods else None)


async def test_past_the_job_bound_the_names_checked_longest_ago_go_first(client, agent_api, monkeypatch):
    """At most `FDNS_JOB_MAX` names a job: those never checked first, then those checked
    longest ago, so every host is checked in turn."""
    monkeypatch.setattr(fdns, "FDNS_JOB_MAX", 2)
    agent, token, subnet, target = await named(client, agent_api, methods=["icmp", "fdns"])
    hosts = [await make_host(client, subnet["id"], f"10.0.0.{n}", hostname=f"h{n}") for n in (10, 11, 12)]
    [job] = await jobs_for(agent_api, token)
    assert [e["host_id"] for e in job["fdns"]] == [hosts[0]["id"], hosts[1]["id"]]
    await accepted(agent_api, token, job, fdns=[{"host_id": h["id"], "addresses": []} for h in hosts[:2]])
    async with SessionLocal() as session:
        await session.execute(
            update(FdnsResult).where(FdnsResult.host_id == hosts[1]["id"]).values(checked_at=_now() - timedelta(days=2))
        )
        await session.commit()
    job = await next_job(agent_api, token, target["id"])
    # never checked, then the older of the two
    assert [e["host_id"] for e in job["fdns"]] == [hosts[1]["id"], hosts[2]["id"]]


def test_the_name_rule_is_the_hosts_fqdn_under_the_subnets_domain():
    assert fdns.sendable_fqdn("NAS", None, "Home.Arpa") == "nas.home.arpa"
    assert fdns.sendable_fqdn("nas", "lab.home.arpa", "home.arpa") == "nas.lab.home.arpa"
    assert fdns.sendable_fqdn("nas", "home.arpa", "home.arpa") == "nas.home.arpa"
    for args in (
        ("nas", "example.com", "home.arpa"),
        ("nas", None, None),
        ("nas", "home.arpa", None),  # a host's own domain with none on the subnet
        ("nás", None, "home.arpa"),
        ("nas.", None, "home.arpa"),
        (None, None, "home.arpa"),
        ("nas", "xhome.arpa", "home.arpa"),
    ):
        assert fdns.sendable_fqdn(*args) is None, args


# --- What ingest keeps ---


async def test_answers_are_stored_for_the_hosts_sent_under_the_name_that_was_asked(client, agent_api):
    agent, token, subnet, target = await named(client, agent_api)
    nas = await make_host(client, subnet["id"], "10.0.0.10", hostname="nas")
    tv = await make_host(client, subnet["id"], "10.0.0.11", hostname="tv")
    [job] = await jobs_for(agent_api, token)
    # renamed while the job ran: the answer is the old name's
    res = await client.put(f"/api/v1/hosts/{nas['id']}", json={"hostname": "storage"})
    assert res.status_code == 200, res.text
    await accepted(
        agent_api, token, job, [obs("10.0.0.10")],
        fdns=[
            {"host_id": nas["id"], "addresses": ["10.0.0.99", "10.0.0.10", "10.0.0.10"]},
            {"host_id": tv["id"], "addresses": []},
        ],
    )
    stored = await rows()
    assert stored[nas["id"]].fqdn == "nas.home.arpa"
    assert stored[nas["id"]].addresses == ["10.0.0.10", "10.0.0.99"]  # each once, sorted
    assert stored[tv["id"]].fqdn == "tv.home.arpa" and stored[tv["id"]].addresses == []
    assert (await target_row(target["id"])).job_fdns is None  # cleared with the lease
    # an answer for a name the host no longer has raises nothing; the other does
    found = [f for f in await findings(client, subnet["id"]) if f["type"] == "fdns_mismatch"]
    assert [(f["ip"], f["observed"]) for f in found] == [("10.0.0.11", "unresolved")]


async def test_an_entry_the_job_did_not_ask_for_or_cannot_be_read_is_not_stored(client, agent_api):
    agent, token, subnet, target = await named(client, agent_api)
    nas = await make_host(client, subnet["id"], "10.0.0.10", hostname="nas")
    tv = await make_host(client, subnet["id"], "10.0.0.11", hostname="tv")
    cam = await make_host(client, subnet["id"], "10.0.0.12", hostname="cam")
    outside = await make_host(client, subnet["id"], "10.0.0.13", hostname="x", domain="example.com")
    [job] = await jobs_for(agent_api, token)
    assert len(job["fdns"]) == 3
    data = await accepted(
        agent_api, token, job,
        fdns=[
            {"host_id": outside["id"], "addresses": ["10.0.0.13"]},  # not sent
            {"host_id": "f" * 36, "addresses": []},  # no such host
            {"host_id": nas["id"], "addresses": ["10.0.0.999"]},  # not an address
            {"host_id": nas["id"], "addresses": ["10.0.0.10"]},  # a second entry for it
            {"host_id": tv["id"], "addresses": ["2001:db8::1"]},  # not IPv4
            {"host_id": cam["id"], "addresses": ["10.0.0.12"]},
        ],
    )
    assert data == {"accepted": 0, "rejected": []}
    assert set(await rows()) == {cam["id"]}


async def test_a_malformed_fdns_list_refuses_the_result_set_whole(client, agent_api):
    agent, token, subnet, target = await named(client, agent_api)
    await make_host(client, subnet["id"], "10.0.0.10", hostname="nas")
    [job] = await jobs_for(agent_api, token)
    for fdns_list in (
        [{"host_id": "x" * 37, "addresses": []}],
        [{"host_id": "x", "addresses": ["10.0.0.1"] * (FDNS_ADDRESSES_MAX + 1)}],
        [{"host_id": "x"}],
        "nas",
    ):
        res = await post_results(agent_api, token, job, fdns=fdns_list)
        assert res.status_code == 422, res.text
    assert (await target_row(target["id"])).current_job_id == job["job_id"]


async def test_a_name_with_no_entry_keeps_its_stored_answer(client, agent_api):
    """An agent leaves out a name no DNS server gave a usable answer for: that says
    nothing, and the answer stored before stays, as a PTR lookup's name stays."""
    agent, token, subnet, target = await named(client, agent_api)
    nas = await make_host(client, subnet["id"], "10.0.0.10", hostname="nas")
    [job] = await jobs_for(agent_api, token)
    await accepted(agent_api, token, job, fdns=[{"host_id": nas["id"], "addresses": ["10.0.0.99"]}])
    first = (await rows())[nas["id"]]
    job = await next_job(agent_api, token, target["id"])
    await accepted(agent_api, token, job, complete=False, error="fdns: no DNS server gave a usable answer")
    kept = (await rows())[nas["id"]]
    assert kept.addresses == ["10.0.0.99"] and kept.checked_at == first.checked_at
    job = await next_job(agent_api, token, target["id"])
    await accepted(agent_api, token, job, fdns=[{"host_id": nas["id"], "addresses": ["10.0.0.10"]}])
    assert (await rows())[nas["id"]].addresses == ["10.0.0.10"]


async def test_rows_of_hosts_that_are_gone_are_pruned_and_a_host_deleted_mid_scan_gets_none(client, agent_api):
    agent, token, subnet, target = await named(client, agent_api)
    nas = await make_host(client, subnet["id"], "10.0.0.10", hostname="nas")
    tv = await make_host(client, subnet["id"], "10.0.0.11", hostname="tv")
    [job] = await jobs_for(agent_api, token)
    await accepted(agent_api, token, job, fdns=[{"host_id": nas["id"], "addresses": []}])
    assert set(await rows()) == {nas["id"]}
    job = await next_job(agent_api, token, target["id"])
    assert (await client.delete(f"/api/v1/hosts/{nas['id']}")).status_code == 204
    assert (await client.delete(f"/api/v1/hosts/{tv['id']}")).status_code == 204
    await accepted(agent_api, token, job, fdns=[{"host_id": tv["id"], "addresses": []}])
    assert await rows() == {}


async def test_no_answer_is_kept_for_a_host_outside_the_scope_and_leaving_it_deletes_one(client, agent_api):
    """Forward-DNS answers exist only inside the scan scope, as observations do (spec
    §5.3): a host whose address was excluded while the job ran gets no row, and
    excluding the address of a host that has one deletes it, in the same transaction."""
    agent, token, subnet, target = await named(client, agent_api)
    nas = await make_host(client, subnet["id"], "10.0.0.10", hostname="nas")
    tv = await make_host(client, subnet["id"], "10.0.0.20", hostname="tv")
    [job] = await jobs_for(agent_api, token)
    res = await client.post(
        f"/api/v1/subnets/{subnet['id']}/reserved",
        json={"start_ip": "10.0.0.20", "end_ip": "10.0.0.20", "scan_excluded": True},
    )
    assert res.status_code == 201, res.text
    await accepted(
        agent_api, token, job,
        fdns=[{"host_id": nas["id"], "addresses": []}, {"host_id": tv["id"], "addresses": []}],
    )
    assert set(await rows()) == {nas["id"]}
    res = await client.post(
        f"/api/v1/subnets/{subnet['id']}/reserved",
        json={"start_ip": "10.0.0.10", "end_ip": "10.0.0.10", "scan_excluded": True},
    )
    assert res.status_code == 201, res.text
    assert await rows() == {}


async def test_ingest_with_answers_writes_no_host_and_no_change_entry(client, agent_api):
    """Invariant 3 with the forward-DNS answers: `services/fdns` reads `hosts` to know
    which hosts are still there and in scope, and writes neither them nor the log."""
    agent, token, subnet, target = await named(client, agent_api)
    nas = await make_host(client, subnet["id"], "10.0.0.10", hostname="nas")
    async with SessionLocal() as session:
        before = (await session.get(Host, nas["id"])).__dict__.copy()
    [job] = await jobs_for(agent_api, token)
    entries = await change_count()
    await accepted(agent_api, token, job, [obs("10.0.0.10")], fdns=[{"host_id": nas["id"], "addresses": ["10.0.0.9"]}])
    assert await change_count() == entries
    async with SessionLocal() as session:
        after = (await session.get(Host, nas["id"])).__dict__.copy()
        assert await session.scalar(select(func.count(Change.id)).where(Change.entity_type == "host")) == 1
    columns = [c.name for c in Host.__table__.columns]
    assert {c: after[c] for c in columns} == {c: before[c] for c in columns}
    assert (await rows())[nas["id"]].addresses == ["10.0.0.9"]


async def test_the_names_a_lease_holds_stay_out_of_audit_snapshots(client, agent_api):
    agent, token, subnet, target = await named(client, agent_api)
    await make_host(client, subnet["id"], "10.0.0.10", hostname="nas")
    [job] = await jobs_for(agent_api, token)
    res = await client.put(f"/api/v1/scan-targets/{target['id']}", json={"interval_minutes": 30})
    assert res.status_code == 200, res.text
    async with SessionLocal() as session:
        entry = await session.scalar(
            select(Change).where(Change.entity_type == "scan_target").order_by(Change.ts.desc())
        )
    assert "job_fdns" not in entry.before and "job_fdns" not in entry.after
    assert "nas.home.arpa" not in str(entry.before) + str(entry.after)


async def test_an_existing_database_gains_the_table_and_the_lease_column(tmp_path):
    path = tmp_path / "old.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.execute(text("DROP TABLE fdns_results"))
            await conn.execute(text("ALTER TABLE scan_targets DROP COLUMN job_fdns"))
        await init_models(engine)
        await init_models(engine)  # a second start-up adds nothing twice
        async with engine.connect() as conn:
            columns = {r[1]: r for r in (await conn.exec_driver_sql("PRAGMA table_info(scan_targets)")).all()}
            tables = {r[0] for r in (await conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table'")).all()}
            indexes = (await conn.exec_driver_sql("PRAGMA index_list(fdns_results)")).all()
        assert "job_fdns" in columns and columns["job_fdns"][3] == 0  # nullable
        assert "fdns_results" in tables
        assert any(r[2] for r in indexes)  # host_id is unique
    finally:
        await engine.dispose()


# --- The finding ---


async def host_with_answer(client, subnet, addresses, ip="10.0.0.10", fqdn="nas.home.arpa", **host):
    made = await make_host(client, subnet["id"], ip, hostname=host.pop("hostname", "nas"), **host)
    await answer(made["id"], fqdn, addresses)
    return made


@pytest.mark.parametrize(
    "addresses, observed",
    [
        ([], "unresolved"),
        (["10.0.0.99"], "10.0.0.99"),
        (["10.0.0.20", "10.0.0.99"], "10.0.0.20, 10.0.0.99"),
        (["10.0.0.10"], None),
        (["10.0.0.9", "10.0.0.10", "10.0.0.11"], None),
    ],
)
async def test_an_answer_without_the_hosts_address_is_a_finding(client, agent_api, addresses, observed):
    """#20's second acceptance line: a name that does not resolve is `unresolved`. An
    answer that holds other addresses only lists them; one that holds the host's own
    raises nothing. The finding is informational: it proposes nothing."""
    agent, token, subnet, target = await named(client, agent_api)
    made = await host_with_answer(client, subnet, addresses)
    found = [f for f in await findings(client, subnet["id"]) if f["type"] == "fdns_mismatch"]
    if observed is None:
        assert found == []
        return
    [f] = found
    assert f["observed"] == observed and f["proposed"] == {} and f["host_id"] == made["id"]
    assert f["ip"] == "10.0.0.10"


async def test_the_observed_value_of_the_largest_answer_fits_a_key(client, agent_api):
    agent, token, subnet, target = await named(client, agent_api)
    widest = [str(ipaddress.ip_address("192.168.255.200") + i) for i in range(FDNS_ADDRESSES_MAX)]
    await host_with_answer(client, subnet, widest)
    [f] = [f for f in await findings(client, subnet["id"]) if f["type"] == "fdns_mismatch"]
    shown = widest[: findings_svc.FDNS_OBSERVED_ADDRESSES]
    assert f["observed"] == ", ".join(shown) + f" and {FDNS_ADDRESSES_MAX - len(shown)} more"
    assert len(f["observed"]) == 214 <= 253
    # it can be dismissed by that key
    assert (await dismiss(client, key(f)))["dismissed"] == 1


@pytest.mark.parametrize("case", ["no-fdns-on-the-target", "no-target", "renamed", "out-of-scope", "domain-moved"])
async def test_no_finding_without_a_current_answer_for_the_name_a_job_would_ask(client, agent_api, case):
    agent, token = await make_agent(client)
    subnet = await lab(client, domain="home.arpa")
    if case != "no-target":
        methods = ["icmp", "rdns"] if case == "no-fdns-on-the-target" else ["icmp", "fdns"]
        await make_target(client, subnet["id"], agent["id"], methods=methods)
    made = await host_with_answer(client, subnet, [])
    if case == "renamed":
        assert (await client.put(f"/api/v1/hosts/{made['id']}", json={"hostname": "storage"})).status_code == 200
    elif case == "out-of-scope":
        res = await client.post(
            f"/api/v1/subnets/{subnet['id']}/reserved",
            json={"start_ip": "10.0.0.10", "end_ip": "10.0.0.10", "scan_excluded": True},
        )
        assert res.status_code == 201
        await answer(made["id"], "nas.home.arpa", [])  # as if it had been kept
    elif case == "domain-moved":
        res = await client.put(f"/api/v1/hosts/{made['id']}", json={"domain": "example.com"})
        assert res.status_code == 200
    assert [f for f in await findings(client, subnet["id"]) if f["type"] == "fdns_mismatch"] == []


async def test_accepting_an_fdns_mismatch_writes_nothing_and_dismissing_it_hides_it_until_it_changes(client, agent_api):
    agent, token, subnet, target = await named(client, agent_api)
    made = await host_with_answer(client, subnet, [])
    [f] = [f for f in await findings(client, subnet["id"]) if f["type"] == "fdns_mismatch"]
    entries = await change_count()
    report = await accept(client, key(f))
    assert report["accepted"] == 0 and report["errors"] == 1
    assert report["results"][0]["reason"] == findings_svc.INFORMATIONAL_REASON
    assert await change_count() == entries
    assert (await dismiss(client, key(f)))["dismissed"] == 1
    assert [f for f in await findings(client, subnet["id"]) if f["type"] == "fdns_mismatch"] == []
    async with SessionLocal() as session:
        await session.execute(
            update(FdnsResult).where(FdnsResult.host_id == made["id"]).values(addresses=["10.0.0.99"])
        )
        await session.commit()
    [again] = [f for f in await findings(client, subnet["id"]) if f["type"] == "fdns_mismatch"]
    assert again["observed"] == "10.0.0.99"


async def test_the_finding_type_filters_and_orders_before_stale(client, agent_api):
    agent, token, subnet, target = await named(client, agent_api)
    await host_with_answer(client, subnet, [])
    only = await findings(client, subnet["id"], type="fdns_mismatch")
    assert [f["type"] for f in only] == ["fdns_mismatch"]
    types = findings_svc.FINDING_TYPES
    assert types.index("name_mismatch") < types.index("fdns_mismatch") < types.index("stale")


async def test_a_target_row_written_by_the_lease_alone_holds_the_names(client, agent_api):
    """The names are scheduling state on the target, written with the lease in one
    compare-and-set, as `job_methods` is."""
    agent, token, subnet, target = await named(client, agent_api, methods=["icmp", "fdns"])
    host = await make_host(client, subnet["id"], "10.0.0.10", hostname="nas")
    [job] = await jobs_for(agent_api, token)
    async with SessionLocal() as session:
        row = await session.get(ScanTarget, target["id"])
        assert row.current_job_id == job["job_id"] and row.job_fdns == [[host["id"], "nas.home.arpa"]]


# --- Over MCP ---


async def test_list_scan_findings_lists_an_fdns_mismatch_and_its_largest_fits_the_bound(client, agent_api):
    """The tool lists the new type, filtered or not, with the note that what the network
    reported is data. Its largest finding (a subnet name of control characters, an FQDN
    of 253 characters, 32 of the widest addresses) is well inside `FINDING_MAX`, below
    the name finding that sets the bound (4,880 bytes)."""
    from .test_mcp import call, encoded, scan_findings

    agent, token = await make_agent(client)
    subnet = await lab(client, cidr="192.168.255.0/24", name="\x01" * 120, domain="home.arpa")
    await make_target(client, subnet["id"], agent["id"], methods=["icmp", "fdns"])
    hostname = ".".join(["a" * 63] * 3 + ["b" * (253 - len(".home.arpa") - 3 * 64)])
    fqdn = f"{hostname}.home.arpa"
    assert len(fqdn) == 253
    widest = [str(ipaddress.ip_address("192.168.255.200") + i) for i in range(FDNS_ADDRESSES_MAX)]
    await host_with_answer(client, subnet, widest, ip="192.168.255.254", hostname=hostname, fqdn=fqdn)
    for args in ({}, {"type": "fdns_mismatch"}, {"subnet_id": subnet["id"]}):
        [f] = await scan_findings(**args)
        assert f["type"] == "fdns_mismatch" and f["host_fqdn"] == fqdn
    assert encoded(f) < 4_880 <= findings_svc.FINDING_MAX
    result = await call("list_scan_findings", {"type": "fdns_mismatch"})
    assert "addresses a DNS server gave" in result.structured_content["note"]


async def test_the_largest_result_set_with_answers_fits_the_body_cap(client, agent_api):
    """What bounds `FDNS_JOB_MAX` from the result set's side (#20; the spec's note on the
    body cap): every address of a /20 observed with a MAC and a 253-character name, the
    longest error, and an answer for each of as many names as a job sends, each with
    the most addresses an entry carries, at IPv4's widest, as the agent's client encodes
    it (compact JSON). It is under `BODY_MAX`, and the route serves it; twice as many
    answers would not be."""
    import json

    from app.agent_protocol import SCAN_ERROR_MAX
    from app.body_limit import BODY_MAX

    label = "a" * 63
    name = ".".join([label, label, label, "a" * 61])
    agent, token = await make_agent(client)
    subnet = await lab(client, cidr="10.0.0.0/20", domain="home.arpa")
    await make_target(client, subnet["id"], agent["id"], methods=ALL)
    [job] = await jobs_for(
        agent_api, token, interfaces=[{"name": "eth0", "cidr": "10.0.0.5/20", "mac": "02:00:00:00:00:05"}]
    )
    assert "arp" in job["methods"] and "fdns" in job["methods"]
    addresses = list(ipaddress.ip_network("10.0.0.0/20").hosts())
    widest = ["255.255.255.255"] * FDNS_ADDRESSES_MAX

    def body(answers: int) -> bytes:
        started = _now().isoformat()
        return json.dumps(
            {
                "protocol": 2, "job_id": job["job_id"], "subnet_id": job["subnet_id"],
                "started_at": started, "finished_at": started, "complete": False, "liveness_complete": False,
                "observations": [
                    {"ip": str(a), "mac": f"02:00:00:00:{i >> 8:02x}:{i & 255:02x}", "rdns_name": name}
                    for i, a in enumerate(addresses)
                ],
                "fdns": [{"host_id": f"{i:036d}", "addresses": widest} for i in range(answers)],
                "error": "x" * SCAN_ERROR_MAX,
            },
            separators=(",", ":"),
        ).encode()

    largest = body(fdns.FDNS_JOB_MAX)
    assert 1_900_000 < len(largest) < BODY_MAX
    assert len(body(2 * fdns.FDNS_JOB_MAX)) > BODY_MAX
    res = await agent_api.post(
        "/api/v1/agent/results",
        content=largest,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["data"]["accepted"] == 4094
