"""Where the agent's token comes from, and the container's enrolment exchange (#22).

The first part scripts the server's answers; the second runs the exchange against the
server's real enrol and check-in routes in process. Every state directory is the
test's own temporary one. No packet is sent."""

import asyncio
import json
import logging
import os
import random
import stat
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from app.database import SessionLocal, engine
from app.main import app
from app.models import Agent as AgentRow
from app.models import Base
from app.services import enrolment
from app.services.device_types import seed_defaults
from sqlalchemy import update

from conftest import BASE, ENROL_TOKEN, ETH0, TOKEN, FakeArp, FakeClock, FakeProber, FakeResolver
from tlg_ipam_agent import enrol
from tlg_ipam_agent.client import AgentClient, Kind
from tlg_ipam_agent.config import ConfigError, Settings, parse_allowed
from tlg_ipam_agent.loop import Agent, reported
from tlg_ipam_agent.protocol import CHECKIN_REFUSED_RETRY_S, CHECKIN_RETRY_MIN_S, PROTOCOL_VERSION

AGENT_ID = "0b6f3c1e-1111-4222-8333-444455556666"


def settings(**tokens) -> Settings:
    return Settings(url=BASE, allowed=parse_allowed(None), max_pps=200, **tokens)


def state(tmp_path) -> str:
    directory = tmp_path / "state"
    directory.mkdir(mode=0o700)
    return str(directory)


