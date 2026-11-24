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

## 2026-11-24 — Claude Code (Opus 5.5), worker — #132: bound request heads, idle connections, pipelining and stalled readers below the app (PR #135)
- **Done:** [PR #135](https://github.com/DeusMaximus/TLG-IPAM/pull/135) (`132-header-idle-bounds`, head `e9855bb`) closes #132. (The worker's own last entry covered round 4 only; the orchestrator has put the whole PR here.)
  - The image's `CMD` is `python -m app.serve` (`api/app/serve.py`): uvicorn's own command line, so `UVICORN_*` settings still apply, with six pinned over it (`workers=1`, no reload, `http=header_limit.HeaderLimitProtocol`, no HTTP/2, `ws="none"`, 5 s keep-alive).
  - `api/app/header_limit.py`, a subclass of uvicorn's httptools protocol:
    - a head (request line and headers) of at most 64 KiB as received, `431 HEADERS_TOO_LARGE`, counted exactly by cutting reads where a head and a declared body end; a head after a chunked body in the same 4 KiB piece is the one stated exception;
    - 10 s for a connection to deliver a head, closed without an answer;
    - one pipelined request held ahead, with reading paused meanwhile;
    - 60 s for a client that stops reading (a paused send buffer, or a closed connection that does not flush), then abort;
    - a refusal by the parser follows one rule: uvicorn's `400` only when no earlier request is still unanswered; otherwise the refused request is dropped without running and the earlier answers go out in order, the connection closing after the last. Plain uvicorn, and `main` before this, wrote the `400` in front of them, which could hide a committed write;
    - a stated bound on parser calls a sender can force.
  - Docs: brief §2.2, §4.3 (the "below the app" paragraph and "Left to the reverse proxy or tunnel"), the `/mcp` refusal table and §9 row; spec §5.7 and §8 (the proxy checklist for nginx, Caddy and Cloudflare Tunnel, every line from documentation); `AGENTS.md` bullets "One uvicorn worker" and "One request-body cap"; README. Four decision comments on #132 record what the image bounds and what stays the proxy's.
  - Merged: `83e392e`, squash, on the owner's go-ahead; the remote branch is deleted. CI green and Codex clean on `e9855bb`; Greptile last read `e2c2ff3`, and the briefed rounds were the outside read after it. The briefed review took six sessions:
    - GPT-6.1 Sol at `e2c2ff3` was stopped by its provider's safety filter before a verdict (hand-over in `.dev/132/manual-handover.md`); its five leads went to the worker.
    - GPT-6 Sol with Daybreak Blue, started from T3 Code, then gave NO-GO at `9ec525c` (two P2s in §8's proxy advice), NO-GO at `48148de` (P2: a committed write lost its answer behind a parser refusal), NO-GO at `4df1508` (P2: the same by another route), and GO at `e9855bb` with no finding. The owner kept the Opus 5.5 worker after the second and third NO-GO.
    - Between the second and third, the orchestrator measured that a fix it had suggested (cutting chunked bodies at every CRLF CRLF) let a sender hold the worker 1.3 s per 1.9 MB body, and sent it back before the round; `.dev/132/crlf_cost.py` and `lost_answer.py` are the scripts.
    - The orchestrator ran 1,321 server tests on `e9855bb`. No follow-ups filed.
- **Decisions:** Deliberate calls in the PR body. A floor in the image plus the proxy in front; httptools kept, not h11; the limit is on bytes as received; 64 KiB although Cloudflare passes 128 KB; no concurrency limit in the image; the subclass relies on uvicorn 0.54 internals (`cycle`, `pipeline`, `flow`, the parser callbacks), held by the tests on both event loops.
- **State:** merged. Server 1,321 on its head (192 of them this PR's). No schema change.
- **Waiting on the owner:** the next server image build is the first real start of the new `CMD`: nobody could build the image here.
- **Untested:** the Docker image; every proxy setting in spec §8 (no proxy ran); a multi-day MCP stream.
- **Next:** #31 writes the deployment document from spec §8. #132 no longer blocks exposing `/mcp` (#26, which still waits on #25).

## 2026-11-24 — Claude Code (Sonnet 5.5), worker — #139: qualify the bulk_create_hosts size example and the brief's import figures (PR #148)
- **Done:**
  - [PR #148](https://github.com/DeusMaximus/TLG-IPAM/pull/148) (`139-import-size-wording`, head `cc6267c`) closes #139. Documentation and tests only: no limit, route or tool result changed. Three files: `api/app/mcp_server.py` (docstring), `TLG-IPAM-Project-Brief.md` (§5.2 row, §4.3 import-size paragraph only), `api/tests/test_length_limits.py`.
  - The `bulk_create_hosts` docstring and §5.2: the 400-record example holds only when every field is plain ASCII with no character JSON escapes (quote, backslash, a control character such as a line break or a tab) in any field. Non-BMP owner: 2,493,771 bytes as `json.dumps` writes it, `413` at the real `/mcp` route. A hostname and an owner of quotes, backslashes or tabs: 2,114,971.
  - §4.3: the measured rows (`full_rows`) are named as measured examples, not a ceiling. They are at every character limit and ASCII (15-character address, 40-character device type). Figures re-measured: 1,000 rows without notes 1,151,011; at most 233 four-byte notes characters (1,295 ASCII with an ASCII owner); notes at the limit 17,546,011 (5,258,011 ASCII); at most 119 rows (398 ASCII notes, 428 with an ASCII owner too); 5-character device type 1,116,011. §4.3 also says a hostname and a device type accept non-ASCII (1,000 rows 2,973,011), that a MAC's punctuation, a domain's leading white space and trailing dots and a `last_seen`'s extra digits are accepted and dropped, and that a padded IPv4 address is refused.
  - Tests: the example's largest call is posted to the real `/mcp` route and creates all 400 hosts; the non-ASCII-owner variant gets `413`; the escaped-ASCII case runs for quote, backslash and tab; the Unicode-row and no-largest-row cases; every §4.3 figure is asserted. The old in-process example test (`fastmcp.Client(mcp)`) skipped the body cap and is replaced.
  - Review: Codex clean at `62977ac`, `d29492e` and `cc6267c`. Greptile P2 at `62977ac` (escapes outside notes) fixed in `d29492e`. An outside read (Codex, GPT-6.1 Sol) at `d29492e` was GO with two P3s (tabs; rows are examples, not a ceiling), both fixed in `cc6267c`, answered in one numbered PR comment. CI green on `cc6267c`.
  - Merged: `1a48707`, squash, on the owner's go-ahead; the remote branch is deleted. Sol's GO was at `d29492e`; the orchestrator read `cc6267c`'s diff against its two remedies and ran 1,136 server tests on that head, and no further round was run for a wording-only change. No follow-ups.
- **Decisions:** PR body Deliberate calls 1–5. The main ones: rebuilt the rows at every character limit instead of describing the old fixture, so all §4.3 figures changed; no row is "the largest" (limits count characters, padding is accepted on MAC, domain and `last_seen`, an IPv4 address is refused padded); the example is judged as `json.dumps` writes it; `test_body_limit.py` and `body_limit.py` untouched (its "about 1.15 MB" comment is accurate for the no-notes rows).
- **State:** merged. Tests: server 1,136. No schema change; nothing in `web/` or the agent touched.
- **Waiting on the owner:** nothing from this entry.
- **Next:** nothing owed on #139. #132 (PR #135) owns the other §4.3 paragraphs and the `AGENTS.md` bullets "One uvicorn worker" and "One request-body cap".
