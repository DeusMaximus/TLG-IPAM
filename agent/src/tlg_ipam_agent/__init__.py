"""TLG-IPAM scan agent (v0.2 spec §5.6).

The agent checks in with the server, runs the jobs it is given one at a time inside
the owner's allowlist, and reports what answered. It never executes, downloads or
updates from anything the server sends (spec §7.2, invariant 1): a job is data, in a
fixed vocabulary, and the agent's code comes only from the published release.
"""

# The release version, the same string as the server's `api/app/version.py` (#24): a
# literal, which flit reads for the wheel's version without importing the package.
# `.agents/releases.md` is the checklist for changing it.
__version__ = "0.2.0"
