"""The release version of the server, which the scan agent shares (#24).

One version names a release: the server reports it (`GET /health`, `settings.version`),
the agent's `__version__` (`agent/src/tlg_ipam_agent/__init__.py`) is the same string,
and the release pipeline (`.github/workflows/release.yml`) publishes the agent's image
and wheel under it from the tag `v<VERSION>`. The Agents page pins its install commands
to it, and flags an agent that reports an older one (`agents.update_available`).

It is a PEP 440 public version in its normal form with no local `+…` part: `0.2.0` at a
release, and the next release's `.devN` on `main` between releases. Change it here and
in the agent's `__init__.py` together; `agent/tests/test_release.py` and
`web/src/utils/install.test.js` hold the two to the same string and to that form, and
`.agents/releases.md` is the checklist. It is read from the code, never from the
environment, so a server reports the release it runs.
"""

VERSION = "0.2.0.dev0"
