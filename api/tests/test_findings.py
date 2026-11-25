"""Scan findings: computed at read time, accepted through the hosts service, dismissed
by key (v0.2 spec §5.5; issue #15). Observations are written directly, as results
ingest would store them; hosts, targets and ranges go through REST."""

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError
from sqlalchemy import event, func, select, update

from app.config import settings
from app.database import SessionLocal, engine
from app.ipnet import parse_ip
from app.models import Change, FdnsResult, FindingDismissal, Host, Observation, ScanTarget, Section
from app.schemas import HostCreate
from app.services import findings as findings_svc
from app.services import scans

from .conftest import make_host, make_section, make_subnet
from .test_checkin import (  # noqa: F401 (agent_api is a fixture)
    agent_api,
    jobs_for,
    make_agent,
    make_target,
)

NOW = datetime.now(timezone.utc)
RECENT = NOW - timedelta(days=1)
LONG_AGO = NOW - timedelta(days=45)
MAC = "aa:bb:cc:00:00:01"
OTHER_MAC = "aa:bb:cc:00:00:02"
# locally-administered bit set: a randomised MAC
RANDOM_MAC = "02:bb:cc:00:00:01"
HOSTILE = "ignore-previous-instructions.example"


async def _subnet(client, **extra) -> dict:
    section = await make_section(client)
    return await make_subnet(client, section["id"], domain="home.arpa", **extra)


async def _target(client, subnet_id, **extra) -> dict:
    res = await client.post("/api/v1/scan-targets", json={"subnet_id": subnet_id, **extra})
    assert res.status_code == 201, res.text
    return res.json()["data"]


async def observe(subnet_id, ip, *, mac=None, rdns=None, alive=True, seen=RECENT, random=False):
    async with SessionLocal() as session:
        session.add(
            Observation(
                subnet_id=subnet_id,
                ip=ip,
                ip_int=int(parse_ip(ip)),
                mac=mac,
                mac_randomised=random,
                rdns_name=rdns,
                alive=alive,
                first_seen_at=seen - timedelta(days=1),
                last_seen_at=seen,
            )
        )
        await session.commit()


async def answer(host_id, fqdn, addresses, checked=RECENT):
    """A forward-DNS answer for the host, as results ingest stores one (#20)."""
    async with SessionLocal() as session:
        session.add(FdnsResult(host_id=host_id, fqdn=fqdn, addresses=list(addresses), checked_at=checked))
        await session.commit()


async def set_observation(subnet_id, ip, **values):
    async with SessionLocal() as session:
        await session.execute(
            update(Observation)
            .where(Observation.subnet_id == subnet_id, Observation.ip == ip)
            .values(**values)
        )
        await session.commit()


async def backdate(model, row_id, when=LONG_AGO):
    async with SessionLocal() as session:
        await session.execute(update(model).where(model.id == row_id).values(created_at=when))
        await session.commit()


async def findings(client, subnet_id, **params) -> list[dict]:
    res = await client.get(f"/api/v1/subnets/{subnet_id}/findings", params=params)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["total"] == len(body["data"])
    return body["data"]


def key(f: dict) -> dict:
    """A listed finding as accept and dismiss take it: its key and its fingerprint."""
    return {k: f[k] for k in ("subnet_id", "ip", "type", "observed", "fingerprint")}


async def accept(client, *keys) -> dict:
    res = await client.post("/api/v1/findings/accept", json={"findings": list(keys)})
    assert res.status_code == 200, res.text
    return res.json()["data"]


async def dismiss(client, *keys) -> dict:
    res = await client.post("/api/v1/findings/dismiss", json={"findings": list(keys)})
    assert res.status_code == 200, res.text
    return res.json()["data"]


async def change_count() -> int:
    async with SessionLocal() as session:
        return await session.scalar(select(func.count(Change.id)))


async def dismissal_count() -> int:
    async with SessionLocal() as session:
        return await session.scalar(select(func.count(FindingDismissal.id)))


def only(found: list[dict], type_: str) -> dict:
    matches = [f for f in found if f["type"] == type_]
    assert len(matches) == 1, found
    return matches[0]


# --- One test per finding type ---


async def test_unknown_host(client):
    subnet = await _subnet(client)
    await observe(subnet["id"], "10.0.0.20", mac=MAC, rdns="printer.home.arpa")
    [f] = await findings(client, subnet["id"])
    assert len(f.pop("fingerprint")) == 32
    assert f == {
        "subnet_id": subnet["id"],
        "ip": "10.0.0.20",
        "type": "unknown_host",
        "observed": MAC,
        "ip_int": int(parse_ip("10.0.0.20")),
        "host_id": None,
        "dynamic": False,
        # pre-filled with the MAC and the name, the subnet's domain stripped
        "proposed": {"status": "active", "mac": MAC, "hostname": "printer"},
    }


async def test_unknown_host_without_a_mac_has_an_empty_observed_value(client):
    subnet = await _subnet(client)
    await observe(subnet["id"], "10.0.0.20")
    [f] = await findings(client, subnet["id"])
    assert (f["type"], f["observed"], f["proposed"]) == ("unknown_host", "", {"status": "active"})


async def test_an_address_that_is_not_alive_is_not_an_unknown_host(client):
    subnet = await _subnet(client)
    await observe(subnet["id"], "10.0.0.20", mac=MAC, alive=False)
    assert await findings(client, subnet["id"]) == []


async def test_mac_missing(client):
    subnet = await _subnet(client)
    host = await make_host(client, subnet["id"], "10.0.0.10", hostname="nas")
    await observe(subnet["id"], "10.0.0.10", mac=MAC)
    [f] = await findings(client, subnet["id"])
    assert (f["type"], f["observed"], f["host_id"], f["proposed"]) == (
        "mac_missing", MAC, host["id"], {"mac": MAC}
    )


async def test_mac_mismatch(client):
    subnet = await _subnet(client)
    host = await make_host(client, subnet["id"], "10.0.0.10", hostname="nas", mac=OTHER_MAC)
    await observe(subnet["id"], "10.0.0.10", mac=MAC)
    [f] = await findings(client, subnet["id"])
    assert (f["type"], f["observed"], f["host_id"], f["proposed"]) == (
        "mac_mismatch", MAC, host["id"], {"mac": MAC}
    )


