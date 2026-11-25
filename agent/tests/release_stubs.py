"""Stand-ins for `gh`, `skopeo` and `sleep`, for `test_release_publish.py`, which puts
them first on the `PATH` of the release workflow's publish steps. They keep the GitHub
releases and the registry as JSON files in a state directory and record every call, so
a test reads what a step did and what it left. Nothing here opens a socket.

Standard library only: each stub runs as `python -I -S` with this directory on its path.

What they answer is taken from `gh` (2.105, `pkg/cmd/api` and `pkg/cmd/release`) and
skopeo, as far as the publish steps use them:

- `gh api repos/<repo>/releases?per_page=100&page=<n> --jq <RELEASES_JQ>` reads one page
  of the list of releases (REST `GET /repos/{owner}/{repo}/releases`), drafts included,
  the 100 releases from the `n`th hundred on; without `--paginate` gh makes the one
  request and follows no `Link` (`pkg/cmd/api/api.go`). The page is answered as gh's
  `processResponse` handles it: a `204` prints nothing and exits 0; a JSON error body
  with a status above 299 prints `gh: <message> (HTTP <status>)` and exits 1; any other
  body goes through the jq (`--jq` is evaluated by go-gh's gojq, which reads `env.TAG`
  from the environment), and a body that is not JSON, or that the jq stops on, exits
  1; a status above 299 that the jq did not stop on exits 1 as `gh: HTTP <status>`. A
  request that gets no answer exits 1 with Go's error. The state's `list_answers`
  replaces the answer to each request in turn (`null` for the real page; `json` says
  whether its `Content-Type` is JSON), so a test sends any of these; once it is used up
  every page is the real one. Simpler than gh: its JSON error path prints only the
  message, where gh can also copy the body to stdout and add scope or SSO advice, and
  `_jq` is not gojq for an `id` or `draft` that is not a number or a boolean. Taken
  from GitHub's documentation, not checked against GitHub: the list holds drafts for a
  token that can push ("only users with push access will receive listings for draft
  releases"), as this job's `contents: write` token can, and `page` counts pages of
  `per_page` releases, every page full but the last.
- `gh release download`, `upload` and `edit` find the release by its tag as gh's
  `FetchRelease` does (`pkg/cmd/release/shared/fetch.go`): it asks for the published
  release with the tag and for a draft with it as its pending tag at once, and takes
  whichever answers first. With neither it fails with `release not found`. When both
  exist, which one it returns is a race, and the stub refuses the call: the step is to
  make none then. `looked_up` records each lookup: the verb and the id of the release
  it returned.
- `gh release create <tag> <asset>` with an asset refuses a tag that a *published*
  release already has (`a release with the same tag name already exists`). A draft
  with the tag does not stop it: it makes a new draft, uploads, then publishes it.
- `gh release download --pattern <name>` fails with `no assets to download` when the
  release has no asset, and `no assets match the file pattern` when it has others.
- `gh release upload` without `--clobber` refuses a name the release already has
  (`asset under the same name already exists`), so an asset is never replaced.
- `skopeo inspect --raw` prints the manifest a tag names, byte for byte, and fails with
  the registry's `manifest unknown` (`name unknown` before a repository's first push)
  for a tag that is absent. `skopeo copy --all --preserve-digests` from an OCI archive
  makes the tag name the archive's index, so the registry serves its digest.

Any other argument list is refused (exit 2) and recorded in `refused.jsonl`, which every
run of a step checks is absent, so a step that changes how it calls them fails its tests
until the stubs, and these notes, follow, whatever the step does with the stub's stderr.
"""

import io
import json
import os
import sys
import tarfile
from pathlib import Path


