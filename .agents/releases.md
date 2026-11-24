# Releases

What a release is, the owner's one-time setup before the first, and the checklist for each. The workflow is `.github/workflows/release.yml` (#24, spec §5.6). Releasing is the owner's: an agent prepares the version bump, the owner pushes the tag.

## What a release is

One version names the server and the scan agent: `VERSION` in `api/app/version.py` and `__version__` in `agent/src/tlg_ipam_agent/__init__.py`, the same string (`agent/tests/test_release.py` and `web/src/utils/install.test.js` fail otherwise). It is a PEP 440 public version in normal form, with no local `+…` part and no epoch. A server reports it at `GET /health`, its Agents page pins the install and update commands to it, and an agent that reports an older one is flagged "update available". Between releases, `main` carries the next release's `.dev0` (`0.3.0.dev0` after `0.2.0`).

A release is the tag `v<version>` on a commit of `main` at that version. Pushing it runs the workflow, which publishes the two artefacts the install commands name (`web/src/utils/install.js`):

| Artefact | Name | Where |
|----------|------|-------|
| Agent image, `linux/amd64` and `linux/arm64` | `ghcr.io/deusmaximus/tlg-ipam-agent:<version>` | GitHub container registry |
| Agent wheel | `tlg_ipam_agent-<version>-py3-none-any.whl` | the GitHub release `v<version>`, so at `https://github.com/DeusMaximus/TLG-IPAM/releases/download/v<version>/…` |

The server is not published as an image: it is built from the tag (`git checkout v<version> && docker compose up --build`), and then reports that version. A server built from `main` between releases reports a `.devN` version that names no artefact, so its install commands do not resolve. Use a release.

### What the workflow does

| Job | Runs on | Permission, and what for | Does |
|-----|---------|--------------------------|------|
| `build` | every trigger | `contents: read`, to check out the code | Checks that the tagged commit is on `main`, that the two versions are one release version and the tag is `v<version>` (`.github/scripts/release_check.py version`), and that the page's `install.releaseVersion` takes it. Builds the wheel with `python -m flit_core.wheel` from the hashed `agent/requirements-build.txt`, at the commit's time (`SOURCE_DATE_EPOCH`), so a rebuild is the same file. Checks its name, metadata and files (`release_check.py wheel`), then installs it with the agent's locked dependencies on Python 3.11 and 3.13 and runs `--version`. Builds the image for both platforms into one OCI archive, with no provenance or SBOM attestation. Then it takes each platform's image out of that archive and runs the start-up checks on it (`.github/scripts/check-agent-image.sh`, `--network none`, arm64 under QEMU). Records the wheel's SHA-256, the image index's digest and the archive's SHA-256, and uploads the wheel and the archive as the run's `release` artefact, the publish jobs' only input, configured to be kept 7 days ([the retry window](#the-retry-window-and-what-to-do-past-it)). |
| `publish-image` | a pushed tag | `packages: write`, to push to ghcr.io | Downloads the artefact and checks the archive's SHA-256. If `:<version>` already exists, it must already be this digest (a re-run), or the job fails. It pushes only when the registry answers that the tag is absent (`MANIFEST_UNKNOWN` or `NAME_UNKNOWN`). Any other failure to read the tag is tried three times, then stops the job with nothing pushed, so a registry error never lets a version be overwritten. Otherwise it pushes the archive as it is (`skopeo copy --all --preserve-digests`) and checks the registry now serves that digest. Checks out nothing. |
| `publish-release` | a pushed tag, after `publish-image` | `contents: write`, to create the release and attach the wheel | Checks the wheel's SHA-256 and creates the release `v<version>` (`--verify-tag`), with the wheel attached and the digest and checksum in its notes. A pre-release version (`rc`, `.dev`) is marked as a pre-release. On a re-run, a release that already holds the wheel must hold this one, or the job fails; one without it gets it attached, and a draft an interrupted run left behind is then published. Checks out nothing. |
| `verify` | a pushed tag, after `publish-release` | `contents: read`, to check out the image check script | Reads the image's index from the registry and pulls it with no credential, as an operator's host would, then runs the start-up checks. Downloads the wheel from the release URL and checks its SHA-256. Runs the systemd tab's `pip install <release wheel URL>` into a fresh venv on the runner's own Python. |

