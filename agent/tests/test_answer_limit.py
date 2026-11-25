"""The largest check-in answer the server can send, against the agent's answer limit
(#75). A check-in answer over `client.RESPONSE_MAX` is refused by the agent and
retried, and the job it carried never runs: the server must never build one.

The answer is built by the server's real check-in route, in process, and read by the
agent's real client and loop. The worst case is made from the server's own bounds, so
a change to either side fails here: the largest subnet a target may have
(`scans.MIN_PREFIX`), its scope split as finely as it can be (every other address
excluded), every method a target can hold, a stored `dns_servers` list longer than
the owner API now accepts, as a subnet saved before the bound keeps, and a stored CIDR
spelled as long as the owner API once took, as a subnet saved before #97 keeps. A job
carries the CIDR's canonical spelling (`ipnet.canonical_cidr`), so the longest CIDR the
server can send is the longest canonical one, whatever the subnet stores. Since #20 it
also carries the forward-DNS names: a host at every scanned address, each with an FQDN
of the longest a DNS name can be, more than a job sends (`fdns.FDNS_JOB_MAX`), and the
bound counts as many as it sends, each with the longest host id and FQDN."""

import ipaddress
import json
import random

import httpx
import pytest
from app.agent_protocol import DNS_SERVERS_MAX
from app.database import SessionLocal
from app.ipnet import canonical_cidr, usable_bounds
from app.main import app
from app.models import Host, ReservedRange, Subnet
from app.schemas import SUBNET_DNS_SERVERS_MAX
from app.services import fdns, scans
from sqlalchemy import update

from conftest import FakeArp, FakeClock, FakeProber, FakeResolver, make_config
from test_e2e import fresh_db, owner, setup  # noqa: F401  (fixtures, and the setup helper)
from tlg_ipam_agent import guard, protocol
from tlg_ipam_agent.client import RESPONSE_MAX, AgentClient
from tlg_ipam_agent.loop import Agent

# the longest an IPv4 address, and so a range end, a DNS server or an interface address
# of a job, can be written; v1 is IPv4-only (`ipnet.parse_ip`, `ipnet.parse_cidr`). A
# job's CIDR is canonical, so this is the longest it can be written too.
_LONGEST_IP = "255.255.255.255"
_LONGEST_CIDR = "255.255.255.255/32"
# The spelling of the /20 below that the review of PR #95 stored through the owner API
# before #97: a prefix padded with 4,298 zeroes. An answer whose job carried it was
# 109,276 bytes here, past the bound below, and the agent refused the job (its `cidr`
# over 64 characters).
_PADDED_CIDR = "192.168.240.0/" + "0" * 4298 + "20"
# A hostname that makes, with the subnet's domain, an FQDN of 253 characters, the
# longest a DNS name can be written (`agent_protocol.fdns_name`); and the longest host id
_DOMAIN = "home.arpa"
_LONGEST_HOSTNAME = ".".join(["a" * 63] * 3 + ["b" * (253 - len(_DOMAIN) - 1 - 3 * 64)])
_LONGEST_FQDN = f"{_LONGEST_HOSTNAME}.{_DOMAIN}"
_LONGEST_HOST_ID = "f" * 36


class Recording(httpx.AsyncBaseTransport):
    """The app in process, with the size of each answer body as sent kept. The agent's
    client reads the same bytes, through its own size check."""

    def __init__(self):
        self.inner = httpx.ASGITransport(app=app)
        self.sizes: list[int] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        res = await self.inner.handle_async_request(request)
        body = await res.aread()
        self.sizes.append(len(body))
        return httpx.Response(res.status_code, headers=res.headers, content=body)


def _encoded(answer: dict) -> int:
    # as FastAPI's JSONResponse renders it
    return len(json.dumps(answer, ensure_ascii=False, separators=(",", ":")).encode())


