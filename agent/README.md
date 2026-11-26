# tlg-ipam-agent

The TLG-IPAM scan agent: it checks in with a TLG-IPAM server, sweeps the subnets the
server assigns it with ICMP echo, ARP and reverse DNS, and reports what answered. It
never sends a packet outside `ALLOWED_CIDRS` (and, when `ALLOWED_DNS_SERVERS` is set,
no DNS query to a server outside it), and never runs, downloads or installs anything
the server sends.

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

**Which DNS servers it may ask** (#93). Unset, `ALLOWED_DNS_SERVERS` limits nothing:
the subnet's servers, or the host's, are asked as they are. Set, a server outside it
is left out before any query (`guard.dns_servers`), whichever of the two named it,
after the list is cut to three, so the setting only ever takes a server away. The
others are asked, and the scan's error says how many were not, counts only; when none
is left, no lookup is made and the scan is incomplete with "rdns not run: all N DNS
servers to ask are outside ALLOWED_DNS_SERVERS", and the names already stored are
kept. An IPv6 item with a scope (`fe80::53%eth0`) is refused at start, and a server with
a scope is never asked while the setting is set: `ipaddress` compares networks without
the scope, but the query goes to the interface the scope names, so the list could not
say which interface's link-local resolver it means. A link-local server written without
a scope is compared by its address alone, and its query goes out on the link the host's
routing picks. For the systemd unit, `install
--allowed-dns-servers …` sets it (#179), and an update run without the option keeps the
value `agent.env` holds, since its absence would mean any server, as it keeps
`ALLOWED_CIDRS` and `MAX_PPS` (#183); delete the line there to remove it. A drop-in's `Environment=ALLOWED_DNS_SERVERS=…`, which these docs used to
suggest, is overridden by `agent.env`'s once that sets one. The agent reads the setting
when it starts: after changing it by hand, restart the agent, and its start-up log line
shows the value it took. A subnet's DNS servers are not checked against anything else, so this is what
stops a server that has been taken over from pointing the agent's queries at a
resolver of its choosing.

**What clears a stored name.** Only a DNS server's answer that an address has no name
(`NXDOMAIN`, or a `NOERROR` without a PTR record that carries the SOA of a zone holding
the name, or no record at all) clears the name the server holds for it. A reply is read
against the question: a PTR record counts only at the address's own reverse name or at
the end of the CNAME chain the reply gives for it, and an SOA only for a zone that
contains that name. A lookup no server answered
usably (silence, `SERVFAIL`, `REFUSED`, a malformed or truncated reply, a referral, also
after a CNAME as in a classless reverse delegation, a CNAME left unresolved, a PTR
record for some other name, or an SOA for a zone that does not hold the name; each
sends the lookup on to the next configured server) leaves the stored name as it is,
and the scan reports
"rdns: no DNS server gave a usable answer for N of M addresses". So a resolver outage
keeps stale names until a server answers again, and never stops the scan from marking
a host that has left the network as gone.

**A warning on every scan from a non-recursive server.** The agent follows neither a
CNAME nor a referral itself. If a subnet's only DNS server does not recurse and answers
its reverse names with an alias it does not resolve (classless reverse delegation,
RFC 2317, served by another server), every scan of that subnet reports "no DNS server
gave a usable answer", and a stale name there is never cleared. That is by design, not
a fault. To fix it, point the subnet at a recursive resolver, or at a server that also
serves the zone the alias points into.

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
[project README](../README.md#run-it-as-a-systemd-unit) shows. Run again without
`--enrol-token` (an update), `install` keeps the stored token and the `ALLOWED_CIDRS`,
`ALLOWED_DNS_SERVERS` and `MAX_PPS` that `agent.env` holds, each replaced only by its
option (`install.kept_settings`, #183), and stops before changing anything when
`agent.env` is in a form it does not write or a kept value no longer parses.

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