class Scripted:
    """The enrol route, scripted: `answers` used in turn, the last one repeating."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.requests: list[tuple[str, dict]] = []
        self.files_seen: list[list[str]] = []
        self.state_dir: str | None = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append((request.headers["authorization"], json.loads(request.content)))
        assert request.url.path == "/api/v1/agent/enrol"
        if self.state_dir is not None:
            self.files_seen.append(sorted(os.listdir(self.state_dir)))
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        status, body = answer
        return httpx.Response(status, json=body)

    def http(self, url: str, token: str) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=httpx.MockTransport(self.handler),
            base_url=url,
            headers={"Authorization": f"Bearer {token}"},
        )


ENROLLED = (200, {"data": {"agent_id": AGENT_ID, "agent_token": TOKEN}})
SPENT = (403, {"error": {"code": "FORBIDDEN", "message": f"echoing {ENROL_TOKEN}"}})
THROTTLED = (429, {"error": {"code": "RATE_LIMITED", "message": "wait"}})


def no_request(url, token):
    raise AssertionError("no request may be made")


async def obtain(state_dir, server=None, *, stop=None, clock=None, **tokens):
    return await enrol.agent_token(
        settings(**tokens),
        state_dir,
        stop=stop or asyncio.Event(),
        http=server.http if server else no_request,
        clock=clock or FakeClock(),
        rng=random.Random(3),
    )


# --- the order ---


async def test_a_token_set_by_hand_wins_and_nothing_is_read_or_stored(tmp_path):
    directory = state(tmp_path)
    (tmp_path / "state" / "token").write_bytes(b"not a token at all")
    assert await obtain(directory, agent_token=TOKEN) == TOKEN
    assert (tmp_path / "state" / "token").read_bytes() == b"not a token at all"


async def test_a_stored_token_wins_over_the_enrolment_token(tmp_path, caplog):
    """Acceptance 1, the agent's side: a recreated container with its volume and a
    spent enrolment token starts with the stored token, and makes no exchange."""
    directory = state(tmp_path)
    (tmp_path / "state" / "token").write_bytes(TOKEN.encode())
    with caplog.at_level(logging.WARNING, logger="tlg_ipam_agent"):
        assert await obtain(directory, enrol_token=ENROL_TOKEN) == TOKEN
    assert "IPAM_ENROL_TOKEN is ignored" in caplog.text
    assert ENROL_TOKEN not in caplog.text and TOKEN not in caplog.text


async def test_a_stored_token_written_by_hand_may_end_in_a_newline(tmp_path):
    directory = state(tmp_path)
    (tmp_path / "state" / "token").write_bytes(TOKEN.encode() + b"\n")
    assert await obtain(directory) == TOKEN


@pytest.mark.parametrize(
    "content",
    [b"", b"\n", b"tlge_" + b"y" * 43, b"tlga_ab cd", TOKEN.encode() + b"\n\n", b" " + TOKEN.encode(),
     "tlga_é".encode(), b"\xff\xfe"],
)
async def test_a_stored_file_that_is_not_a_token_stops_the_agent(tmp_path, content):
    """It is never enrolled over: the enrolment token is not used."""
    directory = state(tmp_path)
    (tmp_path / "state" / "token").write_bytes(content)
    with pytest.raises(ConfigError, match="token") as err:
        await obtain(directory, enrol_token=ENROL_TOKEN)
    assert str(tmp_path / "state" / "token") in str(err.value)
    assert content.decode("latin-1").strip() not in str(err.value) or not content.strip()


async def test_a_stored_token_behind_a_link_is_refused(tmp_path):
    directory = state(tmp_path)
    (tmp_path / "elsewhere").write_bytes(TOKEN.encode())
    (tmp_path / "state" / "token").symlink_to(tmp_path / "elsewhere")
    with pytest.raises(ConfigError, match="cannot be read"):
        await obtain(directory, enrol_token=ENROL_TOKEN)


async def test_no_token_from_anywhere_stops_the_agent(tmp_path):
    with pytest.raises(ConfigError, match="no agent token: set IPAM_ENROL_TOKEN"):
        await obtain(state(tmp_path))


async def test_a_missing_state_directory_reads_as_no_stored_token(tmp_path):
    assert await obtain(str(tmp_path / "none"), agent_token=TOKEN) == TOKEN
    with pytest.raises(ConfigError, match="no agent token"):
        await obtain(str(tmp_path / "none"))


# --- the exchange, scripted ---


async def test_the_exchange_stores_the_token_then_returns_it(tmp_path):
    directory = state(tmp_path)
    server = Scripted(ENROLLED)
    server.state_dir = directory
    assert await obtain(directory, server, enrol_token=ENROL_TOKEN) == TOKEN
    # the enrolment token was presented, the body is what the agent reports
    assert server.requests == [(f"Bearer {ENROL_TOKEN}", reported())]
    # the file existed, empty, before the request
    assert server.files_seen == [["token.new"]]
    path = tmp_path / "state" / "token"
    assert path.read_bytes() == TOKEN.encode()
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert os.listdir(directory) == ["token"]


async def test_a_state_directory_that_is_not_a_mount_point_is_warned_about(tmp_path, caplog):
    """In a container that means no volume: the token would not outlive it."""
    with caplog.at_level(logging.WARNING, logger="tlg_ipam_agent"):
        await obtain(state(tmp_path), Scripted(ENROLLED), enrol_token=ENROL_TOKEN)
    assert "is not a mount point" in caplog.text


async def test_a_state_directory_that_cannot_take_the_token_stops_before_the_exchange(tmp_path):
    """The enrolment token is not spent on an agent with nowhere to keep its token."""
    with pytest.raises(ConfigError, match="Mount a volume at /var/lib/tlg-ipam-agent"):
        await obtain(str(tmp_path / "none"), enrol_token=ENROL_TOKEN)
    open_to_all = tmp_path / "shared"
    open_to_all.mkdir()
    open_to_all.chmod(0o777)
    with pytest.raises(ConfigError, match="writable by users other than its owner"):
        await obtain(str(open_to_all), enrol_token=ENROL_TOKEN)


@pytest.mark.skipif(os.geteuid() == 0, reason="root writes any directory")
async def test_a_read_only_state_directory_stops_before_the_exchange(tmp_path):
    directory = state(tmp_path)
    (tmp_path / "state").chmod(0o500)
    try:
        with pytest.raises(ConfigError, match="cannot be stored"):
            await obtain(directory, enrol_token=ENROL_TOKEN)
    finally:
        (tmp_path / "state").chmod(0o700)


async def test_a_refused_exchange_waits_the_refusal_rule_and_never_loops(tmp_path, caplog):
    """A spent token is retried after 13.5-15 minutes, never at once: the enrol route's
    throttle is shared by every caller, so a tight loop would hold every enrolment at
    429. Nothing is left in the state directory between attempts, and neither token
    nor the server's text reaches the log."""
    directory = state(tmp_path)
    server = Scripted(SPENT)
    clock = FakeClock()
    stop = asyncio.Event()

    async def stopper():
        while clock.t < 1000.0 + 3600:
            await asyncio.sleep(0)
        stop.set()

    task = asyncio.ensure_future(stopper())
    with caplog.at_level(logging.INFO, logger="tlg_ipam_agent"):
        assert await obtain(directory, server, stop=stop, clock=clock, enrol_token=ENROL_TOKEN) is None
    await task
    assert 4 <= len(server.requests) <= 5  # an hour holds at most five tries
    assert all(0.9 * CHECKIN_REFUSED_RETRY_S <= s <= CHECKIN_REFUSED_RETRY_S for s in clock.sleeps)
    assert os.listdir(directory) == []
    assert "HTTP 403 FORBIDDEN" in caplog.text
    assert ENROL_TOKEN not in caplog.text and "echoing" not in caplog.text