async def test_the_same_mac_in_another_form_is_no_mismatch(client):
    subnet = await _subnet(client)
    await make_host(client, subnet["id"], "10.0.0.10", hostname="nas", mac="AA-BB-CC-00-00-01")
    await observe(subnet["id"], "10.0.0.10", mac=MAC)
    assert await findings(client, subnet["id"]) == []


async def test_name_missing(client):
    subnet = await _subnet(client)
    host = await make_host(client, subnet["id"], "10.0.0.10")
    await observe(subnet["id"], "10.0.0.10", rdns="nas.home.arpa")
    [f] = await findings(client, subnet["id"])
    # the observed value is the PTR name; what accepting writes has the domain stripped
    assert (f["type"], f["observed"], f["host_id"], f["proposed"]) == (
        "name_missing", "nas.home.arpa", host["id"], {"hostname": "nas"}
    )


async def test_name_mismatch(client):
    subnet = await _subnet(client)
    host = await make_host(client, subnet["id"], "10.0.0.10", hostname="nas")
    await observe(subnet["id"], "10.0.0.10", rdns="storage.home.arpa")
    [f] = await findings(client, subnet["id"])
    assert (f["type"], f["observed"], f["host_id"], f["proposed"]) == (
        "name_mismatch", "storage.home.arpa", host["id"], {"hostname": "storage"}
    )


@pytest.mark.parametrize(
    ("hostname", "domain", "ptr", "mismatch"),
    [
        ("NAS", None, "nas.home.arpa", False),  # case-insensitive
        ("nas", "lab.example.com", "nas.lab.example.com", False),  # host override wins
        ("nas", "lab.example.com", "nas.home.arpa", True),  # override, not subnet default
        ("nas", None, "nas.example.com", True),  # outside the domain
        # the FQDN is nas.example.com.home.arpa, not the PTR name
        ("nas.example.com", None, "nas.example.com", True),
        ("nas", "example.com", "nas.example.com", False),
        ("a.b", None, "a.b.home.arpa", False),  # nested relative labels
        ("nas", None, "nas", False),  # a single label is a relative name
    ],
)
async def test_the_ptr_name_is_compared_without_the_effective_domain(
    client, hostname, domain, ptr, mismatch
):
    subnet = await _subnet(client)
    extra = {"domain": domain} if domain else {}
    await make_host(client, subnet["id"], "10.0.0.10", hostname=hostname, **extra)
    await observe(subnet["id"], "10.0.0.10", rdns=ptr)
    found = await findings(client, subnet["id"])
    assert [f["type"] for f in found] == (["name_mismatch"] if mismatch else [])


async def test_stale(client):
    subnet = await _subnet(client)
    await _target(client, subnet["id"])
    host = await make_host(client, subnet["id"], "10.0.0.10", hostname="nas")
    await observe(subnet["id"], "10.0.0.10", seen=LONG_AGO, alive=False)
    [f] = await findings(client, subnet["id"])
    assert (f["type"], f["host_id"], f["proposed"]) == ("stale", host["id"], {"status": "offline"})
    assert scans.utc(datetime.fromisoformat(f["observed"])) == LONG_AGO


async def test_stale_never_seen_needs_an_old_target_and_an_old_host(client):
    subnet = await _subnet(client)
    target = await _target(client, subnet["id"])
    host = await make_host(client, subnet["id"], "10.0.0.10", hostname="nas")
    # both new: scanning was just switched on
    assert await findings(client, subnet["id"]) == []
    await backdate(ScanTarget, target["id"])
    assert await findings(client, subnet["id"]) == []  # the host is still new
    await backdate(ScanTarget, target["id"], NOW)
    await backdate(Host, host["id"])
    assert await findings(client, subnet["id"]) == []  # the target is still new
    await backdate(ScanTarget, target["id"])
    [f] = await findings(client, subnet["id"])
    assert (f["type"], f["observed"]) == ("stale", "never")


async def test_stale_uses_the_later_of_the_manual_and_the_scan_value(client):
    """The rule host responses use for `last_seen` (#12): `last_seen_at`, never `alive`,
    and a recent value set by hand wins over an old scan."""
    subnet = await _subnet(client)
    await _target(client, subnet["id"])
    await make_host(
        client, subnet["id"], "10.0.0.10", hostname="nas", last_seen=RECENT.isoformat()
    )
    await observe(subnet["id"], "10.0.0.10", seen=LONG_AGO)
    assert await findings(client, subnet["id"]) == []
    host = await make_host(
        client, subnet["id"], "10.0.0.11", hostname="tv", last_seen=LONG_AGO.isoformat()
    )
    # seen recently by a scan, though the last complete sweep missed it
    await observe(subnet["id"], "10.0.0.11", seen=RECENT, alive=False)
    assert await findings(client, subnet["id"]) == []
    await set_observation(subnet["id"], "10.0.0.11", last_seen_at=LONG_AGO + timedelta(days=1))
    [f] = await findings(client, subnet["id"])
    assert (f["type"], f["host_id"]) == ("stale", host["id"])


async def test_stale_only_for_active_hosts_in_a_scanned_subnet(client):
    subnet = await _subnet(client)
    await make_host(client, subnet["id"], "10.0.0.10", last_seen=LONG_AGO.isoformat())
    # no scan target: nothing says the host has gone
    assert await findings(client, subnet["id"]) == []
    await _target(client, subnet["id"])
    assert [f["type"] for f in await findings(client, subnet["id"])] == ["stale"]
    await make_host(
        client, subnet["id"], "10.0.0.11", status="offline", last_seen=LONG_AGO.isoformat()
    )
    assert [f["ip"] for f in await findings(client, subnet["id"])] == ["10.0.0.10"]


