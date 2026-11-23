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

## 2026-11-24 — Claude Code (Fable 5.2), orchestrator — six PRs merged; #86 in a fix after a NO-GO; #22's review handed to the owner
- **Done:** six PRs squash-merged on the owner's go-ahead, remote branches deleted. The newest worker entries are below; older ones are in the archive.
  - First wave: #122 (#120) `ed4a7d6`; #123 (#121) `d067821`, a packet slot ends with the event-loop step it was granted in; #124 (#21) `0226262`, enrolment tokens, `POST /api/v1/agent/enrol`, re-enrol.
  - Second wave: #127 (#125) `45712d5` and #128 (#126) `ded8622`, Sonnet 5.5, tests only. #131 (#90) `c5d7139`, Opus 5.5: a 16 MiB budget and a 60 s deadline on request bodies; one briefed round (Codex, GPT-6.1 Sol), GO with no findings.
  - #92 is narrowed to what #124 left of it: the plain-HTTP warning comes only after the enrolment token has been issued, and should come before.
- **Decisions (the owner's, 2026-11-24):**
  - Go-ahead for both waves. The second ran five workers at once against the file's limit of three; the owner was told.
  - #100: option 4. The result set reports name lookups separately from liveness; a name is cleared only on an answered "no name"; `PROTOCOL_VERSION` rises. Recorded on the issue.
  - #91: the 2 MiB bound on an import stands and the dialog does not batch. Length limits on write: `domain` 253 characters (PR #129 has it), `notes` 4,096; a row over one is that row's `error`. Recorded on the issue.
  - #5 and #25 (`owner` label): the owner runs them later, after rebuilding the deployment. Do not raise them in every report. #25 still comes before the rest of M3.
- **State:** `main` is `c5d7139` plus hand-off commits. Measured on each PR head merged with `main` before its merge: 1,059 server tests with #131; 924 agent tests since #127.
  - **PR #129 (#86), in a fix.** Briefed round 1 at `591b8bf`: NO-GO, one P2 and two P3s (`.dev/86/review-1.md`, and on the PR). The P2: the scope read (`scans._scopes_of_chunk`) scans all of `reserved_ranges` and returns every excluded range, outside the call's bound. The worker (thread "#86 — bound list_scan_findings…", branch `86-bound-findings-call`) has all three and may answer with a limit on reserved ranges per subnet: bring that to the owner before the merge. Then `@codex review` and round 2 on the new head.
  - **PR #130 (#22), waiting on the owner's manual review.** Head `fb39dd7`, CI green, Codex clean. Two Sol sessions failed: one on the network, one on an access gate ("not authenticated with one of your authorized hardware keys") while it tested who can change the unit's code. `.dev/22/manual-handover.md` has the message word for word and what the session established; its logs are in `.dev/22/review-1-partial/`. Two leads it left unrated: `install.not_root_only` does not check the venv's other packages or the links between the unit's interpreter and its file. They have **not** been sent to the worker; the owner chose to wait for the review.
  - The main checkout was found detached at `fb39dd7` (the review of #130 is being run there). This entry was committed from a separate worktree; do not move the main checkout's HEAD while that review runs.
  - Both open PRs merge cleanly with `main` at `c5d7139`. Measured on the merges before #131 landed: #129 1,028 server tests; #130 1,137 agent and 1,020 server tests, and the container path (first start enrols, a recreated container on the same volume ignores the spent token) repeated with no network.
  - Since #131: a body has 60 s to arrive whole (`408`), and all bodies in progress share 16 MiB (`503`). One sender that keeps the budget full holds every other body off; per-client limits, header size and idle connections are left to the proxy (brief §4.3).
  - Until #22 merges a new agent is enrolled by hand: the Add dialog shows the `tlge_` token and a `curl` for the exchange. Re-enrol is API-only until #23.
  - The enrol throttle is global and in memory: 10 attempts that did not enrol per 60 s.
  - Since #121 scans take longer (a 254-address `icmp,arp,rdns` job at 200 pps: 7.2 s to 7.7 s on macOS in single runs). One that only just fitted its timeout can end incomplete.
  - Still true from earlier entries: ARP is on by default for a new target; a stuck `resolv.conf` read leaves later scans that rely on the host's resolvers incomplete until the agent restarts; a subnet's CIDR stored before #97 keeps its spelling until its next edit.
  - Reviews: Greptile reviews only the first head of a PR. Codex's state is in its summary comment (`codex-review-state:v1`). On 2026-11-24 the owner's network was on a hotspot and three review sessions died on "Connection failed"; each was restarted once, with its findings written to disk as it went.
  - Nothing wakes the orchestrator when a worker opens its PR: look for open PRs at the start of each turn.
- **Waiting on the owner:**
  - The manual review of PR #130 (Codex app, GPT-6 with Daybreak Blue, hardware key).
  - Whether to file a follow-up from #131's round: uvicorn takes unbounded request headers on httptools and never closes idle connections. The reviewer says it may follow the merge but must be closed, in the image or by verified proxy settings, before #26 exposes `/mcp`.
- **Next:**
  - Round 2 on #129 when its fix lands. Verify #130 again when its review is posted; a P1 or P2 goes to the worker (thread "#22 — agent enrolment exchange…").
  - #100 after #22 merges (same agent files; Opus 5.5, briefed). #91 after #86 merges (adds `notes` only; Opus 5.5, automatic reviews).
  - #23 after #22: its systemd tab shows `--enrol-token -` (the token on standard input), its container tab a volume and `IPAM_ENROL_TOKEN`. Then #92 on the same dialog. #24 waits on #22; #94 goes with #24. #93 and #20 are stretches.
  - Then M3 (#26 to #32) after #25. No OAuth grant may be issued before #28's write-scope checks are in force.
  - No real-network test is planned. The owner's permission to send packets on the test host covered 2026-11-23 only: ask again first.

## 2026-11-24 — Claude Code (Opus 5.5), worker — #90: a budget and a deadline on request bodies (PR #131)
- **Done:** [PR #131](https://github.com/DeusMaximus/TLG-IPAM/pull/131) (`90-bound-unauthenticated-bodies`, head `4efe69d`) closes #90.
  - `body_limit.py`: beside the cap, `BODIES_MAX` (16 MiB) bounds the body bytes of all requests in progress, read by the middleware or by the route, counted until the request ends (`503 UNAVAILABLE` past it). `BODY_TIMEOUT_S` (60 s from the headers, whole body) gives `408 REQUEST_TIMEOUT`.
  - Every refusal and every response sent before its body has fully arrived closes the connection. Without that, uvicorn keeps reading and discarding a trickled body after an early `401`.
  - A body the middleware reads is one buffer. A route that reads its own body gets blocks of at least 64 KiB.
  - Docs: brief §4.3 (limits, measurement, "Left to the reverse proxy or tunnel"), §5.1 table, acceptance list; spec §5.7 and the §6.6 row; the `AGENTS.md` cap bullet.
  - Merged: `c5d7139`, squash, on the owner's go-ahead; the remote branch is deleted. CI green and Codex clean on `4efe69d`. Greptile reviewed only the first head (`4598df7`, one P2, fixed and resolved) and was not retriggered. Briefed round (Codex, GPT-6.1 Sol): GO with no findings at `4efe69d`; it repeated the memory measurement on Linux (about 63 to 65 MiB before, about 20 MiB after, both parsers) and ran the body-limit tests six times without a failure. The orchestrator ran 1,059 server tests on the head merged with `main` and repeated the real-server run: 9 held and answered `408` at 60 s, 23 refused. The header and idle-connection follow-up is proposed to the owner and not filed. Nothing else from this entry is waiting on the owner.
- **Decisions:** PR body Deliberate calls 1–11. Budget plus deadline, no spooling. The count runs until the request ends, not until it authenticates, because FastAPI parses a `Body` before the route's dependencies: check-in and the owner routes read a declared-length body before their credential, which the issue had missed. Bytes are counted as they arrive, never reserved. 60 s is twice the agent's 30 s request deadline. A cut body reaches the route as `http.disconnect`, and the middleware swallows the `ClientDisconnect` that follows.
- **State:** server 1058 and agent 922 pass; CI green on `4efe69d`; no schema change; `PROTOCOL_VERSION` unchanged.
  - Measured on macOS, loopback, 32 stalled 1.75 MiB bodies: `a8534eb` +60 to +68 MiB on both parsers; the new head +17 to +21 MiB, 9 held, 23 refused, `408` at 60 s.
  - Codex: one P1 on `4598df7` (per-chunk memory), fixed in `4efe69d`, then clean on `4efe69d` after `@codex review`. Greptile: one P2 on `4598df7` (same finding), fixed and resolved; it has not reviewed `4efe69d`.
  - Measured, not fixed (outside #90): uvicorn's httptools takes unbounded request headers (8 MiB of one unfinished header was accepted), and idle connections stay open on both parsers. Brief §4.3 lists both as the proxy's job.
- **Waiting on the owner:** the briefed round (an author from a different model family; the orchestrator briefs it), a Greptile retrigger on `4efe69d`, then merge. Whether to file a follow-up for header size and idle connections (uvicorn options in the image, or the proxy's job only).
- **Next:** #31 can take its proxy guidance from brief §4.3 and the PR body's "Left to the reverse proxy or tunnel". #86 may change the one brief §4.3 sentence that measures an import, which this PR left alone.

## 2026-11-24 — Claude Code (Sonnet 5.5), worker — #126: ingest credential read is held to the write lock (PR #128)
- **Done:** [PR #128](https://github.com/DeusMaximus/TLG-IPAM/pull/128) (`126-ingest-lock-guard-test`, head `aaac816`) closes #126. One test in `api/tests/test_results.py`, `test_ingest_reads_the_credential_only_while_it_holds_the_write_lock`. It wraps `results._credential_live`, records `_write_locked()` at the read, and asserts `seen == [True]`, so it also fails if the read is moved, doubled or dropped. No file under `api/app/` changed. CI green (backend, agent, frontend, locks). Codex completed on the head with no findings. Greptile reviewed the head with no findings. CodeRabbit skipped. The owner merges.
  - Merged: `ded8622`, squash, on the owner's go-ahead; the remote branch is deleted. CI green, Codex clean and Greptile clean on `aaac816`, the only head. The orchestrator ran 1020 server tests on that head and repeated the mutation: the new test fails on the lock assertion and the four existing cases pass. No follow-up filed. Nothing from this entry is waiting on the owner any more.
- **Decisions:** the issue offered two forms of the test; I took the lock assertion at the read, reusing `_write_locked`, not a revoke landed between read and lease update. The four existing cases already cover the second form's outcome. `seen == [True]` rather than `all(seen)`.
- **State:** green. Measured on this head: 1020 server and 922 agent tests pass (Python 3.13). The mutation (deleting `await scans.take_write_lock(session)` from `results._release`, in a throwaway worktree, never committed) fails only the new test, on the lock assertion; the four existing cases pass on it. The edit and the output are in the PR body. No schema or web change.
- **Waiting on the owner:** merge of #128.
- **Next:** #125 (agent tests) is the other open test-gap issue in this wave. #22, #90 and #86 as in the previous entry.

## 2026-11-24 — Claude Code (Sonnet 5.5), worker — #125: DNS hand-off guard test (PR #127)
- **Done:** [PR #127](https://github.com/DeusMaximus/TLG-IPAM/pull/127) (`125-dns-handoff-guard-test`, head `6559de8`) closes #125. Tests only, plus a comment.
  - `agent/tests/test_dns_send.py`: the DNS counterpart of `test_icmplib_send.py`. It runs the real `DnsResolver.ptr`, the installed dnspython and `QuerySocket` on the real `scan.Pacer`, with 12 concurrent lookups and an always-overdue clock. Only `create_datagram_endpoint` on the running loop is replaced. The stand-in transport records whether the slot of the lookup that sent the datagram is still open (`pacer.ended < granted[address]`), and answers through `datagram_received`. It sends no packet.
  - The control puts an `await` ahead of `dns.asyncquery.udp` and expects every query to reach the transport after its slot has ended.
  - `scan.py`: three comment lines in `Pacer`'s docstring (a `Pacer` belongs to one event loop). No other file changed.
  - Merged: `45712d5`, squash, on the owner's go-ahead; the remote branch is deleted. CI green, Codex clean and Greptile clean on `6559de8`, the only head. The orchestrator ran 924 agent tests on that head (Python 3.13) and repeated the mutation: the new test fails on the open-slot assertion and the control passes. The Python 3.11 count and the blind-check control are the worker's alone. No follow-up filed. Nothing from this entry is waiting on the owner any more.
- **Decisions:**
  - The open slot is judged per lookup, not by a pacer-wide count. A global count reported late sends as open when another lookup's slot was open.
  - The ARP request needs no counterpart: no library sits between its slot and `PacketLink.sendto`. Said in the PR.
- **State:** green. Agent suite: 924 passed on Python 3.13.12 and on 3.11.18 (922 on `main` at `a8534eb`, plus 2). CI is green on `6559de8`. Codex and Greptile both reviewed the head with no findings, and none are open. Mutation (a `sleep(0)` before `dns.asyncquery.udp`): the new test fails on the open-slot assertion and the control passes. Control with a blind check: the control fails. The mutation worktree was removed and never committed. No schema change.
- **Waiting on the owner:** the merge.
- **Next:** nothing from this entry. #22 owns the rest of `agent/`; the one new file in `agent/tests/` and the `Pacer` docstring edit should not conflict with it.

## 2026-11-24 — Claude Code (Opus 5.5), worker — #21: enrolment tokens, the enrol exchange and re-enrol (PR #124)
- **Done:** [PR #124](https://github.com/DeusMaximus/TLG-IPAM/pull/124) (`21-enrolment-tokens`, head `b45a723`) closes #21.
  - "Add agent" creates the agent `pending` with a single-use `tlge_` token good for an hour.
  - `POST /api/v1/agent/enrol` (`services/enrolment`, on its own `enrol_router`, outside `require_agent`) exchanges the bearer for the agent token. One transaction, under the write lock, with the time read after the lock: a compare-and-set on `used_at`, the old agent token revoked, the new one issued, the agent `active`, the agent's own audit entries pruned to 100.
  - `POST /agents/{id}/re-enrol` issues a new enrolment token.
  - A global throttle sits in front: 10 failed attempts per 60 s, in memory.
  - The exchange is logged `source="agent"` with `actor`.
  - Re-enrol, update, revoke and delete read the agent under the write lock (`agents._locked_agent`).
  - Scope extension decided by the orchestrator: results ingest rechecks the agent's state and its token under the write lock (`results._credential_live`), which also closes the owner-revoke race that was on `main`.
  - Docs: brief (new §4.8, §4.6), spec §4.2/§5.4/§5.6/§5.7, `AGENTS.md`, README.
  - No schema change; `PROTOCOL_VERSION` unchanged.
  - Merged: `0226262`, squash, on the owner's go-ahead, after #123; the remote branch is deleted. CI green and Codex clean on `b45a723`. Greptile reviewed only the first head (`c3804f1`: three findings, all fixed and answered) and was not retriggered. Briefed rounds (Codex, GPT-6.1 Sol): NO-GO at `c72e0a8`, GO at `b45a723` with one P3, filed as #126. The orchestrator ran 1019 server, 899 agent and 125 web tests on the head merged with `main`, removed the ingest credential check (its four cases went red), and on an earlier head the claim's fresh timestamp and its `used_at` condition (each caught). Nothing from this entry is waiting on the owner any more.
- **Decisions:** PR body Deliberate calls 1–17.
  - Optional body fields; null leaves the stored value.
  - One 403 for every refusal; the body is read only for a usable token.
  - Only live tokens are revoked.
  - Re-enrol is refused for a disabled agent and leaves leases to run out.
  - The create response field `token` became `enrol_token` plus `enrol_token_expires_at`.
  - The interim dialog never builds a plain-http command across a network.
- **State:** server 1019, agent 898, web 125 tests pass; lint and build clean. CI green and Codex clean on `b45a723`. Greptile last reviewed `c3804f1` (three P1s, all fixed and answered) and needs a manual retrigger. The first briefed round (Codex, GPT-6.1 Sol, on `c72e0a8`) was NO-GO with three P2s and a P3; all are fixed on `b45a723` (call 17). The branch is not merged with `main` at `c28abfb`. Until #22, a new agent is enrolled by hand with the dialog's `curl`; re-enrol is API-only until #23 (the README gives the call).
- **Waiting on the owner:** the second briefed round on `b45a723` (the orchestrator starts it), a Greptile retrigger, then merge.
- **Next:** #22 (the agent exchanges `IPAM_ENROL_TOKEN` itself; the systemd installer) and #23 (guided install, Re-enrol button, pending enrolments). PR #123's files were not touched.
