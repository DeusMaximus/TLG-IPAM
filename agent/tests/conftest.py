"""Fakes for the agent's tests. Nothing here sends a packet: the prober and resolver
record what they were asked, the clock only counts, and the server is either an
httpx mock transport or the real app in process (`test_e2e.py`)."""

import asyncio
import ipaddress
import json
import os
import random
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

# The server's package, for the contract and end-to-end tests. Its settings are read at
# import, so the environment is set first, with a database of this session's own.
_API = Path(__file__).resolve().parents[2] / "api"
sys.path.insert(0, str(_API))
_SESSION_DIR = tempfile.mkdtemp(prefix="tlg-ipam-agent-test-")
# Assigned, never defaulted: a DB_PATH already exported (a real database, say) must
# not reach the server's engine, which `fresh_db` in test_e2e.py empties.
os.environ["API_KEY"] = "test-key"
os.environ["DB_PATH"] = os.path.join(_SESSION_DIR, "ipam.db")
os.environ["STATIC_DIR"] = os.path.join(_SESSION_DIR, "no-static")

from tlg_ipam_agent import scan  # noqa: E402
from tlg_ipam_agent.client import AgentClient  # noqa: E402
from tlg_ipam_agent.config import Config, parse_allowed  # noqa: E402
from tlg_ipam_agent.loop import Agent  # noqa: E402

TOKEN = "tlga_" + "x" * 43
BASE = "https://ipam.example.com"


class FakeClock(scan.Clock):
    """Monotonic time that moves only when slept; `sleeps` records every wait."""

    def __init__(self):
        self.t = 1000.0
        self.sleeps: list[float] = []
        self.wall = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)
        super().__init__(monotonic=lambda: self.t, sleep=self._sleep, now=self._now)

    async def _sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        # tasks that are ready run first, at the time the sleep began, as they would
        # on a real loop; then time moves on
        await asyncio.sleep(0)
        self.t += max(seconds, 0)

    def _now(self) -> datetime:
        return self.wall + timedelta(seconds=self.t - 1000.0)


class FakeProber:
    """Answers for `alive`. `sent` is every address it was asked to ping, with the
    fake clock's time: the stand-in for a packet on the wire."""

    def __init__(self, alive=(), clock: FakeClock | None = None, delay: float = 0.0):
        self.alive = {ipaddress.ip_address(a) for a in alive}
        self.sent: list = []
        self.times: list[float] = []
        self.clock = clock
        self.delay = delay
        self.in_flight = 0
        self.max_in_flight = 0
        self.timeouts: list[float] = []

    async def ping(self, address, timeout):
        self.sent.append(address)
        self.timeouts.append(timeout)
        if self.clock is not None:
            self.times.append(self.clock.t)
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            if self.delay and self.clock is not None:
                await self.clock.sleep(self.delay)
            else:
                await asyncio.sleep(0)
        finally:
            self.in_flight -= 1
        return address in self.alive


class FakeResolver:
    def __init__(self, names=None, clock: FakeClock | None = None, delay: float = 0.0):
        self.names = {ipaddress.ip_address(k): v for k, v in (names or {}).items()}
        self.asked: list = []
        self.timeouts: list[float] = []
        self.clock, self.delay = clock, delay

    async def ptr(self, address, servers, timeout):
        self.asked.append((address, servers))
        self.timeouts.append(timeout)
        if self.delay and self.clock is not None:
            await self.clock.sleep(self.delay)
        else:
            await asyncio.sleep(0)
        return self.names.get(address)


class FakeServer:
    """A scripted server behind httpx's mock transport. `checkins` and `results` are
    lists of `(status, body)` answers, used in turn; the last one repeats."""

    def __init__(self, checkins=None, results=None):
        self.checkins = list(checkins or [(200, {"data": {"checkin_interval_s": 60, "jobs": []}})])
        self.results = list(results or [(200, {"data": {"accepted": 0, "rejected": []}})])
        self.requests: list[tuple[str, dict]] = []
        self.on_checkin = None

    def _next(self, answers):
        return answers.pop(0) if len(answers) > 1 else answers[0]

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append((request.url.path, body))
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        if request.url.path == "/api/v1/agent/checkin":
            if self.on_checkin:
                self.on_checkin()
            answer = self._next(self.checkins)
        elif request.url.path == "/api/v1/agent/results":
            answer = self._next(self.results)
        else:
            answer = 404, {"error": {"code": "NOT_FOUND", "message": "no route"}}
        if isinstance(answer, Exception):
            raise answer
        status, answer = answer
        return httpx.Response(status, json=answer)

    def posted(self, path: str) -> list[dict]:
        return [body for p, body in self.requests if p == path]

    @property
    def results_posted(self) -> list[dict]:
        return self.posted("/api/v1/agent/results")

    @property
    def checkins_posted(self) -> list[dict]:
        return self.posted("/api/v1/agent/checkin")


def make_config(**overrides) -> Config:
    values = dict(url=BASE, token=TOKEN, allowed=parse_allowed(None), max_pps=200)
    values.update(overrides)
    return Config(**values)


def make_agent(server: FakeServer, *, prober=None, resolver=None, clock=None, **config):
    clock = clock or FakeClock()
    http = httpx.AsyncClient(
        transport=httpx.MockTransport(server.handler),
        base_url=BASE,
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    agent = Agent(
        make_config(**config),
        AgentClient(http),
        prober=prober if prober is not None else FakeProber(),
        resolver=resolver if resolver is not None else FakeResolver(),
        interfaces=lambda: [{"name": "eth0", "cidr": "10.0.0.5/24", "mac": "02:00:00:00:00:05"}],
        clock=clock,
        rng=random.Random(1),
    )
    return agent, clock


def job(**overrides) -> dict:
    """A job as the server sends it: the lab subnet, its usable addresses."""
    value = {
        "job_id": "6f1c2a8e-0000-4000-8000-000000000001",
        "subnet_id": "5d0b1f7c-0000-4000-8000-000000000002",
        "cidr": "10.0.0.0/24",
        "ranges": [{"start": "10.0.0.1", "end": "10.0.0.254"}],
        "methods": ["icmp", "rdns"],
        "dns_servers": [],
        "timeout_s": 600,
        "fdns": [],
    }
    value.update(overrides)
    return value


def accepted(*jobs, interval=60) -> tuple[int, dict]:
    return 200, {"data": {"checkin_interval_s": interval, "jobs": list(jobs)}}


def refused(status: int, code: str, message: str = "refused") -> tuple[int, dict]:
    return status, {"error": {"code": code, "message": message}}


@pytest.fixture
def clock():
    return FakeClock()