@pytest.mark.parametrize(
    "answer",
    [THROTTLED, (503, {"error": {"code": "UNAVAILABLE", "message": "busy"}}), httpx.ConnectError("down")],
)
async def test_a_throttled_or_unavailable_exchange_retries_on_the_back_off(tmp_path, answer):
    directory = state(tmp_path)
    server = Scripted(answer, ENROLLED)
    clock = FakeClock()
    assert await obtain(directory, server, clock=clock, enrol_token=ENROL_TOKEN) == TOKEN
    assert len(server.requests) == 2
    assert len(clock.sleeps) == 1 and CHECKIN_RETRY_MIN_S <= clock.sleeps[0] <= 20


@pytest.mark.parametrize(
    "data",
    [
        {"agent_id": AGENT_ID, "agent_token": "tlge_" + "x" * 43},
        # the review of #130: printable and prefixed, but not what the server issues
        {"agent_id": AGENT_ID, "agent_token": "tlga_!"},
        {"agent_id": AGENT_ID, "agent_token": "tlga_x"},
        {"agent_id": AGENT_ID, "agent_token": "tlga_" + "x" * 42},
        {"agent_id": AGENT_ID, "agent_token": "tlga_" + "x" * 44},
        {"agent_id": AGENT_ID, "agent_token": "tlga_" + "x" * 42 + "."},
        {"agent_id": AGENT_ID, "agent_token": TOKEN + "\n"},
        {"agent_id": AGENT_ID, "agent_token": "tlga_" + "x" * 300},
        {"agent_id": AGENT_ID, "agent_token": "tlga_../../etc/x y"},
        {"agent_id": "not-an-id", "agent_token": TOKEN},
        {"agent_id": AGENT_ID},
        {"agent_token": TOKEN},
    ],
)
async def test_an_answer_not_in_the_enrol_shape_writes_nothing(tmp_path, data):
    """Only a token of a token's shape is written; anything else is retried like a 503
    (if the server did enrol, the retry meets 403 and the owner re-enrols)."""
    directory = state(tmp_path)
    server = Scripted((200, {"data": data}), SPENT)
    stop = asyncio.Event()
    clock = FakeClock()

    async def stop_after_two():
        while len(server.requests) < 2:
            await asyncio.sleep(0)
        stop.set()

    task = asyncio.ensure_future(stop_after_two())
    assert await obtain(directory, server, stop=stop, clock=clock, enrol_token=ENROL_TOKEN) is None
    await task
    assert os.listdir(directory) == []


async def test_two_first_starts_on_one_directory_enrol_once(tmp_path):
    """Greptile on #130: two processes sharing one empty volume. The second waits on
    the directory lock while the first exchanges, then takes the token the first
    stored: one request, one token, and no pending file removed from under the first."""
    directory = state(tmp_path)
    release = asyncio.Event()
    requests = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        await release.wait()
        return httpx.Response(ENROLLED[0], json=ENROLLED[1])

    def http(url, token):
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url=url)

    def start():
        return asyncio.ensure_future(
            enrol.agent_token(settings(enrol_token=ENROL_TOKEN), directory, stop=asyncio.Event(), http=http)
        )

    first = start()
    while not requests:
        await asyncio.sleep(0)
    second = start()
    for _ in range(50):
        await asyncio.sleep(0)
    assert not second.done() and len(requests) == 1
    release.set()
    assert await first == TOKEN and await second == TOKEN
    assert len(requests) == 1
    assert os.listdir(directory) == ["token"]


