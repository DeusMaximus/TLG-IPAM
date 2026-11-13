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

## 2026-11-13 — Claude Code (Fable 5.2) — #3 merged (PR #34); follow-ups #35 and #36 filed

- **Done:**
  - [PR #34](https://github.com/DeusMaximus/TLG-IPAM/pull/34) merged as `9620504`, closing #3. `main` now installs from hashed locks and runs FastMCP 4.1.8 with MCP SDK 2.3.0.
  - Filed #35 (set pytest-asyncio's fixture loop scope; the suite prints a deprecation warning at configure time until then) and #36 (`stretch`: CI check that the locks match their `.in` files). Both are in M0.
- **Decisions:**
  - #3 was done by a standalone session in its own worktree, started from the issue, `AGENTS.md`, and this log. It worked without further briefing, so that is the pattern for the next issues.
  - A session in a worktree cannot commit to `main`. It ends its final message with its hand-off entry, and the coordinating session commits it. This is not in `AGENTS.md` yet.
- **State:**
  - `main` at `9620504`: backend suite 80 passed in a venv rebuilt from the lock on Python 3.13. The only warning is the pytest-asyncio one (#35).
  - Dependency changes go through `api/requirements.in` / `requirements-dev.in` and a regenerated lock (`AGENTS.md` → "Dependency lock").
  - No application code has changed yet beyond the one-line test fix in #3.
- **Waiting on the owner:** nothing blocks the next issue. Later: #5 (redeploy and a deliberate restore), #25 (client-compatibility spike), the real-VLAN check in #14, registry permissions for #24, #32 (acceptance run).
- **Next:** #4 (scheduled backup and restore procedure) and #35 are independent and can run side by side. Then M1 in order: #6, #7, #8; #6 and #8 owe a briefed review round.

## 2026-11-13 — Claude Code (Sonnet 5.5) — #3: dependencies locked, FastMCP 4 pinned (PR #34)

- **Done:** [PR #34](https://github.com/DeusMaximus/TLG-IPAM/pull/34) closes #3, head `85e96f8`. CI green. Codex: no findings. Greptile: 5/5, no findings. Merge is the owner's.
  - `api/requirements.in` / `requirements-dev.in` hold the direct dependencies (`fastmcp>=4.1,<5`). `requirements.txt` / `requirements-dev.txt` are now generated locks: `uv pip compile --universal --python-version 3.12 --generate-hashes`, 71 and 78 pins. They resolve FastMCP 4.1.8, MCP SDK 2.3.0, FastAPI 0.162.5. The dev lock is constrained to the runtime lock.
  - The Dockerfile and CI install with `pip install --require-hashes`. `api/tests/test_mcp.py` reads `input_schema`, so the FastMCP deprecation warning is gone.
  - `AGENTS.md` has the new setup command and a "Dependency lock" section with the regenerate commands. README and the brief are updated.
- **Decisions:**
  - uv, not pip-compile, because `--universal` gives one lock for the 3.12 image, 3.13 CI, and any dev machine. Only `pip` is needed to install it; `uv` is needed only to regenerate.
  - The lock file names are unchanged and `--no-header` is used, so the locks hold no local paths. Recompiling reproduces them byte for byte.
- **State:**
  - Backend suite: 80 passed on Python 3.12 and 3.13 from fresh venvs. Frontend lint, tests and build pass. No schema change.
  - The image built on `python:3.12-slim` matches the lock. The container started, `/health` returned 200, and `/mcp` returned 401 without a key.
  - pytest-asyncio 1.5.0 prints a `PytestDeprecationWarning` at configure time about `asyncio_default_fixture_loop_scope` (unset). It is not a FastMCP warning, and `pytest.ini` is untouched. It is worth its own small issue.
  - There is no CI check that the locks match the `.in` files. Install-time `--require-hashes` catches a hand-edited lock.
  - Not tried: installs on Python 3.14 / 3.15.
- **Waiting on the owner:** merge PR #34. Reinstall the long-lived venv with the new setup command; it is still on FastMCP 3.4.2.
- **Next:** #4, then M1 (#6, #7, #8). From now on, dependency changes go through the `.in` files and a regenerated lock, never a hand-edited lock.

## 2026-11-13 — Claude Code (Fable 5.2) — #2 merged (PR #33); parallel test sessions are safe

- **Done:** [PR #33](https://github.com/DeusMaximus/TLG-IPAM/pull/33) merged as `98b844e`, closing #2. Each pytest session now has its own database directory, removed at session end. Codex and Greptile reviewed it with no findings.
- **Decisions:** none.
- **State:**
  - `main` at `98b844e`: backend suite 80 passed. Several pytest sessions can run on one machine at once, so agents can work in separate worktrees.
  - Concurrent sessions can make pytest print `PytestWarning: (rm_rf) error removing …/garbage-…` after the summary. That is pytest's own `tmp_path` housekeeping; the run still passes.
  - `api/requirements.txt` is still unpinned (#3). No application code has changed yet.
  - Milestones M0–M3 and issues #3–#32 are open; the entry below has the labels and the review tiers.
- **Waiting on the owner:** nothing right now. Later: #5 (redeploy and a deliberate restore), #25 (client-compatibility spike), the real-VLAN check in #14, registry permissions for #24, #32 (acceptance run).
- **Next:** #3 (lock dependencies, pin FastMCP 4), then #4, then M1 in order: #6, #7, #8. After M1, M2a (#9 onwards) and M3 (#25 onwards) can run side by side.

## 2026-11-13 — Claude Code (Fable 5.2) — PR #1 merged; milestones and 31 issues filed; #2 in review as PR #33

- **Done:**
  - [PR #1](https://github.com/DeusMaximus/TLG-IPAM/pull/1) merged as `4953b5c`: the spec, this log, the Workflow section, `.agents/`.
  - Five milestones (M0, M1, M2a, M2b, M3) and issues #2–#32 filed. Each issue names its spec sections, what it depends on, its acceptance list, and which review it owes. Labels: `owner` (needs the owner), `briefed-review` (owes a briefed round), `stretch`.
  - [PR #33](https://github.com/DeusMaximus/TLG-IPAM/pull/33) open for #2: each pytest session gets its own database directory.
- **Decisions:**
  - Review is tiered (in `AGENTS.md`): every PR answers the automatic Codex and Greptile findings; twelve issues carry `briefed-review` and owe a briefed round as well (#6, #8, #10, #11, #13, #14, #21, #22, #26, #27, #28, #29).
  - The §7.2 invariants are each assigned to the issue that introduces them, so `AGENTS.md` gains them as the code lands and not before.
- **State:**
  - `main` at `4953b5c`: backend suite 80 passed. No application code has changed yet.
  - Until PR #33 merges, run one pytest session at a time on a machine: on `main`, two at once fail each other (measured: 2 and 3 failures). With PR #33, three at once passed in 33 of 33 runs.
  - `api/requirements.txt` is still unpinned (#3).
- **Waiting on the owner:** merge PR #33. Later: #5 (redeploy and a deliberate restore), #25 (client-compatibility spike: real web-client accounts and a public URL), the real-VLAN check in #14, registry permissions for #24, #32 (acceptance run).
- **Next:** #3 (lock dependencies, pin FastMCP 4), then #4, then M1 in order: #6, #7, #8. After M1, M2a (#9 onwards) and M3 (#25 onwards) can run side by side; inside M2a the agent package (#13) can be built beside ingest (#11) once check-in (#10) is merged.

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
  - Process: one issue, one branch, one PR; merge needs green CI and every automatic review finding (Codex, Greptile) answered; PRs touching authentication, tokens, OAuth, agent ingest, or packet-sending code also need a briefed round with a GO; the owner merges.
  - PR #1's automatic reviews found three gaps in the spec, fixed in the same PR: forward-DNS results had no storage, the systemd agent could not read its own token file, and a job with no liveness method could mark every address dead.
- **State:**
  - No application code has changed. Backend suite: 80 passed on the local venv (FastMCP 3.4.2), and 80 passed on a fresh install (FastMCP 4.1.8, MCP SDK 2.3.0) with one deprecation warning in `tests/test_mcp.py`.
  - `api/requirements.txt` is still unpinned, so a fresh `docker build` installs FastMCP 4.x today. Pinning is M0.
  - `api/tests/conftest.py` points every pytest session at one fixed temp database. Two sessions on one machine at the same time corrupt each other. Run one at a time until the M0 issue that gives each session its own database lands; it is the first thing to do before agents work in parallel.
  - Milestones and issues are not filed yet.
- **Waiting on the owner:** approval of the milestone and issue list; later, the M0 redeploy and deliberate restore test, the M3 client-compatibility spike (needs real web-client accounts and a public URL), and installing agents on the VLANs.
- **Next:** file milestones M0, M1, M2a, M2b, M3 and their issues once approved. Then M0 and M1 serially (they chain through `auth.py`, `main.py`, `mcp_server.py`, `models.py`). After M1, M2a and M3 can run side by side, and inside M2a the `agent/` package can be built against the §5.7 protocol in parallel with server ingest.
