"""The release pipeline's checks before it publishes anything (#24, spec §5.6).

    release_check.py version [--tag v<version>]
        Reads the server's version (`api/app/version.py`) and the agent's
        (`agent/src/tlg_ipam_agent/__init__.py`) without importing either, and refuses
        unless they are the same string, a PEP 440 public version in its normal form
        (the form the Agents page pins its commands to, `install.releaseVersion`), and,
        with `--tag`, that tag is `v<version>`. Prints `version=…` and
        `prerelease=true|false`, for `$GITHUB_OUTPUT`.

    release_check.py wheel <path> --version <version>
        Refuses a wheel that is not the agent's at that version under the name the
        install commands use (`tlg_ipam_agent-<version>-py3-none-any.whl`), or that
        holds anything but the agent's package and its metadata.

Standard library and `packaging` only (`agent/requirements-build.txt`). A refusal
exits 1 with one line that says what is wrong.
"""

import argparse
import ast
import sys
import tomllib
import zipfile
from email.parser import BytesHeaderParser
from pathlib import Path

from packaging.requirements import Requirement
from packaging.version import InvalidVersion, Version

ROOT = Path(__file__).resolve().parents[2]
SERVER_VERSION = ROOT / "api" / "app" / "version.py"
AGENT = ROOT / "agent"
AGENT_VERSION = AGENT / "src" / "tlg_ipam_agent" / "__init__.py"
PACKAGE = "tlg_ipam_agent"
DIST_NAME = "tlg-ipam-agent"


class Refused(Exception):
    """A check that failed; its message is the line printed."""


def literal(path: Path, name: str) -> str:
    """The string assigned to `name` at the top level of `path`: one plain assignment of
    a string literal, which is what flit reads `__version__` from, too."""
    found = [
        node.value.value
        for node in ast.parse(path.read_text(encoding="utf-8")).body
        if isinstance(node, ast.Assign)
        and [getattr(t, "id", None) for t in node.targets] == [name]
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    ]
    if len(found) != 1:
        shown = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path
        raise Refused(f"{shown}: expected one `{name} = \"…\"`, found {len(found)}")
    return found[0]


def release_problem(version: str) -> str | None:
    """Why `version` cannot name a release, or None. A release version is a PEP 440
    public version in its normal form: no local `+…` part, no epoch, spelled as PEP 440
    normalises it (so the wheel flit builds carries it unchanged)."""
    try:
        parsed = Version(version)
    except InvalidVersion:
        return f"{version!r} is not a PEP 440 version"
    if parsed.local is not None:
        return f"{version!r} has a local part (`+{parsed.local}`), which no release carries"
    if parsed.epoch:
        return f"{version!r} has an epoch, which the install commands do not take"
    if str(parsed) != version:
        return f"{version!r} is not in normal form (PEP 440 spells it {str(parsed)!r})"
    return None


def wheel_name(version: str) -> str:
    return f"{PACKAGE}-{version}-py3-none-any.whl"


def check_version(tag: str | None = None) -> str:
    server = literal(SERVER_VERSION, "VERSION")
    agent = literal(AGENT_VERSION, "__version__")
    if server != agent:
        raise Refused(
            f"the server's version ({server}, api/app/version.py) and the agent's "
            f"({agent}, agent/src/tlg_ipam_agent/__init__.py) differ; they are one version"
        )
    problem = release_problem(server)
    if problem:
        raise Refused(f"the version cannot name a release: {problem}")
    if tag is not None and tag != f"v{server}":
        raise Refused(f"the tag {tag!r} is not v{server}, the version of the code it tags")
    return server


def _requirements(lines) -> set[str]:
    return {str(Requirement(line)) for line in lines}


def check_wheel(path: Path, version: str) -> None:
    if path.name != wheel_name(version):
        raise Refused(f"the wheel is {path.name}, not {wheel_name(version)}")
    info = f"{PACKAGE}-{version}.dist-info/"
    with zipfile.ZipFile(path) as wheel:
        names = set(wheel.namelist())
        metadata = BytesHeaderParser().parsebytes(wheel.read(info + "METADATA"))
        tags = BytesHeaderParser().parsebytes(wheel.read(info + "WHEEL"))
    if (metadata["Name"], metadata["Version"]) != (DIST_NAME, version):
        raise Refused(f"the wheel's metadata names {metadata['Name']} {metadata['Version']}")
    if tags.get_all("Tag") != ["py3-none-any"] or tags["Root-Is-Purelib"] != "true":
        raise Refused(f"the wheel is not pure Python for any platform: {tags.get_all('Tag')}")
    project = tomllib.loads((AGENT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    if _requirements(metadata.get_all("Requires-Dist") or []) != _requirements(project["dependencies"]):
        raise Refused("the wheel's Requires-Dist is not agent/pyproject.toml's dependencies")
    if metadata["Requires-Python"] != project["requires-python"]:
        raise Refused(f"the wheel's Requires-Python is {metadata['Requires-Python']}")
    source = {
        f"{PACKAGE}/{p.relative_to(AGENT_VERSION.parent).as_posix()}"
        for p in AGENT_VERSION.parent.rglob("*.py")
        if "__pycache__" not in p.parts
    }
    package = {n for n in names if not n.startswith(info)}
    if package != source:
        extra, missing = sorted(package - source), sorted(source - package)
        raise Refused(f"the wheel's files are not the agent's source: extra {extra}, missing {missing}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    v = sub.add_parser("version")
    v.add_argument("--tag")
    w = sub.add_parser("wheel")
    w.add_argument("path", type=Path)
    w.add_argument("--version", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "version":
            version = check_version(args.tag)
            print(f"version={version}")
            print(f"prerelease={'true' if Version(version).is_prerelease else 'false'}")
        else:
            check_wheel(args.path, args.version)
            print(f"{args.path.name}: the agent's wheel at {args.version}")
    except Refused as refused:
        print(f"::error::{refused}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