class Stub:
    def __init__(self, state_dir: str):
        self.dir = Path(state_dir)

    def load(self, name: str) -> dict:
        return json.loads((self.dir / f"{name}.json").read_text())

    def save(self, name: str, data: dict) -> None:
        (self.dir / f"{name}.json").write_text(json.dumps(data, indent=1, sort_keys=True))

    def record(self, program: str, argv: list[str], stdin: str | None = None) -> None:
        with open(self.dir / "calls.jsonl", "a") as log:
            log.write(json.dumps({"program": program, "argv": argv, "stdin": stdin}) + "\n")


def fail(message: str, code: int = 1) -> int:
    print(message, file=sys.stderr)
    return code


def refuse(program: str, argv: list[str]) -> int:
    return fail(f"stub {program}: unexpected arguments {argv!r}", 2)


def take_flags(argv: list[str], valued: set[str], plain: set[str]) -> tuple[list[str], dict] | None:
    """Positional arguments and flags; None for a flag outside the two sets."""
    args, flags, i = [], {}, 0
    while i < len(argv):
        a = argv[i]
        if a in valued and i + 1 < len(argv):
            flags[a] = argv[i + 1]
            i += 2
        elif a in plain:
            flags[a] = True
            i += 1
        elif a.startswith("-"):
            return None
        else:
            args.append(a)
            i += 1
    return args, flags


# --- gh ---


# The one jq the step lists releases with: a first line `page <length>` for a page that
# is a JSON array, then a line `<id> <draft>` for each release on it whose tag is
# `env.TAG`.
RELEASES_JQ = (
    'if type == "array" then "page \\(length)", (.[] | select(.tag_name == env.TAG) | "\\(.id) \\(.draft)")'
    ' else error("not a list of releases") end'
)
PER_PAGE = 100


class Race(Exception):
    """A FetchRelease lookup with both a published release and a draft for the tag."""


def _find(state: dict, tag: str) -> dict | None:
    """As `gh`'s FetchRelease: the published release with the tag or a draft with it,
    whichever answers first, which is a race when both exist."""
    published = [r for r in state["releases"] if r["tag"] == tag and not r["draft"]]
    drafts = [r for r in state["releases"] if r["tag"] == tag and r["draft"]]
    if published and drafts:
        raise Race
    return (published or drafts or [None])[0]


def _release_json(release: dict) -> dict:
    """A release as `GET /repos/{owner}/{repo}/releases` lists it, in the fields the
    step's jq reads and a few it does not."""
    return {
        "id": release["id"],
        "tag_name": release["tag"],
        "name": release["title"],
        "draft": release["draft"],
        "prerelease": release["prerelease"],
        "assets": [{"name": name} for name in release["assets"]],
    }


def _jq(body: str, tag: str | None) -> tuple[list[str], str | None]:
    """`RELEASES_JQ` on one page's body: the lines it prints before it ends or stops,
    and gojq's error if it stops."""
    try:
        data = json.loads(body)
    except ValueError:
        return [], "invalid character '<' looking for beginning of value"
    if not isinstance(data, list):
        return [], "not a list of releases"
    lines = [f"page {len(data)}"]
    for release in data:
        if not isinstance(release, dict):
            return lines, f"expected an object but got: {type(release).__name__}"
        if release.get("tag_name") == tag:
            lines.append(f"{json.dumps(release.get('id'))} {json.dumps(release.get('draft'))}")
    return lines, None


