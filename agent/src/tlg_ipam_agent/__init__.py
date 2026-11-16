"""TLG-IPAM scan agent (v0.2 spec §5.6).

The agent checks in with the server, runs the jobs it is given one at a time inside
the owner's allowlist, and reports what answered. It never executes, downloads or
updates from anything the server sends (spec §7.2, invariant 1): a job is data, in a
fixed vocabulary, and the agent's code comes only from the published release.
"""

__version__ = "0.2.0.dev0"
