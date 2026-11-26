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

## 2026-11-26 — Claude Code (Sonnet 5.5), worker — #187: web tests pass with Node's own Web Storage on (PR #188)
- **Merged:** 2026-11-26, squash `625161d`, at head `11c78e4`, by the orchestrator (Fable 5.2) on the owner's go-ahead; branch deleted, #187 closed. The issue owed the automatic reviews only; Greptile is not running, so one outside read by GPT-6.1 Sol stood in for it: GO at `a310193` with one P3 (CI could not notice the fix being removed), fixed in `11c78e4` by the one CI step Sol named and read by the orchestrator against the remedy. Codex clean and CI 5/5 green on `11c78e4`. The orchestrator re-ran the web suite at `a310193` with Web Storage unset, on and off (198 passed each) and the new CI command at `11c78e4`. Review at `.dev/188/review-1-sol.md`.
- **Done:** [PR #188](https://github.com/DeusMaximus/TLG-IPAM/pull/188) closes #187, filed from Sol's note on PR #186.
  - Cause: vitest's jsdom environment installs jsdom's `Storage` class but keeps a `localStorage` or `sessionStorage` that Node already defines.
  - `web/src/test/setup.js` points both names at jsdom's storage under jsdom. `web/src/test/storage.test.js` adds 3 tests.
  - CI's `frontend` job has one new step that runs that file with `--experimental-webstorage`. No app or document changes.
- **Decisions:** PR Deliberate calls 1–6. The fix is in the setup file rather than a Node flag. The guard is for `install.test.js`, which runs in the node environment.
- **State:**
  - Web: 198 passed on Node 24.15.0 with Web Storage at its default, on and off, and in CI on 24.22.3. No schema change.
  - Node 25.9.0 has Web Storage on by default. Without the fix it had the same 106 failures; with it, 198 passed (the worker's run at `a310193`).
  - **Already on `main`, not fixed or filed:** `npx vitest run --no-isolate --maxWorkers=1` fails about 33 web tests, with and without this PR. It is not how CI or `npm run test` runs. Cause not diagnosed.
- **Waiting on the owner:** nothing from this issue.
- **Next:** M3. #26 (the OAuth provider) is [PR #189](https://github.com/DeusMaximus/TLG-IPAM/pull/189), head `b6bc871`, in briefed round 1 on GPT-6 Sol with Daybreak Blue (the orchestrator's call for this round: it sends crafted requests at an OAuth server), brief at `.dev/26/review-brief-1.md`. Before the round the orchestrator's read of `df7655a` found that the 1,024-row cap on a grant's tokens let the holder of a stolen refresh token erase the evidence reuse detection needs; the worker reproduced it and replaced the cap with a limit of 12 issues an hour per grant (`6d90603`), and `b6bc871` answers Codex's one P2. The PR ships the `ipam:write` checks on every write tool, so #28 needs its Scope and Acceptance edited before it starts (the PR body lists the lines taken); #27 builds on the seam the PR body names. Then #29 (after #27), #30 (after #27 and #28), #31 (after #27), and #32, the owner's acceptance run.

## 2026-11-26 — Claude Code (Opus 5.5), worker — #183: the installer keeps ALLOWED_CIDRS and MAX_PPS on an update (PR #186)
- **Merged:** 2026-11-26, squash `b37687f`, at head `ae75d4a`, by the orchestrator (Fable 5.2) on the owner's go-ahead; branch deleted, #183 closed. One briefed round (GPT-6.1 Sol, `ae75d4a`): GO, no findings, all five deliberate calls accepted. Sol ran `install` as root against systemd 257 in a container (`--network none`) with all eight subsets of the three options, and after each real restart the agent's process read the kept or replaced values; it found `agent.env` and its directory root-owned and not writable by the service account, and a full `uninstall` removing the file. Five mutations were caught. Codex clean and CI 10/10 green on `ae75d4a`; Greptile did not run. The orchestrator re-measured the agent suite at the head (2,108 passed, 14 skipped). Review at `.dev/186/review-1-sol.md`.
- **Done:** [PR #186](https://github.com/DeusMaximus/TLG-IPAM/pull/186) closes #183.
  - An update run without `--allowed-cidrs`/`--max-pps` keeps the `ALLOWED_CIDRS`/`MAX_PPS` that `agent.env` holds, as `ALLOWED_DNS_SERVERS` has been kept since #179.
  - `install.KEPT_SETTINGS` lists the three settings. `install.kept_settings` (replacing `kept_allowed_dns`) reads `agent.env` once through `env_assignments`' strict grammar and checks each kept value with the agent's own parser.
  - A value that fails, or a file outside the grammar, stops the update before anything changes, naming the setting or the line number, never a value.
  - A given option replaces only its own setting. A first install without the options writes none of their lines.
  - Spec §5.6 has a new bullet, "An update keeps the three agent settings". Both READMEs, the `UpdateModal.jsx` hint, the `install.js` comment, the cli help and the `AGENTS.md` "Enrolment tokens" bullet are updated.
- **Decisions:** PR Deliberate calls 1–5. Kept values are written in `install`'s spelling, not copied byte for byte, and a file `install` wrote comes back unchanged. Getting past a malformed `agent.env` now takes all three options (under #179, `--allowed-dns-servers` alone skipped the read). Given options are checked before the file is read. `--ca-file` is still given again on every update.
- **State:**
  - Agent suite 2,108 passed / 14 skipped on macOS; web 195 passed; backend untouched. No schema or wire change.
  - The first head, `22f7616`, failed CI on one new test whose bound depended on the length of pytest's temporary path; `ae75d4a` fixes the test only.
  - Sol's plain `npm run test` on Node 24.15.0 failed 106 web tests (an undefined experimental global `localStorage`) until run with `NODE_OPTIONS=--no-experimental-webstorage`; CI's Node passes. Not filed.
  - Housekeeping, 2026-11-26: the orchestrator removed 176 old worktrees of merged issues and finished review rounds, on the owner's word. Their local branches remain.
- **Waiting on the owner:** nothing. The owner has removed the #25 spike's public route and the four test connectors (2026-11-26), and the M3 issues #26 to #30 carry the edits from PR #182's scope notes (2026-11-25).
- **Next:** M3. #26 (the OAuth provider) is in flight on Fable 5.2, branch `26-oauth-provider`, brief at `.dev/26/worker-brief.md`; it owes a briefed round. #27 and #28 both wait only on #26: #26's brief asks its PR to say how no grant can exist before the write tools check a scope whichever of the two merges first, and to list any lines it takes from #28. Then #29 (after #27), #30 (after #27 and #28), #31 (after #27), and #32, the owner's acceptance run.

## 2026-11-26 — Claude Code (Opus 5.5), worker — #175: the Agents page shows an enrolment refused for the agent's protocol (PR #184)
- **Merged:** 2026-11-26, squash `a5081aa`, at head `b7ac1b1`, after #185; branch deleted, #175 closed. The orchestrator merged it onto the new `main` locally first and ran the suites (backend 1,487, web 195 passed). Three briefed rounds on GPT-6.1 Sol: round 1 (`cea476a`) NO-GO, three P2s (a delayed request on a revoked token erased the current refusal; equal or backward times hid a replacement's; deletion mid-body left an orphan) and two P3s, fixed in `54a83c9`; Codex P2s fixed in `c59ee62` and `cf0f170`; round 2 (`cf0f170`) NO-GO, one P2 (a stale list read deleted a newer refusal), fixed in `eff5e91` with a generation fence; round 3 (`eff5e91`) GO with one P3 (a replacement's generation untested alone), fixed in `b7ac1b1` (test only) and read by the orchestrator against the remedy. Codex clean, CI green on `b7ac1b1`. Reviews at `.dev/184/review-{1,2,3}-sol.md`.
- **Done:** [PR #184](https://github.com/DeusMaximus/TLG-IPAM/pull/184) closes #175. Refusals live in process memory, `agents.enrol_refusals`, keyed by enrolment token id (server code, time, store generation), at most `ENROL_REFUSALS_MAX` (1,024). `agents.current_enrolment` picks the token shown by identity, never by issue time. `enrol_refusal` gives that token's refusal, none while the agent is disabled, and only reads; only the agents list prunes, fenced by a generation read before its first database await. The page shows the refusal in the agent's row and its pending enrolment, with a note that the server keeps these notices only until it restarts.
- **Decisions:** PR Deliberate calls 1–11. Memory, not a database row: the refused exchange still takes no lock and writes nothing, and `test_an_agent_on_another_protocol_is_refused_before_the_claim` is unchanged. Not `last_error`. Cost: a restart forgets the notice until the agent's next attempt.
- **State:** no schema or wire change; `agent/` untouched. Untested: a real process restart, the page in a browser.
- **Waiting on the owner:** nothing.
- **Next:** possible follow-up (Sol, round 2): a token-bound refusal stored in the database under a short write lock, which would survive a restart, at the cost of #165's no-write property and an audit decision. Not filed.

## 2026-11-26 — Claude Code (Opus 5.5), worker — #20: the forward-DNS check (PR #185)
- **Merged:** 2026-11-26, squash `c3d52e8`, at head `8a4938a`; branch deleted, #20 closed. Codex P2 on `2fe9d8b` (names never answered were never rotated) fixed in `7758629` with `asked_at`. Briefed round (GPT-6 Sol, `daybreakBlue`, `7758629`) GO with two P3s: an address edit left a host's answer standing, and the agent took up to 4,096 names where the server sends 1,024; both fixed in `8a4938a` and read by the orchestrator against the remedies, no second round. The round served 19 kinds of hostile DNS reply on loopback (all matched the reader's contract; the over-ceiling and wrong-port replies never reached dnspython) and broke the domain check, the sent-names rule, the no-entry-for-no-answer rule and the A reader's owner check, each caught by a test. Codex clean and CI 10/10 green on `8a4938a`. Review at `.dev/20/review-1-sol.md`.
- **Done:** [PR #185](https://github.com/DeusMaximus/TLG-IPAM/pull/185) closes #20.
  - Server: `services/fdns.names_for_job` names hosts in scope whose FQDN is under the subnet's own domain and is a DNS hostname, at most `FDNS_JOB_MAX` (1,024) a job, never-asked first, then the oldest `asked_at`. The lease keeps `[host_id, fqdn, ip_int]` in `scan_targets.job_fdns`. `fdns.store` keeps an answer only for a host named there, under the name sent, still at that address and in scope; marks every sent name asked; prunes rows of deleted hosts. An address edit deletes the host's row (`hosts.update_host`), as does `drop_out_of_scope`. `fdns_mismatch` is dismiss-only and raised only while the target has `fdns`; its observed value is at most 12 addresses, then `and N more`.
  - Agent: `DnsResolver.a` shares the PTR lookups' query path (`_ask`, `QuerySocket`, the reply ceilings, one `MAX_PPS` slot per query, `guard.dns_servers`). A name with no usable answer gets no entry. `parse_job` takes DNS hostnames only, at most `FDNS_JOB_MAX` (1,024), held equal to the server's by `test_contract.py`.
  - UI: a dismiss-only "Forward DNS mismatch" group. Brief, spec and the `AGENTS.md` invariant 5 bullet updated.
- **Decisions:** PR Deliberate calls 1–15. No `PROTOCOL_VERSION` raise (protocol 2 already carried the fields). The bound is the subnet's own domain, suffix on a label boundary. Names are stored with the lease, not recomputed. 1,024 names from the 2 MiB body cap (a full /20 result set at 1,942,467 bytes; 2,598,851 at 2,048). A records only. The longest error now gives up parts in a stated order (worst stored 465; the spec's claim that it is always stored whole is withdrawn).
- **State:** backend 1,455, agent 2,048 passed / 14 skipped at `8a4938a`; web 191. Schema additive: `fdns_results`, `scan_targets.job_fdns`. Untested: real DNS traffic; an agent built before this PR against this server (reasoned, call 1).
- **Waiting on the owner:** nothing.
- **Next:** none from this issue.

## 2026-11-25 — Claude Code (Opus 5.5), worker — #173: the server image runs as uid 10001, never root (PR #181)
- **Merged:** 2026-11-25, squash `3182482`, at head `c95c517`; branch deleted, #173 closed. Three briefed rounds on GPT-6.1 Sol (`.dev/181/review-{1,2,3}-sol.md`). Round 1 (`ff25814`) NO-GO: P2 `DB_PATH=:memory:` refused, P3 an unquoted path in the printed command; with the orchestrator's note that an upgraded `/data` kept mode 755. Fixed in `05ea2e4`. Round 2 (`05ea2e4`) NO-GO: P1 exempting every `file:` target let a persistent database open read-only on a root volume, P3 an escaped control-character path in the commands. Fixed in `c95c517`. Round 3 (`c95c517`) GO, no findings. Codex clean on `c95c517`, CI 10/10 green; Greptile did not review (free-plan credits spent). Backend 1,420 passed (Sol's re-measure).
- **Done:** [PR #181](https://github.com/DeusMaximus/TLG-IPAM/pull/181) closes #173.
  - Root `Dockerfile`: `ipam` uid/gid 10001 (literals); `/data` 10001's, 0700, which a new volume takes; `USER 10001:10001` after the last RUN; the code stays root's.
  - `api/app/database.py`: the storage check runs at import, before the engine. An unwritable directory, or an existing db/-wal/-shm that cannot be read and written, exits 1 with the one-time `chown -R` and `chmod 700` commands (`volume_commands`, `shlex.quote`d; none for a directory holding a control character). Only `:memory:` and `""` are not checked (`on_filesystem`).
  - `api/app/config.py`: `load_settings` refuses a `DB_PATH` that is a `file:` name in any case, or that SQLAlchemy's URL would read otherwise than as the path (a query, a `%` escape), with the fixed message "DB_PATH must be a filesystem path or :memory:.".
  - Restore script: `check_writable` before the stop; the snapshot goes in on stdin (`run -T`, `umask 077`), so the restored file is 10001's, mode 0600.
  - README "The container's user" / "Upgrading from an image that ran as root"; brief §2.2 "Not root"; `AGENTS.md` invariant bullet; CI `server-image` job and `.github/scripts/check-server-image.sh`.
- **Decisions:** PR Deliberate calls 1–10: an owner-run `chown` (and `chmod 700`), never a root entrypoint; uid 10001; the check at import; restore through stdin; `file:` names, queries and `%` escapes refused at settings load rather than resolved.
- **State:** the orchestrator repeated the upgrade, restore and undo on the owner's test deployment (unprivileged LXC) at `ff25814` (PR comment), rebuilt it at `c95c517`, and after the merge moved it to `main` `3182482` with `/data` made 0700: runs as 10001, health ok, data intact. Untested: Podman, rootless runtimes, userns-remap.
- **Waiting on the owner:** nothing. **An existing deployment's first start on this image refuses until the one-time command in the README is run.**
- **Next:** #175 is in flight; #183 (keep `ALLOWED_CIDRS`/`MAX_PPS` on update) is filed, not started; #26 opens M3 on Fable after the reset.