def gh_api(stub: Stub, state: dict, argv: list[str]) -> int:
    """`gh api <one page of the releases list> --jq RELEASES_JQ`, answered as gh's
    `processResponse` answers it (module docstring)."""
    repo = os.environ.get("GITHUB_REPOSITORY")
    prefix = f"repos/{repo}/releases?per_page={PER_PAGE}&page="
    if (
        len(argv) != 4
        or argv[0] != "api"
        or not argv[1].startswith(prefix)
        or not argv[1].removeprefix(prefix).isdigit()
        or argv[2:] != ["--jq", RELEASES_JQ]
        or not os.environ.get("GH_TOKEN")
    ):
        return refuse("gh", argv)
    page = int(argv[1].removeprefix(prefix))
    if page < 1:
        return refuse("gh", argv)
    answer = state["list_answers"].pop(0) if state["list_answers"] else None
    state["list_requests"].append(page)
    stub.save("github", state)
    if answer is None:
        releases = [_release_json(r) for r in state["releases"]]
        answer = {"status": 200, "json": True, "body": json.dumps(releases[(page - 1) * PER_PAGE : page * PER_PAGE])}
    if "network" in answer:
        return fail(answer["network"])
    status = answer["status"]
    if status == 204:
        return 0
    if answer["json"] and status >= 400:
        error_body = json.loads(answer["body"])
        message = error_body.get("message") if isinstance(error_body, dict) else None
        if message:
            return fail(f"gh: {message} (HTTP {status})")
    lines, error = _jq(answer["body"], os.environ.get("TAG"))
    if lines:
        print("\n".join(lines), flush=True)
    if error:
        return fail(error)
    if status > 299:
        return fail(f"gh: HTTP {status}")
    return 0


def gh(stub: Stub, argv: list[str]) -> int:
    state = stub.load("github")
    repo = os.environ.get("GITHUB_REPOSITORY")
    if argv[:1] == ["api"]:
        return gh_api(stub, state, argv)
    if argv[:1] != ["release"] or len(argv) < 3:
        return refuse("gh", argv)
    verb, rest = argv[1], argv[2:]
    parsed = take_flags(
        rest,
        valued={"--repo", "--pattern", "--dir", "--title", "--notes-file"},
        plain={"--verify-tag", "--prerelease", "--draft=false"},
    )
    if parsed is None:
        return refuse("gh", argv)
    args, flags = parsed
    if flags.get("--repo") != repo or not os.environ.get("GH_TOKEN"):
        return refuse("gh", argv)
    if verb in state.get("fail", {}):
        return fail(state["fail"][verb])
    releases = state["releases"]
    tag = args[0]
    if verb in ("download", "upload", "edit"):
        try:
            release = _find(state, tag)
        except Race:
            return refuse("gh", argv)
        state["looked_up"].append({"verb": verb, "id": release and release["id"]})
        stub.save("github", state)

    def notes() -> str:
        return Path(flags["--notes-file"]).read_text()

    def asset(path: str) -> tuple[str, str]:
        return Path(path).name, Path(path).read_bytes().hex()

    if verb == "create" and len(args) == 2 and {"--repo", "--verify-tag", "--title", "--notes-file"} <= flags.keys() <= {
        "--repo", "--verify-tag", "--title", "--notes-file", "--prerelease"
    }:
        if tag not in state["tags"]:
            return fail(f"tag {tag} doesn't exist in the repo {repo}, aborting due to --verify-tag flag")
        if any(r["tag"] == tag and not r["draft"] for r in releases):
            return fail(f"a release with the same tag name already exists: {tag}")
        name, data = asset(args[1])
        releases.append({
            "id": max((r["id"] for r in releases), default=0) + 1, "tag": tag, "draft": False, "prerelease": "--prerelease" in flags,
            "title": flags["--title"], "notes": notes(), "assets": {name: data},
        })
    elif verb == "download" and args == [tag] and flags.keys() == {"--repo", "--pattern", "--dir"}:
        if release is None:
            return fail("release not found")
        if not release["assets"]:
            return fail("no assets to download")
        if flags["--pattern"] not in release["assets"]:
            return fail("no assets match the file pattern")
        out = Path(flags["--dir"])
        out.mkdir(parents=True, exist_ok=True)
        (out / flags["--pattern"]).write_bytes(bytes.fromhex(release["assets"][flags["--pattern"]]))
    elif verb == "upload" and len(args) == 2 and flags.keys() == {"--repo"}:
        if release is None:
            return fail("release not found")
        name, data = asset(args[1])
        if name in release["assets"]:
            return fail(f"asset under the same name already exists: [{name}]")
        release["assets"][name] = data
    elif verb == "edit" and args == [tag] and {"--repo", "--title", "--notes-file"} <= flags.keys() <= {
        "--repo", "--title", "--notes-file", "--draft=false", "--prerelease"
    }:
        if release is None:
            return fail("release not found")
        release.update(title=flags["--title"], notes=notes(), prerelease="--prerelease" in flags)
        if "--draft=false" in flags:
            release["draft"] = False
    else:
        return refuse("gh", argv)
    stub.save("github", state)
    return 0


