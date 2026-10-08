"""Licence line of generated files (PLAT-120, generator output exception, doc/architecture/lizenz-und-release.md 3.3).

Every file a neXtBSW generator writes starts with an SPDX line (<tag> is the SPDX licence tag) and a note:

    // <tag>: Apache-2.0 WITH AdditionRef-neXtBSW-Generator-Output-Exception
    // Generator output exception (NOTICE): this generated file may be used under terms of your choice.

The exception itself is the section "Generator output exception" of the NOTICE file of the repository (the exact
wording is in `EXCEPTION_TEXT` and in LICENSES/AdditionRef-neXtBSW-Generator-Output-Exception.txt; tests keep the
three in step). The line names Apache-2.0 because the generated text contains parts of the templates; the addition
says that the obligations of that licence do not apply to the output. The user of the generator may replace the line
with the licence of his own project: the exception grants exactly that.

The tag is spelt in two pieces below so that REUSE tools do not read this module as a file with that licence line.

`stamp` is idempotent (a text that already carries the line is returned unchanged) and chooses the comment syntax from
the file name. A file type without a comment syntax (JSON) is returned unchanged; `style_of` returns None for it, and
callers that must know (the "every generated file" tests) ask. Unknown types raise: a new generated file type has to
be given a comment syntax here on purpose.
"""

from __future__ import annotations

from pathlib import PurePosixPath

TAG = "SPDX-" + "License-Identifier"
EXCEPTION_ID = "AdditionRef-neXtBSW-Generator-Output-Exception"
IDENTIFIER = f"Apache-2.0 WITH {EXCEPTION_ID}"
NOTE = "Generator output exception (NOTICE): this generated file may be used under terms of your choice."

EXCEPTION_TEXT = (
    "Generator output exception: The output of the neXtBSW generators (source code, headers, linker scripts, "
    "configuration and documentation generated from the templates in this repository) may be used, modified and "
    "distributed under terms of your choice, without the obligations of the Apache License 2.0 that would otherwise "
    "apply to the parts of the templates contained in that output. This exception does not apply to the generators "
    "and templates themselves.")

# comment syntax by file suffix; None: the format has no comments
_BY_SUFFIX = {
    ".h": "//", ".hpp": "//", ".hh": "//", ".c": "//", ".cpp": "//", ".cc": "//",
    ".ld": "/*", ".dld": "/*", ".inc": "/*", ".s": "/*", ".a2l": "/*",
    ".cmake": "#", ".yaml": "#", ".yml": "#", ".py": "#", ".toml": "#", ".sha256": "#", ".cfg": "#", ".sh": "#",
    ".m": "%", ".md": "<!--", ".html": "<!--",
    ".json": None,
}
_BY_NAME = {"CMakeLists.txt": "#", "CMakeLists.txt.in": "#", "CMakePresets.json.in": None}


def style_of(path: str) -> str | None:
    """Comment opener (`//`, `/*`, `#`, `%`, `<!--`) for a generated file name; None if the format has no comments."""
    name = PurePosixPath(str(path).replace("\\", "/")).name
    if name in _BY_NAME:
        return _BY_NAME[name]
    suffix = PurePosixPath(name).suffix.lower()
    if suffix == ".in":                       # foo.cmake.in, application.dld.in: the suffix before .in decides
        suffix = PurePosixPath(name[:-3]).suffix.lower()
    if suffix not in _BY_SUFFIX:
        raise ValueError(f"generated file {name}: no comment syntax for the licence line "
                         "(tools/nxconfig/genlicense.py)")
    return _BY_SUFFIX[suffix]


def header_lines(style: str) -> list[str]:
    """The licence line and the note in the comment syntax `style`."""
    if style in ("//", "#", "%"):
        return [f"{style} {TAG}: {IDENTIFIER}", f"{style} {NOTE}"]
    if style == "/*":
        return [f"/* {TAG}: {IDENTIFIER} */", f"/* {NOTE} */"]
    if style == "<!--":
        return [f"<!-- {TAG}: {IDENTIFIER} -->", f"<!-- {NOTE} -->"]
    raise ValueError(f"unknown comment style {style!r}")


def has_line(text: str) -> bool:
    return any(TAG in line for line in text.splitlines()[:12])


def stamp(text: str, path: str, style: str | None = None) -> str:
    """`text` of the generated file `path` with the licence line on top (after a `#!` line); idempotent."""
    style = style if style is not None else style_of(path)
    if style is None or has_line(text):
        return text
    newline = "\r\n" if "\r\n" in text[:400] else "\n"
    block = newline.join(header_lines(style)) + newline
    if text.startswith("#!"):
        first, _, rest = text.partition("\n")
        return first + "\n" + block.replace("\r\n", "\n") + rest
    return block + text


def stamp_if_known(text: str, path: str) -> str:
    """`stamp` for scaffolds the user then owns (wizard output): a file type without a known syntax stays as it is."""
    try:
        return stamp(text, path)
    except ValueError:
        return text


def stamp_all(files: dict[str, str], skip: tuple[str, ...] = ()) -> dict[str, str]:
    """`stamp` for every file of a `{relative path: text}` dictionary except the names in `skip`."""
    return {name: (text if name in skip else stamp(text, name)) for name, text in files.items()}