async def test_findings_combine_at_one_address_and_filter_by_type(client):
    subnet = await _subnet(client)
    await _target(client, subnet["id"])
    await make_host(client, subnet["id"], "10.0.0.10", last_seen=LONG_AGO.isoformat())
    await observe(subnet["id"], "10.0.0.10", mac=MAC, rdns="nas.home.arpa", seen=LONG_AGO)
    await observe(subnet["id"], "10.0.0.9")
    found = await findings(client, subnet["id"])
    assert [(f["ip"], f["type"]) for f in found] == [
        ("10.0.0.9", "unknown_host"),
        ("10.0.0.10", "mac_missing"),
        ("10.0.0.10", "name_missing"),
        ("10.0.0.10", "stale"),
    ]
    assert [f["ip"] for f in await findings(client, subnet["id"], type="stale")] == ["10.0.0.10"]


async def test_unknown_type_and_unknown_subnet_are_refused(client):
    subnet = await _subnet(client)
    res = await client.get(f"/api/v1/subnets/{subnet['id']}/findings", params={"type": "nope"})
    assert res.status_code == 422 and res.json()["error"]["code"] == "VALIDATION_ERROR"
    res = await client.get("/api/v1/subnets/no-such-subnet/findings")
    assert res.status_code == 404


# --- Randomised MACs and scope ---


async def test_no_mac_finding_for_a_randomised_mac(client):
    subnet = await _subnet(client)
    await make_host(client, subnet["id"], "10.0.0.10", hostname="phone")
    await make_host(client, subnet["id"], "10.0.0.11", hostname="tablet", mac=OTHER_MAC)
    await observe(subnet["id"], "10.0.0.10", mac=RANDOM_MAC, random=True)
    await observe(subnet["id"], "10.0.0.11", mac=RANDOM_MAC, random=True)
    assert await findings(client, subnet["id"]) == []


async def test_no_finding_for_an_address_outside_scope(client):
    """Rows like these never exist (ingest refuses them, and leaving scope deletes
    them); written directly, they still raise nothing."""
    subnet = await _subnet(client, dhcp_start="10.0.0.100", dhcp_end="10.0.0.199")
    await _target(client, subnet["id"])
    res = await client.post(
        f"/api/v1/subnets/{subnet['id']}/reserved",
        json={"start_ip": "10.0.0.2", "end_ip": "10.0.0.9", "scan_excluded": True},
    )
    assert res.status_code == 201, res.text
    # in the excluded range: a host with every kind of finding
    host = await make_host(client, subnet["id"], "10.0.0.5", last_seen=LONG_AGO.isoformat())
    await backdate(Host, host["id"])
    await observe(subnet["id"], "10.0.0.5", mac=MAC, rdns="nas.home.arpa", seen=LONG_AGO)
    # in the DHCP pool, which is not scanned
    await observe(subnet["id"], "10.0.0.150", mac=MAC)
    assert await findings(client, subnet["id"], include_dynamic=True) == []


async def test_unknown_host_in_a_scanned_pool_is_dynamic_and_hidden(client):
    subnet = await _subnet(client, dhcp_start="10.0.0.100", dhcp_end="10.0.0.199")
    await _target(client, subnet["id"], scan_dhcp_pool=True)
    await observe(subnet["id"], "10.0.0.150", mac=MAC)
    await observe(subnet["id"], "10.0.0.50")
    assert [f["ip"] for f in await findings(client, subnet["id"])] == ["10.0.0.50"]
    found = await findings(client, subnet["id"], include_dynamic=True)
    assert [(f["ip"], f["dynamic"]) for f in found] == [("10.0.0.50", False), ("10.0.0.150", True)]
    # accepting one records it as a DHCP host
    assert found[1]["proposed"] == {"status": "dhcp", "mac": MAC}


# --- Accept ---


async def _host(host_id) -> Host:
    async with SessionLocal() as session:
        return await session.get(Host, host_id)


async def _last_change() -> Change:
    async with SessionLocal() as session:
        return await session.scalar(select(Change).order_by(Change.ts.desc()).limit(1))


async def test_accepting_unknown_host_creates_it_through_the_host_path(client):
    subnet = await _subnet(client)
    await observe(subnet["id"], "10.0.0.20", mac=MAC, rdns="printer.home.arpa")
    [f] = await findings(client, subnet["id"])
    before = await change_count()
    report = await accept(client, key(f))
    assert (report["accepted"], report["skipped"], report["errors"]) == (1, 0, 0)
    [result] = report["results"]
    assert result["status"] == "accepted"
    host = (await client.get(f"/api/v1/hosts/{result['host_id']}")).json()["data"]
    assert (host["ip"], host["mac"], host["hostname"], host["status"], host["fqdn"]) == (
        "10.0.0.20", MAC, "printer", "active", "printer.home.arpa"
    )
    assert await findings(client, subnet["id"]) == []
    # one entry, the hosts service's own, saying the value came from a scan
    assert await change_count() == before + 1
    change = await _last_change()
    assert (change.entity_type, change.entity_id, change.action, change.source) == (
        "host", host["id"], "create", "api"
    )
    assert change.summary == "host created at 10.0.0.20 in lab from scan finding unknown_host"
    assert change.after["mac"] == MAC and change.after["hostname"] == "printer"


@pytest.mark.parametrize(
    ("host_fields", "observation", "type_", "field", "value"),
    [
        ({"hostname": "nas"}, {"mac": MAC}, "mac_missing", "mac", MAC),
        ({"hostname": "nas", "mac": OTHER_MAC}, {"mac": MAC}, "mac_mismatch", "mac", MAC),
        ({}, {"rdns": "nas.home.arpa"}, "name_missing", "hostname", "nas"),
        ({"hostname": "nas"}, {"rdns": "storage.home.arpa"}, "name_mismatch", "hostname", "storage"),
    ],
)
async def test_accepting_updates_the_host_through_the_host_path(
    client, host_fields, observation, type_, field, value
):
    subnet = await _subnet(client)
    host = await make_host(client, subnet["id"], "10.0.0.10", **host_fields)
    await observe(subnet["id"], "10.0.0.10", **observation)
    f = only(await findings(client, subnet["id"]), type_)
    before = await change_count()
    report = await accept(client, key(f))
    assert report["results"] == [
        {
            "index": 0,
            "subnet_id": subnet["id"],
            "ip": "10.0.0.10",
            "type": type_,
            "status": "accepted",
            "host_id": host["id"],
        }
    ]
    assert getattr(await _host(host["id"]), field) == value
    assert await findings(client, subnet["id"]) == []
    assert await change_count() == before + 1
    change = await _last_change()
    assert (change.entity_type, change.entity_id, change.action) == ("host", host["id"], "update")
    assert change.summary == f"host at 10.0.0.10 updated from scan finding {type_}"
    assert change.before[field] == host_fields.get(field) and change.after[field] == value


