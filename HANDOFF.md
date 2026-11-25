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

## 2026-11-25 — Claude Code (Opus 5.5), worker — #156: the answer-cap memory tests measure the client in a fresh interpreter (PR #158)
- **Merged:** 2026-11-25, squash `606e1d4`, at head `34c9b40`; branch deleted. Entry committed by the orchestrator, with the review and state lines brought up to date.
- **Done:** [PR #158](https://github.com/DeusMaximus/TLG-IPAM/pull/158) (`156-answer-cap-memory-test`, head `34c9b40`) closes #156. Tests only; `client.py` and `RESPONSE_MAX` unchanged.
  - Cause: each check-in makes httpcore try `import sniffio` (not installed). Under pytest the attempt passes the assertion-rewriting hook, whose `PurePath` interns a fresh string that is then freed (mortal in 3.13). That uses up a slot of the interned-string dict, and when the slots run out the dict rebuilds its table (3,844,800 B in a full run). `tracemalloc` counts the new table in full. The free-slot count at the test moves with every collected module's text: 49 on `main`, 20 on `main` + PR #155 in a `python:3.13-slim` container.
  - Reproduced in that container with the test unchanged. A plugin (`edge156.py`, in the PR body) uses up interned slots before the test, and the merged tree then fails with CI's figures: 4,022,997–4,028,543 and 5,314,720–5,315,023 B. After the fix, all 10 shifts pass. The orchestrator re-ran one shift of each: 5,313,160 B held before, passing after.
  - `agent/tests/answer_memory.py` (new, not collected) runs the client in `python -I`: no pytest, no hook, no server. It warms up with one check-in and measures the second. `test_answers._held` runs it, with kill and reap on timeout or cancel (Greptile P2, fixed in `34c9b40`). The compressed-answer, plain-answer and raw-chunk tests use it with their limits unchanged.
  - New `test_the_measurement_sees_a_client_that_holds_too_much`: a reader with twice the cap (2.7 MB) and one that decodes first (81 MB) are each over their limit.
  - Tests: agent 1,388 → 1,390. Container: 1,388 + 2 skipped on the branch, 1,403 + 2 skipped merged with PR #155.
  - Review: Codex clean on `34c9b40`; CI green (9 checks). Greptile read `0b0d71e` (one P2, fixed); in place of its re-run, an outside read by Codex (GPT-6.1 Sol) at `34c9b40`: GO, no findings (`.dev/156/review-1.md` and the PR). It read the interned dict's free slots directly (unchanged over 20 check-ins in the child) and ran the plain test 50 times: largest 1.84 MB, 1.87 MB under CPU load, against 2.10 MB.
- **Decisions:** PR body Deliberate calls 1–4. A child process, because `tracemalloc` has no filtered peak and the rebuild is on the client's own stack. Limits kept, with the budget and measured figures stated: plain 1.47 MB on Linux, 1.57–1.87 MB on macOS, against 2.10 MB; measured, not derived as a bound. The raw-chunk test moved too, for one rule.
- **State:** merged. No schema, protocol, brief or `AGENTS.md` change.
- **Waiting on the owner:** nothing from this PR.
- **Untested:** the CI runner's exact interned-slot state; Python other than 3.13.
- **Next:** PR #155's `agent` job on a head that holds this fix.

## 2026-11-25 — Claude Code (Opus 5.5), worker — #92: say before an enrolment token is issued that the page is on plain HTTP (PR #157)
- **Merged:** 2026-11-25, squash `2abfcb4`, at head `884cda2`; branch deleted. Entry committed by the orchestrator, with the state lines brought up to date. The orchestrator ran lint, the 188 web tests and the build at the head before the merge.
- **Done:** [PR #157](https://github.com/DeusMaximus/TLG-IPAM/pull/157) (`92-plain-http-warning`, head `884cda2`) closes #92.
  - `web/src/components/EnrolmentSteps.jsx`: `PlainHttpBeforeIssue` (new, `agent-plain-http-before`) is shown before the request that issues a token. `PlainHttpWarning` (`agent-needs-https`) now has two wordings. With `issued`, beside the token: the token has already crossed in clear; run the agent soon; if its exchange is refused, revoke and re-enrol over HTTPS; the agent token crosses only over a plain-HTTP exchange, which the commands never make. Without it (update commands): no mention of a token.
  - `AddAgentModal` form and `ReEnrolModal` confirmation show the notice when `!serverUrlFor(location).secure`; both still issue. The rule stays `install.agentServerUrl`'s; `web/` parses no address. No command changed (`install.js` and its test untouched).
  - Docs: spec §5.6 has a new bullet, "A page served over plain HTTP"; brief §6.2 has a sentence on it; `AGENTS.md` guided-install bullet has one sentence.
  - Tests: web 179 → 188 (`Agents.test.jsx`). The before-issue tests fail with the notice removed; the before-issue and update tests fail with the old wording restored. Server 1,340 and agent 1,388 pass (no code changes there).
  - Review: Codex clean and Greptile 5/5 on `884cda2`; CI green (6 checks). No fix commits. It owed no briefed round.
- **Decisions:** PR body Deliberate calls 1–4. Re-enrol warns before issuing too. The update dialog's warning drops the token wording. The remedy is revoke then re-enrol, not delete (keeps the scan targets) and not disable (enabling would revive the other party's token). The notice warns and does not block.
- **State:** merged. No schema change; `PROTOCOL_VERSION` unchanged; no server or agent change.
- **Waiting on the owner:** nothing from this PR.
- **Untested:** a real browser on a real plain-HTTP origin (jsdom tests pass a `location` object).
- **Next:** the owner key crossing in clear from such a page stays #31's (deployment guidance).

## 2026-11-24 — Claude Code (Opus 5.5), worker — #138: the unit discards the CA trust settings the system manager supplies (PR #152)
- **Merged:** 2026-11-25, squash `0537950`, at head `cbc4c9c`; branch deleted. Entry committed by the orchestrator, with the review and merge lines brought up to date.
- **Done:** [PR #152](https://github.com/DeusMaximus/TLG-IPAM/pull/152) (`138-unit-manager-trust`, head `cbc4c9c`) closes #138.
  - `install.discarded_trust(ca_file)` (new): `SSL_CERT_DIR` always, `SSL_CERT_FILE` unless `--ca-file` sets it. `render_unit(env_path, interpreter, ca_file)` writes `UnsetEnvironment=<those>` after `EnvironmentFile=`. Fixed names only; the `--ca-file` path stays in agent.env.
  - Measured on systemd 257 (Debian 13, `--privileged --cgroupns=private --network none`): `UnsetEnvironment=` is applied after the manager's environment, `Environment=` and `EnvironmentFile=`, whatever the line order. `EnvironmentFile=` overrides the manager's value. The bare-name form is needed (`NAME=value` unsets only on a match). An empty `UnsetEnvironment=` in a drop-in clears the list. The same holds for `DefaultEnvironment=`.
  - Real runs: `main`'s wheel reproduces the finding (install 0, token spent, `CERTIFICATE_VERIFY_FAILED`); this head checks in for the manager setting file, directory, both or neither, with and without `--ca-file`; `uninstall` leaves `/etc`, `/var/lib`, `/usr/lib/systemd` and the user database as before.
  - Docs: spec §5.6 (install paragraph), `AGENTS.md` "Enrolment tokens" bullet, `README.md` private-CA paragraph.
  - Tests: agent 1,362 → 1,374 on the branch, 1,379 merged with `main` (`unit_environment`, a stand-in for systemd's ordering built from the unit text; 8 manager × `--ca-file` cases; 3 live-TLS `--ca-file` cases; `_as_the_service` now builds its environment from the unit). 9 fail with the line removed. No server change.
  - Review: Codex clean and Greptile 5/5 on `cbc4c9c`; CI green (10 checks). Briefed round by Codex (GPT-6.1 Sol) at `cbc4c9c`: GO, its round-3 finding on PR #130 closed, no new findings; it replayed the reproduction on a real systemd 257 and ran 30 probes of the ordering (review on the PR and in `.dev/138/review-1.md`).
- **Decisions:** Deliberate calls 1–6 in the PR body: the unit discards rather than the installer reading `systemctl show-environment`; `SSL_CERT_FILE` is not unset with `--ca-file`; `SSL_CERT_DIR` is unset even with it; bare names; an owner's drop-in is the owner's; only the two OpenSSL variables. No new import or listed name, so `test_invariants.py` is unchanged.
- **State:** merged. No schema change. A unit installed before this keeps its old text until `install` is run again.
- **Waiting on the owner:** nothing from this PR.
- **Untested:** an LXC, distributions other than Debian, systemd versions other than 257.
- **Next:** #136 and #140 each edit `install.py`, one at a time. The image `ipam130-sol-systemd:review` on this machine runs systemd for installer checks; `docker exec` in it starts with umask 0000, so set `umask 022` first or `trust.check` refuses the venv.

## 2026-11-24 — Claude Code (Opus 5.5), worker — #143: open enrolments by the server's clock, none for a disabled agent; release versions in normal form (PR #150)
- **Done:** [PR #150](https://github.com/DeusMaximus/TLG-IPAM/pull/150) (`143-agents-page-followups`, head `dabbfce`) closes #143. (Written by the orchestrator from the PR body: the worker's thread was still waiting on CI when the PR merged.)
  - Server, response only: `services/agents.expires_in_s` and `schemas.TokenOut.expires_in_s`, the whole seconds a live enrolment token has left by the server's clock (`agents.live`), rounded down, null for a token that is not live and for an agent token. Every owner answer for an agent carries it. No MCP tool reads it.
  - Page (`web/src/utils/agents.js`, `pages/Agents.jsx`): the countdown is the server's number less the time since the request was sent, measured with `performance.now()` (`monotonicNow`), never the wall clock, and it updates on its own timer, not only on the 30-second list refresh. A disabled agent's live token is shown as "enrolment blocked until it is enabled" and is not listed in Pending enrolments; Revoke stays enabled for it.
  - `web/src/utils/install.js`: `releaseVersion` refuses a leading zero in any numeric part, so `00.02.00` and `0.2.0rc01` render the `<version>` placeholder.
  - `web/src/utils/install.test.js` reads its accepted and refused version cases from `agent/tests/test_release.py`, strictly: a case in a shape it cannot read exactly as Python does (single quotes, a tuple across lines, an escape) fails the test instead of being skipped. The page and the release check judge one list.
  - Docs: brief §3.9 (the `expires_in_s` row) and §6.2, spec §5.6 and §5.8, the `AGENTS.md` guided-install bullet.
  - Merged: `a095c64`, squash, on the owner's go-ahead; the remote branch is deleted. CI green and Codex clean on `dabbfce`. Greptile read only the first head (one finding, fixed), so Sol (GPT-6.1 Sol) read it twice in its place: GO at `24494f4` with two P3s (the countdown used the wall clock and only moved on the refresh; the case reader did not decode Python escapes), both fixed in `89614c9`; GO at `d4fb98e` with one P3 (a fractional timer delay could fire before the clock moved), fixed in `dabbfce` with the reviewer's own one-line remedy. The orchestrator read that last diff against the remedy and ran 179 web tests, lint and build on the head, and 1,340 server and 1,371 agent tests on `d4fb98e`. No follow-ups.
- **Decisions:** PR body Deliberate calls 1–7. The main ones: the clock item is decided from a duration the server gives, not shown as uncertain; `expires_in_s` follows the token's rule alone, and the page applies the disabled rule from the agent's state; counting from the request errs early by the request's own time.
- **State:** merged. No schema change; `PROTOCOL_VERSION` unchanged; no command's shape or artefact name changed.
- **Waiting on the owner:** nothing from this entry.
- **Untested:** a real browser with its clock set off or stepped (the tests fake the clocks in jsdom).
- **Next:** #92 (the plain-HTTP warning before a token is issued) is the dialog's remaining open issue. #147 (release pipeline tests) edits `agent/tests/test_release.py`, which this PR also changed, and can start now.

## 2026-11-24 — Claude Code (Opus 5.5), worker — #149: the CNAME walk keyed by wire bytes; which PTR record wins (PR #151)
- **Done:** [PR #151](https://github.com/DeusMaximus/TLG-IPAM/pull/151) (`149-cname-walk-keys`, head `ad268e2`) closes #149. (Written by the orchestrator from the PR body: the worker's thread was still finishing when the PR merged.)
  - `probe.answered_name` keys its CNAME map and visited set by `Name.to_digestable()` (the name's wire form, labels lowercased, lengths included), not by `dns.name.Name`, whose hash in dnspython 2.9.2 ignores where labels split. A 512-link chain of colliding names went from about 724,000 `Name.fullcompare` calls to one. The walk visits the same names in the same order.
  - Spec §5.2, brief §4.6, `AGENTS.md` and `read_reply`'s docstring state the order the code already had: a PTR at the chain's end is the name (its first record when there are several), whatever else the reply holds; a PTR elsewhere is never the name, and with none at the chain's end makes the reply no usable answer.
  - Tests (`agent/tests/test_probe.py`, +5): a chain of colliding names costs work in step with its links, counted with `sys.setprofile` and never timed, asserting its own premise that the names hash alike; of several PTR records at the answered name the first is read (Greptile's P2); the counting helper puts back the profiler hook it found (the briefed round's P3).
  - Merged: `fd1e83e`, squash, on the owner's go-ahead; the remote branch is deleted. CI green and Codex clean on `ad268e2`. Briefed round (Codex, GPT-6.1 Sol): GO at `7cc32ec`, both findings from PR #146 closed, one P3 in the new test, fixed in `ad268e2`; the orchestrator read that diff against the remedy and ran 1,367 agent tests on the head, with no further round for a test-only change.
- **Decisions:** PR body Deliberate calls 1–4: the key is `to_digestable()`; the test counts work; the spec says the first of several PTR records is read; the spec says the parse is dnspython's own cost.
- **State:** merged. No new import, query or wire change (`PROTOCOL_VERSION` 2). No schema change.
- **Waiting on the owner:** whether to file an issue for the remaining exposure the review describes: dnspython's parser has the same collision cost before the walk runs (1.67 s for a 65 KB reply, measured by the reviewer with a substituted socket), and the agent does not refuse a reply larger than the 1,232 bytes it advertises. Only a resolver the owner configured can send one.
- **Untested:** a near-maximum reply over a real UDP path; the full suite on Python 3.11 and 3.13 by the reviewer (CI runs both).
- **Next:** #20 and #93 are in the same file and untouched.
