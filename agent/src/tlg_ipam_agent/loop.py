"""The check-in loop (spec §5.1, §5.7).

The agent checks in on the interval the server gives, and keeps doing so while a job
runs. Jobs run one at a time, in a single worker, in the order they arrived. What the
loop does with each answer is the rule in spec §5.7 (`agent_protocol.CHECKIN_*`):

- `OK`: its jobs are queued, the back-off is reset, and the next check-in is due
  after `checkin_interval_s`.
- `RETRY` (429, 5xx, no answer): bounded back-off with jitter (`backoff.Backoff`).
- `REFUSED` (401, 403, 409, 422, other 4xx): 13.5-15 minutes, never a tight loop.

Only an `OK` answer queues a job. Nothing else, and nothing the agent remembers from
an earlier answer, starts a sweep.
"""

import asyncio
import logging
import platform
import random
import socket
from collections.abc import Callable

from . import __version__, guard, scan
from .backoff import Backoff, refused_wait
from .client import AgentClient, Kind
from .config import Config
from .probe import ArpLink, Prober, Resolver
from .protocol import (
    HOSTNAME_MAX,
    LEASE_S,
    PLATFORM_MAX,
    PROTOCOL_VERSION,
    SUPPORTED,
    VERSION_MAX,
    JobRefused,
    job_ref,
    parse_job,
    printable,
)

log = logging.getLogger("tlg_ipam_agent")

REDACTED = "tlga_[redacted]"


class Redact(logging.Filter):
    """Takes the agent's token out of every record a handler emits, traceback included.
    A backstop: the agent logs no text the server wrote (`client.Answer.message`), but
    an exception or a library's own log line could still carry the token."""

    def __init__(self, token: str):
        super().__init__()
        self.token = token

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        if self.token in message:
            record.msg, record.args = message.replace(self.token, REDACTED), None
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text and self.token in record.exc_text:
            record.exc_text = record.exc_text.replace(self.token, REDACTED)
        if record.stack_info and self.token in record.stack_info:
            record.stack_info = record.stack_info.replace(self.token, REDACTED)
        return True

# the server's interval is honoured inside these bounds
INTERVAL_MIN_S = 10
INTERVAL_MAX_S = 3600
# jobs waiting behind the one running; the server sends at most one per check-in and
# none while this agent holds a lease, so this is never reached in normal running
QUEUE_MAX = 4
# reported at check-in, and not yet used by the server: the methods this release runs
CAPABILITIES = sorted(SUPPORTED)
# a backstop past the job's own deadline, on the event loop's clock: a scan still
# running this long after its timeout is stopped, so one stuck await cannot hold the
# worker until the job's lease runs out
SCAN_GRACE_S = 30


def checkin_body(interfaces: list[dict]) -> dict:
    return {
        "version": printable(__version__, VERSION_MAX),
        "protocol": PROTOCOL_VERSION,
        "hostname": printable(socket.gethostname(), HOSTNAME_MAX),
        "platform": printable(platform.platform(), PLATFORM_MAX),
        "interfaces": interfaces,
        "capabilities": CAPABILITIES,
    }