async def test_accepting_stale_sets_the_host_offline(client):
    subnet = await _subnet(client)
    await _target(client, subnet["id"])
    host = await make_host(client, subnet["id"], "10.0.0.10", last_seen=LONG_AGO.isoformat())
    [f] = await findings(client, subnet["id"])
    report = await accept(client, key(f))
    assert report["accepted"] == 1
    assert (await _host(host["id"])).status == "offline"
    assert await findings(client, subnet["id"]) == []
    change = await _last_change()
    assert (change.before["status"], change.after["status"]) == ("active", "offline")


async def test_accepting_a_finding_whose_value_changed_writes_nothing(client):
    subnet = await _subnet(client)
    host = await make_host(client, subnet["id"], "10.0.0.10", hostname="nas", mac=OTHER_MAC)
    await observe(subnet["id"], "10.0.0.10", mac=MAC)
    [f] = await findings(client, subnet["id"])
    # a later scan saw another MAC
    await set_observation(subnet["id"], "10.0.0.10", mac="aa:bb:cc:00:00:03")
    before = await change_count()
    report = await accept(client, key(f))
    assert (report["accepted"], report["skipped"]) == (0, 1)
    assert report["results"][0]["status"] == "skipped"
    assert report["results"][0]["reason"] == "changed"
    assert (await _host(host["id"])).mac == OTHER_MAC
    assert await change_count() == before


async def test_accepting_a_finding_that_no_longer_exists_writes_nothing(client):
    subnet = await _subnet(client)
    await observe(subnet["id"], "10.0.0.20", mac=MAC)
    [f] = await findings(client, subnet["id"])
    await make_host(client, subnet["id"], "10.0.0.20", hostname="mine")
    before = await change_count()
    report = await accept(client, key(f))
    assert report["results"][0]["reason"] == "changed"
    assert await change_count() == before
    # and a stale finding for a host that has since been seen
    await _target(client, subnet["id"])
    stale = await make_host(client, subnet["id"], "10.0.0.30", last_seen=LONG_AGO.isoformat())
    f = only(await findings(client, subnet["id"]), "stale")
    await observe(subnet["id"], "10.0.0.30", seen=RECENT)
    assert (await accept(client, key(f)))["results"][0]["reason"] == "changed"
    assert (await _host(stale["id"])).status == "active"


async def test_accept_reports_each_record_and_does_not_abort_the_batch(client):
    subnet = await _subnet(client)
    await observe(subnet["id"], "10.0.0.20", mac=MAC)
    await observe(subnet["id"], "10.0.0.21")
    found = await findings(client, subnet["id"])
    stale_key = {**key(found[0]), "observed": OTHER_MAC}
    report = await accept(
        client,
        "not an object",
        {**key(found[0]), "type": "fdns_mismatch"},
        {**key(found[0]), "ip": "10.0.0.256"},
        {**key(found[0]), "extra": 1},
        stale_key,
        key(found[0]),
        key(found[1]),
        key(found[1]),  # the same key twice: the first created the host
    )
    assert (report["accepted"], report["skipped"], report["errors"]) == (2, 2, 4)
    statuses = [(r["index"], r["status"], r.get("reason")) for r in report["results"]]
    assert [s[:2] for s in statuses] == [
        (0, "error"), (1, "error"), (2, "error"), (3, "error"),
        (4, "skipped"), (5, "accepted"), (6, "accepted"), (7, "skipped"),
    ]
    assert statuses[2][2] == "IP '10.0.0.256' is invalid"
    assert [f["ip"] for f in await findings(client, subnet["id"])] == []


async def test_an_error_from_the_hosts_service_is_reported_and_rolled_back(client, monkeypatch):
    subnet = await _subnet(client)
    await observe(subnet["id"], "10.0.0.20", mac=MAC)
    await observe(subnet["id"], "10.0.0.21", mac=OTHER_MAC)
    first, second = await findings(client, subnet["id"])
    real = findings_svc.hosts_svc.create_host

    async def refuse_first(session, payload, source, **kw):
        if payload.ip == "10.0.0.20":
            raise findings_svc.validation_error("refused")
        return await real(session, payload, source, **kw)

    monkeypatch.setattr(findings_svc.hosts_svc, "create_host", refuse_first)
    report = await accept(client, key(first), key(second))
    assert [(r["status"], r.get("reason")) for r in report["results"]] == [
        ("error", "refused"),
        ("accepted", None),
    ]
    assert [f["ip"] for f in await findings(client, subnet["id"])] == ["10.0.0.20"]


async def test_a_dismissed_finding_can_still_be_accepted(client):
    subnet = await _subnet(client)
    host = await make_host(client, subnet["id"], "10.0.0.10")
    await observe(subnet["id"], "10.0.0.10", mac=MAC)
    [f] = await findings(client, subnet["id"])
    assert (await dismiss(client, key(f)))["dismissed"] == 1
    assert (await accept(client, key(f)))["accepted"] == 1
    assert (await _host(host["id"])).mac == MAC


async def test_accept_and_dismiss_take_the_write_lock_before_recomputing(client, monkeypatch):
    """Both write on the strength of the scope, so the lock comes first (`AGENTS.md`,
    scan-scope bullet): no other writer can change what they judge before they commit."""
    subnet = await _subnet(client)
    await observe(subnet["id"], "10.0.0.20", mac=MAC)
    [f] = await findings(client, subnet["id"])
    calls = []
    lock, context = scans.take_write_lock, findings_svc._context

    async def spy_lock(session):
        calls.append("lock")
        await lock(session)

    async def spy_context(session, subnet_id):
        calls.append("context")
        return await context(session, subnet_id)

    monkeypatch.setattr(scans, "take_write_lock", spy_lock)
    monkeypatch.setattr(findings_svc, "_context", spy_context)
    await dismiss(client, key(f))
    assert calls == ["lock", "context"]
    calls.clear()
    await accept(client, key(f))
    assert calls == ["lock", "context"]


