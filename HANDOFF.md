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

## 2026-11-26 — Claude Code (Opus 5.5), worker — #28: MCP_REMOTE_WRITES and the grant as actor (PR #193)
- **Merged:** 2026-11-26, squash `694639d`, at head `59f941a`, by the orchestrator (Fable 5.2) on the owner's go-ahead; branch deleted, #28 closed. One briefed round (GPT-6.1 Sol, `c75f2e0`): GO with one P3, test only (the setting could stop reaching `load_code`, `exchange_refresh` or `_same_pair` with every test green), all eleven deliberate calls accepted; fixed in `59f941a` with `test_every_answer_with_a_scope_follows_the_setting_and_can_write`. No second round: the orchestrator read the commit against the remedy and repeated Sol's three mutations (each now fails one test). Codex clean and CI 6/6 green on `59f941a`; Greptile did not run. The orchestrator ran the backend suite at both heads (1,923, 1,925 passed). Review at `.dev/193/review-1-sol.md`.
- **Done:** [PR #193](https://github.com/DeusMaximus/TLG-IPAM/pull/193) closes #28.
  - `config`: `MCP_REMOTE_WRITES` (`mcp_remote_writes_problem`, `Settings.remote_writes`): only `true`/`false` in any case; anything else stops the start with a fixed message.
  - `services/oauth.grant_scopes(grant, *, remote_writes)` gives `ipam:write` only for `can_write` with the setting on. `remote_writes` is a required keyword on every function that answers with scopes; the module still reads no settings.
  - The provider holds the value (`mcp_auth(…, remote_writes=)`, `remote_writes_of`); `create_app` copies it to `app.state.remote_writes` for the link-window routes.
  - `link_window.writes_may_be_allowed(remote_writes)`; the window state gains `remote_writes`, which is what #30's checkbox reads.
  - `mcp_server._actor()` gives `grant:<name>`, or `grant-id:<grant id>` when no name was kept, in a session of its own; the nine write tools and the services they call carry `actor`. The owner key's writes stay null.
  - `INSTRUCTIONS`, `list_device_types` and `list_scan_findings` say what to do when a write tool is absent on a read-only connection.
  - `AGENTS.md` invariant 7; brief and spec updated.
- **Decisions:** PR Deliberate calls 1–11. Changing the setting is a restart. A read-write grant writes again when the setting is back on, and **until #30 there is no supported per-link revoke**: leaving the setting off is how a read-write link is stopped (brief §5.5, spec §6.5). `MCP_REMOTE_WRITES=true` without `PUBLIC_BASE_URL` is accepted and changes nothing.
- **State:** backend 1,925 passed at `59f941a`. No schema or dependency change; `web/` and `agent/` untouched. Sol confirmed by running it that a session is judged by each request's credential (an owner-key session continued with a grant's token is `404`; two grants of one registration sharing a session each get their own permissions and actor). **One unexplained failure** of `test_oauth_restart.py::test_a_read_write_grant_writes_while_the_setting_is_on_and_not_after_a_restart_with_it_off` in one of the worker's full runs, no traceback kept; not seen again in the worker's two later full runs, the orchestrator's run or CI. The orchestrator's guess, unproven: a start-up deadline missed while two suites ran at once in one worktree. If it recurs, capture `--tb=long` and file it.
- **Waiting on the owner:** nothing from this issue.
- **Next:** [PR #194](https://github.com/DeusMaximus/TLG-IPAM/pull/194) (#31, deployment docs, Sonnet 5.5) is open at `e347ef4`, held for this merge: it rebases, checks its five `MCP_REMOTE_WRITES` passages against this code, and then gets one outside read by GPT-6.1 Sol (its claim about Cloudflare Tunnel's "Protect with Access" path matching needs checking). #29 (24-hour deletion, name sanitising and the tightened consent-page name, edited on the owner's word) and #30 (UI) are ready; #190 follows #29, since both edit `services/oauth.py`. #32 is the owner's acceptance run. #191 (web tests in one shared worker) is outside the milestone.

## 2026-11-26 — Claude Code (Opus 5.5), worker — #27: link window and consent page (PR #192)
- **Merged:** 2026-11-26, squash `b517a13`, at head `96decb7`, by the orchestrator (Fable 5.2) on the owner's go-ahead; branch deleted, #27 closed. Three briefed rounds on GPT-6 Sol with Daybreak Blue. Round 1 (`143c905`) NO-GO, two P2s (a cancel or replace could answer before an approval in flight committed a grant; a caller with no credential could burn every window the owner opened) and two P3s, fixed in `824668c`. Round 2 (`824668c`) GO with three P3s (a wrong-code audit entry committed after its window ended; a sixth guess compared when the fifth's audit write was pending; documents claiming more than holds), fixed in `96decb7`. Round 3 (`96decb7`, the new commits) GO, no findings. Codex clean and CI 6/6 green on each head; Greptile did not run. The orchestrator re-ran the backend suite at each head (1,854, 1,871, 1,879 passed) and, with the owner's leave, ran the page in a real browser (below). Reviews at `.dev/192/review-{1,2,3}-sol.md`.
- **Done:** [PR #192](https://github.com/DeusMaximus/TLG-IPAM/pull/192) closes #27. With `PUBLIC_BASE_URL` set a client can be linked end to end.
  - `services/link_window.py`: the window in process memory, a 10-character code held as SHA-256; opening, cancelling, an approval through its commit and the window's one wrong-code entry all hold `_transitions`; an unexpected approval failure ends the window `failed`.
  - `consent.py`: `GET`/`POST /mcp/consent`, fixed templates with every value escaped, a strict CSP, a form token bound to the transaction, an `Origin` check.
  - `routers/link_window.py`: `GET`/`POST`/`DELETE /api/v1/link-window`, owner key. The state carries `expires_in_s`, `wrong_codes` and `ended` for #30.
  - `AGENTS.md` invariant 6; brief §2.3, §3.6, §4.2, §5.5, §7; spec §6.3, §6.4, §6.6, §6.10.
- **Decisions:** PR Deliberate calls 1–14.
  - **The owner's decision, 2026-11-26: wrong codes are counted per request, never the window.** The fifth ends that request; no wrong code closes or shortens the window; the right code always links. It replaces "five wrong codes burn the window", which round 1 showed an outsider could use to hold linking shut. Guessing is bounded by the code's size (30^10) against the window's ten minutes: Sol measured at most 580 wrong codes a second on loopback, about 1 in 1.7 billion a window.
  - `can_write: true` is refused (`409 REMOTE_WRITES_OFF`) until #28 changes `link_window.writes_may_be_allowed`.
  - Opening a window replaces one that is open; every end of a window drops every pending request; the right code on a request that then fails leaves the window open.
  - One audit entry a window for its first wrong code, written under the transition lock, so an outsider writes the log at most once a window and can make the owner's open or cancel wait behind one database write (about six seconds at worst with the database busy, Sol's measurement).