class Agent:
    def __init__(
        self,
        config: Config,
        client: AgentClient,
        *,
        prober: Prober,
        resolver: Resolver,
        arp_link: Callable[[str], ArpLink],
        interfaces: Callable[[], list[dict]],
        clock: scan.Clock | None = None,
        rng: random.Random | None = None,
    ):
        self.config = config
        self.client = client
        self.prober = prober
        self.resolver = resolver
        self.arp_link = arp_link
        self.interfaces = interfaces
        self.clock = clock or scan.Clock()
        self.rng = rng or random.SystemRandom()
        self.backoff = Backoff(self.rng)
        self.queue: asyncio.Queue = asyncio.Queue(QUEUE_MAX)

    # --- check-in ---

    async def checkin_once(self) -> float:
        """One check-in. Queues the jobs of an accepted one, and returns how long to
        wait before the next."""
        answer = await self.client.checkin(checkin_body(self.interfaces()))
        if answer.kind is Kind.OK:
            self.backoff.reset()
            # `data` has the check-in shape: an integer interval and a list of jobs
            self._queue_jobs(answer.data["jobs"])
            interval = answer.data["checkin_interval_s"]
            return float(min(max(interval, INTERVAL_MIN_S), INTERVAL_MAX_S))
        if answer.kind is Kind.RETRY:
            wait = self.backoff.next()
            log.warning("check-in not accepted (%s); retrying in %.0f s", answer.message, wait)
            return wait
        self.backoff.reset()
        wait = refused_wait(self.rng)
        log.error("check-in refused (%s); trying again in %.0f s", answer.message, wait)
        return wait

    def _queue_jobs(self, jobs) -> None:
        if not isinstance(jobs, list):
            return
        for raw in jobs:
            try:
                self.queue.put_nowait(raw)
            except asyncio.QueueFull:
                log.error("a job arrived while %d were waiting; it is not run", QUEUE_MAX)

    # --- jobs ---

    async def run_job(self, raw) -> None:
        """Guard, sweep and report one job from an accepted check-in."""
        ref = job_ref(raw)
        if ref is None:
            log.error("a job without a usable job_id and subnet_id cannot be run or reported")
            return
        received = self.clock.monotonic()
        try:
            job = parse_job(ref, raw)
            addresses = guard.plan(job, self.config.allowed)
        except JobRefused as refused:
            # no packet is sent for a refused job; the refusal is reported
            log.error("job %s %s", ref.job_id, refused.reason)
            body = scan.refusal(ref, refused.reason, self.clock)
        else:
            log.info("job %s: %d addresses in %s", ref.job_id, len(addresses), job.network)
            scanning = scan.run(
                job,
                addresses,
                prober=self.prober,
                resolver=self.resolver,
                arp_link=self.arp_link,
                # read again as the job starts: the ARP sweep needs the interface on
                # the subnet as it is now, not as the last check-in saw it
                interfaces=self.interfaces(),
                max_pps=self.config.max_pps,
                clock=self.clock,
            )
            try:
                body = await asyncio.wait_for(scanning, job.timeout_s + SCAN_GRACE_S)
            except TimeoutError:
                # what the scan saw is lost; the server keeps the error and clears the
                # lease, and the result set marks nothing dead
                log.error("job %s did not end within its timeout; stopped", ref.job_id)
                body = scan.refusal(
                    ref, f"agent error: the scan did not end within {job.timeout_s} s", self.clock
                )
            except Exception as exc:  # a bug, not a refusal: report it, keep running
                log.exception("job %s failed", ref.job_id)
                body = scan.refusal(ref, f"agent error: {type(exc).__name__}", self.clock)
        await self.report(body, received)

    async def report(self, body: dict, received: float) -> None:
        """Post a result set, retrying on the back-off while the job's lease could still
        be live. Each request is cut off when the lease runs out, so a server that
        drips its answer cannot hold the worker past it. An acknowledgment that is not
        the results shape is retried like a 503. Any refusal of the result set (409
        JOB_NOT_CURRENT, 413, 422, 401, 403) is final."""
        backoff = Backoff(self.rng)
        end = received + LEASE_S
        while True:
            left = end - self.clock.monotonic()
            if left <= 0:
                log.error("results of job %s not delivered before its lease ran out", body["job_id"])
                return
            answer = await self.client.results(body, budget=left)
            if answer.kind is Kind.OK:
                # validated counts only (`client.results_data`)
                log.info(
                    "job %s reported: %d accepted, %d rejected",
                    body["job_id"],
                    answer.data["accepted"],
                    answer.data["rejected"],
                )
                return
            if answer.kind is Kind.REFUSED:
                log.error("results of job %s refused (%s)", body["job_id"], answer.message)
                return
            wait = backoff.next()
            if self.clock.monotonic() + wait > end:
                log.error(
                    "results of job %s not delivered before its lease ran out (%s)",
                    body["job_id"],
                    answer.message,
                )
                return
            log.warning("results of job %s not accepted (%s); retrying in %.0f s",
                        body["job_id"], answer.message, wait)
            await self.clock.sleep(wait)

    async def worker(self) -> None:
        while True:
            raw = await self.queue.get()
            try:
                await self.run_job(raw)
            except Exception:  # one job's failure never stops the next
                log.exception("a job could not be run or reported")
            finally:
                self.queue.task_done()

    # --- running ---

    async def run(self, stop: asyncio.Event | None = None) -> None:
        """Check in until `stop` is set, with the worker running beside the loop."""
        stop = stop or asyncio.Event()
        worker = asyncio.create_task(self.worker())
        try:
            while not stop.is_set():
                wait = await self.checkin_once()
                if worker.done():  # the worker never returns; surface why it stopped
                    worker.result()
                await self._pause(wait, stop)
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)

    async def _pause(self, seconds: float, stop: asyncio.Event) -> None:
        sleeping = asyncio.ensure_future(self.clock.sleep(seconds))
        stopping = asyncio.ensure_future(stop.wait())
        try:
            await asyncio.wait({sleeping, stopping}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            sleeping.cancel()
            stopping.cancel()
