# Session hand-off log

The next session may be a different agent or model with no shared context. Every session that changes this repo adds an entry **at the top** (newest first) before it finishes.

- **Five entries live here.** When yours makes six, move the oldest, verbatim, to the top of `.agents/handoff/YYYY-MM.md` (the month in its date) in the same commit.
- **An entry carries state in about 40 lines:** what was done, what was decided, what is half-finished or broken, what comes next. Detail belongs in the issue or PR; link it.
- **The newest entry is self-sufficient about live state.** Restate or link anything from an older entry that still matters. Rotation drops the older ones, so nothing live may depend on them.
- **Entries commit straight to `main`.** If two sessions add entries at once, keep both, newest first.

To find something in the archive, grep it:

```bash
grep -n '^## ' .agents/handoff/*.md      # every archived title, with its date
grep -rn '#12' .agents/handoff/          # every mention of an issue or PR number
```

Template:

```markdown
## YYYY-MM-DD — <agent (model)> — <short title>
- **Done:**
- **Decisions:**
- **State:** (tests? schema? anything half-finished or broken?)
- **Waiting on the owner:**
- **Next:**
```

---

## 2026-11-13 — Claude Code (Fable 5.2) — v0.2 spec accepted; process skeleton added

- **Done:**
  - `docs/v0.2-spec.md`: the v0.2 spec (scan agents, remote MCP), accepted at draft 5 after a review against the code. It is the contract for unshipped work; the brief stays the description of `main`.
  - Process skeleton: this file, the Workflow section in `AGENTS.md`, `.agents/README.md`, `.agents/review-brief.md`, `.dev/` gitignored.
  - Brief: a real domain removed from the footer.
- **Decisions** (all recorded in the spec's §9):
  - Linking a remote MCP client uses a one-time code from the private UI. There is no `OWNER_PASSWORD`.
  - ARP scanning is an active request/reply sweep. The kernel neighbour table is not read.
  - Scan scope: the DHCP pool is not scanned unless enabled per subnet; reserved ranges are scanned unless excluded per range; exclusion is absolute and not configurable over MCP.
  - M2 is split: M2a (ingest, observations, scope, findings, container agent with a hand-made token) and M2b (enrolment, guided install, systemd installer).
  - Process: one issue, one branch, one PR; merge needs green CI and a GO from a reviewer in a different model family; the owner merges.
- **State:**
  - No application code has changed. Backend suite: 80 passed on the local venv (FastMCP 3.4.2), and 80 passed on a fresh install (FastMCP 4.1.8, MCP SDK 2.3.0) with one deprecation warning in `tests/test_mcp.py`.
  - `api/requirements.txt` is still unpinned, so a fresh `docker build` installs FastMCP 4.x today. Pinning is M0.
  - `api/tests/conftest.py` points every pytest session at one fixed temp database. Two sessions on one machine at the same time corrupt each other. Run one at a time until the M0 issue that gives each session its own database lands; it is the first thing to do before agents work in parallel.
  - Milestones and issues are not filed yet.
- **Waiting on the owner:** approval of the milestone and issue list; later, the M0 redeploy and deliberate restore test, the M3 client-compatibility spike (needs real web-client accounts and a public URL), and installing agents on the VLANs.
- **Next:** file milestones M0, M1, M2a, M2b, M3 and their issues once approved. Then M0 and M1 serially (they chain through `auth.py`, `main.py`, `mcp_server.py`, `models.py`). After M1, M2a and M3 can run side by side, and inside M2a the `agent/` package can be built against the §5.7 protocol in parallel with server ingest.