# --- Dismiss ---


async def test_a_dismissed_finding_stays_hidden_until_the_observed_value_changes(client):
    subnet = await _subnet(client)
    await make_host(client, subnet["id"], "10.0.0.10", hostname="nas")
    await observe(subnet["id"], "10.0.0.10", rdns="storage.home.arpa")
    [f] = await findings(client, subnet["id"])
    report = await dismiss(client, key(f))
    assert (report["dismissed"], report["skipped"], report["errors"]) == (1, 0, 0)
    assert await findings(client, subnet["id"]) == []
    # a later scan with the same answer: still hidden
    await set_observation(subnet["id"], "10.0.0.10", last_seen_at=NOW)
    assert await findings(client, subnet["id"]) == []
    # a new answer: back, with the new value
    await set_observation(subnet["id"], "10.0.0.10", rdns_name="backup.home.arpa")
    [back] = await findings(client, subnet["id"])
    assert (back["type"], back["observed"]) == ("name_mismatch", "backup.home.arpa")
    # dismissing it again replaces the dismissal
    assert (await dismiss(client, key(back)))["dismissed"] == 1
    assert await findings(client, subnet["id"]) == []
    assert await dismissal_count() == 1
    # and the old value, should it come back, shows again
    await set_observation(subnet["id"], "10.0.0.10", rdns_name="storage.home.arpa")
    assert [f["observed"] for f in await findings(client, subnet["id"])] == ["storage.home.arpa"]


async def test_dismiss_stores_only_a_finding_that_exists_as_listed(client):
    subnet = await _subnet(client)
    await observe(subnet["id"], "10.0.0.20", mac=MAC)
    [f] = await findings(client, subnet["id"])
    before = await change_count()
    report = await dismiss(
        client,
        {**key(f), "observed": "anything a client likes"},
        {**key(f), "ip": "10.0.0.21"},
        {**key(f), "subnet_id": "no-such-subnet"},
        {**key(f), "type": "bogus"},
    )
    assert [(r["status"], r.get("reason")) for r in report["results"]][:3] == [
        ("skipped", "changed")
    ] * 3
    assert report["results"][3]["status"] == "error"
    assert await dismissal_count() == 0
    assert await change_count() == before


async def test_dismissing_twice_is_skipped(client):
    subnet = await _subnet(client)
    await observe(subnet["id"], "10.0.0.20", mac=MAC)
    [f] = await findings(client, subnet["id"])
    report = await dismiss(client, key(f), key(f))
    assert [(r["status"], r.get("reason")) for r in report["results"]] == [
        ("dismissed", None),
        ("skipped", "already dismissed"),
    ]
    assert (await dismiss(client, key(f)))["results"][0]["reason"] == "already dismissed"
    assert await dismissal_count() == 1


async def test_dismissals_are_audit_logged_per_subnet_without_values_in_the_summary(client):
    subnet = await _subnet(client)
    await make_host(client, subnet["id"], "10.0.0.10")
    await observe(subnet["id"], "10.0.0.10", rdns=f"{HOSTILE}")
    await observe(subnet["id"], "10.0.0.20", mac=MAC)
    found = await findings(client, subnet["id"])
    before = await change_count()
    await dismiss(client, *[key(f) for f in found])
    assert await change_count() == before + 1
    change = await _last_change()
    assert (change.entity_type, change.entity_id, change.action, change.source) == (
        "subnet", subnet["id"], "update", "api"
    )
    assert change.summary == "2 scan finding(s) dismissed in lab"
    assert change.after == {
        "dismissed": [
            {"ip": "10.0.0.10", "type": "name_missing", "observed": HOSTILE},
            {"ip": "10.0.0.20", "type": "unknown_host", "observed": MAC},
        ]
    }


async def test_ui_source_is_recorded(client):
    subnet = await _subnet(client)
    await observe(subnet["id"], "10.0.0.20", mac=MAC)
    await observe(subnet["id"], "10.0.0.21", mac=OTHER_MAC)
    first, second = await findings(client, subnet["id"])
    for path, f in (("dismiss", first), ("accept", second)):
        res = await client.post(
            f"/api/v1/findings/{path}", json={"findings": [key(f)]}, headers={"X-TLG-Client": "ui"}
        )
        assert res.status_code == 200
        assert (await _last_change()).source == "ui"


async def test_an_empty_or_oversized_request_is_refused(client):
    for body in ({"findings": []}, {"findings": [{}] * 1001}, {}):
        for path in ("accept", "dismiss"):
            res = await client.post(f"/api/v1/findings/{path}", json=body)
            assert res.status_code == 422, (path, body)


# --- Dismissals leave with their address ---


async def test_dismissals_are_deleted_when_their_address_leaves_scope(client):
    subnet = await _subnet(client, dhcp_start="10.0.0.100", dhcp_end="10.0.0.199")
    target = await _target(client, subnet["id"], scan_dhcp_pool=True)
    await observe(subnet["id"], "10.0.0.5", mac=MAC)
    await observe(subnet["id"], "10.0.0.150", mac=OTHER_MAC)
    await observe(subnet["id"], "10.0.0.50")
    found = await findings(client, subnet["id"], include_dynamic=True)
    await dismiss(client, *[key(f) for f in found])
    assert await dismissal_count() == 3

    res = await client.post(
        f"/api/v1/subnets/{subnet['id']}/reserved",
        json={"start_ip": "10.0.0.2", "end_ip": "10.0.0.9", "scan_excluded": True},
    )
    assert res.status_code == 201
    res = await client.put(f"/api/v1/scan-targets/{target['id']}", json={"scan_dhcp_pool": False})
    assert res.status_code == 200
    async with SessionLocal() as session:
        left = (await session.scalars(select(FindingDismissal.ip))).all()
    assert left == ["10.0.0.50"]


