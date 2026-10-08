# SPDX-License-Identifier: Apache-2.0
"""Release tags including release candidates (TOOL-232).

`VERSION` stays `MAJOR.MINOR.PATCH` without suffix (ARCH-55, cm-plan.md 3.2). A release of that version is tagged
`vX.Y.Z`; a **release candidate** of the same version is tagged `vX.Y.Z-rcN` (N = 1, 2, ... without leading zero),
Release R1 is `v1.0.0-rc1` on `VERSION` 1.0.0. Both kinds are a release tag in the sense of the release tools
(`tools.ci check --release`, `tools.ci release`, `tools/nxupdate --to`): annotated, on `master`, the tree clean, and
the version part of the tag equal to `VERSION`. Any other spelling (`v1.0.0-rc`, `v1.0.0-rc01`, `v1.0.0-beta1`,
`v1.0.0-rc.1`) is not a release tag.

Standard library only: `tools/ci/ci.py` loads this file directly (before any package is installed), and
`tools/ci/release.py` and `tools/nxupdate` load it the same way. It deliberately does not change
`tools/nxconfig/version.py` (R1 freeze); moving the rule there is a task after R1.
"""

from __future__ import annotations

import re

_NUMBER = r"(?:0|[1-9][0-9]*)"
_VERSION = rf"{_NUMBER}\.{_NUMBER}\.{_NUMBER}"
_TAG = re.compile(rf"^v(?P<version>{_VERSION})(?:-rc(?P<rc>[1-9][0-9]*))?$")
#: what a release tag would look like at first glance - `v` and a digit (cm-plan.md 3.2: no other tag starts so)
_LOOKS_LIKE = re.compile(r"^v[0-9]")


def parse(tag: str) -> tuple[str, int | None] | None:
    """`v1.0.0` -> ("1.0.0", None), `v1.0.0-rc1` -> ("1.0.0", 1); None for anything else."""
    match = _TAG.match(tag or "")
    if not match:
        return None
    return match["version"], (int(match["rc"]) if match["rc"] else None)


def is_release_tag(tag: str) -> bool:
    """A release tag `vX.Y.Z` or a release candidate tag `vX.Y.Z-rcN`."""
    return parse(tag) is not None


def is_candidate(tag: str) -> bool:
    found = parse(tag)
    return found is not None and found[1] is not None


def version_of(tag: str) -> str | None:
    """The version part: `v1.0.0-rc1` -> 1.0.0; None if `tag` is no release tag."""
    found = parse(tag)
    return found[0] if found else None


def looks_like_release(tag: str) -> bool:
    """Starts like a release tag (`v` + digit), whether or not it is a valid one."""
    return bool(_LOOKS_LIKE.match(tag or ""))


def sort_key(tag: str) -> tuple:
    """Order of release tags: by version, a candidate before the release of its version, rc1 before rc2."""
    version, rc = parse(tag)
    numbers = tuple(int(x) for x in version.split("."))
    return numbers + ((0, rc) if rc is not None else (1, 0))


def problem(tag: str, version: str) -> str | None:
    """Why `tag` cannot be the release tag of `VERSION` `version`; None if it can."""
    found = parse(tag)
    if found is None:
        return (f"{tag!r} ist kein Release-Tag: erlaubt sind v{version} und v{version}-rcN "
                f"(N = 1, 2, ... ohne fuehrende Null)")
    if found[0] != version:
        return f"{tag} passt nicht zu VERSION {version} (erwartet v{version} oder v{version}-rcN)"
    return None


def pick(tags, version: str) -> str | None:
    """The release tag of `version` among `tags` (for example those on HEAD): the release `vX.Y.Z` itself if it is
    there, otherwise the newest candidate `vX.Y.Z-rcN`; None if none of them belongs to `version`."""
    own = [t for t in tags if version_of(t) == version]
    return max(own, key=sort_key) if own else None
