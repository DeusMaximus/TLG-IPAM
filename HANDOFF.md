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

## 2026-11-25 — Claude Code (Opus 5.5), worker — #170: a notice when a later DNS server answered after a refused reply (PR #171)
- **Merged:** 2026-11-25, squash `31978e5`, at head `3428c82`; branch deleted, #170 closed. Codex clean and Greptile 5/5 on `3428c82`; briefed round on GPT-6 Sol with Daybreak Blue (started there directly: the round builds refused DNS replies) GO with no findings: 1,812 re-measured, three mutations red, the 500/501 boundary probed with four-digit counts, and every reader of `last_scan_error` traced (UI amber "Agent reported" line only; no health state, no MCP tool, excluded from audit). The orchestrator ran the agent suite on #171 merged with #172: 1,827 passed, 4 skipped. No follow-up filed.
- **Done:** [PR #171](https://github.com/DeusMaximus/TLG-IPAM/pull/171) closes #170.
  - `probe.py`: `AnsweredPastRefusal(answer, too_large, too_many_records)`, what `DnsResolver.ptr` returns in place of a name or `NO_NAME` when an earlier attempt ended with a reply from its own server refused unread; `answered()` gives the bare answer when nothing was refused.
  - `scan.py`: `answered_later()` builds the notice in `refused_unread`'s words; `run` unwraps the answer, so the record is decided as before, and counts the lookup by limit; `error_text(errors, *optional)` gives parts up in order, the notice first, then #164's counts.
  - `conftest.FakeResolver(later=…)`. Spec §5.2, brief §4.6 and the DNS sentences of `AGENTS.md`'s "The agent never executes…" bullet updated.
- **Decisions:** PR Deliberate calls 1–7. The owner is told (a first server whose every reply is refused costs up to 254 queries and budget slots a scan); the notice goes in `error`, not a new field, so a scan that learned every name can carry a non-null `last_scan_error`, shown as "Agent reported: …" apart from "incomplete"; it is the first part given up at the 500 cap, so #164's longest errors store as before; any refusal before the answer counts.
- **State:** agent 1,812 passed, 4 skipped at the head (`main` 1,783). No schema change, no wire change, `PROTOCOL_VERSION` 2, `test_invariants.py` unchanged. Untested: the UI's layout of a long notice; a real forwarder (loopback responders stand in).
- **Next:** #93 or #20, one at a time; both touch `probe.py`.

## 2026-11-25 — Claude Code (Opus 5.5), worker — #165: enrolment refuses an agent on another protocol before its token is spent (PR #172)
- **Merged:** 2026-11-25, squash `2afe2c0`, at head `01cd32c`; branch deleted, #165 closed. Codex clean and Greptile 5/5 on `01cd32c`; briefed round on GPT-6.1 Sol GO with no findings and every deliberate call standing (`.dev/165/review-1-sol.md`): both suites re-measured, seven mutations red, and the new agent's actual enrol request run in process against the actual server at `a8cb345` (enrolled `200`), which closes the PR's untested old-server line. The orchestrator re-ran the agent suite at the head (1,798 passed, 4 skipped) and the server's enrolment tests (78). Follow-up filed with the owner's word: #175 (show an enrolment protocol refusal on the Agents page).
- **Done:** [PR #172](https://github.com/DeusMaximus/TLG-IPAM/pull/172) closes #165.
  - `agent_protocol.py`: `EnrolRequest.protocol` (`StrictInt | None`); `ENROL_PROTOCOL_ABSENT = 2`; `ENROL_TOO_OLD`/`ENROL_TOO_NEW` (`AGENT_TOO_OLD`, `AGENT_TOO_NEW`); `enrol_protocol`, `enrol_protocol_refusal` (check-in's rule).
  - `services/enrolment._exchange`: after the body is read and before validation, the write lock and `_claim`, a protocol mismatch is `409` with a fixed message naming no protocol. Nothing written, token unused, counts against the throttle. `protocol` is never stored.
  - Agent: `enrol.enrol_body()` (the report plus `protocol`); `enrol.protocol_refusal` builds the operator message; `enrol_and_store` logs it and retries on the refusal wait; `install._not_enrolled` uses it and adds "run install again, with the same token". The two codes are copied into the agent's `protocol.py`, held by `test_contract.py`.
  - Spec §5.6 has a new paragraph, "The agent's protocol, before the claim"; §5.7, brief §3.9 and §4.8, and the `AGENTS.md` "Enrolment tokens" and "wire contract" bullets are updated. #162's e2e test is replaced by `test_a_new_token_form_spends_an_old_agents_enrolment_only_if_the_protocol_is_not_raised`.
- **Decisions:** PR Deliberate calls 1–11: a body field, not a header; checked after the token lookup and before the lock; check-in's rule; two `409` codes; absent or null means 2, a non-integer is `422`; it counts against the throttle; `PROTOCOL_VERSION` not raised; the agent keeps retrying; nothing recorded on the agent row.
- **State:** agent 1,798 passed, 4 skipped; server 1,378; web 188 (untouched). No schema change; `PROTOCOL_VERSION` 2. In `install.py` only `_not_enrolled` changed.
- **Next:** #169 (shares `install.py`) can start now.

## 2026-11-25 — Claude Code (Fable 5.2), orchestrator — session closed: #136, #159 and #164 merged; nothing in flight
- **Done:** three PRs verified, briefed, fixed and merged this session's last leg, each squash, branch deleted, worker's entry below or in the archive:
  - [PR #167](https://github.com/DeusMaximus/TLG-IPAM/pull/167) (#136) `069af99`: the installer asks the kernel, as the service account, whether it can read the `--ca-file`.
  - [PR #166](https://github.com/DeusMaximus/TLG-IPAM/pull/166) (#159) `bc3ba04`: `publish-release` stops when it cannot tell whether the tag has a release; only the two publish steps may be given a credential.
  - [PR #168](https://github.com/DeusMaximus/TLG-IPAM/pull/168) (#164) `33ea51b`: a job's error counts the PTR lookups whose reply the agent refused unread, by limit.
  - Each got one briefed round (GO with P3s: two, two and four), the P3s fixed in the same PR by its worker, and the fix read by the orchestrator against the reviewer's remedy with Codex clean on the new head. No second round, by the owner's standing preference.
  - Filed, with the owner's word: **#169** (the installer's venv check still judges by mode bits, the model #136 removed for the CA bundle) and **#170** (decide whether to report a first DNS server whose replies are always refused while a later one answers).
- **Decisions:**
  - The owner (2026-11-25): a review round may be **started** on GPT-6 Sol with Daybreak Blue when the orchestrator judges a GPT-6.1 Sol refusal likely, said in the report; a judgment per round, not a rule (`.agents/orchestration.md`, "When a model refuses"). Used once: #164's round, which builds refused DNS replies. #136's and #159's rounds ran to the end on GPT-6.1 Sol.
  - Merge order was #167, #166, #168: #167 and #168 edited the same `AGENTS.md` bullet, and #168's worker merged `main` and resolved it by hand. #166 was merged on a local run of the suite over its tree merged with `main` (its files are disjoint from #167's).
- **State:** `main` is green. Agent 1,783 passed and 4 skipped (the root-only installer cases, which run in a container), server 1,357, web 188. No PR is open, no worker is running, no review round is running. No schema or protocol change this leg (`PROTOCOL_VERSION` 2).
  - Nothing has ever been published by the release workflow; the first tag is the owner's. `.agents/releases.md` is the checklist: step 3 the base-image refresh (with the DNS-ceiling premise recheck when the image's Python changes), step 5 the cross-attempt artefact download, and on the first release a look at `publish-release`'s log to see the tag's release listed (#159's assumptions about draft listing).
  - Never built or run by anyone: the server's Docker image with the `python -m app.serve` `CMD`; a proxy in front of the server against spec §8.
  - Recorded, not filed: `publish-image`'s warning prints skopeo's stderr with its newlines (the pattern #159 tidied in `publish-release`); after exec, a process running as a reused service account can ptrace the CA check's program where the host allows it (#136's review, not rated).
- **Waiting on the owner:**
  - #5 and #25 (`owner` label), deferred until the deployment is rebuilt behind a tunnel; M3 (#26–#30, #32) waits on #25.
  - The first release tag.
  - No real-network test without asking again: the permission to send packets on the test host covered 2026-11-23 only.
  - The owner is considering Claude Opus 5.5 as orchestrator for a session started before the weekly limit resets; the roster in `.agents/orchestration.md` is unchanged until they say so.
- **Next:** ready and not started, all owing a briefed round but #31:
  - `probe.py`, one at a time: #93 (resolver allowlist on the agent's host), #20 (forward-DNS check), #170.
  - `install.py` / enrolment: #165 (refuse an agent the server cannot answer before its token is spent), then #169; they may share `install.py`, so not together unless #165 turns out to be server-side only.
  - #31 (deployment document from spec §8): no code, can run beside any of the above.
  - At most three workers, none sharing a code file. Review briefs and reviews of this session are in `.dev/<issue>/` (not committed).

## 2026-11-25 — Claude Code (Opus 5.5), worker — #164: the job's error counts DNS replies refused for size or record count (PR #168)
- **Merged:** 2026-11-25, squash `33ea51b`, at head `ae46dda`; branch deleted, #164 closed.
- **Done:** [PR #168](https://github.com/DeusMaximus/TLG-IPAM/pull/168) closes #164.
  - `probe.py`: when no server gave a usable answer and one attempt ended in `ReplyTooLarge` or `ReplyTooManyRecords`, `ptr` returns `UnreadReplies(too_large, too_many_records)` instead of None. Only the query's own server can cause it.
  - `scan.py`:
    - `refused_unread`: the refusal sentence.
    - `no_answer`: the unanswered count, then the refusals.
    - `cut_short`: the refusals after a PTR timeout.
    - `error_text`: keeps the counts whole or drops them whole.
  - `protocol.py`: `scan_error_fits`.
  - Spec §5.2 says what the owner is told and what to do: `minimal-responses` in BIND or Unbound; for a forwarder (dnsmasq, Pi-hole, AdGuard Home, Technitium, a router), the upstream that produces the PTR answer. A PTR record set that is itself over the limit is the zone's own data.
  - Brief §4.6 and the `AGENTS.md` bullet "The agent never executes…" each get one sentence. The merge with #167 kept `main`'s wording and added only that sentence.
- **The error now:**
  - With nothing refused, the text is unchanged from before #164.
  - With refusals: `rdns: no DNS server gave a usable answer for 3 of 4 addresses, names kept; the agent refused a reply over 1232 bytes in 2 lookups, over 1 question or 32 records in 1 lookup`.
  - A limit that refused nothing is left out, and one lookup reads "1 lookup".
  - After a deadline: `timed out after T s during the PTR lookups; rdns: before the timeout, the agent refused a reply …`, counted over the lookups that returned. The plain unanswered count stays out, as before.
- **Longest error:** 495 characters against the 500 cap, a margin of 5; 405 in the timeout form. Past the cap the counts are dropped whole, never cut before a number.
- **Decisions:** PR Deliberate calls 1–6.
  - The count travels as a third "no usable answer" value; None stays legal and no fake resolver had to change.
  - A lookup a later server answered is not counted.
  - A lookup can be in both counts.
  - The second count covers both header limits.
  - "Their stored names are kept" becomes "names kept" when refusals are reported, so the sentence fits.
- **Review:**
  - Codex and Greptile were clean on `30246ab`; Codex was clean on `ae46dda`, which Greptile did not re-review.
  - Briefed round 1 (Codex, GPT-6 Sol, at `30246ab`): GO with four P3s, all fixed in `c197f10`:
    1. a deadline hid known refusals;
    2. the cap was held only by a 2-character margin;
    3. the wording ("for 0", no unit, "or 1 question");
    4. two coverage gaps (malformed-only rows, unequal counts).
  - The orchestrator read the fixes against the remedies; no round 2.
  - Twelve mutations all fail tests, the reviewer's nine included.
- **Final counts at `ae46dda`:** agent 1,783 passed, 4 skipped (`main` before it: 1,741 passed, 4 skipped; 42 new); server 1,357 passed; web `install.test.js` 18 passed.
- **State:** merged. No schema change; `PROTOCOL_VERSION` stays 2; no wire change; `test_invariants.py` unchanged.
- **Waiting on the owner:** nothing from this PR.
- **Untested or assumed:**
  - No real resolver that sends a non-minimal reply over 32 records, or ignores the advertised payload; loopback responders stand in for both.
  - The forwarder guidance comes from the products' documentation, not from running them.
  - The longest-error test lists the failure texts `probe.py` raises today. A new raise site, or an exception name longer than the builtins' 22 characters, could drop the counts but never cut them.
  - How the UI shows a long `last_scan_error`.
- **Next:** #170 (a first server whose replies are refused while a later one answers goes unreported). Then #93 or #20, one at a time; all touch `probe.py`.

## 2026-11-25 — Claude Code (Opus 5.5), worker — #159: publish-release stops when it cannot tell whether the tag has a release (PR #166)
- **Merged:** 2026-11-25, squash `bc3ba04`, at head `5efdd34`; branch deleted, #159 closed. Tests: 1,723 agent at the head; 1,741 passed and 4 skipped on the tree merged with `main` after PR #167 (orchestrator's run).
- **Done:** [PR #166](https://github.com/DeusMaximus/TLG-IPAM/pull/166).
  - `release.yml`, `publish-release`: it reads the repository's releases page by page (`gh api …/releases?per_page=100&page=N`, no `--paginate`). The jq prints `page <length>` first for a JSON array, then `<id> <draft>` for each release whose tag is the tag (`env.TAG`). Each answer must print its marker. The list ends at the first page shorter than 100, and an attempt that reaches page 20 full fails.
    - No release → `gh release create`, as before.
    - One release → the wheel is checked or attached, and a draft is published, as before.
    - More than one → stop with nothing changed (gh's `FetchRelease` would answer with either).
    - Could not tell → three attempts (15/30/45 s), each from page 1 with the list emptied, then stop with nothing created, uploaded or edited.
    - gh's error text goes on one warning line.
    - `publish-image`, the names and the permissions are unchanged.
  - `release_stubs.py`: the `gh release view` model and `lookup_wins` are gone. A one-page `gh api` stand-in, from gh 2.105 `pkg/cmd/api` and go-gh 2.17 jq, refuses any other argument list. `FetchRelease` calls are refused when a published release and a draft share the tag.
  - `test_release.py`: `test_only_the_two_publish_steps_are_given_the_token`. A credential is the `secrets` context, or the `github` context reached any way but one named property other than `token` (`toJSON(github)` included). 21 placements are refused and 8 named-property expressions allowed.
  - Docs: `.agents/releases.md` (the jobs table, and what the owner sees and does for each stop); the `AGENTS.md` "One version" bullet; one sentence each in spec §5.6 and brief §2.4.
- **Review:**
  - Briefed round (Codex, GPT-6.1 Sol) at `640b30d`: GO with two P3s, the whole `github` context and a later-page `204`. Both were fixed in `5efdd34` and read by the orchestrator against the remedies (a page with no marker taken for the end of the list failed five tests).
  - Automatic reviews: Codex clean at `640b30d` and `5efdd34`; Greptile no findings at `640b30d`, not retriggered.
- **Decisions:** PR Deliberate calls 1–6:
  - the list endpoint rather than get-by-tag, whose 404 is not proof of absence;
  - more than one release stops the job;
  - a marker on every page, asked for page by page, at most 20 pages;
  - the retry copied from `publish-image`;
  - the scope of the token rule;
  - the tripwire changed only in the stub's accepted call, with no new shell word.
- **State:** merged.
  - Mutations caught at `5efdd34` (bash 5, publish tests, the author's): old behaviour 38 failed, no retry 41, `TAG=` dropped 25, more-than-one branch 8, a page without its marker ending the list 6, page 1 as the whole list 16, no page bound 2, list not emptied 1, newlines kept 1.
  - Publish tests pass on bash 3.2 (macOS) and 5.2.37 (`--network none`).
  - No schema change, no agent code change.
- **Untested or assumed (nothing has ever been published by this workflow):**
  - that the job's `contents: write` token counts as "push access", so drafts appear in the list;
  - that a draft's `tag_name` in the list is the tag it will publish under;
  - that GitHub fills every page but the last and leaves no release out;
  - gh's real output for each answer (the stand-in is read from source, not run against gh);
  - the 20-page stop, which no real repository has reached;
  - that a re-run attempt can download the artefact (still `.agents/releases.md` step 5).
- **Waiting on the owner:** the first tag, and the release checklist with it.
- **Next:**
  - On the first release, check that `publish-release` lists the tag's draft or release as expected (its log shows each page read).
  - Optional follow-up, not filed: `publish-image`'s warning prints skopeo's stderr with its newlines.