# --- skopeo ---


def archive_index(path: str) -> bytes:
    """The image index an OCI archive holds: the blob its `index.json` names."""
    with tarfile.open(path) as tar:
        (entry,) = json.loads(tar.extractfile("index.json").read())["manifests"]
        return tar.extractfile(f"blobs/sha256/{entry['digest'].removeprefix('sha256:')}").read()


def skopeo(stub: Stub, argv: list[str]) -> int:
    state = stub.load("registry")
    image = os.environ["IMAGE"]
    host = image.split("/", 1)[0]
    if argv[:1] == ["login"]:
        if argv != ["login", host, "--username", os.environ.get("GITHUB_ACTOR"), "--password-stdin"]:
            return refuse("skopeo", argv)
        state["logins"].append(sys.stdin.read())
    elif argv == ["logout", host]:
        state["logouts"] += 1
    elif argv[:2] == ["inspect", "--raw"] and len(argv) == 3 and argv[2].startswith(f"docker://{image}:"):
        tag = argv[2].removeprefix(f"docker://{image}:")
        errors = state["inspect_errors"]
        if errors:
            message = errors.pop(0)
            stub.save("registry", state)
            return fail(f'time="2026-11-24T00:00:00Z" level=fatal msg="{message}"')
        if tag not in state["tags"]:
            unknown = "manifest unknown" if state["tags"] or state["repository_exists"] else "name unknown"
            return fail(
                f'time="2026-11-24T00:00:00Z" level=fatal msg="Error parsing image name \\"{argv[2]}\\": '
                f'reading manifest {tag} in {image}: {unknown}"'
            )
        sys.stdout.buffer.write(bytes.fromhex(state["tags"][tag]))
        return 0
    elif (
        argv[:3] == ["copy", "--all", "--preserve-digests"]
        and len(argv) == 5
        and argv[3].startswith("oci-archive:")
        and argv[4].startswith(f"docker://{image}:")
    ):
        if not state["logins"] or len(state["logins"]) <= state["logouts"]:
            return fail("unauthorized: authentication required")
        index = archive_index(argv[3].removeprefix("oci-archive:"))
        # a registry that rewrites what it is sent: the tag then names another digest
        if state["rewrite_on_push"]:
            index += b"\n"
        state["tags"][argv[4].removeprefix(f"docker://{image}:")] = index.hex()
    else:
        return refuse("skopeo", argv)
    stub.save("registry", state)
    return 0


def main(program: str, state_dir: str) -> int:
    stub = Stub(state_dir)
    argv = sys.argv[1:]
    stdin = None
    if program == "skopeo" and argv[:1] == ["login"]:
        stdin = sys.stdin.read()
        sys.stdin = io.StringIO(stdin)
    stub.record(program, argv, stdin)
    if program == "gh":
        code = gh(stub, argv)
    elif program == "skopeo":
        code = skopeo(stub, argv)
    elif program == "sleep" and len(argv) == 1 and argv[0].isdigit():
        code = 0
    else:
        code = refuse(program, argv)
    # A refusal is also recorded here, since a step can send the stub's stderr to a
    # file or to /dev/null and take the failure as an answer it expects.
    if code == 2:
        with open(stub.dir / "refused.jsonl", "a") as log:
            log.write(json.dumps({"program": program, "argv": argv}) + "\n")
    return code

