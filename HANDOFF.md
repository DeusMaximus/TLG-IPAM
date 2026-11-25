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

## 2026-11-25 — Claude Code (Opus 5.5), worker — #93: `ALLOWED_DNS_SERVERS`, a resolver allowlist on the agent's host (PR #177)
- **Merged:** 2026-11-25, squash `2c54ef4`, at head `4352115`; branch deleted, #93 closed. Round 1 (GPT-6.1 Sol, `131349e`) NO-GO: P2 a scoped IPv6 entry let a server on another interface through, P3 no restart after the drop-in; both fixed in `1d73e4e`. Round 2 (GPT-6.1 Sol, `1d73e4e`) GO with one P3 (an unconditional kernel-refusal claim), fixed in wording in `4352115` and read by the orchestrator against the remedy; no third round. `main` (#178) merged in `4038143`: checked by the orchestrator as mechanical (every file but `AGENTS.md` equal to an automatic merge; both sides' sentence edits to the shared bullet carried over exactly). Codex clean on `4352115`, CI 9/9 green. Orchestrator's agent suite at the head: 1,908 passed, 14 skipped. Reviews at `.dev/93/review-1-sol.md` and `review-2-sol.md`. Follow-up filed with the owner's word: #179 (`install --allowed-dns-servers`).
- **Done:** [PR #177](https://github.com/DeusMaximus/TLG-IPAM/pull/177) closes #93.
  - `config.parse_allowed_dns`: unset is None. A value set but empty, a name, a URL, host bits, or an IPv6 item with a zone stops the agent at start, naming the item by its place.
  - `guard.dns_servers` narrows the chosen servers after the cut to 3. It never keeps a zoned server while the setting is set; an unscoped address is compared by address alone, and the host's routing picks its link.
  - `scan.run(…, allowed_dns=)`; `scan.outside_allowed` gives the notice, or the "rdns not run" error when no server is left (incomplete, names kept).
  - `loop.py` passes the setting; `cli.py` shows it in `--help` and the start-up log line. Agents page: one hint sentence per tab.
  - Docs: spec §5.2 and §5.6, both READMEs, brief §2.2, and the DNS sentences of the `AGENTS.md` agent bullet.
- **Decisions:** PR Deliberate calls 1–13: drop the server, don't refuse the job; the list applies to `resolv.conf` too; no fallback to the host's resolvers; a partial drop leaves the scan complete, its notice given up last, after #164's counts; zones refused, not matched; for systemd, a drop-in followed by `systemctl restart`.
- **State:** no schema change, no wire change, `install.py` untouched. Untested: a live systemd drop-in, real link-local traffic, the web hint text.
- **Next:** #179. #20 (fdns) is next on `probe.py`.

## 2026-11-25 — Claude Code (Opus 5.5), worker — #169: the installer asks the kernel whether the service account can use the venv (PR #178)
- **Merged:** 2026-11-25, squash `8d623c8`, at head `967bdf5`; branch deleted, #169 closed. Briefed round on GPT-6.1 Sol at `f144b11`: GO with one P3, every Deliberate call standing (`.dev/169/review-1-sol.md`); the owner accepted call 4 (`useradd` then `userdel` on a refused first install). The P3 (an exit 0 from a start-up hook read as usable) fixed by the worker in `967bdf5`, read by the orchestrator against the reviewer's remedy, Codex clean there, no second round (the owner's standing preference); Greptile 5/5 on `f144b11` only. All nine checks green at `967bdf5`. The orchestrator ran the agent suite on the head merged with `main` at `f7a9aa3`: 1,858 passed, 14 skipped (root-only). No follow-up filed.
- **Done:** [PR #178](https://github.com/DeusMaximus/TLG-IPAM/pull/178) closes #169.
  - New `agent/src/tlg_ipam_agent/reach.py` (`python -I -B -m tlg_ipam_agent.reach`, no argument, `os`/`stat`/`sys` only): walks `sys.prefix` and the package, follows no link, skips `__pycache__`. Exits `UNUSABLE` (3) at the first directory or file `os.access` says the user cannot use, naming it on stderr; exits `USABLE` (7) when there is none.
  - `install.py`: `unreachable()` gone; `as_user` module-level (was `read_as.as_user`); `runs_as` runs the unit's interpreter on `reach` as the account, 60 s deadline. 7 → usable, 3 → `IN_THE_TREE`, other positive exit or exec `EACCES` → `NOT_STARTED`, 0, a signal or anything else → `NOT_CHECKED`. The check runs after the account exists and before the CA check, so before anything is written, on updates too.
  - `test_invariants.py`: `PROCESSES_STARTED_IN` key `as_user`; `errno` in `ALLOWED_IMPORTS`. Spec §5.6 venv sentence and the two `AGENTS.md` installer sentences updated.
- **Decisions:** PR Deliberate calls 1–10. Interpreter plus walk rather than `--version` or GNU `find`. `__pycache__` skipped; an unreadable source refused even with readable bytecode. Call 10 (the P3): an exit 0 from start-up hooks is no answer; this does not attest against dishonest root-placed start-up code.
- **State:** agent 1,858 passed, 14 skipped (macOS); as root, Debian 13, `--network none`: 1,852 passed, 20 skipped. Live systemd replay (branch vs `main`: ACL on venv, ACL on a module, supplementary group, umask 077) in the PR body. No schema or wire change; `PROTOCOL_VERSION` 2.
- **Disclosed limits the reviewer measured:** a dependency outside the two trees (via `.pth` or a link) is accepted though the account cannot import it, as before; no AppArmor/SELinux; no drop-ins.
- **Next:** PR #177 (#93) conflicts with this one in one `AGENTS.md` line and resolves it. #179 (`install --allowed-dns-servers`) can start once #177 merges.

## 2026-11-25 — Claude Code (Sonnet 5.5), worker — #174: three gaps in the backup and restore doc (PR #176)
- **Merged:** 2026-11-25, squash `f7a9aa3`, at head `238f789`; branch deleted, #174 closed. CI green, Codex clean and Greptile 5/5 on `238f789`; no briefed round owed. The open acceptance line (the unit, copied as written with the URL edited, pulls a snapshot) was run by the orchestrator on the owner's test host, Debian 13 with systemd 257: `systemctl start` exit 0, journal `wrote …` (output on the PR). That host's nightly timer now runs this unit.
- **Done:** [PR #176](https://github.com/DeusMaximus/TLG-IPAM/pull/176) closes #174. Only `docs/backup-and-restore.md` changes.
  - The service unit has `Environment=TLG_IPAM_URL=https://ipam.home.arpa`; the crontab has a `TLG_IPAM_URL=` line; the by-hand run passes it through `sudo -u backup env`. A paragraph says the default is a placeholder and covers plain HTTP on port 8080.
  - The restore section says `curl` errors while the app starts are expected, and what success and a final failure look like.
  - "A second copy" records #5's restore from a whole-container hypervisor backup: one run, quiet database, no guarantee.
- **Decisions:** PR Deliberate calls 1–5. The retries are documented, not quieted, so both scripts are byte for byte as on `main`. `curl` prints one line per failed try, so the doc does not promise just one.
- **State:** no code change. Untested: the crontab line and the `sudo` part of the by-hand command.
- **Next:** #173 (the server image runs as root) will change the restore steps in this doc.

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