async def test_a_token_issued_but_not_stored_stops_the_agent(tmp_path, monkeypatch):
    directory = state(tmp_path)
    server = Scripted(ENROLLED)

    def fail(self, data):
        self.discard()
        raise enrol.store.StoreError("disk full")

    monkeypatch.setattr(enrol.store.PendingFile, "commit", fail)
    with pytest.raises(ConfigError, match="issued but not stored") as err:
        await obtain(directory, server, enrol_token=ENROL_TOKEN)
    assert TOKEN not in str(err.value)
    assert os.listdir(directory) == []


# --- against the server's real routes ---


@pytest.fixture
async def fresh_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    async with SessionLocal() as session:
        await seed_defaults(session)
    enrolment.throttle.reset()
    yield
    enrolment.throttle.reset()


@pytest.fixture
async def owner(fresh_db):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test", headers={"X-API-Key": "test-key"}
    ) as client:
        yield client


def real_http(url: str, token: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test", headers={"Authorization": f"Bearer {token}"}
    )


async def _checkin(token: str):
    async with real_http(BASE, token) as http:
        agent = Agent(
            settings().config(token),
            AgentClient(http),
            prober=FakeProber(),
            resolver=FakeResolver(),
            arp_link=FakeArp(),
            interfaces=lambda: ETH0,
            clock=FakeClock(),
            rng=random.Random(1),
        )
        return await agent.client.checkin({**reported(), "protocol": PROTOCOL_VERSION, "interfaces": ETH0, "capabilities": []})


async def test_first_start_enrols_and_a_recreated_container_starts_on_the_stored_token(owner, tmp_path):
    """Acceptance 1 against the real routes: the first start exchanges the enrolment
    token and stores the agent token; a second start with the same state directory
    and the same, now spent, enrolment token makes no exchange and checks in."""
    created = (await owner.post("/api/v1/agents", json={"name": "vlan10"})).json()["data"]
    directory = state(tmp_path)
    first = await enrol.agent_token(
        settings(enrol_token=created["enrol_token"]), directory, stop=asyncio.Event(), http=real_http
    )
    assert first.startswith("tlga_")
    assert (tmp_path / "state" / "token").read_bytes() == first.encode()
    async with SessionLocal() as session:
        row = await session.get(AgentRow, created["id"])
        assert row.state == "active"
        assert row.version == reported()["version"]
    assert (await _checkin(first)).kind is Kind.OK

    # the spent token is refused by the server, so the second start must not send it
    async with real_http(BASE, created["enrol_token"]) as http:
        assert (await enrol.exchange(http)).status == 403
    enrolment.throttle.reset()
    async with SessionLocal() as session:  # past the server's 10 s check-in gap
        await session.execute(
            update(AgentRow).values(last_checkin_at=datetime.now(timezone.utc) - timedelta(seconds=30))
        )
        await session.commit()
    second = await enrol.agent_token(
        settings(enrol_token=created["enrol_token"]), directory, stop=asyncio.Event(), http=no_request
    )
    assert second == first
    assert (await _checkin(second)).kind is Kind.OK
    assert len(enrolment.throttle._taken) == 0


async def test_a_re_enrol_token_replaces_a_lost_volumes_token(owner, tmp_path):
    """The recovery path: the volume is gone, the owner re-enrols, the new token is
    exchanged into an empty state directory and the old agent token stops working."""
    created = (await owner.post("/api/v1/agents", json={"name": "vlan10"})).json()["data"]
    old = await enrol.agent_token(
        settings(enrol_token=created["enrol_token"]), state(tmp_path), stop=asyncio.Event(), http=real_http
    )
    again = (await owner.post(f"/api/v1/agents/{created['id']}/re-enrol")).json()["data"]
    fresh = tmp_path / "rebuilt"
    fresh.mkdir(mode=0o700)
    new = await enrol.agent_token(
        settings(enrol_token=again["enrol_token"]), str(fresh), stop=asyncio.Event(), http=real_http
    )
    assert new != old
    assert (await _checkin(new)).kind is Kind.OK
    assert (await _checkin(old)).status == 403