async def test_the_largest_checkin_answer_is_inside_the_agents_limit(owner):  # noqa: F811
    net = ipaddress.ip_network(("192.168.255.0", scans.MIN_PREFIX), strict=False)
    interfaces = [{"name": "eth0", "cidr": f"{net[1]}/{net.prefixlen}", "mac": "02:00:00:00:00:05"}]
    agent_row, subnet, _ = await setup(owner, cidr=str(net), methods=scans.SCAN_METHODS)

    # every other usable address excluded: the most ranges a scope can be split into
    first, last = usable_bounds(net)
    scanned = range(first, last + 1, 2)
    # as many as the review of PR #70 set through the owner API before the bound
    legacy = [str(ipaddress.ip_address(int(ipaddress.ip_address("172.16.0.1")) + i)) for i in range(65_000)]
    assert len(legacy) > SUBNET_DNS_SERVERS_MAX
    async with SessionLocal() as session:
        for ip_int in range(first + 1, last + 1, 2):
            ip = str(ipaddress.ip_address(ip_int))
            session.add(
                ReservedRange(
                    subnet_id=subnet["id"], start_ip=ip, end_ip=ip,
                    start_int=ip_int, end_int=ip_int, scan_excluded=True,
                )
            )
        # a host at every scanned address, each named at the longest (#20)
        for ip_int in scanned:
            session.add(
                Host(
                    subnet_id=subnet["id"], ip=str(ipaddress.ip_address(ip_int)), ip_int=ip_int,
                    hostname=_LONGEST_HOSTNAME,
                )
            )
        # stored before the owner API bounded the list (#75) and canonicalised the
        # CIDR (#97), and kept by it
        await session.execute(
            update(Subnet)
            .where(Subnet.id == subnet["id"])
            .values(dns_servers=legacy, cidr=_PADDED_CIDR, domain=_DOMAIN)
        )
        await session.commit()

    transport = Recording()
    http = httpx.AsyncClient(
        transport=transport,
        base_url="https://test",
        headers={"Authorization": f"Bearer {agent_row['token']}"},
    )
    agent = Agent(
        make_config(token=agent_row["token"]),
        AgentClient(http),
        prober=FakeProber(),
        resolver=FakeResolver(),
        arp_link=FakeArp(),
        interfaces=lambda: interfaces,
        clock=FakeClock(),
        rng=random.Random(7),
    )
    # the agent took the answer and queued its job, rather than retrying
    assert await agent.checkin_once() == 60
    [size] = transport.sizes
    raw = agent.queue.get_nowait()
    assert agent.queue.empty()

    # it is the worst case the server can build: the finest scope, every method the
    # server issues for a target that holds them all, and as many servers as a job carries
    assert len(raw["ranges"]) == len(scanned)
    assert [r["start"] for r in raw["ranges"]] == [str(ipaddress.ip_address(i)) for i in scanned]
    assert raw["methods"] == ["icmp", "arp", "rdns", "fdns"]
    assert raw["dns_servers"] == legacy[:DNS_SERVERS_MAX]
    # the CIDR in its canonical spelling, whatever the subnet stores
    assert canonical_cidr(_PADDED_CIDR) == str(net)
    assert raw["cidr"] == str(net)
    # as many names as a job sends, each at the longest: more hosts than that are named
    assert len(_LONGEST_FQDN) == 253
    assert len(raw["fdns"]) == fdns.FDNS_JOB_MAX < len(scanned)
    assert {entry["fqdn"] for entry in raw["fdns"]} == {_LONGEST_FQDN}

    # and the agent runs it
    parsed = protocol.parse_job(protocol.job_ref(raw), raw)
    assert len(guard.plan(parsed, make_config().allowed)) == len(scanned)
    assert len(parsed.fdns) == fdns.FDNS_JOB_MAX

    # The same answer with every address in it, and the CIDR, written at its longest,
    # and every method a target can hold, bounds what any subnet of this size can
    # produce.
    answer = {"data": {"checkin_interval_s": 60, "jobs": [raw]}}
    assert _encoded(answer) == size
    bound = {
        "data": {
            "checkin_interval_s": 60,
            "jobs": [
                {
                    **raw,
                    "cidr": _LONGEST_CIDR,
                    "ranges": [{"start": _LONGEST_IP, "end": _LONGEST_IP}] * len(raw["ranges"]),
                    "methods": list(scans.SCAN_METHODS),
                    "dns_servers": [_LONGEST_IP] * DNS_SERVERS_MAX,
                    # as many names as a job may send, of any scope, each at the longest
                    "fdns": [{"host_id": _LONGEST_HOST_ID, "fqdn": _LONGEST_FQDN}] * fdns.FDNS_JOB_MAX,
                }
            ],
        }
    }
    assert size <= _encoded(bound) < RESPONSE_MAX

    # What the server sent before #75, the whole stored list, would not have fitted.
    unbounded = {"data": {"checkin_interval_s": 60, "jobs": [{**raw, "dns_servers": legacy}]}}
    assert _encoded(unbounded) > RESPONSE_MAX

    # What the server sent before #97, the CIDR as stored, passed that bound, and the
    # agent refused the job it carried.
    as_stored = {**raw, "cidr": _PADDED_CIDR}
    assert _encoded({"data": {"checkin_interval_s": 60, "jobs": [as_stored]}}) > _encoded(bound)
    with pytest.raises(protocol.JobRefused):
        protocol.parse_job(protocol.job_ref(as_stored), as_stored)
