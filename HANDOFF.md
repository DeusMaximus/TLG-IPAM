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

## 2026-11-25 — Claude Code (Opus 5.5), worker — #179: `install --allowed-dns-servers` (PR #180)
- **Merged:** 2026-11-25, squash `b88b539`, at head `90ca0d0`; branch deleted, #179 closed. Round 1 (GPT-6.1 Sol, `f158f01`) NO-GO, one P2: the keep-on-update reader read `agent.env` line by line, so an `ALLOWED_DNS_SERVERS=` line inside another variable's multi-line quoted value was taken as the setting and widened `10.0.0.53` to `0.0.0.0/0` (reproduced under systemd 257). Fixed in `22196e4`: `install.env_assignments` checks the whole file against the grammar `install` writes (split at `\n` alone, printable ASCII, no backslash, no quotes, unquoted single-line values) before reading any assignment, and refuses otherwise with the line's number and no content. Round 2 (`22196e4`) GO with one P3 (a wrong claim that systemd continues a comment ending in `\`), fixed in wording in `90ca0d0` and read by the orchestrator against the remedy; no third round. Codex clean on `90ca0d0`, CI green. Greptile did not review (free-plan credits spent). Reviews at `.dev/180/review-1-sol.md` and `review-2-sol.md`.
- **Done:** [PR #180](https://github.com/DeusMaximus/TLG-IPAM/pull/180) closes #179. `install --allowed-dns-servers` writes `ALLOWED_DNS_SERVERS` to `agent.env`, checked by `config.parse_allowed_dns` before anything changes, a bad item named by its place. Docs (spec §5.6, both READMEs, `EnrolmentSteps`/`UpdateModal` hints, `AGENTS.md` "Enrolment tokens") make the option the way; no drop-in.
- **Decisions:** PR Deliberate calls. An update without the option keeps the `agent.env` value (`install.kept_allowed_dns`), unlike `--allowed-cidrs`: absence here means "any server", not a safe default. Removal is deleting the line; no flag. `install` never writes an `agent.env` over `store.READ_MAX` (4 KiB). `EnvironmentFile=` overrides a drop-in's `Environment=` (measured on systemd 257); an old drop-in applies only while `agent.env` sets none. The page's systemd install command does not gain the option. `--allowed` is now an ambiguous abbreviation.
- **State:** agent suite 1,949 passed (macOS), 1,943 as root on Linux (Sol's count, round 2). No schema or wire change.
- **Waiting on the owner:** whether keep-on-update should also apply to `--allowed-cidrs`/`--max-pps` (possible follow-up); whether a removal flag is wanted.
- **Next:** #175 (show an enrolment protocol refusal on the Agents page), now unblocked; #20 (fdns) next on `probe.py`.

## 2026-11-25 — Claude Code (Fable 5.2), worker — #25: the web-client spike's findings written into spec §6 (PR #182)
- **Done:** [PR #182](https://github.com/DeusMaximus/TLG-IPAM/pull/182) closes #25. The spike's harness and raw log are in `.agents/spikes/25/`, written by the orchestrator (Opus 5.5) from the run on the owner's test deployment; the findings are the comment on #25. `docs/v0.2-spec.md` §6 and §9 carry them:
  - §6.3: the link window opens before the URL is given to a client (ChatGPT and Gemini register on URL entry); a registration is not a link; registration accepts only `https` redirect URIs with a host, or `http` to loopback; a loopback-IP redirect authorises with any port, `localhost` by name only with its registered port; the redirect host is derived from the authorise request's validated `redirect_uri`, shown in ASCII form.
  - §6.4: `oauth_clients` gains `token_endpoint_auth_method` and the hashed client secret, checked by the provider's own request-aware authenticator; `oauth_grants` gains `redirect_uri`; `last_used_at` is the client's last contact, not the owner's use.
  - §6.5: a tool the caller lacks the scope for is hidden, and a direct call answers `Unknown tool`; no docstring tells an assistant to retry.
  - §6.6: the cap of 50 counts every registration, and one with no unrevoked grant is reclaimable; the list shows the grant's host and **Last contact**.
  - §6.7: unserved `/.well-known/` paths answer 404, never the SPA; `HEAD /mcp` answers as `GET` does.
  - §6.8 is the record of the spike; §6.10 gains the acceptance lines; §9 has nothing open.
- **Decisions:** PR Deliberate calls 1–18. RFC 8707 `resource` is checked at authorise only (`invalid_target`), absent is accepted. No CIMD: all four clients link by dynamic registration. The grant stores the whole `redirect_uri` as authorised. A client registered in one window can authorise in a later one. Hashing the client secret is kept.
- **State:** no application code, no schema. Round 1 (GPT-6.1 Sol, `57030fb`) NO-GO with two P2s (a hash-only secret needs a request-aware authenticator; the SDK accepts any redirect scheme) and two P3s, fixed in `459625c`. Round 2 (`459625c`) GO with one P3 (the loopback-port exception), fixed in `a0b300e` and read by the orchestrator against the remedy. Codex clean on `a0b300e`, CI green. Greptile's free plan ran out of credits this billing period, so it no longer reviews.
- **Merged:** `6da48bc`, 2026-11-25.
- **Waiting on the owner:** approve the M3 issue edits below; remove the spike's public route and the four test connectors.
- **Next:** edit the M3 issues from the PR's scope notes: #26 (the authenticator, the redirect policy and the loopback-port exception, the `resource` check), #27, #28, #29 (its 51st-registration line needs qualifying) and #30. Then M3 can start.

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

