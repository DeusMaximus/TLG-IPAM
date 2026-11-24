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

## 2026-11-24 — Claude Code (Opus 5.5), worker — #144: a PTR or a negative SOA is read only when it answers the question (PR #146)
- **Done:**
  - [PR #146](https://github.com/DeusMaximus/TLG-IPAM/pull/146) (`144-ptr-soa-owner-check`, head `22967e0`) closes #144.
  - `probe.read_reply` reads one reply against its question:
    - `answered_name` follows the reply's own CNAME chain from the question to its end; a loop is no usable answer.
    - A PTR record counts only at that name; a PTR at any other owner makes the reply no usable answer.
    - `says_no_name(reply, name)` counts an SOA only for a zone containing that name; an SOA only for other zones is no usable answer.
    - No usable answer keeps the stored name and asks the next configured server, as for a referral.
  - No new query, no new import, no wire change (`PROTOCOL_VERSION` 2); nothing under `api/` changed.
  - Spec §5.2 gains "Which records of a reply are read". Agent README and brief §4.6 carry the operating note: a sole non-recursive server returning an unresolved alias warns on every scan; remedy is a recursive resolver or one serving the alias's target zone. `AGENTS.md`'s wire-contract bullet updated.
  - Tests: agent 1,252 → 1,319 (38-row `read_reply` table, 8 new loopback responder actions, pacing and route tests extended); server 1,129. With `main`'s `probe.py` under the new tests, exactly the 31 new cases fail.
  - Merged: `af8f49d`, squash, on the owner's go-ahead; the remote branch is deleted. CI green, Codex clean and Greptile 5/5 on `22967e0`, the only head. Briefed round (Codex, GPT-6.1 Sol): GO at `22967e0` with two P3s, no Deliberate call overruled; it measured 1,362 agent tests on the head merged with `main`. The orchestrator ran 1,319 agent and 1,129 server tests on the head. The two P3s are filed as #149 (a reply built from names whose hashes collide makes the CNAME walk quadratic; the spec does not state that a PTR at the chain's end wins over one elsewhere).
- **Decisions:** Deliberate calls 1–9 in the PR body. The main ones:
  - A PTR is read at the chain's end only.
  - An unrelated PTR spoils the whole reply, even beside the right SOA.
  - An unrelated SOA with an empty answer is no usable answer.
  - `NXDOMAIN` is read as before.
  - Two #100 table rows had a fixture SOA that did not contain the question; corrected, and the old fixture kept as `sibling-soa`.
- **State:** merged. No schema change.
- **Waiting on the owner:** nothing from this entry.
- **Untested:** any real network or resolver; DNAME; an `NXDOMAIN` carrying an unrelated SOA; DNSSEC and malformed-message fuzzing.
- **Next:**
  - #149, #20 and #93 are all in `probe.py`, so they run one at a time.

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
