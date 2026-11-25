"""What the release workflow's publish steps do (#147, spec §5.6), run as the workflow
holds them.

`test_release.py` holds the workflow's shape. These tests take the one `run` script of
`publish-image` and of `publish-release` out of `.github/workflows/release.yml`, as
written, and run it the way the runner does (`bash --noprofile --norc -eo pipefail`),
in a temporary directory laid out as `actions/download-artifact` leaves the build's
`release` artefact, with each `${{ }}` of the step's `env` given a value. The `PATH`
holds stand-ins for `gh`, `skopeo` and `sleep` (`release_stubs.py`, which says what
of the real programs they copy) and the four text tools the scripts use, and nothing
else. Each test then reads what the step wrote and what it left: the registry's tags,
the releases and their wheels, and every call made.

A DEBUG trap (`TRACE`, through `BASH_ENV`) records the commands bash runs in the step,
as it is about to run each, and `Publish.run` fails on a recorded command whose word is
not one of `SHELL_WORDS`, the stubs, the tools or a function the script defines
(`Publish.unapproved`), whatever the step did with that command's stderr or exit
status. `test_every_line_of_each_publish_script_runs_in_some_test` makes each line of
either script that holds a command run in some test, so a command added on any line is
run by one.

This is a tripwire for changes made in good faith (a new command, a changed argument,
a branch no test takes): it makes one fail a test until someone looks. It is not a
sandbox, and it does not hold against a script written to get past it, which is what
review of the workflow's diff is for. It stops nothing: a command is judged by its
text after the step has run (a quoted or variable-built word, `"/bin/c"` or `$cmd`, is
refused as unknown), and a changed script is run as it is, so the tests' own runs of
one run what it names. It does not see: a step that writes to the trace file
(truncating it erases what came before); any redirection's target
(`> /dev/"tcp"/host/port` opens a socket); what an `EXIT` trap's handler runs; and a
command in a part of a line no test reaches (`a || b` where `a` always succeeds in
the tests).
"""

import copy
import hashlib
import io
import itertools
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"
STUBS = Path(__file__).resolve().parent / "release_stubs.py"

VERSION = "0.2.0"
REPO = "DeusMaximus/TLG-IPAM"
TOKEN = "ghs_" + "t" * 36
SHA = "a" * 40
# the programs other than the stubs that the publish scripts run
TOOLS = ("cut", "grep", "head", "sha256sum")
# The commands a step may run: these shell builtins and keywords, the stubs, the tools,
# and the functions the script itself defines.
SHELL_WORDS = {"echo", "printf", "test", "[", "[[", "((", "exit", "break", "continue", "true", "false", ":",
               "for", "case", "while", "until"}
STUB_NAMES = ("gh", "skopeo", "sleep")
# Sourced by bash before the script (`BASH_ENV`): records each command as bash is about
# to run it, with its line, NUL-separated, into the file `STEP_TRACE` names. The trap
# function and the two names are read-only, and `trap - DEBUG` and `set +T` are
# themselves recorded, and refused, before they take effect. The file is not protected:
# the step can write to it. `functrace` carries the trap into functions and `$( )`.
TRACE = r"""readonly PATH STEP_TRACE
set -o functrace
__step_trace() { printf '%s\t%s\0' "$1" "$BASH_COMMAND" >> "$STEP_TRACE"; }
readonly -f __step_trace
trap '__step_trace "$LINENO"' DEBUG
"""
# what the runner sets that the scripts read
RUNNER = {"GITHUB_ACTOR": "github-actions[bot]", "GITHUB_REPOSITORY": REPO, "GITHUB_SHA": SHA}


@pytest.fixture(scope="module")
def workflow():
    return yaml.safe_load(WORKFLOW.read_text())


def _digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _image_index(note: str = "") -> bytes:
    """An OCI image index naming two platforms, as the build's archive holds one."""
    manifests = [
        {
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "digest": _digest(f"{arch}{note}".encode()),
            "size": 1000,
            "platform": {"os": "linux", "architecture": arch},
        }
        for arch in ("amd64", "arm64")
    ]
    return json.dumps({"schemaVersion": 2, "manifests": manifests}).encode()