async def test_dismissals_are_deleted_with_the_subnet(client):
    subnet = await _subnet(client)
    await _target(client, subnet["id"])
    await make_host(client, subnet["id"], "10.0.0.10")
    await observe(subnet["id"], "10.0.0.10", mac=MAC)
    [f] = await findings(client, subnet["id"])
    await dismiss(client, key(f))
    res = await client.delete(f"/api/v1/subnets/{subnet['id']}", params={"force": "true"})
    assert res.status_code == 204, res.text
    assert await dismissal_count() == 0


# --- Network strings stay data ---


async def test_a_name_from_the_network_never_reaches_a_change_summary(client):
    """`rdns_name` is any device's claim about itself. Accepting it makes it owner data,
    through the hosts service, but no summary repeats it (spec §7.2, invariant 8)."""
    subnet = await _subnet(client)
    host = await make_host(client, subnet["id"], "10.0.0.10")
    await observe(subnet["id"], "10.0.0.10", rdns=HOSTILE)
    await observe(subnet["id"], "10.0.0.20", mac=MAC, rdns=HOSTILE)
    await make_host(client, subnet["id"], "10.0.0.30", hostname="nas")
    await observe(subnet["id"], "10.0.0.30", rdns=HOSTILE)
    found = await findings(client, subnet["id"])
    assert sorted(f["type"] for f in found) == ["name_mismatch", "name_missing", "unknown_host"]
    report = await accept(client, *[key(f) for f in found])
    assert report["accepted"] == 3
    got = (await client.get(f"/api/v1/hosts/{host['id']}")).json()["data"]
    assert got["fqdn"] == HOSTILE
    async with SessionLocal() as session:
        summaries = (await session.scalars(select(Change.summary))).all()
    assert not [s for s in summaries if "ignore" in s or MAC in s]


# --- Accept is bound to the whole proposal (fingerprint) ---


async def test_accept_needs_the_listed_fingerprint(client):
    subnet = await _subnet(client)
    await observe(subnet["id"], "10.0.0.20", mac=MAC)
    [f] = await findings(client, subnet["id"])
    unkeyed = {k: v for k, v in key(f).items() if k != "fingerprint"}
    report = await accept(client, unkeyed, {**key(f), "fingerprint": "0" * 32})
    assert [(r["status"], r["reason"]) for r in report["results"]] == [
        ("error", "fingerprint: required to accept"),
        ("skipped", "changed"),
    ]
    assert (await accept(client, key(f)))["accepted"] == 1


