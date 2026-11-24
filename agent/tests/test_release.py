"""The release (#24, spec §5.6): the server and the agent are one version, a version a
release can carry, and the release workflow publishes only from a tag, with the least
permissions, from pinned actions and images.

What a pushed tag does can be seen only when one is pushed; these tests hold what the
workflow is (`.github/workflows/release.yml`) and the checks it runs before it publishes
(`.github/scripts/release_check.py`). `web/src/utils/install.test.js` holds the names it
publishes under to the ones the Agents page renders."""

import importlib.util
import re
import tomllib
import zipfile
from pathlib import Path

import pytest
import yaml
from packaging.specifiers import SpecifierSet

import tlg_ipam_agent
from app import config
from app.version import VERSION

ROOT = Path(__file__).resolve().parents[2]
AGENT = ROOT / "agent"
WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"

_spec = importlib.util.spec_from_file_location(
    "release_check", ROOT / ".github" / "scripts" / "release_check.py"
)
release_check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(release_check)


# --- One version ---


def test_the_server_and_the_agent_are_one_version():
    assert tlg_ipam_agent.__version__ == VERSION
    assert config.settings.version == VERSION


def test_the_version_is_one_a_release_can_carry():
    assert release_check.release_problem(VERSION) is None
    # read as the workflow reads it, without importing either package
    assert release_check.check_version() == VERSION
    assert release_check.check_version(f"v{VERSION}") == VERSION


def test_the_environment_cannot_change_the_servers_version(monkeypatch):
    """A server reports the release it runs: `VERSION` in its environment is not read,
    so the install commands and `update_available` cannot be pointed elsewhere."""
    monkeypatch.setenv("VERSION", "9.9.9")
    assert config.Settings().version == VERSION
    assert "version" not in config.Settings.model_fields


# The cases `install.test.js` gives `install.releaseVersion`, with the same verdict:
# what the release check takes, the page pins its commands to
@pytest.mark.parametrize("version", ["0.2.0", "1.0", "0.2.0rc1", "0.2.0.dev0", "0.2.0.post1"])
def test_a_release_version(version):
    assert release_check.release_problem(version) is None


@pytest.mark.parametrize(
    "version, reason",
    [
        ("", "not a PEP 440 version"),
        ("v0.2.0", "not in normal form"),
        ("0.2.0+local", "local part"),
        ("0.2.0-rc1", "not in normal form"),
        ("1!2.0", "epoch"),
        ("0.2 ", "not in normal form"),
        ("0.2.0RC1", "not in normal form"),
        ("01.2", "not in normal form"),
        ("custom-build", "not a PEP 440 version"),
    ],
)
def test_not_a_release_version(version, reason):
    assert reason in release_check.release_problem(version)


@pytest.mark.parametrize("tag", ["0.2.0", "v0.2.0x", "v0.2", "refs/tags/v0.2.0"])
def test_a_tag_that_is_not_v_and_the_version_is_refused(tag):
    with pytest.raises(release_check.Refused, match="is not v"):
        release_check.check_version(tag.replace("0.2.0", VERSION))


