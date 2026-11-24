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

## 2026-11-24 — Claude Code (Opus 5.5), worker — #100: name lookups reported apart from liveness (PR #142)
- **Done:**
  - [PR #142](https://github.com/DeusMaximus/TLG-IPAM/pull/142) (`100-name-lookup-state`, head `9b196e2`) closes #100 under option 4.
  - **Protocol 2:** `PROTOCOL_VERSION` 1 → 2. Results carry `protocol` (absent means 1), `liveness_complete`, and `rdns_no_name` on each record. Ingest marks the dead by `liveness_complete` and clears a stored name only when the record carries `rdns_no_name`. Protocol-1 result sets keep protocol 1's rule.
  - **Briefed round 1** (Codex GPT-6.1 Sol, NO-GO at `dfae349`) fixed in `217e525`:
    - P2: `probe.says_no_name` reads the authority section first, so a CNAME followed by a delegation is no answer and the next server is asked.
    - P3: every ARP interruption path asserts `liveness_complete` false.
  - `main` at `7beb687` (#23) merged in `d41b7a6`. `9b196e2` makes `test_guided_install.py` send `PROTOCOL_VERSION + 1` as the mismatch.
  - Merged: `5ecf088`, squash, on the owner's go-ahead; the remote branch is deleted. CI green and Codex clean on `9b196e2`; Greptile read only the first head (5/5), and the briefed rounds were the outside read after it. Briefed round 2 (Codex, GPT-6.1 Sol): GO at `9b196e2`, both round-1 findings closed, no new finding. The orchestrator ran 1,252 agent and 1,129 server tests on that head. Follow-up filed on the owner's say: #144, the resolver reads a PTR or a negative SOA without checking it belongs to the question (older than #100, described in round 2), with an operating note for a subnet whose only DNS server does not recurse.
- **Decisions:** Deliberate calls 1–10 in the PR body. The reviewer kept 1–9 and revised 4. Call 10 is new: a CNAME with no PTR and an empty authority is no answer.
- **State:**
  - merged. Tests on the merged tree: agent 1,252, server 1,129, web 154.
  - No schema change, no new import or listed name.
- **Waiting on the owner:** nothing from this entry.
- **Untested:** any real network or resolver; the built image; a real protocol-1 agent.
- **Next:**
  - #24 publishes the first image at protocol 2.
  - #20 and #93 untouched; #144 is in the same agent file.
  - `web/src/pages/Agents.test.jsx` keeps a static fixture message that mentions protocols 2 and 1, which is harmless.

## 2026-11-24 — Claude Code (Opus 5.5), worker — #23: guided install, pending enrolments, version flags, scan offers (PR #141)
- **Done:**
  - [PR #141](https://github.com/DeusMaximus/TLG-IPAM/pull/141) (`23-ui-guided-install`, head `1718163`) closes #23.
  - The Add agent dialog is now the guided install of spec §5.6: Container and systemd tabs, each with prerequisites and commands pinned to the server's `/health` version. The systemd install reads the enrolment token from standard input (`--enrol-token -`).
  - Agents page: Re-enrol (with the commands that remove an old token from the host first), a pending-enrolments list, an "update available" flag, a protocol-mismatch line, the update commands, and "Scan *subnet* with this agent?" offers.
  - Server, read-only:
    - `agents.agent_out` adds `update_available` (PEP 440, `packaging`) and `protocol_mismatch` (read from the prefix of the stored `last_error`).
    - `scans.suggestions` serves `GET /api/v1/scan-targets/suggestions` in two statements; REST only.
  - `packaging` is now a direct dependency. All four locks were regenerated; only their `# via` comments changed.
  - Spec §5.6 gains the update and re-enrol command table and a paragraph on where the artefact names come from. The brief, README and `AGENTS.md` (one new bullet) are updated.
  - Review fixes:
    - `78ef42e` (Codex P2, Greptile P1): a container from the first release's install (`IPAM_AGENT_TOKEN`, no volume) is told to re-enrol rather than update.
    - `1718163` (Greptile P2): the modal overlay scrolls.
  - Merged: `a36c153`, squash, on the owner's go-ahead; the remote branch is deleted. CI green and Codex clean on `1718163`. Greptile read only the first head, so one outside read by Sol (GPT-6.1 Sol) stood in for its re-run: GO at `1718163` with three P3s, none of the 13 Deliberate calls overruled. The orchestrator ran 1,112 server and 154 web tests, lint and build on that head. The three P3s are filed as #143 (a disabled agent's enrolment shown as open; the pending list following the browser's clock; release versions not in normal form accepted).
- **Decisions:** Deliberate calls 1–13 in the PR body. The main ones:
  - The flags are computed on the server.
  - The mismatch is read from `last_error`, so `agent_protocol.py` is untouched.
  - Accepting an offer is `POST /scan-targets` with the server defaults, then one `POST /subnets/{id}/scan`.
  - Only subnets with no scan target are offered.
  - "Not now" lasts only while the page is open.
- **State:**
  - merged. Tests: server 1,112, agent 1,193, web 154.
  - No schema change; `PROTOCOL_VERSION` unchanged.
- **Waiting on the owner:** nothing from this entry.
- **Untested:** the commands against real artefacts (#24); the dialogs in a real browser.
- **Next:**
  - #24 must publish `ghcr.io/deusmaximus/tlg-ipam-agent:<version>` and `releases/download/v<version>/tlg_ipam_agent-<version>-py3-none-any.whl`, and give the server and agent one version. Today they differ: the server's `settings.version` is `0.1.0` and the agent's `__version__` is `0.2.0.dev0`.
  - #92 (the plain-HTTP warning before a token is issued) and #143 are the dialog's open follow-ups.

## 2026-11-24 — Claude Code (Opus 5.5), worker — #22: the agent's enrolment exchange and the systemd installer (PR #130)
- **Done:**
  - [PR #130](https://github.com/DeusMaximus/TLG-IPAM/pull/130) (`22-agent-enrol-install`, head `b7fb4ae`) closes #22.
  - The branch includes `main` at `211b53f` (merges `181ab1b` and `b7fb4ae`).
  - Round 1 (NO-GO at `fb39dd7`) was fixed in `ebe385e`, and Codex's P2 on it in `4dc5d6b`.
  - Round 2 (NO-GO at `4dc5d6b`: one P2, two P3s) was fixed in `eed4f91`:
    - `install.service_trust` is the one source of the unit's trust and of the exchange's; inherited `SSL_CERT_FILE`/`SSL_CERT_DIR` are dropped, and refused when the unit would not get them;
    - `uninstall` keeps `created-user` until the account and group are gone, so it can be retried;
    - each token the install learns is added to `Redact` (`secret=`);
    - the argument refusal gives re-enrol guidance;
    - §5.6 and `AGENTS.md` say what holds of `subprocess` (a running agent never imports `install` or `trust`).
  - Merged: `8081d8c`, squash, on the owner's go-ahead; the remote branch is deleted. CI green and Codex clean on `b7fb4ae`; Greptile read only the first head, and the briefed rounds were the outside read after it. The briefed review (Codex, GPT-6.1 Sol) took three rounds: NO-GO at `fb39dd7`, NO-GO at `4dc5d6b`, GO at `b7fb4ae` with one P3. After the second NO-GO the owner kept the Opus 5.5 worker on the fix. The orchestrator ran 1,193 agent and 1,083 server tests on `b7fb4ae`. Follow-ups filed, on the owner's say:
    - #136: the CA preflight judges mode bits, not ACLs or supplementary groups (round 2, finding 2);
    - #138: the unit does not discard CA trust settings the system manager supplies (round 3, finding 1);
    - #140: argparse's own errors repeat an argument, and the agent token's exact form is stated only on the agent's side.
- **State:**
  - merged. Tests on the merged tree: agent 1,193 on 3.13 and 3.11.18, server 1,083.
  - No change under `api/`, no schema change, `PROTOCOL_VERSION` unchanged.
  - The container runs (Debian 13 with systemd, `--network none`) are in the PR body:
    - run A at `ebe385e`;
    - run B at `eed4f91`;
    - the round-1 reproductions at `ebe385e`;
    - the round-2 reproductions at `eed4f91`, HTTPS with a private CA.
- **Decisions:** Deliberate calls 1–17 in the PR body. Since round 1, calls 13 and 14 changed:
  - call 14: refusal over carrying inherited trust into the unit, with #136 as the known limit;
  - call 13: the record goes last, and the crash between `useradd` and writing the record is manual recovery.
- **Waiting on the owner:** nothing from this entry.
- **Untested:** unprivileged LXC, non-Debian distributions, OpenSSL variables other than `SSL_CERT_FILE` and `SSL_CERT_DIR`, ACLs and supplementary groups (#136), two containers on one volume.
- **Next:**
  - #23 renders `… install --url … --enrol-token -` and the container command with the volume and `IPAM_ENROL_TOKEN`;
  - #24 publishes the image and the wheel;
  - #136, #138 and #140 are the installer's open follow-ups.

## 2026-11-24 — Claude Code (Opus 5.5), worker — #133: list_scan_findings checks a subnet_id by its id alone (PR #137)
- **Done:** [PR #137](https://github.com/DeusMaximus/TLG-IPAM/pull/137) (`133-findings-subnet-lookup`, head `ee19d28`) closes #133.
  - `findings.list_named` checks `subnet_id` with `select(Subnet.id)` and `errors.not_found`, not `subnets.get_subnet`. The refusal and the statement count are unchanged.
  - `test_mcp.py`: the plan test and the work test run with and without `subnet_id`. New `returned_bytes` and `test_no_read_of_a_call_returns_text_the_listing_does_not_use`: every statement of a call returns the same bytes with 10 or 1,000,000 bytes of subnet notes and section description, and 10 or 4,096 of host notes. The start-up test names `ix_reserved_ranges_subnet_excluded`.
  - With the wide read put back, the new test fails with `'bytes returned': (100, 1000090)`. The command and output are in the PR body.
  - `AGENTS.md` (only the parenthesis naming the cost tests, in "MCP tools are hand-curated"), brief §5.2 row, spec §5.8 and the `CALL_STATEMENTS` comment: what is counted is SQLite instructions (the rows read or passed over), not returned bytes or Python's processing.
  - Merged: `cc0cf6e`, squash, on the owner's go-ahead; the remote branch is deleted. CI green, Codex clean and Greptile clean (5/5) on `ee19d28`, the only head. The orchestrator ran 1,084 server tests on that head. No follow-ups.
- **Decisions:** PR Deliberate calls 1–3: a byte check rather than a column list; the plan test matches the lookup's SQL text; spec §5.8 gets one clarifying sentence. REST `list_findings` still uses `get_subnet` (owner-only, outside the issue).
- **State:** merged. 1,084 server and 924 agent tests pass. No schema change. Nothing in #132's or #22's areas touched.
- **Waiting on the owner:** nothing from this entry.
- **Next:** #135 and #130 both touch the "MCP tools are hand-curated" bullet's neighbours only; #137 changed one parenthesis in it, so a merge with either stays mechanical.

## 2026-11-24 — Claude Code (Opus 5.5), worker — #91: a host's notes at most 4,096 characters; a row over a limit is that row's error (PR #134)
- **Done:** [PR #134](https://github.com/DeusMaximus/TLG-IPAM/pull/134) (`91-notes-length-limit`, head `47e6544`) closes #91, per the owner's decision of 2026-11-24.
  - `schemas.HOST_NOTES_MAX` (4,096), checked by `services/hosts._check_notes` on create, update, next-free and each bulk row: `422` "notes: a host's notes may be at most 4096 characters, not N", never repeating the value; in a bulk import, that row's `error`.
  - Tests for UI, REST and MCP bulk paths for both `notes` and `domain` (the domain's UI and MCP cases were missing). 14 new server tests in `api/tests/test_length_limits.py`.
  - `bulk_create_hosts` docstring and brief §5.2 row: a call is at most 2 MiB of encoded JSON, refused whole otherwise; split a batch yourself; 400 records of 4,096-character plain-ASCII notes (no quotes, backslashes, line breaks) fit.
  - Brief §3.4, §3.7, §4.2, §4.3 (re-measured: 1,000 rows without notes are 1,110,573 bytes; they fit with at most 243 four-byte characters of notes each, 1,335 ASCII with an ASCII owner; at most 119 rows at every limit, 401 with ASCII notes), §5.1, §6.2; `AGENTS.md` "Bulk endpoints" bullet.
  - Optional dialog item built: `utils/csv.importSize` measures the request; over the cap the dialog names the most rows a file can hold (every run of that many consecutive rows fits, at most 1,000) and does not send. 7 new web tests.
  - Merged: `216bec1`, squash, on the owner's go-ahead; the remote branch is deleted. CI green, Codex clean and Greptile clean on `47e6544`. The orchestrator ran 1,083 server and 132 web tests on that head. #91 owed no briefed round; the owner asked for one read by Sol (GPT-6.1 Sol) in place of a Greptile re-run, and it was a GO at `47e6544` with two P3 documentation follow-ups, not yet filed (the review is on the PR thread):
    - The docstring's and §5.2's 400-record example holds only when the other text fields are ASCII too. With the tests' 120-character non-BMP owner the same call is 2,477,553 bytes and is refused `413`.
    - Brief §4.3 calls its measured rows "every bounded field at its maximum", but the fixture's `device_type` is 5 characters of a possible 40, so the figures are that fixture's. The 1,110,573-byte figure is not asserted by a test.
- **Decisions:** PR body Deliberate calls 1–6. An update resending notes stored over the limit, unchanged, is let through (the `dns_servers` precedent, not #86's domain rule), because the UI resends every field. The check is in the service, not a schema `max_length`, so OpenAPI does not advertise it. The limit counts characters.
- **State:** merged. Locally: 1,083 server tests, 132 web tests, lint and build pass. No schema change. Nothing in #132's or #22's areas, or in `services/findings.py`, was touched.
- **Waiting on the owner:** whether to file the two P3 follow-ups.
- **Next:** #133 can start (it owns `services/findings.py` and the findings tests). #135 and #130 merge `main` before their next review round; #134 and #135 both edit brief §4.3, in different paragraphs.
