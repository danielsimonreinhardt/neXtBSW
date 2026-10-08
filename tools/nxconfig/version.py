"""Version identity of a neXtBSW tree (ARCH-55, doc/architecture/customer-project.md gap N1).

The file VERSION at the root of the tree is the only source of the neXtBSW version: one line MAJOR.MINOR.PATCH
(SemVer, decision of the user 2026-09-22). The root CMakeLists.txt reads it for project(VERSION), nxconfig and the
Studio read it through this module, tools/ci compares it with a release tag vX.Y.Z.

Standard library only: tools/ci loads this file directly (it runs before any package is installed).
"""

import re
import subprocess
from pathlib import Path

VERSION_FILE = "VERSION"
# MAJOR.MINOR.PATCH without leading zeros; no pre-release or build suffix (a release is the tag vX.Y.Z, SYS-68, and
# CMake's project(VERSION) takes numbers only)
PATTERN = r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
_VERSION = re.compile(rf"^{PATTERN}$")
TAG_PREFIX = "v"


class VersionError(Exception):
    """VERSION is missing or not MAJOR.MINOR.PATCH."""


def is_version(text: str) -> bool:
    return bool(_VERSION.match(text))


def parse(text: str) -> tuple[int, int, int]:
    match = _VERSION.match(text)
    if not match:
        raise VersionError(f"{text!r} is not a version MAJOR.MINOR.PATCH")
    return int(match[1]), int(match[2]), int(match[3])


def version_file(tree: Path) -> Path:
    return Path(tree) / VERSION_FILE


def read_version(tree: Path) -> str:
    """The version of a neXtBSW tree from its VERSION file. VersionError if missing or malformed."""
    file = version_file(tree)
    try:
        lines = file.read_text(encoding="utf-8").splitlines()
    except OSError as e:
        raise VersionError(f"cannot read {file}: {e.strerror or e}") from None
    text = lines[0].strip() if lines else ""
    if len(lines) != 1 or not is_version(text):
        raise VersionError(f"{file} must hold exactly one line MAJOR.MINOR.PATCH (is {'; '.join(lines)!r})")
    return text


def tag_of(version: str) -> str:
    """0.1.0 -> v0.1.0, the release tag of a version (SYS-68)."""
    return TAG_PREFIX + version


def version_of_tag(tag: str) -> str | None:
    """v0.1.0 -> 0.1.0; None for a tag that is no release tag vMAJOR.MINOR.PATCH."""
    if tag.startswith(TAG_PREFIX) and is_version(tag[len(TAG_PREFIX):]):
        return tag[len(TAG_PREFIX):]
    return None


def _git(tree: Path, *args: str) -> str | None:
    try:
        done = subprocess.run(["git", "-C", str(tree), *args], capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.strip() if done.returncode == 0 else None


def git_describe(tree: Path) -> str | None:
    """`git describe --tags --always --dirty` of the tree; None if it is no git checkout or git is missing."""
    return _git(tree, "describe", "--tags", "--always", "--dirty")


def release_tags_at_head(tree: Path) -> list[str] | None:
    """Release tags vMAJOR.MINOR.PATCH on the checked-out commit; None if it is no git checkout."""
    out = _git(tree, "tag", "--points-at", "HEAD")
    if out is None:
        return None
    return sorted(t for t in out.split() if version_of_tag(t) is not None)
