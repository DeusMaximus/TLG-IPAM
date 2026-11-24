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