async def post_results(agent_api, token, job, records):
    started = datetime.now(timezone.utc) - timedelta(seconds=30)
    res = await agent_api.post(
        "/api/v1/agent/results",
        json={
            "job_id": job["job_id"],
            "subnet_id": job["subnet_id"],
            "started_at": started.isoformat(),
            "finished_at": (started + timedelta(seconds=5)).isoformat(),
            "complete": True,
            "observations": records,
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["data"]["rejected"] == []


async def scanned_twice(client, agent_api, first: dict, second: dict) -> tuple[dict, dict]:
    """A scanned subnet, its finding at 10.0.0.22 after a first real scan, and the
    subnet after a second scan reports `second` there."""
    agent, token = await make_agent(client)
    subnet = await _subnet(client)
    await make_target(client, subnet["id"], agent["id"])
    [job] = await jobs_for(agent_api, token)
    await post_results(agent_api, token, job, [{"ip": "10.0.0.22", **first}])
    [f] = await findings(client, subnet["id"])
    res = await client.post(f"/api/v1/subnets/{subnet['id']}/scan")
    assert res.status_code == 202, res.text
    [job] = await jobs_for(agent_api, token)
    await post_results(agent_api, token, job, [{"ip": "10.0.0.22", **second}])
    return subnet, f


@pytest.mark.parametrize("mac", [None, MAC], ids=["no-mac", "same-mac"])
async def test_a_later_scan_with_another_name_makes_the_listed_proposal_stale(
    client, agent_api, mac
):
    """The review's reproduction: `observed` is the MAC (or empty) and did not change,
    but the name accepting would write did."""
    with_mac = {"mac": mac} if mac else {}
    subnet, f = await scanned_twice(
        client,
        agent_api,
        {**with_mac, "rdns_name": "old.home.arpa"},
        {**with_mac, "rdns_name": "replacement.home.arpa"},
    )
    assert (f["observed"], f["proposed"]["hostname"]) == (mac or "", "old")
    [now] = await findings(client, subnet["id"])
    assert now["observed"] == f["observed"] and now["fingerprint"] != f["fingerprint"]
    before = await change_count()
    report = await accept(client, key(f))
    assert (report["accepted"], report["skipped"]) == (0, 1)
    assert report["results"][0]["reason"] == "changed"
    assert (await client.get("/api/v1/hosts")).json()["data"] == []
    assert await change_count() == before
    # the proposal as it now stands is still accepted
    assert (await accept(client, key(now)))["accepted"] == 1


async def test_a_later_scan_with_the_same_answer_keeps_the_proposal(client, agent_api):
    answer = {"mac": MAC, "rdns_name": "old.home.arpa"}
    subnet, f = await scanned_twice(client, agent_api, answer, answer)
    report = await accept(client, key(f))
    assert report["accepted"] == 1
    host = (await client.get(f"/api/v1/hosts/{report['results'][0]['host_id']}")).json()
    assert (host["data"]["fqdn"], host["data"]["mac"]) == ("old.home.arpa", MAC)


async def test_a_domain_change_makes_a_name_proposal_stale(client):
    subnet = await _subnet(client)
    host = await make_host(client, subnet["id"], "10.0.0.10")
    await observe(subnet["id"], "10.0.0.10", rdns="nas.lab.home.arpa")
    [f] = await findings(client, subnet["id"])
    assert f["proposed"] == {"hostname": "nas.lab"}
    res = await client.put(f"/api/v1/subnets/{subnet['id']}", json={"domain": "lab.home.arpa"})
    assert res.status_code == 200, res.text
    [now] = await findings(client, subnet["id"])
    assert now["observed"] == f["observed"] and now["proposed"] == {"hostname": "nas"}
    assert (await accept(client, key(f)))["results"][0]["reason"] == "changed"
    assert (await _host(host["id"])).hostname is None


async def test_a_pool_change_makes_an_unknown_host_proposal_stale(client):
    subnet = await _subnet(client, dhcp_start="10.0.0.100", dhcp_end="10.0.0.199")
    await _target(client, subnet["id"], scan_dhcp_pool=True)
    await observe(subnet["id"], "10.0.0.50", mac=MAC)
    [f] = await findings(client, subnet["id"])
    assert (f["dynamic"], f["proposed"]["status"]) == (False, "active")
    res = await client.put(f"/api/v1/subnets/{subnet['id']}", json={"dhcp_start": "10.0.0.40"})
    assert res.status_code == 200, res.text
    [now] = await findings(client, subnet["id"], include_dynamic=True)
    assert (now["observed"], now["dynamic"], now["proposed"]["status"]) == (MAC, True, "dhcp")
    assert (await accept(client, key(f)))["results"][0]["reason"] == "changed"
    assert (await client.get("/api/v1/hosts")).json()["data"] == []


async def test_a_new_host_at_the_address_makes_a_proposal_stale(client):
    subnet = await _subnet(client)
    first = await make_host(client, subnet["id"], "10.0.0.10")
    await observe(subnet["id"], "10.0.0.10", mac=MAC)
    [f] = await findings(client, subnet["id"])
    assert (await client.delete(f"/api/v1/hosts/{first['id']}")).status_code == 204
    second = await make_host(client, subnet["id"], "10.0.0.10")
    assert (await accept(client, key(f)))["results"][0]["reason"] == "changed"
    assert (await _host(second["id"])).mac is None


# --- Accepted names: the stored FQDN is the PTR name ---


@pytest.mark.parametrize(
    ("ptr", "hostname", "domain"),
    [
        ("nas.home.arpa", "nas", None),
        ("a.b.home.arpa", "a.b", None),  # nested relative labels
        ("nas.example.com", "nas", "example.com"),  # outside the subnet's domain
        ("home.arpa", "home", "arpa"),  # the bare domain
    ],
)
@pytest.mark.parametrize("type_", ["unknown_host", "name_missing", "name_mismatch"])
async def test_an_accepted_name_gives_the_host_the_ptr_name_as_its_fqdn(
    client, type_, ptr, hostname, domain
):
    subnet = await _subnet(client)
    if type_ == "name_missing":
        await make_host(client, subnet["id"], "10.0.0.10")
    elif type_ == "name_mismatch":
        await make_host(client, subnet["id"], "10.0.0.10", hostname="old")
    await observe(subnet["id"], "10.0.0.10", rdns=ptr)
    f = only(await findings(client, subnet["id"]), type_)
    expected = {"hostname": hostname, **({"domain": domain} if domain else {})}
    assert {k: v for k, v in f["proposed"].items() if k in ("hostname", "domain")} == expected
    report = await accept(client, key(f))
    assert report["accepted"] == 1
    host = (await client.get(f"/api/v1/hosts/{report['results'][0]['host_id']}")).json()["data"]
    assert (host["hostname"], host["domain"], host["fqdn"]) == (hostname, domain, ptr)
    assert await findings(client, subnet["id"]) == []


async def test_a_ptr_name_outside_a_host_override_sets_the_override(client):
    subnet = await _subnet(client)
    host = await make_host(client, subnet["id"], "10.0.0.10", hostname="nas", domain="lab.example.com")
    await observe(subnet["id"], "10.0.0.10", rdns="nas.home.arpa")
    [f] = await findings(client, subnet["id"])
    assert (f["type"], f["proposed"]) == ("name_mismatch", {"hostname": "nas", "domain": "home.arpa"})
    assert (await accept(client, key(f)))["accepted"] == 1
    got = (await client.get(f"/api/v1/hosts/{host['id']}")).json()["data"]
    assert got["fqdn"] == "nas.home.arpa"


async def test_a_single_label_ptr_name_is_a_relative_hostname(client):
    subnet = await _subnet(client)
    await observe(subnet["id"], "10.0.0.20", rdns="printer")
    [f] = await findings(client, subnet["id"])
    assert f["proposed"] == {"status": "active", "hostname": "printer"}


# --- The recompute is what accept and dismiss judge ---


async def test_an_address_put_out_of_scope_after_listing_is_skipped(client):
    """The finding listed in scope; the observation row is then back out of scope (it
    is written directly: drop_out_of_scope deleted the real one). Only the recompute can
    refuse it."""
    subnet = await _subnet(client)
    await make_host(client, subnet["id"], "10.0.0.5")
    await observe(subnet["id"], "10.0.0.5", mac=MAC)
    await observe(subnet["id"], "10.0.0.6", mac=OTHER_MAC)
    first, second = await findings(client, subnet["id"])
    res = await client.post(
        f"/api/v1/subnets/{subnet['id']}/reserved",
        json={"start_ip": "10.0.0.2", "end_ip": "10.0.0.9", "scan_excluded": True},
    )
    assert res.status_code == 201
    await observe(subnet["id"], "10.0.0.5", mac=MAC)
    await observe(subnet["id"], "10.0.0.6", mac=OTHER_MAC)
    assert (await accept(client, key(first)))["results"][0]["reason"] == "changed"
    assert (await dismiss(client, key(second)))["results"][0]["reason"] == "changed"
    assert await dismissal_count() == 0


async def test_a_mac_found_randomised_after_listing_is_skipped(client):
    subnet = await _subnet(client)
    await make_host(client, subnet["id"], "10.0.0.10")
    await make_host(client, subnet["id"], "10.0.0.11")
    await observe(subnet["id"], "10.0.0.10", mac=MAC)
    await observe(subnet["id"], "10.0.0.11", mac=OTHER_MAC)
    first, second = await findings(client, subnet["id"])
    for ip in ("10.0.0.10", "10.0.0.11"):
        await set_observation(subnet["id"], ip, mac_randomised=True)
    assert (await accept(client, key(first)))["results"][0]["reason"] == "changed"
    assert (await dismiss(client, key(second)))["results"][0]["reason"] == "changed"


async def test_a_dismissal_hides_only_its_own_address_and_type(client):
    subnet = await _subnet(client)
    # the same type and observed value at two addresses
    await observe(subnet["id"], "10.0.0.20")
    await observe(subnet["id"], "10.0.0.21")
    # two types at one address
    await make_host(client, subnet["id"], "10.0.0.10")
    await observe(subnet["id"], "10.0.0.10", mac=MAC, rdns="nas.home.arpa")
    found = await findings(client, subnet["id"])
    by = {(f["ip"], f["type"]): f for f in found}
    await dismiss(client, key(by[("10.0.0.20", "unknown_host")]))
    assert ("10.0.0.21", "unknown_host") in {(f["ip"], f["type"]) for f in await findings(client, subnet["id"])}
    await dismiss(client, key(by[("10.0.0.10", "mac_missing")]), key(by[("10.0.0.10", "name_missing")]))
    assert [(f["ip"], f["type"]) for f in await findings(client, subnet["id"])] == [
        ("10.0.0.21", "unknown_host")
    ]


def _another_writer_gets_in() -> bool:
    """Whether a second connection can take SQLite's write lock right now."""
    con = sqlite3.connect(settings.db_path, timeout=0.1, isolation_level=None)
    try:
        con.execute("BEGIN IMMEDIATE")
        con.rollback()
        return True
    except sqlite3.OperationalError:
        return False
    finally:
        con.close()


@pytest.mark.parametrize("route", ["accept", "dismiss"])
async def test_no_other_writer_gets_in_between_the_recompute_and_the_write(
    client, monkeypatch, route
):
    """The lock is held from before the recompute until the write commits: a lock taken
    and then lost to a commit or a rollback would let another writer in here."""
    subnet = await _subnet(client)
    await observe(subnet["id"], "10.0.0.20", mac=MAC)
    [f] = await findings(client, subnet["id"])
    seen = []
    current, create = findings_svc._current, findings_svc.hosts_svc.create_host

    async def after_recompute(*args):
        found = await current(*args)
        seen.append(("recomputed", _another_writer_gets_in()))
        return found

    async def at_the_write(*args, **kw):
        seen.append(("writing", _another_writer_gets_in()))
        return await create(*args, **kw)

    monkeypatch.setattr(findings_svc, "_current", after_recompute)
    monkeypatch.setattr(findings_svc.hosts_svc, "create_host", at_the_write)
    report = await (accept if route == "accept" else dismiss)(client, key(f))
    assert report[{"accept": "accepted", "dismiss": "dismissed"}[route]] == 1
    expected = [("recomputed", False)] + ([("writing", False)] if route == "accept" else [])
    assert seen == expected
    assert _another_writer_gets_in()


def _validation_error() -> ValidationError:
    try:
        HostCreate.model_validate({})
    except ValidationError as exc:
        return exc
    raise AssertionError("HostCreate accepted an empty body")


@pytest.mark.parametrize("refusal", ["api_error", "validation_error"])
async def test_a_refused_record_leaves_nothing_behind_for_the_next(client, monkeypatch, refusal):
    """The hosts service may have written before it refused; the next record's commit
    must not carry that partial write with it."""
    subnet = await _subnet(client)
    await observe(subnet["id"], "10.0.0.20", mac=MAC)
    await observe(subnet["id"], "10.0.0.21", mac=OTHER_MAC)
    first, second = await findings(client, subnet["id"])
    real = findings_svc.hosts_svc.create_host

    async def refuse_first(session, payload, source, **kw):
        if payload.ip == "10.0.0.20":
            session.add(Section(name="partial write"))
            await session.flush()
            raise (
                findings_svc.validation_error("refused")
                if refusal == "api_error"
                else _validation_error()
            )
        return await real(session, payload, source, **kw)

    monkeypatch.setattr(findings_svc.hosts_svc, "create_host", refuse_first)
    report = await accept(client, key(first), key(second))
    assert [r["status"] for r in report["results"]] == ["error", "accepted"]
    async with SessionLocal() as session:
        names = (await session.scalars(select(Section.name))).all()
    assert "partial write" not in names


# --- Cost ---


@contextmanager
def statements():
    count = [0]

    def before(*_args):
        count[0] += 1

    event.listen(engine.sync_engine, "before_cursor_execute", before)
    try:
        yield count
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", before)


async def test_listing_costs_the_same_statements_however_many_hosts(client):
    small = await _subnet(client)
    await make_host(client, small["id"], "10.0.0.10")
    await observe(small["id"], "10.0.0.10", mac=MAC)
    section = await make_section(client, name="big")
    big = await make_subnet(client, section["id"], cidr="10.1.0.0/24", name="big")
    async with SessionLocal() as session:
        for i in range(1, 201):
            ip = f"10.1.0.{i}"
            session.add(Host(subnet_id=big["id"], ip=ip, ip_int=int(parse_ip(ip))))
            session.add(
                Observation(subnet_id=big["id"], ip=ip, ip_int=int(parse_ip(ip)), mac=MAC)
            )
        await session.commit()
    counts = []
    for subnet in (small, big):
        with statements() as count:
            found = await findings(client, subnet["id"])
        counts.append(count[0])
    assert len(found) == 200
    # the subnet, its scope (three), its target, then hosts, observations, dismissals
    assert counts == [9, 9]


@pytest.mark.parametrize(
    ("observation", "dismissed", "edit", "shown"),
    [
        ({"rdns": "nas.home.arpa"}, "name_missing", {"hostname": "other"}, "name_mismatch"),
        ({"mac": MAC}, "mac_missing", {"mac": OTHER_MAC}, "mac_mismatch"),
    ],
)
async def test_a_dismissal_does_not_hide_another_type_with_the_same_value(
    client, observation, dismissed, edit, shown
):
    """Same address, same observed value, another type: an owner edit turns a dismissed
    `*_missing` into a `*_mismatch`, which is a new finding."""
    subnet = await _subnet(client)
    host = await make_host(client, subnet["id"], "10.0.0.10")
    await observe(subnet["id"], "10.0.0.10", **observation)
    [f] = await findings(client, subnet["id"])
    assert f["type"] == dismissed
    await dismiss(client, key(f))
    assert await findings(client, subnet["id"]) == []
    assert (await client.put(f"/api/v1/hosts/{host['id']}", json=edit)).status_code == 200
    [now] = await findings(client, subnet["id"])
    assert (now["type"], now["observed"]) == (shown, f["observed"])
