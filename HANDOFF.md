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

## 2026-11-25 — Claude Code (Opus 5.5), worker — #136: the CA bundle's access asked of the kernel, as the service account (PR #167)
- **Merged:** 2026-11-25, squash `069af99`, at head `6429037`; branch deleted, #136 closed. Agent suite at the head, merged with `main` at `3feec43`: 1,662 passed, 4 skipped (the root-only cases). As root on Linux, `test_install.py` + `test_invariants.py`: 595 passed, 2 skipped. Server 1,357, web 188.
- **Done:** [PR #167](https://github.com/DeusMaximus/TLG-IPAM/pull/167) (`136-ca-access-as-service`).
  - `install.read_as` replaces `blocked_for`/`_allowed`. It runs `head -c 1 -- <path>` as the service user, and `test -e <path>` only when that fails, to tell "the file itself" from "a directory or link on the way".
  - The programs run through `subprocess.run(user=, group=, extra_groups=)`, with `COMMAND_ENV`, `cwd="/"`, stdio on `/dev/null`, no shell and `CA_CHECK_TIMEOUT_S` (10 s).
  - `Host.service_reads` passes `os.getgrouplist(SERVICE_USER, gid)` and prints the command without the path (#140).
  - `NOT_CHECKED` (a program missing or refused, or past the deadline) refuses the install before the exchange. `install.py` no longer imports `trust`.
  - Tripwire: `test_invariants.PROCESSES_STARTED_IN` (`Host.run`, `read_as.as_user`, with the exact keywords) and `SUBPROCESS_NAMES`.
    - `_process_starts` refuses the module named anywhere but as an attribute base (an import or assignment alias, a default argument), the string `'subprocess'`, `from subprocess import`, other names, an uncalled `run`, an `env` other than `COMMAND_ENV`, and `**keywords`.
    - No forbidden-name list changed.
  - `AGENTS.md` ("Enrolment tokens" and "agent never executes" bullets) and spec §5.6 updated. The brief has no CA-check text.
- **Verified:** as root in a `--network none` Debian 13 / systemd 257 container, with the server on loopback over HTTPS and agent wheels built from the branch and from `main`, by the author and again by the reviewer.
  - ACL that denies the account: the branch refuses with the token unused, and the same token enrols once the entry is gone. `main` spent the token, and the unit logged `CERTIFICATE_VERIFY_FAILED`.
  - Supplementary group: the branch enrols and checks in; `main` refused.
  - No image had `setfacl` and there was no network to add it, so the ACL was written as the `system.posix_acl_access` xattr (`deny_by_acl` in the tests).
- **Decisions:** PR Deliberate calls 1–9.
  - Fixed programs through `subprocess`'s user switching, not `os.fork` (which would copy root's memory, the enrolment token included, into the account's process), not Python run as the account, not `access(2)`.
  - The operator's `--ca-file` path is the one argument that is not fixed.
  - A check that cannot be made refuses the install.
  - `unreachable()` (the venv check, others' bits) is left as it was: a possible follow-up.
- **Review:** briefed round (Codex, GPT-6.1 Sol) GO at `2db1f1c` with two P3s, both fixed in `6429037` and answered on the PR:
  - an assignment alias of `subprocess` passed the tripwire; the reproduction now fails 13 tests;
  - with the deadline removed, the FIFO test hung the suite; it now fails at 5 s on its own bound (`FIFO_BOUND_S`), kills the process groups and reaps them, and no `head` is left.

  Automatic reviews: Greptile, one P2, fixed in `2db1f1c` (the root fixture no longer changes the mode of `--basetemp`'s parent directories); Codex clean on `a110963`, `2db1f1c` and `6429037`.
- **State:** merged. No schema change, no protocol change, no new import.
- **Untested / assumed:**
  - Ptrace (recorded, not rated): after exec, a process already running as a reused account can ptrace `head` or `test` where `ptrace_scope=0` and falsify the answer. Such an account can already sabotage its own agent, so the mechanism stands.
  - The deadline bounds only an interruptible wait; only the FIFO case was measured. A program in an uninterruptible wait (a hard network mount) is not reaped promptly, and `_ca_file`'s stat, the group lookup and starting the process are outside the deadline.
  - The check answers for the unit `install` writes, not one a drop-in changes (`SupplementaryGroups=`, `InaccessiblePaths=`, a private mount namespace, the unit's own security profile).
  - Also untested: AppArmor or SELinux, distributions other than Debian 13, busybox built without `head -c`, an unprivileged LXC.
- **Waiting on the owner:** nothing from this PR.
- **Next:** #165 (refuse an agent the server cannot answer before its token is spent) is not started.

## 2026-11-25 — Claude Code (Fable 5.2), orchestrator — state after #94, #140 and #160 merged; three workers in flight
- **Done:** since the orchestrator entry below this one's worker entries (same date; it holds the session's decisions and lessons, which still stand), merged by squash with the owner's go-ahead: #94 (PR #161), #140 (PR #162), #160 (PR #163). Filed with the owner's go-ahead: #164 and #165. #159's scope gained a bullet (a secret handed to a `build` step passes every shape test). `main` is at `6cb82b6` plus hand-off commits: 1,644 agent, 1,357 server and 188 web tests.
- **In flight** (three, the limit; none shares a code file):
  - **#136** (check the CA bundle's access as the service account), branch `136-ca-access-as-service`. Owes a briefed round. It will widen `test_invariants.py`'s lists.
  - **#159** (stop `publish-release` when it cannot tell whether the tag has a release; hold which steps are given a secret), branch `159-release-lookup-fail-closed`. Owes a briefed round.
  - **#164** (say in the job's error how many DNS replies were refused for size or record count), branch `164-dns-refusal-reasons`. Owes a briefed round.
  - Nothing wakes the orchestrator when a worker opens its PR: list open PRs at the start of each turn, then watch, verify and brief.
- **Queue:** #93 and #20 after #164, one at a time (all in `probe.py`); #165 (refuse an agent the server cannot answer before its enrolment token is spent) after #136, since both reach the installer's exchange; #31 (deployment document from spec §8) is unblocked and not started. M3 (#26–#30, #32) waits on #25.
- **Decisions since the last orchestrator entry:**
  - P3s from a briefed round or an outside read go back to the PR's worker to fix in the same PR, on stretch issues too (#94), not only on follow-ups; the orchestrator reads the fix against the reviewer's remedy, with Codex clean on the new head.
  - A reviewer's recommendation that reaches past the PR's files (#164, #165) is put to the owner as a new issue instead.
  - Before merging a PR whose CI ran against an older `main`, either update its branch (when the two may interact) or run the suite on the merged tree locally (when their files are disjoint: done for #162 after #161).
- **Reviewer models:** GPT-6.1 Sol completed the rounds on #156, #140 and #94, and was stopped by its safety filter on #160, as on #132, #153 and #147: every time on work that builds hostile input (DNS replies, pipelined requests, a shell harness). GPT-6 Sol with Daybreak Blue completed each restarted round. The orchestrator proposed starting rounds on DNS replies on GPT-6 Sol directly; the owner has not answered, so the roster's order stands: 6.1 first.
- **State:** nothing half-finished in `main`; CI green on it. `PROTOCOL_VERSION` is 2. Review briefs, reviews and hand-over files are in `.dev/<issue>/` in the main checkout (not committed).
- **Waiting on the owner:**
  - The first build of the server image with `CMD ["python", "-m", "app.serve"]` (#132); no proxy has been run against spec §8.
  - The first release, by `.agents/releases.md`: nothing has been published. Step 3 is now the base-image refresh (#94), with a recheck of the DNS ceiling's premise when the image's Python changes (#160); step 5 checks the cross-attempt artefact download (#147).
  - #5 and #25 (`owner` label), deferred. Permission to send packets on the test host covered 2026-11-23 only.
- **Next:** verify the three PRs as they open and brief Sol on each; then the queue above.