Every other permission is none (`permissions: {}` at the top). Every action is pinned to a commit, and the BuildKit and binfmt images the build runs are pinned to digests. A pull request that changes what goes into the artefacts (`agent/`, `api/app/version.py`, `install.js`, the workflow or its scripts) runs `build` alone, and so does a run by hand (Actions → Release → Run workflow), which has no input that turns publishing on. No `latest` tag is pushed, nothing goes to PyPI, and nothing is signed: the install commands name an exact version, and the release notes carry the digest and checksum to compare against.

## One-time setup, before the first tag

1. **Workflow permissions.** The repository's default token permission (Settings → Actions → General → Workflow permissions) can stay read-only: each job asks for what it uses. Nothing changes here unless the account forbids workflows from asking for write permissions.
2. **The container package.** If `ghcr.io/deusmaximus/tlg-ipam-agent` does not exist yet, the first `publish-image` creates it, connected to this repository by the image's `org.opencontainers.image.source`. If it exists already (pushed by hand earlier), open it (Profile → Packages → `tlg-ipam-agent` → Package settings → Manage Actions access) and give this repository the **Write** role. Without that, `publish-image` fails with `denied`.
3. **Make the package public, after the first publish.** A new container package is private, so `docker pull` without a login fails and `verify` fails with "cannot be read without a credential". In Package settings → Danger Zone → Change visibility, choose **Public**, then re-run `verify` from the workflow run (Re-run failed jobs). This is needed once: later versions inherit the package's visibility.
4. **Optional hardening:**
   - A tag ruleset (Settings → Rules → Rulesets → New tag ruleset, target `v*`) that restricts creation, update and deletion to the owner. It stops a version tag being pushed or moved by anyone else with write access.
   - Release immutability (Settings → General → Releases), so a published release's wheel and tag cannot be changed afterwards. `gh release create` attaches the wheel to a draft before publishing it, so the first run works with it. A re-run that had to attach the wheel to a release already published would be refused. Release the next version instead.

## Checklist for each release

1. **Pick the version**: `X.Y.Z`, or `X.Y.ZrcN` for a release candidate. It is a pre-release, and the install commands of a server at that version use it like any other.
2. **Read what changed since the last tag** (`git log v<last>..main`). If `PROTOCOL_VERSION` (`api/app/agent_protocol.py`) changed, every agent must be updated together with the server: an older agent's check-ins are refused with a protocol mismatch, and it is given no scans until it runs the new release. Say so in the release notes (edit them after the run).
3. **Bump PR.** A PR that sets `VERSION` in `api/app/version.py` and `__version__` in `agent/src/tlg_ipam_agent/__init__.py` to `X.Y.Z`, and nothing else. Its Release workflow run builds and checks both artefacts at `X.Y.Z` (the run on the PR publishes nothing). Merge it with CI green.
4. **Tag the merge commit** on `main`, and push the tag:

   ```sh
   git fetch origin && git checkout --detach origin/main
   git log -1 --oneline          # the bump's merge commit
   git tag -a vX.Y.Z -m vX.Y.Z
   git push origin vX.Y.Z
   ```

   A tag is pushed once. Never move or reuse one: an operator who installed `X.Y.Z` has those artefacts.
5. **Watch the run** (Actions → Release): `build`, `publish-image`, `publish-release`, `verify`. On the first release, `verify` stops at the package's visibility (setup step 3).

   **Once, on the first release, after `verify` has passed:** measure the retry before one is needed. Open the run's `publish-image` job and re-run that job alone (Re-run this job). GitHub then re-runs the jobs that depend on it, `publish-release` and `verify`, and leaves `build` as it was. Nothing in it can change the version. Check:
   - `publish-image` downloaded the artefact the first attempt uploaded, and logs `is already this build (sha256:…)` with the digest `build` recorded (its outputs, and the release notes);
   - `publish-release` changed nothing (no upload and no edit in its log), and `verify` passes.

   Record the result in the hand-off entry. If the download fails, a re-run of a failed publish job cannot work either: the retry window below is then nil, and a failed publish is handled as past the window.
6. **See it from an operator's side.** The release page lists the wheel, and its notes give the image digest. Build the server from the tag and open Agents → Add agent: both tabs' commands name `X.Y.Z`, and the image and wheel URL they show are the published ones. An agent of the previous release is flagged "update available".
7. **Open the next cycle.** A PR that sets both versions to the next `.dev0` (`X.Y.(Z+1).dev0`, or `X.(Y+1).0.dev0`).
8. **Hand-off entry**: the version, the run, the image digest, anything that failed and how it was fixed.