- **State:** backend 1,879 passed at `96decb7`. No schema or dependency change; `web/` and `agent/` untouched.
  - **Real browser, by the orchestrator (T3 Code's built-in browser against `python -m app.serve` on loopback at `96decb7`):** the stylesheet applies under the CSP with no violation reported; the form's post is accepted (the browser's `Origin` passes the check) and a wrong code shows the form again; the right code, typed in lower case with a hyphen, is followed by the `303` to the client's redirect URI, which received `code` and `state` and no `Referer`; the code exchanged for tokens (`ipam:read`); the window ended `linked`; the change log held `open`, `wrong_code`, `create`. A client name of markup rendered as text; a punycode redirect host rendered as `xn--…`. One browser only; no real web client (#32).
  - An outsider can still make the owner's attempt start again while a window is open, by pushing its request out of the 32 pending or reclaiming its client's unlinked registration at the cap of 50. The documents say so; #190 owns it.
- **Issues, on the owner's word:** #27's acceptance line on the fifth wrong code edited to what shipped, with a note that its Scope's "burn" wording is superseded (original in `.dev/m3-edits-2/27.before-192.md`).
- **Waiting on the owner:** whether a client name that itself says "verified" and names another host, shown beside the "unverified" tag, is distinguishable enough from the screenshots given in the session (#27's acceptance line is the owner's judgement; #29's sanitising is where it would change).
- **Next:** #28 (`MCP_REMOTE_WRITES`), which the owner said to start once this merged. Then #29, #30, #31 and #190 are all ready after #27; #30 also needs #28. #32 is the owner's acceptance run.

## 2026-11-26 — Claude Code (Fable 5.2), worker — #26: the OAuth provider: storage, tokens and discovery (PR #189)
- **Merged:** 2026-11-26, squash `6cc93bb`, at head `66da33e`, by the orchestrator (Fable 5.2) on the owner's go-ahead; branch deleted, #26 closed. Before the briefed rounds the orchestrator's read of the first head (`df7655a`) found that a cap of 1,024 token rows a grant let the holder of a stolen refresh token refresh about 511 times and erase the row reuse detection needs; the worker reproduced it and fixed it in `6d90603`. Codex's one P2 (a `client_secret_basic` client naming itself in the header alone) was fixed in `b6bc871`. Two briefed rounds on GPT-6 Sol with Daybreak Blue (the orchestrator's call for this PR, since the rounds send crafted requests at an OAuth server). Round 1 (`b6bc871`) NO-GO, three P2s: a retry left two refresh chains alive; registrations were unbounded once a window opens; the issue limit's `429` made an MCP SDK client discard working tokens. Fixed in `130f9ba`. Round 2 (`130f9ba`) GO with two P3s: no test held the kind inside the token derivation (fixed in `66da33e`, test only, read by the orchestrator against the remedy and checked by mutation: both new tests fail with the label removed), and issue #26's wording (edited, below). Codex clean and CI 6/6 green on `66da33e`; Greptile did not run. The orchestrator ran the backend suite on `130f9ba` merged with `main`: 1,812 passed. Reviews at `.dev/189/review-{1,2}-sol.md`.
- **Done:** [PR #189](https://github.com/DeusMaximus/TLG-IPAM/pull/189) closes #26. With `PUBLIC_BASE_URL` set the server is its own OAuth authorisation server for `/mcp`, with the owner key beside it; without it nothing changes.
  - `api/app/services/oauth.py` holds every rule; `api/app/oauth_provider.py` is the layer FastMCP and the MCP SDK call. Tables `oauth_clients`, `oauth_grants`, `oauth_tokens`; secrets, codes and tokens stored as SHA-256 only.
  - Discovery at the root ahead of the SPA; every other `/.well-known/` path is a 404, OAuth on or off; OAuth paths 404 on a trailing slash.
  - `main.create_app`, `mcp_server.build_mcp` and `oauth_provider.mcp_auth` are factories; the test session's app has OAuth off and `api/tests/oauth_flow.py` builds a second app with it on.
  - Spec §6, brief §2.3, §3.6, §3.14–§3.16, §5.1, §5.5, and the `AGENTS.md` bullets (audit rule, MCP tools, response shapes, owner key, the new OAuth bullet) carry it.
- **Decisions:** PR Deliberate calls 1–18.
  - **No client can be linked yet.** `oauth.linking_open()` is always False until #27: register and authorise answer `403`, and no route calls `oauth.approve`.
  - **Seam for #27:** authorise keeps the request in `oauth.pending` and redirects to `<base>/mcp/consent?txn=<id>`; the consent handler calls `oauth.approve(session, txn, can_write=…)`, which writes the grant, the code and the "grant issued" audit entry. `approve` raises `oauth.Refused("access_denied")` when the registration was reclaimed while the request was pending; #27's handler must show a start-again page for it, not a 500.
  - **The scope checks ship here, not in #28.** Every write tool requires `ipam:write` and a grant's token carries `ipam:read` alone (`oauth.grant_scopes`), so no grant can write whichever of #27 and #28 merges first.
  - **The cap of 50 registrations and its reclaim rule ship here, not in #29**, counted under the write lock. Until #29 a declared name is kept only when it needs no cleaning (`oauth.kept_name`).
  - **One refresh chain per grant:** a replacement is derived by HMAC under a per-row salt, with the kind inside what is signed, so a retry inside 60 seconds gets the same pair again and writes nothing; a later reuse revokes the grant.
  - **No refresh limit and no `429`:** a refresh token is not rotated in its first five minutes and is answered with its own pair; a grant holds at most 8,640 unexpired refresh tokens (Sol measured it over a simulated 30 days) and no unexpired row is ever deleted.
  - **Audit:** grant issued and grant revoked by refresh reuse are logged (`source="oauth"`); registrations (stored or reclaimed), token issue and rotation, and `last_used_at` are not.
- **State:** backend 1,813 passed at `66da33e` (the worker's count; 1,487 before). Schema additive: three new tables. No dependency change; `web/`, `agent/`, `README.md`, `.env.example` and `docker-compose.yml` untouched. Untested: a real web client against the endpoints under `/mcp/`; how the four clients take a refresh answer that repeats the pair they hold (the MCP SDK's client keeps its tokens on it); the server behind a proxy; `mcp-remote` by hand on the final head (run at `df7655a`; the in-process test passes at `66da33e`).
- **Issues, on the owner's word (2026-11-26):** #26's lifetimes line and refresh acceptance line edited to what shipped. #27 gained the seam, the start-again page and its acceptance line; its audit line lost "grant issued". #28 lost the scope-check lines and gained an acceptance line for a read-write grant with the setting on. #29 lost the cap and keeps the 24-hour deletion and the name sanitising. Originals in `.dev/m3-edits-2/`. Filed: #190 (M3, `briefed-review`: a link whose code is never exchanged, and a registration reclaimed while its request is pending; depends on #27) and #191 (web tests fail in one shared worker, `--no-isolate --maxWorkers=1`: 33 and 36 of 198 on two runs on `main`; not in a milestone).
- **Waiting on the owner:** whether #27 and #28 may run side by side though both edit `api/app/services/oauth.py` (`.agents/orchestration.md` says issues sharing a code file run one after the other; #28's part is the one function `grant_scopes`).
- **Next:** #27 (link window and consent page) and #28 (`MCP_REMOTE_WRITES`) are ready. Then #29 and #31 (after #27), #30 (after #27 and #28), #190 (after #27), and #32, the owner's acceptance run.

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
