# tlg-ipam-agent

The TLG-IPAM scan agent: it checks in with a TLG-IPAM server, sweeps the subnets the
server assigns it with ICMP echo and reverse DNS, and reports what answered. It never
sends a packet outside `ALLOWED_CIDRS`, and never runs, downloads or installs anything
the server sends.

Running it, and what each setting does: the "Scan agent" section of the
[project README](../README.md#scan-agent). The design is in
[`docs/v0.2-spec.md`](../docs/v0.2-spec.md) §5.

Tests, from this directory (one-time setup:
`python3 -m venv .venv && .venv/bin/pip install --require-hashes -r requirements-dev.txt`):

```sh
.venv/bin/python -m pytest
```

The tests send no packet: the prober and resolver are fakes, and the end-to-end test
runs the agent's client against the server's real routes in process.