def _version_file(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_two_versions_are_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(
        release_check, "SERVER_VERSION", _version_file(tmp_path, "version.py", 'VERSION = "0.1.0"\n')
    )
    with pytest.raises(release_check.Refused, match="differ; they are one version"):
        release_check.check_version()


def test_one_version_that_cannot_name_a_release_is_refused(tmp_path, monkeypatch):
    for attr, name, text in [
        ("SERVER_VERSION", "version.py", 'VERSION = "0.2.0+local"\n'),
        ("AGENT_VERSION", "__init__.py", '__version__ = "0.2.0+local"\n'),
    ]:
        monkeypatch.setattr(release_check, attr, _version_file(tmp_path, name, text))
    with pytest.raises(release_check.Refused, match="cannot name a release"):
        release_check.check_version()


@pytest.mark.parametrize(
    "text",
    [
        'VERSION = "0.2.0"\nVERSION = "0.3.0"\n',  # two
        'VERSION = ".".join(["0", "2", "0"])\n',  # computed, which flit would not read
        "VERSION: str\n",
        'if True:\n    VERSION = "0.2.0"\n',  # not at the top level
    ],
)
def test_a_version_is_one_plain_string_literal(tmp_path, text):
    with pytest.raises(release_check.Refused, match="expected one"):
        release_check.literal(_version_file(tmp_path, "version.py", text), "VERSION")


# --- The wheel ---


def _source_files():
    package = AGENT / "src" / "tlg_ipam_agent"
    return {
        f"tlg_ipam_agent/{p.relative_to(package).as_posix()}": p.read_bytes()
        for p in package.rglob("*.py")
        if "__pycache__" not in p.parts
    }


def _wheel(tmp_path, version=VERSION, *, name=None, files=None, metadata=None, tags=None):
    """A wheel laid out as flit lays out the agent's, with any part replaced."""
    project = tomllib.loads((AGENT / "pyproject.toml").read_text())["project"]
    fields = {
        "Metadata-Version": "2.4",
        "Name": "tlg-ipam-agent",
        "Version": version,
        "Requires-Python": project["requires-python"],
        "Requires-Dist": project["dependencies"],
    }
    fields.update(metadata or {})
    lines = []
    for key, value in fields.items():
        for one in value if isinstance(value, list) else [value]:
            lines.append(f"{key}: {one}")
    info = f"tlg_ipam_agent-{version}.dist-info"
    path = tmp_path / (name or release_check.wheel_name(version))
    with zipfile.ZipFile(path, "w") as wheel:
        for member, data in (_source_files() if files is None else files).items():
            wheel.writestr(member, data)
        wheel.writestr(f"{info}/METADATA", "\n".join(lines) + "\n")
        wheel.writestr(
            f"{info}/WHEEL",
            tags or "Wheel-Version: 1.0\nGenerator: flit 3.12.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        )
        wheel.writestr(f"{info}/entry_points.txt", "[console_scripts]\n")
        wheel.writestr(f"{info}/RECORD", "")
    return path


def test_the_agents_wheel_passes(tmp_path):
    release_check.check_wheel(_wheel(tmp_path), VERSION)


def test_the_wheel_is_named_as_the_install_commands_name_it(tmp_path):
    assert release_check.wheel_name("0.2.0") == "tlg_ipam_agent-0.2.0-py3-none-any.whl"
    with pytest.raises(release_check.Refused, match="not tlg_ipam_agent"):
        release_check.check_wheel(_wheel(tmp_path, name="tlg_ipam_agent-0.2.0-py3-none-any.whl"), "0.3.0")


@pytest.mark.parametrize(
    "change, match",
    [
        ({"metadata": {"Version": "0.0.1"}}, "metadata names"),
        ({"metadata": {"Name": "other"}}, "metadata names"),
        ({"metadata": {"Requires-Dist": ["httpx"]}}, "Requires-Dist"),
        ({"metadata": {"Requires-Python": ">=3.8"}}, "Requires-Python"),
        ({"tags": "Root-Is-Purelib: true\nTag: cp313-cp313-linux_x86_64\n"}, "pure Python"),
    ],
)
def test_a_wheel_whose_metadata_is_not_the_agents_is_refused(tmp_path, change, match):
    with pytest.raises(release_check.Refused, match=match):
        release_check.check_wheel(_wheel(tmp_path, **change), VERSION)


def test_a_wheel_with_other_files_or_without_the_agents_is_refused(tmp_path):
    extra = {**_source_files(), "tests/conftest.py": b"", "tlg_ipam_agent/planted.py": b""}
    with pytest.raises(release_check.Refused, match=r"extra \['tests/conftest.py', 'tlg_ipam_agent/planted.py'\]"):
        release_check.check_wheel(_wheel(tmp_path, files=extra), VERSION)
    missing = {k: v for k, v in _source_files().items() if k != "tlg_ipam_agent/guard.py"}
    (tmp_path / "again").mkdir()
    with pytest.raises(release_check.Refused, match=r"missing \['tlg_ipam_agent/guard.py'\]"):
        release_check.check_wheel(_wheel(tmp_path / "again", files=missing), VERSION)


def test_the_build_lock_holds_the_backend_pyproject_names():
    """The release builds the wheel with the hashed `requirements-build.txt`, so its
    `flit_core` must be the one `[build-system]` asks for."""
    requires = tomllib.loads((AGENT / "pyproject.toml").read_text())["build-system"]["requires"]
    lines = [
        line.strip()
        for line in (AGENT / "requirements-build.in").read_text().splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert [line for line in lines if line.startswith("flit_core")] == requires
    pinned = re.search(r"^flit-core==(\S+) ", (AGENT / "requirements-build.txt").read_text(), re.M)
    assert pinned and pinned.group(1) in SpecifierSet(requires[0].removeprefix("flit_core"))


# --- The workflow ---


@pytest.fixture(scope="module")
def workflow():
    return yaml.safe_load(WORKFLOW.read_text())


PUBLISH_IF = "github.event_name == 'push' && github.ref_type == 'tag'"
PERMISSIONS = {
    "build": {"contents": "read"},
    "publish-image": {"packages": "write"},
    "publish-release": {"contents": "write"},
    "verify": {"contents": "read"},
}


def test_it_publishes_only_for_a_pushed_version_tag(workflow):
    # YAML 1.1 reads the key `on` as true
    on = workflow[True]
    assert on["push"] == {"tags": ["v*"]}
    assert set(on) == {"push", "pull_request", "workflow_dispatch"}
    assert on["workflow_dispatch"] is None  # no input can turn publishing on
    jobs = workflow["jobs"]
    assert set(jobs) == set(PERMISSIONS)
    assert "if" not in jobs["build"]
    for name in ("publish-image", "publish-release", "verify"):
        assert jobs[name]["if"] == PUBLISH_IF
    assert jobs["publish-image"]["needs"] == "build"
    assert jobs["publish-release"]["needs"] == ["build", "publish-image"]
    assert jobs["verify"]["needs"] == ["build", "publish-release"]


def test_each_job_holds_only_the_permission_it_uses(workflow):
    assert workflow["permissions"] == {}
    for name, job in workflow["jobs"].items():
        assert job["permissions"] == PERMISSIONS[name], name


def _steps(workflow):
    for name, job in workflow["jobs"].items():
        for step in job["steps"]:
            yield name, step


def test_the_publish_jobs_check_out_nothing(workflow):
    """They publish the files the build job made and checked, so no code of the tag
    runs where a write credential is."""
    for name, step in _steps(workflow):
        if name.startswith("publish-"):
            assert "run" in step or step["uses"].startswith("actions/download-artifact@"), step


def test_every_action_is_pinned_to_a_commit_and_every_image_to_a_digest(workflow):
    for _, step in _steps(workflow):
        if "uses" in step:
            assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", step["uses"]), step["uses"]
        given = step.get("with") or {}
        # the binfmt image setup-qemu-action runs, the BuildKit image setup-buildx-action runs
        for image in [given.get("image"), given.get("driver-opts", "").removeprefix("image=") or None]:
            if image is not None:
                assert re.fullmatch(r"docker\.io/\S+@sha256:[0-9a-f]{64}", image), image
    images = [s.get("with", {}) for _, s in _steps(workflow) if "uses" in s]
    assert sum("image" in w for w in images) == 1
    assert sum(w.get("driver-opts", "").startswith("image=") for w in images) == 1


def test_no_expression_is_expanded_into_a_script(workflow):
    """Every value reaches a `run` through `env`, so none can become shell code."""
    for _, step in _steps(workflow):
        assert "${{" not in step.get("run", ""), step.get("name")


def test_checkout_keeps_no_credential(workflow):
    for _, step in _steps(workflow):
        if step.get("uses", "").startswith("actions/checkout@"):
            assert step["with"]["persist-credentials"] is False