## When a step fails

- **`build`:** nothing was published. Fix the cause on `main` with a PR. Then delete the tag (`git push --delete origin vX.Y.Z`, and `git tag -d vX.Y.Z` locally) and tag the fixed commit with the same version. This is the one case where a tag is reused, since nothing was published under it.
- **`publish-image`:** `denied` means setup step 2. "Could not tell whether … exists, so nothing was pushed" means the registry failed to answer three times: re-run the failed jobs. Anything else: re-run the failed jobs of the same run. The build is not repeated, and the archive is pushed by its digest. All of this within the retry window below.
- **`publish-release`:** the image is already published. Re-run the failed jobs, within the retry window below. Only `publish-release`, and `verify` after it, run again: `publish-image` succeeded and is not re-run. `publish-release` finishes a release it had left as a draft (the wheel checked or attached, then published). If the Releases page then also shows a draft for `vX.Y.Z` beside the published release (a failed lookup makes `gh` create a second release beside the draft an interrupted run left), delete the draft, telling it by its Draft label, since its title is the same. The published release is the one the wheel's URL names. While both exist, `gh` can answer a lookup with either, so a re-run can also fail at the draft; it never replaces a wheel or unpublishes the release (`agent/tests/test_release_publish.py`). Delete the draft and re-run.
- **`verify`:** on the first release, setup step 3. Otherwise compare what it fetched with the digests the build recorded (in the job's log and the release notes) before doing anything else. `verify` reads only the registry and the release, not the build's artefact, so it can be re-run at any time GitHub allows a re-run (30 days).
- **A release that is wrong once published:** do not replace its artefacts (the workflow refuses to) or move its tag. Release the next patch version, and say in the bad release's notes which version replaces it.

### The retry window, and what to do past it

The `build` job hands the publish jobs one thing: the run's `release` artefact (the wheel and the image archive), with the digests it recorded as job outputs. It is configured to be kept **7 days** from the build's upload (`retention-days: 7`). It can be gone sooner: deleted by hand, or capped lower by the repository's artifact retention setting. The run page's Artifacts section lists it while it remains. That is the window in which a failed publish can be retried. It was kept at 7 days rather than lengthened: a release is watched as it runs (checklist step 5), and a week is ample to fix a permission or a package setting and retry.

There are two ways to retry, and they are not the same:

- **Re-run failed jobs** (the run page → Re-run jobs → Re-run failed jobs) runs the failed job and the jobs after it, and nothing before. `build` is not run again. GitHub documents that the re-run jobs get its outputs. They are expected to download its artefact too, and so to publish the same files under the same digests. **That a re-run attempt can download an artefact an earlier attempt uploaded is an assumption, not yet measured.** GitHub's documentation does not say so; checklist step 5 measures it on the first release. This is the retry. It works while the artefact remains available (configured for 7 days), and GitHub allows a re-run for 30 days after the run started, so the artefact is the limit.
- **Re-run all jobs** runs `build` again from the tag. The wheel is built at the commit's time and comes out the same file, but the image does not: it records when it was built, and the base image `--pull` fetches can have moved, so its digest differs. Once the image is published, `publish-image` then refuses the version ("already published as …, not this build's …") and nothing more is published. Use it only when nothing was published.

Whether the image was published: `publish-image`'s log ends with `pushed ghcr.io/deusmaximus/tlg-ipam-agent:X.Y.Z@sha256:…`, and the package's page (Profile → Packages → `tlg-ipam-agent`) lists `X.Y.Z`. While the package is still private, a read of the tag without a login fails whether or not it exists.

Past the window (the artefact is gone), and after a whole re-run that `publish-image` refused:

- **If the image was not published,** nothing was: treat it as a failed `build`. Delete the tag, push it again on the same commit, and the new run builds and publishes from the start.
- **If the image was published and the release was not,** the version is spent. Never rebuild it to finish it: a rebuild is another image under a name operators may already have pulled, and the workflow refuses to push it. Leave the published image as it is (do not delete it: an operator may run it), and release the next patch version through the checklist from step 1. A server built from `vX.Y.Z` shows a wheel URL that does not resolve, so build servers from the new tag. Record it in the hand-off entry.
