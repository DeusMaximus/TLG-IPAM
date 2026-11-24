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

**What clears a stored name.** Only a DNS server's answer that an address has no name
(`NXDOMAIN`, or a `NOERROR` without a PTR record that carries the zone's SOA, or no
record at all) clears the name the server holds for it. A lookup no server answered
usably (silence, `SERVFAIL`, `REFUSED`, a malformed or truncated reply, a referral, also
after a CNAME as in a classless reverse delegation, or a CNAME left unresolved; each
sends the lookup on to the next configured server) leaves the stored name as it is,
and the scan reports
"rdns: no DNS server gave a usable answer for N of M addresses". So a resolver outage
keeps stale names until a server answers again, and never stops the scan from marking
a host that has left the network as gone.

**The packet budget (`MAX_PPS`).** Every echo request, ARP request and DNS query the
agent sends takes a slot of one budget, which it keeps from one scan to the next. Each
packet is handed to the kernel in the step of the agent's event loop that its slot is
granted in, or not at all: one the socket cannot take at once is a failed send, not one
kept to go later. A slot ends once that step has, so once its packet has been handed
over, and the next comes no sooner than `1/MAX_PPS` after that, by the agent's clock. No
two packets are handed over less than `1/MAX_PPS` apart, and a sleep that wakes late or
a hand-off the agent's process was slow to make moves the later slots, never bringing
one sooner. The budget stops at the hand-off: the kernel's and the network device's
queues can still put two packets closer together on the wire. Nor does it cover the
kernel's own ARP: pinging an address on the agent's own subnet that is
not in the kernel's neighbour table makes the kernel ask for it first, up to 3 times
(`mcast_solicit`) a second apart for an address that does not answer. A scan with `arp`
and `rdns` only sends no echo request, and so draws none of those for the addresses
it scans.

**What the agent opens and writes.** One module, `store.py`, opens files itself: the
stored agent token (`/var/lib/tlg-ipam-agent/token`), read on every start without
following a link or waiting on a FIFO and written once, at enrolment, beside its path
with its mode and owner set first and then renamed into place; and the files
`tlg-ipam-agent install` writes and `uninstall` removes. Libraries open what else it
reads (pathlib an interface's MAC under `/sys/class/net`, dnspython `/etc/resolv.conf`,
the TLS stack its CA bundle). Only `install.py` starts a process: `useradd`, `userdel`,
`groupdel` and `systemctl`, with fixed arguments. `tests/test_invariants.py` holds that
as a tripwire on the source, not a proof: it refuses `open` in every spelling, the ways
of writing, renaming and removing a file the allowed modules name, and those of
starting a process, outside the one module each is allowed in, and allows each library
read only in the module listed for it. It sees names, so code that reaches a file
without one it lists passes it, and review holds the rule (spec §7.2, invariant 1).

**Building the wheel** (for the systemd install of a build of `main`; each release
publishes one, `.agents/releases.md`): `.venv/bin/pip wheel --no-deps . -w dist` from
this directory. Then, on the host,
`sudo python3 -m venv /opt/tlg-ipam-agent`, `sudo /opt/tlg-ipam-agent/bin/pip install
tlg_ipam_agent-<version>-py3-none-any.whl`, and `tlg-ipam-agent install` as the
[project README](../README.md#run-it-as-a-systemd-unit) shows.

Tests, from this directory (one-time setup:
`python3 -m venv .venv && .venv/bin/pip install --require-hashes -r requirements-dev.txt`):

```sh
.venv/bin/python -m pytest
```

The tests send no packet off this machine: the prober and the ARP socket are fakes,
the resolver is a fake or the real one against DNS responders on loopback, and the
end-to-end tests run the agent's client against the server's real routes in process.
The enrolment and install tests write only inside a temporary directory, and the
install's host (its users and systemd) is a fake that records the programs it would
run.
The real resolver reads only the files and FIFOs its tests make in a temporary
directory, never this machine's own `/etc/resolv.conf`.
