# tlg-ipam-agent

The TLG-IPAM scan agent: it checks in with a TLG-IPAM server, sweeps the subnets the
server assigns it with ICMP echo, ARP and reverse DNS, and reports what answered. It
never sends a packet outside `ALLOWED_CIDRS`, and never runs, downloads or installs
anything the server sends.

Running it, and what each setting does: the "Scan agent" section of the
[project README](../README.md#scan-agent). The design is in
[`docs/v0.2-spec.md`](../docs/v0.2-spec.md) §5.

**Reverse DNS and the host's `/etc/resolv.conf`.** A subnet that lists no DNS servers
is looked up through the agent host's `/etc/resolv.conf`, read again for every scan.
The agent reads it in a thread of its own: it looks at the path first (`os.stat`), and
has dnspython open and parse it only if, when it looks, the path is a regular file (or
a link to one) of at most 64 KiB; the read must end within a second. What the look
finds to be anything else gives no server and is not opened: a FIFO, a device, a
directory or a larger file. The look is not a bound on what is then read: a file that
grows past 64 KiB after the look is parsed whole, and a FIFO put at the path after the
look is opened, and the read waits on it. A path that gives no server marks the scan
incomplete with "rdns not run: the agent has no DNS server to ask", and the names
already stored are kept (if the scan's own time runs out while it waits for the read,
it reports the reverse-DNS timeout instead). If the read is stuck (a network mount
that stopped answering, or that FIFO), every later scan of a subnet without DNS
servers reports the same, and fixing the file is not enough: the stuck read must end,
or the agent be restarted. A subnet that lists its own DNS servers is not affected, so
setting them is the other way out.

**What the agent opens.** No file, itself: libraries open what it reads (pathlib an
interface's MAC under `/sys/class/net`, dnspython `/etc/resolv.conf`, the TLS stack its
CA bundle), and it writes none. `tests/test_invariants.py` holds that as a tripwire on the source, not a proof:
it refuses `open` in every spelling and the ways of writing a file the allowed modules
name, and allows each library read only in the module listed for it. It sees names,
so code that reaches a file without one it lists passes it, and review holds the rule
(spec §7.2, invariant 1).

Tests, from this directory (one-time setup:
`python3 -m venv .venv && .venv/bin/pip install --require-hashes -r requirements-dev.txt`):

```sh
.venv/bin/python -m pytest
```

The tests send no packet off this machine: the prober and the ARP socket are fakes,
the resolver is a fake or the real one against DNS responders on loopback, and the
end-to-end tests run the agent's client against the server's real routes in process.
The real resolver reads only the files and FIFOs its tests make in a temporary
directory, never this machine's own `/etc/resolv.conf`.
