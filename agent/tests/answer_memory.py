"""What the agent's client holds of one check-in answer, measured in a fresh
interpreter (#156). `test_answers.py` runs this file as `python -I answer_memory.py
network URL READER` or `... chunks SIZES` and reads one line of JSON from it.

Measured inside the test process, the figure also counted what the process did for
others while the check-in ran: the test's own server, garbage left by earlier tests,
and the interpreter's own tables. One of those decided it. Each check-in makes
httpcore try `import sniffio`, which is not installed; under pytest the attempt passes
pytest's assertion-rewriting import hook, which builds a `pathlib` path and so
interns a new string that is freed again. Each such string takes a slot of the
interned-string dict, and when the slots run out the dict rebuilds its table: a
block of 1.9 or 3.8 MB, which `tracemalloc` counts in full because the table it
replaces was allocated before tracing began. How many slots are left when the test
starts depends on every string interned before it, the text of every collected test
module included, so whether the rebuild fell inside the check-in changed with an
unrelated module.

Here the interpreter runs the agent's client and nothing else: no pytest, no import
hook, no server (the test serves the answer from its own process). A first check-in
loads what loads on first use, and only the second is measured. A bare interpreter
takes no interned slot per check-in, so the measured figure is the client's.

Cases:
  network URL READER   the agent's client against the test's server at URL. READER
                       is `agent` for the client as it ships, or a replacement body
                       reader that holds what the agent's refuses: `twice-the-cap`
                       (raw bytes up to 2 * RESPONSE_MAX) or `decodes` (the body
                       decoded, then capped). The replacements show that the
                       measurement sees a client that holds too much.
  chunks SIZE,...      the client against a transport handing over raw chunks of
                       those sizes, allocated before tracing starts
"""

import asyncio
import json
import os
import sys
import tracemalloc

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "src"))

import httpx  # noqa: E402

from tlg_ipam_agent import client as agent_client  # noqa: E402
from tlg_ipam_agent.client import RESPONSE_MAX, AgentClient, make_http  # noqa: E402

TOKEN = "tlga_" + "x" * 43


class Chunks(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk


async def twice_the_cap(res: httpx.Response) -> bytes:
    """The agent's reader with a cap twice the agent's: a second copy of the cap held."""
    body = bytearray()
    async for chunk in res.aiter_raw():
        if len(body) + len(chunk) > 2 * RESPONSE_MAX:
            raise agent_client._Unreadable(f"answer over {2 * RESPONSE_MAX} bytes")
        body += chunk
    return bytes(body)


async def decodes(res: httpx.Response) -> bytes:
    """A reader that caps the body but decodes it first, as httpx's `aiter_bytes` does."""
    body = bytearray()
    async for chunk in res.aiter_bytes():
        if len(body) + len(chunk) > RESPONSE_MAX:
            raise agent_client._Unreadable(f"answer over {RESPONSE_MAX} bytes")
        body += chunk
    return bytes(body)


async def measured(checkin) -> dict:
    tracemalloc.start()
    try:
        base = tracemalloc.get_traced_memory()[0]
        answer = await checkin()
        peak = tracemalloc.get_traced_memory()[1] - base
    finally:
        tracemalloc.stop()
    return {"peak": peak, "kind": answer.kind.name, "status": answer.status, "message": answer.message}


async def over_the_network(url: str) -> dict:
    async with make_http(url, TOKEN) as http:
        client = AgentClient(http)
        await client.checkin({})
        return await measured(lambda: client.checkin({}))


async def from_chunks(sizes: list[int]) -> dict:
    def client_for(chunks):
        transport = httpx.MockTransport(lambda request: httpx.Response(200, stream=Chunks(chunks)))
        return AgentClient(httpx.AsyncClient(transport=transport, base_url="https://x"))

    await client_for([b"x"]).checkin({})
    chunks = [b"x" * n for n in sizes]
    client = client_for(chunks)
    return await measured(lambda: client.checkin({}))


READERS = {"twice-the-cap": twice_the_cap, "decodes": decodes}


def main(case: str, *args: str) -> dict:
    if case == "chunks":
        (sizes,) = args
        return asyncio.run(from_chunks([int(n) for n in sizes.split(",")]))
    if case == "network":
        url, reader = args
        if reader != "agent":
            agent_client._bounded_body = READERS[reader]
        return asyncio.run(over_the_network(url))
    raise SystemExit(f"unknown case {case!r}")


if __name__ == "__main__":
    print(json.dumps(main(*sys.argv[1:])))
