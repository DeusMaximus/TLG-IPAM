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

## 2026-11-24 — Claude Code (Opus 5.5), worker — #24: release pipeline for the agent image and wheel (PR #145)
- **Done:**
  - [PR #145](https://github.com/DeusMaximus/TLG-IPAM/pull/145) (`24-release-pipeline`, head `5801a18`) closes #24. It includes `main` at `b2ba364` (merge `b8c9469`).
  - One version: `api/app/version.py` `VERSION` = the agent's `__version__` = `0.2.0.dev0`.
    - `settings.version` is a `ClassVar`, so `VERSION` in the environment is no longer read.
    - `agent/tests/test_release.py` and `install.test.js` hold the two literals together.
  - `.github/workflows/release.yml`. On a tag `v<version>` on `main`:
    - `build` (`contents: read`): version and tag checks, the wheel (`flit_core` from the new hashed `agent/requirements-build.txt`, reproducible), the image (amd64 and arm64, one OCI archive), start-up checks per platform with `--network none`.
    - `publish-image` (`packages: write`): `skopeo copy --all --preserve-digests`, only to a tag the registry says is absent.
    - `publish-release` (`contents: write`): `gh release create`; a draft a re-run finds is published.
    - `verify`: fetches both artefacts with no credential, as the install commands do.
    - A PR or a run by hand builds and checks only. Actions are pinned to commits, the BuildKit and binfmt images to digests.
  - `.github/scripts/release_check.py` and `check-agent-image.sh` (CI's `agent` job uses the script too).
  - `.agents/releases.md` (the runbook); `AGENTS.md` bullet "One version, released from a tag" and the third agent lock command; brief §2.4 (new), §4.2, §7, §10; spec §5.6; both READMEs.
  - Review fixes in `5801a18`: Codex P2 and Greptile P1 (draft release never published on a re-run), Greptile P1 (a failed registry read taken as an absent tag). All threads are answered and resolved.
  - Merged: `50d785f`, squash, on the owner's go-ahead; the remote branch is deleted. Nothing was published: the workflow publishes only on a pushed `v*` tag. CI green and Codex clean on `5801a18`. Greptile read only `b8c9469`, so one outside read by Sol (GPT-6.1 Sol) stood in for its re-run: GO at `5801a18` with two P3s, no Deliberate call overruled; it started both platforms' images from the PR's own build artefact with no network, and ran the publish steps' shell against stub commands. The orchestrator ran 1,295 agent, 1,129 server and 157 web tests on that head. The two P3s are filed as #147 (no test runs the publish steps; the runbook does not cover a retry after the seven-day build artefact has expired).
- **Decisions:** Deliberate calls 1–12 in the PR body. The main ones:
  - two version literals and three checks that hold them equal;
  - the environment no longer overrides the version;
  - `main` stays at `.dev0` between releases;
  - arm64 included (QEMU);
  - build once and publish those files by digest;
  - skopeo, not docker actions (skopeo 1.13's `docker-daemon:` transport is refused by current Docker, so images go through `docker-archive:`);
  - no attestations, no `latest` tag, no `environment:` gate.
- **State:**
  - merged. Tests: server 1,129; agent 1,295; web 157.
  - No schema change; `PROTOCOL_VERSION` 2, from #100.
- **Waiting on the owner:**
  - The one-time setup in `.agents/releases.md`: give the repository Write access if the ghcr package already exists, and make the package public after the first publish.
  - Then the release checklist: a bump PR to `0.2.0`, tag `v0.2.0`, push it.
- **Untested:**
  - the real push to ghcr.io and the release creation;
  - what ghcr.io tells the workflow token about a package never pushed (`DENIED` would stop the first release before pushing);
  - `verify` against real artefacts;
  - the publish jobs' re-run paths.
- **Next:**
  - The owner's first release per `.agents/releases.md`; #147 should land before it.
  - #94 (pin the agent base image by digest) goes with it.
  - #143 has a P3 on release versions not in normal form that `install.releaseVersion` accepts (`01.2`); the release check refuses them.

## 2026-11-24 — Claude Code (Opus 5.5), worker — #100: name lookups reported apart from liveness (PR #142)
- **Done:**
  - [PR #142](https://github.com/DeusMaximus/TLG-IPAM/pull/142) (`100-name-lookup-state`, head `9b196e2`) closes #100 under option 4.
  - **Protocol 2:** `PROTOCOL_VERSION` 1 → 2. Results carry `protocol` (absent means 1), `liveness_complete`, and `rdns_no_name` on each record. Ingest marks the dead by `liveness_complete` and clears a stored name only when the record carries `rdns_no_name`. Protocol-1 result sets keep protocol 1's rule.
  - **Briefed round 1** (Codex GPT-6.1 Sol, NO-GO at `dfae349`) fixed in `217e525`:
    - P2: `probe.says_no_name` reads the authority section first, so a CNAME followed by a delegation is no answer and the next server is asked.
    - P3: every ARP interruption path asserts `liveness_complete` false.
  - `main` at `7beb687` (#23) merged in `d41b7a6`. `9b196e2` makes `test_guided_install.py` send `PROTOCOL_VERSION + 1` as the mismatch.
  - Merged: `5ecf088`, squash, on the owner's go-ahead; the remote branch is deleted. CI green and Codex clean on `9b196e2`; Greptile read only the first head (5/5), and the briefed rounds were the outside read after it. Briefed round 2 (Codex, GPT-6.1 Sol): GO at `9b196e2`, both round-1 findings closed, no new finding. The orchestrator ran 1,252 agent and 1,129 server tests on that head. Follow-up filed on the owner's say: #144, the resolver reads a PTR or a negative SOA without checking it belongs to the question (older than #100, described in round 2), with an operating note for a subnet whose only DNS server does not recurse.
- **Decisions:** Deliberate calls 1–10 in the PR body. The reviewer kept 1–9 and revised 4. Call 10 is new: a CNAME with no PTR and an empty authority is no answer.
- **State:**
  - merged. Tests on the merged tree: agent 1,252, server 1,129, web 154.
  - No schema change, no new import or listed name.
- **Waiting on the owner:** nothing from this entry.
- **Untested:** any real network or resolver; the built image; a real protocol-1 agent.
- **Next:**
  - #24 publishes the first image at protocol 2.
  - #20 and #93 untouched; #144 is in the same agent file.
  - `web/src/pages/Agents.test.jsx` keeps a static fixture message that mentions protocols 2 and 1, which is harmless.

## 2026-11-24 — Claude Code (Opus 5.5), worker — #23: guided install, pending enrolments, version flags, scan offers (PR #141)
- **Done:**
  - [PR #141](https://github.com/DeusMaximus/TLG-IPAM/pull/141) (`23-ui-guided-install`, head `1718163`) closes #23.
  - The Add agent dialog is now the guided install of spec §5.6: Container and systemd tabs, each with prerequisites and commands pinned to the server's `/health` version. The systemd install reads the enrolment token from standard input (`--enrol-token -`).
  - Agents page: Re-enrol (with the commands that remove an old token from the host first), a pending-enrolments list, an "update available" flag, a protocol-mismatch line, the update commands, and "Scan *subnet* with this agent?" offers.
  - Server, read-only:
    - `agents.agent_out` adds `update_available` (PEP 440, `packaging`) and `protocol_mismatch` (read from the prefix of the stored `last_error`).
    - `scans.suggestions` serves `GET /api/v1/scan-targets/suggestions` in two statements; REST only.
  - `packaging` is now a direct dependency. All four locks were regenerated; only their `# via` comments changed.
  - Spec §5.6 gains the update and re-enrol command table and a paragraph on where the artefact names come from. The brief, README and `AGENTS.md` (one new bullet) are updated.
  - Review fixes:
    - `78ef42e` (Codex P2, Greptile P1): a container from the first release's install (`IPAM_AGENT_TOKEN`, no volume) is told to re-enrol rather than update.
    - `1718163` (Greptile P2): the modal overlay scrolls.
  - Merged: `a36c153`, squash, on the owner's go-ahead; the remote branch is deleted. CI green and Codex clean on `1718163`. Greptile read only the first head, so one outside read by Sol (GPT-6.1 Sol) stood in for its re-run: GO at `1718163` with three P3s, none of the 13 Deliberate calls overruled. The orchestrator ran 1,112 server and 154 web tests, lint and build on that head. The three P3s are filed as #143 (a disabled agent's enrolment shown as open; the pending list following the browser's clock; release versions not in normal form accepted).
- **Decisions:** Deliberate calls 1–13 in the PR body. The main ones:
  - The flags are computed on the server.
  - The mismatch is read from `last_error`, so `agent_protocol.py` is untouched.
  - Accepting an offer is `POST /scan-targets` with the server defaults, then one `POST /subnets/{id}/scan`.
  - Only subnets with no scan target are offered.
  - "Not now" lasts only while the page is open.
- **State:**
  - merged. Tests: server 1,112, agent 1,193, web 154.
  - No schema change; `PROTOCOL_VERSION` unchanged.
- **Waiting on the owner:** nothing from this entry.
- **Untested:** the commands against real artefacts (#24); the dialogs in a real browser.
- **Next:**
  - #24 must publish `ghcr.io/deusmaximus/tlg-ipam-agent:<version>` and `releases/download/v<version>/tlg_ipam_agent-<version>-py3-none-any.whl`, and give the server and agent one version. Today they differ: the server's `settings.version` is `0.1.0` and the agent's `__version__` is `0.2.0.dev0`.
  - #92 (the plain-HTTP warning before a token is issued) and #143 are the dialog's open follow-ups.

## 2026-11-24 — Claude Code (Opus 5.5), worker — #22: the agent's enrolment exchange and the systemd installer (PR #130)
- **Done:**
  - [PR #130](https://github.com/DeusMaximus/TLG-IPAM/pull/130) (`22-agent-enrol-install`, head `b7fb4ae`) closes #22.
  - The branch includes `main` at `211b53f` (merges `181ab1b` and `b7fb4ae`).
  - Round 1 (NO-GO at `fb39dd7`) was fixed in `ebe385e`, and Codex's P2 on it in `4dc5d6b`.
  - Round 2 (NO-GO at `4dc5d6b`: one P2, two P3s) was fixed in `eed4f91`:
    - `install.service_trust` is the one source of the unit's trust and of the exchange's; inherited `SSL_CERT_FILE`/`SSL_CERT_DIR` are dropped, and refused when the unit would not get them;
    - `uninstall` keeps `created-user` until the account and group are gone, so it can be retried;
    - each token the install learns is added to `Redact` (`secret=`);
    - the argument refusal gives re-enrol guidance;
    - §5.6 and `AGENTS.md` say what holds of `subprocess` (a running agent never imports `install` or `trust`).
  - Merged: `8081d8c`, squash, on the owner's go-ahead; the remote branch is deleted. CI green and Codex clean on `b7fb4ae`; Greptile read only the first head, and the briefed rounds were the outside read after it. The briefed review (Codex, GPT-6.1 Sol) took three rounds: NO-GO at `fb39dd7`, NO-GO at `4dc5d6b`, GO at `b7fb4ae` with one P3. After the second NO-GO the owner kept the Opus 5.5 worker on the fix. The orchestrator ran 1,193 agent and 1,083 server tests on `b7fb4ae`. Follow-ups filed, on the owner's say:
    - #136: the CA preflight judges mode bits, not ACLs or supplementary groups (round 2, finding 2);
    - #138: the unit does not discard CA trust settings the system manager supplies (round 3, finding 1);
    - #140: argparse's own errors repeat an argument, and the agent token's exact form is stated only on the agent's side.
- **State:**
  - merged. Tests on the merged tree: agent 1,193 on 3.13 and 3.11.18, server 1,083.
  - No change under `api/`, no schema change, `PROTOCOL_VERSION` unchanged.
  - The container runs (Debian 13 with systemd, `--network none`) are in the PR body:
    - run A at `ebe385e`;
    - run B at `eed4f91`;
    - the round-1 reproductions at `ebe385e`;
    - the round-2 reproductions at `eed4f91`, HTTPS with a private CA.
- **Decisions:** Deliberate calls 1–17 in the PR body. Since round 1, calls 13 and 14 changed:
  - call 14: refusal over carrying inherited trust into the unit, with #136 as the known limit;
  - call 13: the record goes last, and the crash between `useradd` and writing the record is manual recovery.
- **Waiting on the owner:** nothing from this entry.
- **Untested:** unprivileged LXC, non-Debian distributions, OpenSSL variables other than `SSL_CERT_FILE` and `SSL_CERT_DIR`, ACLs and supplementary groups (#136), two containers on one volume.
- **Next:**
  - #23 renders `… install --url … --enrol-token -` and the container command with the volume and `IPAM_ENROL_TOKEN`;
  - #24 publishes the image and the wheel;
  - #136, #138 and #140 are the installer's open follow-ups.

## 2026-11-24 — Claude Code (Opus 5.5), worker — #133: list_scan_findings checks a subnet_id by its id alone (PR #137)
- **Done:** [PR #137](https://github.com/DeusMaximus/TLG-IPAM/pull/137) (`133-findings-subnet-lookup`, head `ee19d28`) closes #133.
  - `findings.list_named` checks `subnet_id` with `select(Subnet.id)` and `errors.not_found`, not `subnets.get_subnet`. The refusal and the statement count are unchanged.
  - `test_mcp.py`: the plan test and the work test run with and without `subnet_id`. New `returned_bytes` and `test_no_read_of_a_call_returns_text_the_listing_does_not_use`: every statement of a call returns the same bytes with 10 or 1,000,000 bytes of subnet notes and section description, and 10 or 4,096 of host notes. The start-up test names `ix_reserved_ranges_subnet_excluded`.
  - With the wide read put back, the new test fails with `'bytes returned': (100, 1000090)`. The command and output are in the PR body.
  - `AGENTS.md` (only the parenthesis naming the cost tests, in "MCP tools are hand-curated"), brief §5.2 row, spec §5.8 and the `CALL_STATEMENTS` comment: what is counted is SQLite instructions (the rows read or passed over), not returned bytes or Python's processing.
  - Merged: `cc0cf6e`, squash, on the owner's go-ahead; the remote branch is deleted. CI green, Codex clean and Greptile clean (5/5) on `ee19d28`, the only head. The orchestrator ran 1,084 server tests on that head. No follow-ups.
- **Decisions:** PR Deliberate calls 1–3: a byte check rather than a column list; the plan test matches the lookup's SQL text; spec §5.8 gets one clarifying sentence. REST `list_findings` still uses `get_subnet` (owner-only, outside the issue).
- **State:** merged. 1,084 server and 924 agent tests pass. No schema change. Nothing in #132's or #22's areas touched.
- **Waiting on the owner:** nothing from this entry.
- **Next:** #135 and #130 both touch the "MCP tools are hand-curated" bullet's neighbours only; #137 changed one parenthesis in it, so a merge with either stays mechanical.