class Publish:
    """One publish job's `run` script, ready to run against a registry and releases
    the test sets up. `run()` runs it once; `github`, `registry` and `calls` are then
    what it left."""

    def __init__(self, tmp_path: Path, workflow: dict, job: str, version: str = VERSION, script: str | None = None):
        self.job = workflow["jobs"][job]
        (step,) = [s for s in self.job["steps"] if "run" in s]
        assert workflow["defaults"]["run"]["shell"] == "bash"
        self.script, self.step_env = step["run"] if script is None else script, step.get("env", {})
        self.workflow_env = workflow["env"]
        self.version = version
        self.tag = f"v{version}"
        self.work, self.bin, self.state = (tmp_path / d for d in ("work", "bin", "state"))
        for d in (self.work, self.bin, self.state):
            d.mkdir(parents=True)

        # the build's `release` artefact, at the paths `upload-artifact` gave it
        self.wheel_name = f"tlg_ipam_agent-{version}-py3-none-any.whl"
        self.wheel = b"the wheel this run built"
        (self.work / "dist").mkdir()
        (self.work / "dist" / self.wheel_name).write_bytes(self.wheel)
        self.index = _image_index()
        self.digest = _digest(self.index)
        (self.work / "dist-image").mkdir()
        archive = self.work / "dist-image" / "agent-image.tar"
        with tarfile.open(archive, "w") as tar:
            layout = {
                "oci-layout": b'{"imageLayoutVersion": "1.0.0"}',
                "index.json": json.dumps({
                    "schemaVersion": 2,
                    "manifests": [{
                        "mediaType": "application/vnd.oci.image.index.v1+json",
                        "digest": self.digest,
                        "size": len(self.index),
                    }],
                }).encode(),
                f"blobs/sha256/{self.digest.removeprefix('sha256:')}": self.index,
            }
            for name, data in layout.items():
                info = tarfile.TarInfo(name)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
        self.outputs = {
            "version": version,
            "prerelease": "false",
            "wheel_sha256": hashlib.sha256(self.wheel).hexdigest(),
            "image_digest": self.digest,
            "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        }

        # what GitHub and the registry hold before the step runs
        self.github = {
            "tags": [self.tag], "releases": [], "fail": {}, "list_answers": [], "list_requests": 0, "looked_up": [],
        }
        self.registry = {
            "tags": {},
            "repository_exists": True,
            "inspect_errors": [],
            "rewrite_on_push": False,
            "logins": [],
            "logouts": 0,
        }
        self.calls: list[dict] = []
        # (line, command) for each command the trap recorded the step about to run
        self.trace: list[tuple[int, str]] = []

        for program in STUB_NAMES:
            stub = self.bin / program
            stub.write_text(
                f"#!{sys.executable} -IS\n"
                "import sys\n"
                f"sys.path.insert(0, {str(STUBS.parent)!r})\n"
                "import release_stubs\n"
                f"sys.exit(release_stubs.main({program!r}, {str(self.state)!r}))\n"
            )
            stub.chmod(0o755)
        for tool in TOOLS:
            (self.bin / tool).symlink_to(shutil.which(tool))

    def release(self, *, draft: bool, wheel: bytes | None = None, tag: str | None = None) -> dict:
        """A release the step finds; `wheel`, if given, is attached under its name."""
        release = {
            "id": len(self.github["releases"]) + 1,
            "tag": tag or self.tag,
            "draft": draft,
            "prerelease": False,
            "title": tag or self.tag,
            "notes": "left by an earlier run",
            "assets": {} if wheel is None else {self.wheel_name: wheel.hex()},
        }
        self.github["releases"].append(release)
        return release

    def env(self) -> dict:
        def value(expression: str) -> str:
            match = re.fullmatch(r"\$\{\{ (needs\.build\.outputs\.(\w+)|secrets\.GITHUB_TOKEN) \}\}", expression)
            assert match, f"the test gives no value for {expression}"
            return TOKEN if match.group(2) is None else self.outputs[match.group(2)]

        used = set(re.findall(r"\$\{?(GITHUB_\w+)", self.script))
        assert used <= RUNNER.keys(), used - RUNNER.keys()
        return {
            "PATH": str(self.bin),
            "BASH_ENV": str(self.state / "trace.sh"),
            "STEP_TRACE": str(self.state / "trace"),
            **self.workflow_env,
            **RUNNER,
            **{name: value(expression) for name, expression in self.step_env.items()},
        }

    def execute(self) -> subprocess.CompletedProcess:
        """Runs the step once, and reads what it left; `run` also checks what it ran."""
        (self.state / "trace.sh").write_text(TRACE)
        (self.state / "github.json").write_text(json.dumps(self.github))
        (self.state / "registry.json").write_text(json.dumps(self.registry))
        script = self.state / "step.sh"
        script.write_text(self.script)
        result = subprocess.run(
            [shutil.which("bash"), "--noprofile", "--norc", "-eo", "pipefail", str(script)],
            cwd=self.work,
            env=self.env(),
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.github = json.loads((self.state / "github.json").read_text())
        self.registry = json.loads((self.state / "registry.json").read_text())
        log = self.state / "calls.jsonl"
        self.calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
        trace = self.state / "trace"
        records = trace.read_text().split("\0")[:-1] if trace.exists() else []
        self.trace = [(int(line), command) for line, command in (r.split("\t", 1) for r in records)]
        return result

    def unapproved(self) -> list[str]:
        """Each recorded command whose word is not one the step may run. It is found
        after the step has run: nothing here stops a command."""
        allowed = SHELL_WORDS | set(STUB_NAMES) | set(TOOLS) | _functions(self.script)
        return [command for _, command in self.trace if (_command_word(command) or ":") not in allowed]

    def refused(self) -> list[dict]:
        """Each call a stub refused, even one whose stderr the step discarded."""
        log = self.state / "refused.jsonl"
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def run(self) -> subprocess.CompletedProcess:
        """Runs the step and fails on a recorded command it may not run, a call a stub
        refused, or a program it could not find: the tripwire (module docstring)."""
        result = self.execute()
        assert self.trace, "the trap recorded nothing"
        assert self.unapproved() == [], self.unapproved()
        assert self.refused() == [], self.refused()
        assert "command not found" not in result.stderr, result.stderr
        return result

    def argvs(self, program: str, verb: str | None = None) -> list[list[str]]:
        return [
            c["argv"] for c in self.calls
            if c["program"] == program and (verb is None or c["argv"][:1] == [verb] or c["argv"][1:2] == [verb])
        ]


_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(\[[^]]*\])?\+?=")
_REDIRECTION = re.compile(r"\d*(<<<|<<|>>|<>|>\||>&|<&|&>>|&>|>|<)")


def _words(command: str) -> list[str]:
    """`command` split into words as bash would before expansion: quotes, `$( )`,
    `${ }`, backquotes and `( )` keep their spaces."""
    words, word, nest, i = [], "", [], 0
    while i < len(command):
        c = command[i]
        top = nest[-1] if nest else None
        if top == "'":
            if c == "'":
                nest.pop()
        elif c == "\\":
            word += command[i : i + 2]
            i += 2
            continue
        elif top == '"':
            if c == '"':
                nest.pop()
            elif command.startswith("$(", i) or command.startswith("${", i):
                nest.append(command[i + 1])
                word += command[i : i + 2]
                i += 2
                continue
            elif c == "`":
                nest.append("`")
        elif top == "`":
            if c == "`":
                nest.pop()
        elif c in "\"'`(":
            nest.append(c)
        elif command.startswith("${", i):
            nest.append("{")
            word += "${"
            i += 2
            continue
        elif (c == ")" and top == "(") or (c == "}" and top == "{"):
            nest.pop()
        elif c.isspace() and top is None:
            if word:
                words.append(word)
            word = ""
            i += 1
            continue
        word += c
        i += 1
    return words + [word] if word else words


def _command_word(command: str) -> str | None:
    """The word bash runs `command` as, past its assignments and redirections; None for
    a command that only assigns."""
    words = iter(_words(command))
    for word in words:
        if _ASSIGNMENT.match(word):
            continue
        if _REDIRECTION.fullmatch(word):
            next(words, None)
            continue
        if _REDIRECTION.match(word):
            continue
        return word
    return None


def _functions(script: str) -> set[str]:
    return set(re.findall(r"^\s*([A-Za-z_]\w*)\s*\(\)", script, re.M))


def test_reading_a_command_word():
    assert _command_word('have=$(digest_of manifest.json)') is None
    assert _command_word('draft=$(gh release view "$tag" --repo "$GITHUB_REPOSITORY" 2> /dev/null)') is None
    assert _command_word('TAG=$tag gh api --paginate "repos/$GITHUB_REPOSITORY/releases?per_page=100"') == "gh"
    assert _command_word('flags=(--prerelease)') is None
    assert _command_word('ref="docker://$IMAGE:$VERSION"') is None
    assert _command_word('x="a b" y=$(c d) skopeo copy a b') == "skopeo"
    assert _command_word('2> err grep -qiE \'manifest unknown|name unknown\' inspect.err') == "grep"
    assert _command_word('>out echo "$(curl x)"') == "echo"
    assert _command_word('"$cmd" args') == '"$cmd"'
    assert _command_word('/usr/bin/curl x') == "/usr/bin/curl"
    assert _command_word('echo "a \\" b" c') == "echo"
    assert _words('echo "x $(printf "%s" "a b") y" `z w` ${v:-a b}') == [
        "echo", '"x $(printf "%s" "a b") y"', "`z w`", "${v:-a b}"
    ]


def test_the_artefact_is_laid_out_where_the_publish_scripts_read_it(workflow):
    """The fixture's layout is the build's upload: the two paths' common root is the
    workspace, so `download-artifact` puts them back at the same relative paths."""
    (upload,) = [s for s in workflow["jobs"]["build"]["steps"] if s.get("uses", "").startswith("actions/upload-artifact@")]
    assert upload["with"]["path"].split() == ["dist/*.whl", "dist-image/agent-image.tar"]
    for job in ("publish-image", "publish-release"):
        (download,) = [s for s in workflow["jobs"][job]["steps"] if "uses" in s]
        assert download["with"] == {"name": upload["with"]["name"]}


# --- publish-image ---


@pytest.fixture
def image(tmp_path, workflow):
    return Publish(tmp_path, workflow, "publish-image")


def _pushes(publish: Publish) -> list[list[str]]:
    return publish.argvs("skopeo", "copy")


@pytest.mark.parametrize("first_push", [False, True], ids=["manifest-unknown", "name-unknown"])
def test_a_tag_the_registry_says_is_absent_is_pushed_once_as_this_build(image, first_push):
    image.registry["repository_exists"] = not first_push
    result = image.run()
    assert result.returncode == 0, result.stdout + result.stderr
    assert _pushes(image) == [
        ["copy", "--all", "--preserve-digests", "oci-archive:dist-image/agent-image.tar",
         f"docker://{image.workflow_env['IMAGE']}:{VERSION}"]
    ]
    assert image.registry["tags"] == {VERSION: image.index.hex()}
    assert f"pushed {image.workflow_env['IMAGE']}:{VERSION}@{image.digest}" in result.stdout
    # the token goes in on standard input, never on a command line
    assert image.registry["logins"] == [TOKEN]
    assert not any(TOKEN in " ".join(c["argv"]) for c in image.calls)
    assert image.registry["logouts"] == 1


def test_a_tag_that_is_already_this_build_is_left_as_it_is(image):
    image.registry["tags"] = {VERSION: image.index.hex()}
    result = image.run()
    assert result.returncode == 0, result.stdout + result.stderr
    assert _pushes(image) == []
    assert image.registry["tags"] == {VERSION: image.index.hex()}
    assert f"is already this build ({image.digest})" in result.stdout


def test_a_version_published_with_other_content_is_never_pushed_over(image):
    other = _image_index(note="another build")
    image.registry["tags"] = {VERSION: other.hex()}
    result = image.run()
    assert result.returncode == 1
    assert _pushes(image) == []
    assert image.registry["tags"] == {VERSION: other.hex()}
    assert f"is already published as {_digest(other)}, not this build's {image.digest}" in result.stdout


# What a registry or the network can answer that is not "absent". `unknown:` is the
# distribution spec's UNKNOWN error code, which shares a word with the two that are.
REGISTRY_ERRORS = [
    "reading manifest 0.2.0 in ghcr.io/deusmaximus/tlg-ipam-agent: received unexpected HTTP status: 502 Bad Gateway",
    "reading manifest 0.2.0 in ghcr.io/deusmaximus/tlg-ipam-agent: denied: permission_denied: The token provided does not match expected scopes.",
    "reading manifest 0.2.0 in ghcr.io/deusmaximus/tlg-ipam-agent: unauthorized: authentication required",
    "reading manifest 0.2.0 in ghcr.io/deusmaximus/tlg-ipam-agent: toomanyrequests: retry-after: 30s",
    "reading manifest 0.2.0 in ghcr.io/deusmaximus/tlg-ipam-agent: unknown: unknown error",
    "pinging container registry ghcr.io: Get \\\"https://ghcr.io/v2/\\\": dial tcp: lookup ghcr.io: i/o timeout",
]


@pytest.mark.parametrize(
    "error", REGISTRY_ERRORS, ids=["502", "denied", "unauthorized", "toomanyrequests", "unknown", "timeout"]
)
@pytest.mark.parametrize("published", [False, True], ids=["absent", "published"])
def test_a_registry_that_cannot_be_read_is_never_taken_for_absent(image, error, published):
    """Three failed reads stop the job with nothing pushed, whatever the tag holds."""
    if published:
        image.registry["tags"] = {VERSION: _image_index(note="another build").hex()}
    before = dict(image.registry["tags"])
    image.registry["inspect_errors"] = [error] * 3
    result = image.run()
    assert result.returncode == 1
    assert _pushes(image) == []
    assert image.registry["tags"] == before
    assert len(image.argvs("skopeo", "inspect")) == 3
    assert f"could not tell whether {image.workflow_env['IMAGE']}:{VERSION} exists, so nothing was pushed" in result.stdout


def test_a_read_that_fails_and_then_answers_is_judged_by_the_answer(tmp_path, workflow):
    absent = Publish(tmp_path / "absent", workflow, "publish-image")
    absent.registry["inspect_errors"] = REGISTRY_ERRORS[:2]
    assert absent.run().returncode == 0
    assert len(_pushes(absent)) == 1 and absent.registry["tags"] == {VERSION: absent.index.hex()}

    other = _image_index(note="another build")
    published = Publish(tmp_path / "published", workflow, "publish-image")
    published.registry["tags"] = {VERSION: other.hex()}
    published.registry["inspect_errors"] = REGISTRY_ERRORS[:2]
    assert published.run().returncode == 1
    assert _pushes(published) == [] and published.registry["tags"] == {VERSION: other.hex()}


def test_an_archive_that_is_not_the_one_the_build_checked_is_not_pushed(image):
    image.outputs["archive_sha256"] = "0" * 64
    result = image.run()
    assert result.returncode == 1
    assert image.calls == []  # not even a login
    assert image.registry["tags"] == {}


def test_a_push_the_registry_does_not_serve_as_this_digest_fails_the_job(image):
    image.registry["rewrite_on_push"] = True
    result = image.run()
    assert result.returncode == 1
    assert len(_pushes(image)) == 1
    assert "pushed " not in result.stdout


# --- publish-release ---


@pytest.fixture
def release(tmp_path, workflow):
    return Publish(tmp_path, workflow, "publish-release")


def _writes(publish: Publish) -> list[list[str]]:
    return [a for verb in ("create", "upload", "edit") for a in publish.argvs("gh", verb)]


def _published_with_this_wheel(publish: Publish, release: dict) -> None:
    assert release["draft"] is False
    assert release["assets"] == {publish.wheel_name: publish.wheel.hex()}
    assert release["title"] == publish.tag
    for fact in (publish.digest, publish.outputs["wheel_sha256"], f"{publish.workflow_env['IMAGE']}:{publish.version}", SHA):
        assert fact in release["notes"], fact


@pytest.mark.parametrize("version, prerelease", [(VERSION, False), ("0.2.0rc1", True)])
def test_a_new_release_is_created_published_with_the_wheel(tmp_path, workflow, version, prerelease):
    publish = Publish(tmp_path, workflow, "publish-release", version=version)
    publish.outputs["prerelease"] = "true" if prerelease else "false"
    result = publish.run()
    assert result.returncode == 0, result.stdout + result.stderr
    (made,) = publish.github["releases"]
    _published_with_this_wheel(publish, made)
    assert made["prerelease"] is prerelease
    assert "--verify-tag" in publish.argvs("gh", "create")[0]


@pytest.mark.parametrize("attached", [True, False], ids=["wheel-attached", "wheel-missing"])
def test_a_draft_an_interrupted_run_left_is_finished_and_published(release, attached):
    release.release(draft=True, wheel=release.wheel if attached else None)
    result = release.run()
    assert result.returncode == 0, result.stdout + result.stderr
    (finished,) = release.github["releases"]
    _published_with_this_wheel(release, finished)
    assert len(release.argvs("gh", "upload")) == (0 if attached else 1)
    assert release.argvs("gh", "create") == []


def test_a_draft_holding_another_wheel_is_left_as_it_was(release):
    release.release(draft=True, wheel=b"a wheel some other build made")
    before = json.loads(json.dumps(release.github["releases"]))
    result = release.run()
    assert result.returncode == 1
    assert f"{release.tag} already holds another {release.wheel_name}" in result.stdout
    assert release.github["releases"] == before
    assert _writes(release) == []


@pytest.mark.parametrize("attached", ["same", "missing", "other"])
def test_a_release_already_published(release, attached):
    """A re-run after the release was published: the same wheel is left alone, a
    missing one attached, another one refused."""
    wheel = {"same": release.wheel, "missing": None, "other": b"a wheel some other build made"}[attached]
    existing = release.release(draft=False, wheel=wheel)
    before = json.loads(json.dumps(existing))
    result = release.run()
    (after,) = release.github["releases"]
    if attached == "other":
        assert result.returncode == 1
        assert after == before
        assert _writes(release) == []
        return
    assert result.returncode == 0, result.stdout + result.stderr
    assert after["draft"] is False
    assert after["assets"] == {release.wheel_name: release.wheel.hex()}
    assert _writes(release) == ([] if attached == "same" else [release.argvs("gh", "upload")[0]])


@pytest.mark.parametrize("draft", [True, False], ids=["draft", "published"])
@pytest.mark.parametrize("attached", ["same", "other"])
def test_a_failed_download_never_replaces_an_attached_wheel(release, draft, attached):
    """A download that fails is taken for a missing wheel, and the upload that follows
    is refused for a name the release already has: the job fails and nothing is
    replaced or published."""
    wheel = release.wheel if attached == "same" else b"a wheel some other build made"
    release.release(draft=draft, wheel=wheel)
    before = json.loads(json.dumps(release.github["releases"]))
    release.github["fail"]["download"] = "HTTP 502: Bad Gateway (https://api.github.com/repos/x/y/releases/assets/1)"
    result = release.run()
    assert result.returncode == 1
    assert release.github["releases"] == before
    assert release.argvs("gh", "edit") == [] and release.argvs("gh", "create") == []


def test_a_failed_download_of_a_draft_without_the_wheel_attaches_it_and_publishes(release):
    release.release(draft=True)
    release.github["fail"]["download"] = "HTTP 502: Bad Gateway"
    assert release.run().returncode == 0
    (finished,) = release.github["releases"]
    _published_with_this_wheel(release, finished)


def _error(status: int, message: str) -> dict:
    """A page GitHub answers with a JSON error body, as its REST API does."""
    return {"status": status, "json": True, "body": json.dumps({"message": message, "status": str(status)})}


# What a read of the list of releases can get that is not a list (#159), one answer per
# page request: GitHub's own refusals, a body that is not JSON (a proxy's or a load
# balancer's page), no answer at all, and success statuses that carry no list.
LIST_FAILURES = {
    "401": _error(401, "Bad credentials"),
    "403-permission": _error(403, "Resource not accessible by integration"),
    "403-rate-limit": _error(403, "API rate limit exceeded for installation ID 1."),
    "404": _error(404, "Not Found"),
    "429": _error(429, "You have exceeded a secondary rate limit. Please wait a few minutes before you try again."),
    "500": _error(500, "Server Error"),
    "502-html": {"status": 502, "json": False, "body": "<html><body><h1>502 Bad Gateway</h1></body></html>"},
    "503-html": {"status": 503, "json": False, "body": "<html><body>No server is available</body></html>"},
    "504": _error(504, "We couldn't respond to your request in time. Sorry about that."),
    "no-answer": {"network": 'Get "https://api.github.com/repos/DeusMaximus/TLG-IPAM/releases?per_page=100": '
                             "dial tcp: lookup api.github.com: i/o timeout"},
    "200-html": {"status": 200, "json": False, "body": "<html><body>Sign in to the network</body></html>"},
    "200-object": {"status": 200, "json": True, "body": json.dumps({"message": "not a list"})},
    "204": {"status": 204},
}
UNREAD = "could not tell whether v{version} has a release, so nothing was created, uploaded or edited; re-run the failed jobs"
OTHER = b"a wheel some other build made"


def _seed(publish: Publish, held: str) -> None:
    """What the tag holds before the step: no release, a draft an interrupted run left,
    or a published release with this build's wheel."""
    if held == "draft":
        publish.release(draft=True)
    elif held == "published":
        publish.release(draft=False, wheel=publish.wheel)


def _nothing_written(publish: Publish, before: list[dict]) -> None:
    assert _writes(publish) == []
    assert publish.github["releases"] == before
    # nor read through gh's own lookup, which the step makes only once it knows
    assert publish.argvs("gh", "download") == []


@pytest.mark.parametrize("failure", LIST_FAILURES.values(), ids=LIST_FAILURES.keys())
@pytest.mark.parametrize("held", ["none", "draft"])
def test_a_list_that_cannot_be_read_publishes_nothing(release, failure, held):
    """Three reads that do not say what the tag holds stop the job: no create, upload
    or edit, and the error says to re-run the failed jobs. Before #159 both of these
    went on to `gh release create`, beside the draft in the second."""
    _seed(release, held)
    before = copy.deepcopy(release.github["releases"])
    release.github["list_answers"] = [failure] * 3
    result = release.run()
    assert result.returncode == 1
    _nothing_written(release, before)
    assert len(release.argvs("gh", "api")) == 3 and release.github["list_requests"] == 3
    assert UNREAD.format(version=VERSION) in result.stdout
    assert result.stdout.count("::warning::listing the releases of") == 3
    assert [c["argv"] for c in release.calls if c["program"] == "sleep"] == [["15"], ["30"], ["45"]]


def _many(publish: Publish, count: int) -> None:
    """`count` releases of other versions, listed before the tag's own."""
    for n in range(count):
        publish.release(draft=False, tag=f"v0.1.{n}")


@pytest.mark.parametrize("where", ["first-page", "second-page", "nowhere"])
def test_a_list_whose_later_page_fails_is_not_read(tmp_path, workflow, where):
    """What gh printed before a page failed is not a list: a failed second page stops
    the job whichever page the tag's release is on, or if it has none."""
    publish = Publish(tmp_path, workflow, "publish-release")
    if where == "first-page":
        publish.release(draft=True)
    _many(publish, 150)
    if where == "second-page":
        publish.release(draft=True)
    before = copy.deepcopy(publish.github["releases"])
    publish.github["list_answers"] = [None, LIST_FAILURES["502-html"]] * 3
    result = publish.run()
    assert result.returncode == 1
    _nothing_written(publish, before)
    assert publish.github["list_requests"] == 6
    assert UNREAD.format(version=VERSION) in result.stdout


@pytest.mark.parametrize("held", ["none", "draft", "published"])
def test_a_list_that_fails_and_then_answers_is_judged_by_the_answer(release, held):
    _seed(release, held)
    before = copy.deepcopy(release.github["releases"])
    release.github["list_answers"] = [LIST_FAILURES["502-html"], LIST_FAILURES["no-answer"]]
    result = release.run()
    assert result.returncode == 0, result.stdout + result.stderr
    assert release.github["list_requests"] == 3
    assert result.stdout.count("::warning::listing the releases of") == 2
    (after,) = release.github["releases"]
    if held == "published":
        assert release.github["releases"] == before and _writes(release) == []
    else:
        _published_with_this_wheel(release, after)
    assert len(release.argvs("gh", "create")) == (1 if held == "none" else 0)


@pytest.mark.parametrize("held", ["none", "draft", "published"])
def test_every_page_of_the_list_is_read(tmp_path, workflow, held):
    """The tag's release on the second page of 100 is found: a draft there is finished,
    not created again beside, and a published release there is left as it is."""
    publish = Publish(tmp_path, workflow, "publish-release")
    _many(publish, 150)
    _seed(publish, held)
    before = copy.deepcopy(publish.github["releases"])
    result = publish.run()
    assert result.returncode == 0, result.stdout + result.stderr
    assert publish.github["list_requests"] == 2
    mine = [r for r in publish.github["releases"] if r["tag"] == publish.tag]
    assert len(mine) == 1
    if held == "published":
        assert publish.github["releases"] == before and _writes(publish) == []
    else:
        _published_with_this_wheel(publish, mine[0])
    assert len(publish.argvs("gh", "create")) == (1 if held == "none" else 0)


def test_other_tags_releases_are_not_the_tags(release):
    """Only a release whose tag is exactly v<version> is the tag's: a release candidate,
    a longer tag that ends with it and a draft of another version are not."""
    release.release(draft=False, tag=f"{release.tag}rc1", wheel=OTHER)
    release.release(draft=True, tag=f"x{release.tag}")
    release.release(draft=True, tag="v0.1.0")
    result = release.run()
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(release.argvs("gh", "create")) == 1
    (made,) = [r for r in release.github["releases"] if r["tag"] == release.tag]
    _published_with_this_wheel(release, made)


def test_a_wheel_that_is_not_the_one_the_build_checked_is_not_released(release):
    release.outputs["wheel_sha256"] = "0" * 64
    result = release.run()
    assert result.returncode == 1
    assert release.calls == []
    assert release.github["releases"] == []


def test_the_steps_run_nothing_but_the_stubs_and_the_text_tools(tmp_path, workflow):
    """Every program each step ran is on the test's `PATH` (a missing one would be
    `command not found`); the stubs are the only ones that act."""
    for job in ("publish-image", "publish-release"):
        publish = Publish(tmp_path / job, workflow, job)
        assert publish.run().returncode == 0
        assert {c["program"] for c in publish.calls} <= {"gh", "skopeo", "sleep"}
        assert sorted(os.listdir(publish.bin)) == sorted(("gh", "skopeo", "sleep", *TOOLS))


# --- What a step may run ---


def _scenarios(tmp_path, workflow, job):
    """Runs of `job` that between them take every branch of its script."""
    other = b"a wheel some other build made"
    if job == "publish-image":
        def setups():
            yield "absent", lambda p: None
            yield "same", lambda p: p.registry["tags"].update({VERSION: p.index.hex()})
            yield "other", lambda p: p.registry["tags"].update({VERSION: _image_index(note="x").hex()})
            yield "error", lambda p: p.registry["inspect_errors"].extend(REGISTRY_ERRORS[:3])
    else:
        def setups():
            yield "new", lambda p: p.outputs.update(prerelease="true")
            yield "draft-same", lambda p: p.release(draft=True, wheel=p.wheel)
            yield "draft-missing", lambda p: p.release(draft=True)
            yield "draft-other", lambda p: p.release(draft=True, wheel=other)
            yield "published-same", lambda p: p.release(draft=False, wheel=p.wheel)
            yield "unread", lambda p: p.github["list_answers"].extend([LIST_FAILURES["500"]] * 3)
            yield "two", lambda p: (p.release(draft=False, wheel=p.wheel), p.release(draft=True))
    for name, setup in setups():
        publish = Publish(tmp_path / name, workflow, job)
        setup(publish)
        publish.run()
        yield publish


_STRUCTURE = re.compile(r"fi|done|esac|else|then|do|;;|\{|\}.*|[^\s()]+\)")


def _command_lines(script: str) -> set[int]:
    """The lines of `script` that hold a command: not blank, a comment, a keyword or
    brace that closes or opens a block, a `case` pattern, or the arguments of a
    command continued from the line before (a continued line that starts with `|`,
    `||` or `&&` holds a command).

    It reads only the constructs the two publish scripts use: one command per line or
    per `\\` continuation, `if`, `for` and `case` blocks, brace groups and one-line
    functions. A here-document is refused, naming its line; a quoted string that spans
    lines is not seen, and its lines are read as commands."""
    lines, continued = set(), False
    for number, line in enumerate(script.splitlines(), 1):
        if re.search(r"(?<!<)<<(?!<)", line):
            raise ValueError(f"line {number} opens a here-document, which _command_lines does not read: {line.strip()}")
        text = line.strip()
        after_continuation, continued = continued, text.endswith("\\")
        if not text or text.startswith("#") or _STRUCTURE.fullmatch(text):
            continue
        if after_continuation and not text.startswith(("|", "&")):
            continue
        lines.add(number)
    return lines


def test_reading_which_lines_hold_a_command():
    script = 'if false; then\n  curl x \\\n    --flag\n  # a note\nfi\ncase $a in\n  b)\n    echo "$a" \\\n      || exit 1\n    ;;\nesac\n'
    assert _command_lines(script) == {1, 2, 6, 8, 9}
    with pytest.raises(ValueError, match="line 2 opens a here-document"):
        _command_lines("echo a\ncat <<'END'\njust text\nEND\n")
    assert _command_lines("grep x <<< \"$a\"\n") == {1}


@pytest.mark.parametrize("job", ["publish-image", "publish-release"])
def test_every_line_of_each_publish_script_runs_in_some_test(tmp_path, workflow, job):
    """So a command added anywhere in either script is run, and checked, by a test:
    a line no test reaches fails here until one does."""
    runs = list(_scenarios(tmp_path, workflow, job))
    ran = {line for publish in runs for line, _ in publish.trace}
    script = runs[0].script
    missing = sorted(_command_lines(script) - ran)
    assert missing == [], [script.splitlines()[n - 1].strip() for n in missing]


# Changes made in good faith that a step's stderr and exit status would hide: each is
# the step's first line, its stderr discarded and its failure handled. The first two are
# the review's of PR #154 (an unknown program, and one handed the token). Each runs as
# written (`curl` is not on the `PATH`; `/bin/mkdir` makes a directory in the test's own
# work directory), and is found in the record afterwards.
MUTATIONS = {
    "unknown-program": "curl https://example.invalid/ 2>/dev/null || true",
    "token-in-an-argument": 'curl -fsS -H "Authorization: Bearer ${TOKEN:-$GH_TOKEN}" https://example.invalid/ 2>/dev/null || true',
    "a-path": "/bin/mkdir made-by-a-path 2>/dev/null || true",
    "a-path-in-a-substitution": 'x=$(/bin/mkdir made-in-a-substitution 2>/dev/null || true)',
    "command-builtin": "command -v curl >/dev/null 2>&1 || true",
    "eval": "eval true 2>/dev/null || true",
    "trap-removed": "trap - DEBUG 2>/dev/null || true",
}


@pytest.mark.parametrize("mutation", MUTATIONS.values(), ids=MUTATIONS.keys())
@pytest.mark.parametrize("job", ["publish-image", "publish-release"])
def test_a_command_the_step_may_not_run_is_caught_whatever_it_does_with_its_failure(tmp_path, workflow, job, mutation):
    (step,) = [s for s in workflow["jobs"][job]["steps"] if "run" in s]
    publish = Publish(tmp_path, workflow, job, script=mutation + "\n" + step["run"])
    result = publish.execute()
    first_line = [command for line, command in publish.trace if line == 1]
    assert publish.unapproved() and publish.unapproved()[0] in first_line
    if mutation == MUTATIONS["unknown-program"]:
        # what the step's own answer shows: nothing, so only the record catches it
        assert result.returncode == 0
        assert "command not found" not in result.stderr
        assert publish.refused() == []


# --- More than one release under one tag ---


@pytest.mark.parametrize("leftover", ["none", "same", "other"])
@pytest.mark.parametrize("first", ["published", "draft"])
def test_a_published_release_and_a_leftover_draft_stop_the_job_unchanged(tmp_path, workflow, leftover, first):
    """A draft beside the published release (left by a run before #159, or made by
    hand): gh's own lookups would answer with either, by a race (`FetchRelease`), so
    the step makes none, writes nothing, and says to delete the draft and re-run. In
    whichever order the list holds them, and whatever wheel the draft holds."""
    publish = Publish(tmp_path, workflow, "publish-release")
    wheel = {"none": None, "same": publish.wheel, "other": OTHER}[leftover]
    if first == "published":
        published = publish.release(draft=False, wheel=publish.wheel)
        draft = publish.release(draft=True, wheel=wheel)
    else:
        draft = publish.release(draft=True, wheel=wheel)
        published = publish.release(draft=False, wheel=publish.wheel)
    before = copy.deepcopy(publish.github["releases"])
    result = publish.run()
    assert result.returncode == 1
    _nothing_written(publish, before)
    assert publish.github["looked_up"] == []
    held = "; ".join(f"{r['id']} {str(r['draft']).lower()}" for r in before)
    assert (
        f"::error::{publish.tag} has more than one release (id, draft: {held}), so nothing was changed; "
        "delete the leftover draft on the Releases page" in result.stdout
    )
    assert {published["id"], draft["id"]} == {r["id"] for r in before}


def test_two_drafts_under_one_tag_stop_the_job_unchanged(release):
    release.release(draft=True)
    release.release(draft=True, wheel=release.wheel)
    before = copy.deepcopy(release.github["releases"])
    result = release.run()
    assert result.returncode == 1
    _nothing_written(release, before)
    assert f"{release.tag} has more than one release (id, draft: 1 true; 2 true)" in result.stdout
