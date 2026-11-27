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

## 2026-11-28 — Claude Code (Sonnet 5.5), worker — #202: both versions set to 0.3.0.dev0 (PR #205); 0.2.0 is released
- **Merged:** 2026-11-28, squash `4fa028c`, at head `d70eb72`, by the orchestrator (Fable 5.2) on the owner's go-ahead, straight after #204; branch deleted, #202 closed. It owed the automatic review only: Codex clean at that head, CI green, Greptile not run (out of credits). The orchestrator read the diff and the PR's Release run; the suite counts are the worker's and CI's. The State, Waiting and Next lines below are the orchestrator's, written after the merge.
- **Done:** [PR #205](https://github.com/DeusMaximus/TLG-IPAM/pull/205) closes #202: `VERSION` and the agent's `__version__` are `0.3.0.dev0` (`.agents/releases.md`, step 7). Two lines, nothing else.
  - Release run 46011040968 on the PR head: `version=0.3.0.dev0`, `prerelease=true`; wheel and both image platforms built and checked; both publish jobs and `verify` skipped. Base pin current.
  - **The 0.2.0 release, checklist steps 4 to 8 (2026-11-27):** the owner pushed `v0.2.0` on `7fee67d`. [Run 46008510831](https://github.com/DeusMaximus/TLG-IPAM/actions/runs/46008510831): all four jobs passed on attempt 1; nothing failed. Image index `sha256:51ace516cf75b89a72d1fe21c958e8f201c8962beb4f5ab5ea985cbb1d5232da`; wheel SHA-256 `c17851be5944e2c0b9522a41d629315506fdf07493134d833cc6f7690d0bc16a`; base `python:3.13-slim@sha256:7249d5587b78209a006dc4a3b5340678f84ea729239193aa559962cd8c1fce50`.
  - The package was readable with no login from its first publish, so the first `verify` passed and nothing had to be made public; why was not established. Step 5's one-off retry measurement was run by the orchestrator (attempt 2, `publish-image` alone): it downloaded attempt 1's artefact, logged `is already this build`, and `publish-release` uploaded and edited nothing. #203 put both into the release documentation (entry below).
  - Step 6, by the orchestrator: the release lists the wheel, which downloads with no login; the image's index was fetched with no login and its digest is the one above. The owner's test deployment was rebuilt from the tag (`/health` reports `0.2.0`) and its agent recreated from the published image, pulled with no login: it checks in at `0.2.0` with no update flag. Not looked at: the Agents page's install commands in a browser (the owner's check).
- **Decisions:** PR Deliberate calls 1–2. Other mentions of `0.2.0` and `0.2.0.dev0` are examples or records and stay. The orchestrator filed #200, #202 and #203 on the owner's word for the release and its follow-ups.
- **State:** `main` is `4fa028c` plus this entry, at version `0.3.0.dev0`. One tag and one release exist: `v0.2.0`. At `d70eb72`, locally (the worker) and in CI: backend 2,003; agent 2,108 / 14 skipped; web 250 plus 3 storage tests. No schema, dependency or lock change. `PROTOCOL_VERSION` is 2 on both sides. **Every milestone, M0 to M3, has no open issue; the only open issue is #191.**
  - **The owner's test deployment** runs the tag `v0.2.0` with remote MCP on and remote writes off, published through a tunnel that passes the seven paths alone, with its scan agent on the published `0.2.0` image. Three links are live (Claude.ai, ChatGPT, Mistral). Its addresses, names and proxy arrangement stay out of the repository. The script and logs of #32's scripted pass are in the main checkout's `.dev/32/`, uncommitted.
  - `release.yml`'s `verify` error message still says "if this is the first release, make the package public". It is conditional advice; #203 was documentation only and left it.
  - Carried from earlier entries, not rechecked: one unexplained failure of `test_oauth_restart.py::test_a_read_write_grant_writes_while_the_setting_is_on_and_not_after_a_restart_with_it_off` during #28 (if it recurs, keep `--tb=long` and file it). `.agents/orchestration.md` doesn't yet say workers wake the orchestrator with a `t3_thread_send` line at PR open and finish (the owner's file to change). Local branches of merged work remain. On a Cloudflare Tunnel, leave "Protect with Access" off for the MCP host (PR #194).
- **Waiting on the owner:** nothing blocks. Theirs when they choose: the Agents page check above, and what v0.3 holds (no milestone or issue exists for it).
- **Next:**
  1. #191 (web tests fail in one shared worker), outside the milestones.
  2. Before the next release: check the base-image pin again (`.agents/releases.md`, step 3). The release documentation now describes a first release as it went; a private package and a retry days after the build remain cases not seen.

## 2026-11-27 — Claude Code (Sonnet 5.5), worker — #203: release docs record what `v0.2.0` showed (PR #204)
- **Merged:** 2026-11-28, squash `543c5e6`, at head `77b08cb`, by the orchestrator (Fable 5.2) on the owner's go-ahead; branch deleted, #203 closed. It owed the automatic review only: Codex clean at that head, CI green, Greptile not run. The orchestrator read the diff against the run's logs and searched the head for the two old claims (none left). Live state is in the entry above; this entry's Waiting and Next lines are the worker's, from before the merge.
- **Done:** [PR #204](https://github.com/DeusMaximus/TLG-IPAM/pull/204) closes #203.
  - `.agents/releases.md`: the package was readable with no login from its first publish; the private-package steps stay as a case not seen. The retry measurement is recorded, and step 5 no longer asks for it. A re-run logs `is already this build`, then the same `pushed …` line.
  - `AGENTS.md` and the brief: a re-run's artefact download is recorded as measured.
  - **The release:** `v0.2.0` tags `7fee67d`. [Run 46008510831](https://github.com/DeusMaximus/TLG-IPAM/actions/runs/46008510831): all four jobs passed on attempt 1. Attempt 2 re-ran `publish-image` and the two jobs after it, and all passed; `build` did not re-run. Nothing failed.
  - This replaces the #200 entry's expectation that the package would be private and `verify` would stop.
- **Decisions:** PR Deliberate calls 1–5. The cause of the public package is not established. The measurement covers **Re-run this job** about six minutes after the upload, not **Re-run failed jobs** or a later download. "Before any package setting had been changed" rests on #203's account.
- **State:** CI and Codex clean at `77b08cb`. Locally, `test_release.py` and `test_release_publish.py`: 214 passed; the lock commands are still in `AGENTS.md`. No code, workflow, test, version, schema or lock change.
- **Waiting on the owner:** merging PR #204 (done, see Merged).
- **Next:** superseded by the entry above.

## 2026-11-27 — Claude Code (Sonnet 5.5), worker — #200: both versions set to 0.2.0 (PR #201); #32 closed
- **Merged:** 2026-11-27, squash `7fee67d`, at head `159df9b`, by the orchestrator (Fable 5.2) on the owner's go-ahead; branch deleted, #200 closed. It owed the automatic review only: Codex clean at that head, CI 10/10, Greptile not run (out of credits). The orchestrator read the diff and the Release run's jobs and log; the suite counts are the worker's and CI's. The State, Waiting and Next lines below are the orchestrator's, written after the merge.
- **Done:** [PR #201](https://github.com/DeusMaximus/TLG-IPAM/pull/201) closes #200: `VERSION` and the agent's `__version__` are `0.2.0` (`.agents/releases.md`, step 3). Two lines, nothing else.
  - Release run 46004574910 on the PR head: wheel and both image platforms built and checked at `0.2.0`; the two publish jobs and `verify` skipped.
  - Base pin current: the run printed `python:3.13-slim still names sha256:7249d5587b78209a006dc4a3b5340678f84ea729239193aa559962cd8c1fce50`, the digest `agent/Dockerfile` pins. No pin PR was owed.
  - **#32 closed 2026-11-27**, by the orchestrator on the owner's go-ahead. Its closing comment is the record of the acceptance run on the owner's test deployment at `efe0deb`: which §6.10 lines were shown with Claude.ai, ChatGPT and Mistral, which by a scripted pass (72 checks, one failed on the script's own input), and which rest on the test suite alone (the 50-registration cap, the 24-hour deletion, a window that ran out, wrong codes over successive windows). No issue was filed against any line. **M3 has no open issue.**
- **Decisions:** PR Deliberate calls 1–3. The other `0.2.0.dev0` mentions are examples and stay. No test, brief or `AGENTS.md` change. The orchestrator filed #200 for the bump without a separate go-ahead, on the owner's "start the release", and told the owner.
- **State:** `main` is `7fee67d` plus this entry, at version `0.2.0`. The repository has no tag and no release. At `159df9b`, locally (the worker) and in CI: backend 2,003; agent 2,108 / 14 skipped. Worker, locally: web 250, lint and build clean; both images pass their checks; locks unchanged. No schema, dependency or lock change. `PROTOCOL_VERSION` is 2 on both sides.
  - **The owner's test deployment** runs `main` at `efe0deb` (version `0.2.0.dev0`) with remote MCP on and remote writes off, published through a tunnel that passes the seven paths alone, with a locally built scan agent enrolled. Three links are live (Claude.ai, ChatGPT, Mistral), and a backup and restore were run there during #32. Its addresses, names and proxy arrangement stay out of the repository. The script and logs of the scripted pass are in the main checkout's `.dev/32/`, uncommitted.
  - Carried from earlier entries, not rechecked: one unexplained failure of `test_oauth_restart.py::test_a_read_write_grant_writes_while_the_setting_is_on_and_not_after_a_restart_with_it_off` during #28 (if it recurs, keep `--tb=long` and file it). `.agents/orchestration.md` doesn't yet say workers wake the orchestrator with a `t3_thread_send` line at PR open and finish (the owner's file to change). Local branches of merged work remain. On a Cloudflare Tunnel, leave "Protect with Access" off for the MCP host (PR #194).
- **Waiting on the owner:** tag `7fee67d` as `v0.2.0` and push the tag (`.agents/releases.md`, step 4). It is the first release: the first publish creates the container package as private, so `verify` stops until the owner makes the package public and re-runs the failed jobs (one-time setup, step 3).
- **Next:**
  1. Watch the tag's Release run (step 5). After `verify` passes, re-run `publish-image` alone once to measure the retry (the artefact is kept 7 days), and record the result here.
  2. Step 6: check the release page, then build a server from the tag and compare its Agents page commands with the published artefacts. Then rebuild the test deployment from the tag and recreate its agent from the published image.
  3. Step 7: a PR setting both versions to `0.3.0.dev0`. Step 8: a hand-off entry with the run and the image digest, taken from the tag's run, not the PR's.
  4. #191 (web tests fail in one shared worker), outside the milestones; the only open issue.

## 2026-11-27 — Claude Code (Sonnet 5.5), worker — #198: Gemini documented as not supported (PR #199)
- **Merged:** 2026-11-27, squash `6b06bf3`, at head `0a3bad0`, by the orchestrator (Fable 5.2) on the owner's go-ahead; #198 closed. It owed the automatic reviews only: Codex clean at that head, CI 6/6, Greptile not run (out of credits). The orchestrator re-ran the web checks on the head (lint, 250 tests, build) and read the diff. The State, Waiting and Next lines below are the orchestrator's, written after the merge.
- **Done:** [PR #199](https://github.com/DeusMaximus/TLG-IPAM/pull/199) closes #198. Claude.ai, ChatGPT and Mistral are the supported web clients; Gemini links but is unreliable, not supported and not recommended (owner's decision, 2026-11-27).
  - Spec: goal 3 and §6.10's first line name three. §6.8 has a new dated part with the observations and the inference marked; short notes in §6.3 step 2 and §6.4.
  - Brief (§5.5, Linked clients page), `docs/deployment.md` ("Supported web clients") and two UI strings follow. Two new web tests.
  - No server change: nothing refuses or special-cases Gemini.
- **Decisions:** PR Deliberate calls 1–7. One sentence in §6.8 says "the four" elsewhere in the spec means the spike's four: what was measured, not what is supported. §9 and the spike findings are unchanged. The record doesn't say which link Task mode ran on. It names Google's public redirect host, `oauth-redirect.googleusercontent.com`. #32 was retitled and its scope line edited to the three clients.
- **State:** `main` is `6b06bf3` plus this entry. Web 250, lint and build clean. No schema, dependency or lock change. Pages not checked in a browser.
  - Carried, not re-run here: backend 2,003 at `b68c1dc`; agent 2,108 / 14 skipped.
  - **The owner's test deployment** runs `main` at `117763a` with remote MCP on (read-only: `MCP_REMOTE_WRITES=false`), published through a Cloudflare Tunnel that passes the seven paths alone: `docs/deployment.md`'s outside check passed there, private paths answered by the tunnel. A scan agent built from that commit is enrolled and has scanned one subnet. Its addresses, names and proxy arrangement stay out of the repository.
  - **#32 so far, on that deployment:** Claude.ai, ChatGPT and Mistral each linked and answered tool calls. Seen as well: with no link window open, registration and authorisation refused from outside while existing links kept working; a read-only link offered the nine read tools only; `/.well-known/openid-configuration` `404` and `HEAD /mcp` `401` with `WWW-Authenticate`; Claude.ai refreshing about an hour after linking and carrying on with no reconnect prompt.
  - Still open: one unexplained failure of `test_oauth_restart.py::test_a_read_write_grant_writes_while_the_setting_is_on_and_not_after_a_restart_with_it_off` during #28 (not seen since; if it recurs keep `--tb=long` and file it). `.agents/orchestration.md` doesn't yet say workers wake the orchestrator with a `t3_thread_send` line at PR open and finish (the owner's file to change). Workers can't wait on Codex. Local branches of merged work remain.
- **Waiting on the owner:** the rest of #32. For the owner's own deployment, PR #194's finding stands: on a Cloudflare Tunnel leave "Protect with Access" off for this host.
- **Next:**
  1. #32 (`owner`): the lines of spec §6.10 not yet shown. Only a real client can show: ChatGPT and Mistral still linked after their first access token expired; revoking a client in the UI stopping its next request; a read-write link writing, the change carrying the client as `actor`, and write access going when `MCP_REMOTE_WRITES` is turned off; links surviving a container restart and a restore. The protocol lines (redirect URI rules, the 50 cap, wrong codes, refresh reuse, `invalid_target`, client secrets, the owner key over `mcp-remote`) are held by the test suite and were not run on the deployment; a scripted client can run most, with the owner's go-ahead to open link windows there. Close #32 with each line ticked or an issue filed.
  2. The release, `.agents/releases.md`, after #32 so its fixes land before the version bump. The version is still `0.2.0.dev0`; `PROTOCOL_VERSION` is 2 on both sides. The base-image pin was current on 2026-11-27; check again just before the bump PR. It's the first release (no tag exists), so the one-time setup applies: the first publish creates the container package as private; the owner makes it public, then re-runs `verify`. Step 5's one-off re-run of `publish-image` tests the retry. After it, the test deployment's agent is recreated from the published image.
  3. #191 (web tests fail in one shared worker), outside the milestone; not started.

## 2026-11-27 — Claude Code (Opus 5.5), worker — #190: registry loose ends (PR #197); M3's code issues are all merged
- **Merged:** 2026-11-27, squash `c5b74b1`, at head `10c2eb0`, by the orchestrator (Fable 5.2) on the owner's go-ahead; branch and worktrees deleted, #190 closed. Two briefed rounds on GPT-6.1 Sol: round 1 NO-GO at `c9ecbb6` (one P2, three P3s), round 2 GO at `b68c1dc` with no new finding. `10c2eb0` then shortened the page's note at the owner's request, wording only, so it had no further round: Codex clean on it, CI 6/6, and the orchestrator read the commit. Greptile did not run (out of credits).
- **Done:** [PR #197](https://github.com/DeusMaximus/TLG-IPAM/pull/197). The OAuth server's behaviour is unchanged; one situation is now marked and two decisions are recorded.
  - `oauth.never_completed(now)`: an SQL predicate (unrevoked, `last_used_at` null, no `oauth_tokens` row but codes past their expiry), asked as a column of the Linked clients list's one statement. The answer gains `never_completed`; `link_out` never reports it for a revoked link.
  - Web: `links.neverCompletedNote` shows a note under Last contact for a marked link, from the server's flag alone, and says from the list's own answer which case it is: revoking frees the registration's place, frees none because the registration holds another link listed, or the registration is gone.
  - `_make_room` is unchanged but for its docstring; `REGISTRY_FULL`'s message now says every link of one registered client must be revoked. Spec §6.3 and §6.6, brief §3.14, §4.2, §5.5, §6.2, `docs/deployment.md` and `AGENTS.md` updated.
- **Decisions:** PR Deliberate calls 1–7.
  - A link approved whose code is never exchanged is left for the owner, marked; the server revokes nothing itself. Its registration keeps its place among the 50 until every link of that registration is revoked.
  - A registration with a pending request is still reclaimed at the cap. Preferring to spare it raises an outsider's cost by at most 32 authorise requests, and never reclaiming it would let an outsider make the registry refuse the owner's client.
  - Not taken, and not required by the reviewer: the server reclaiming a registration whose only links are never completed. It is the owner's to ask for.
  - The list's flag can be stale for an exchange whose claim had not committed when the list read; the next read is correct, and the note says "as of this list".
- **State:** `main` is `c5b74b1` plus this entry. At `b68c1dc`: backend 2,003 (the orchestrator's, the worker's and the reviewer's runs agree). At `10c2eb0`: web 248, lint and build clean; agent 2,108 / 14 skipped (no agent change since `c9ecbb6`). No schema, dependency or lock change. The orchestrator checked the page in a real browser at `c9ecbb6` and `b68c1dc`, with the codes' real five minutes; the shortened wording of `10c2eb0` was not looked at in a browser.
  - Still open from earlier entries: the one unexplained failure of `test_oauth_restart.py::test_a_read_write_grant_writes_while_the_setting_is_on_and_not_after_a_restart_with_it_off` during #28 (not seen since; if it recurs keep `--tb=long` and file it). `.agents/orchestration.md` does not yet say that workers wake the orchestrator with a `t3_thread_send` line at PR open and at finish; it is the owner's file to change. GitHub has shown a CI job as in progress for some minutes after its steps succeeded; it resolves by itself, re-run the job if it ever blocks a merge. Local branches of merged work remain.
- **Waiting on the owner:** #32, the acceptance run, which waits on the owner's internet connection (down on 2026-11-27). For the owner's own deployment, PR #194's finding stands: on a Cloudflare Tunnel leave "Protect with Access" off for this host, or put an nginx with the deployment page's path allow-list between the tunnel and the app.
- **Next:**
  1. #32 (`owner`): the acceptance run against Claude.ai, ChatGPT, Gemini and Mistral (spec §6.10). Untested until then: any real web client against the endpoints under `/mcp/`; how each takes a refresh that repeats the pair it holds (a token is not rotated in its first five minutes); how each recovers when its registration was deleted at 24 hours; a live tunnel.
  2. The release, `.agents/releases.md`, after #32 so that any fix the run turns up lands before the bump. The version is still `0.2.0.dev0`. Checked 2026-11-27: `python:3.13-slim` still names the digest `agent/Dockerfile` pins, so no pin PR is owed now; check again just before the bump PR. It is the first release (the repository has no tag), so the checklist's one-time setup applies: the container package is created private by the first publish and the owner makes it public, then re-runs `verify`; step 5's one-off re-run of `publish-image` measures the retry. `PROTOCOL_VERSION` is 2 on both sides.
  3. #191 (web tests fail in one shared worker), outside the milestone; not started.
