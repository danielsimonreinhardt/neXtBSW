"""Editing a YAML file without losing the author's layout (TOOL-66, used by the format migrations).

A migration runs on a file somebody wrote by hand: a customer `project.yaml` full of comments, alignment and
flow-style entries (`{ name: A, part: B }`). Re-dumping a ruamel round-trip document would keep the *comments*,
but not the layout: measured over the 61 configuration files of this tree, 25 of them come back reformatted
(flow mappings lose their inner spaces, sequences change indentation, wrapped scalars are joined). A migration
that reformats a quarter of the workspace buries its own change in noise and makes the review useless.

So this module edits the **text**, guided by the round-trip document:

  - the ruamel document (`YamlFile.tree`) is what a migration step *reads* - values, structure, presence of keys;
  - every change is a line edit at the position ruamel recorded (`lc.key`, `lc.value`), so all other lines stay
    byte for byte what they were: comments, blank lines, alignment, quoting style, line endings.

The vocabulary (`set`, `add`, `remove`, `rename`) covers what format steps do. A step that has to restructure a
document calls `rewrite()`; the file is then dumped from the ruamel document, comments survive but the layout may
change, and `reformatted` says so in the migration report.

Positions are 0-based rows and columns, as ruamel reports them; `Message`s built from them use 1-based lines.
"""

import io
import re
from dataclasses import dataclass, field
from pathlib import Path

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.error import YAMLError

from .messages import Path_, format_path

# Round-trip settings used for reading and for the rewrite() escape hatch. width: never re-wrap a long line.
INDENT = {"mapping": 2, "sequence": 4, "offset": 2}
WIDTH = 4096


def yaml() -> YAML:
    y = YAML(typ="rt")
    y.preserve_quotes = True
    y.width = WIDTH
    y.indent(**INDENT)
    return y


class EditError(Exception):
    """The requested edit cannot be made as a line edit (missing key, block scalar, overlapping edits)."""


@dataclass(frozen=True)
class Edit:
    """A replacement of the lines [row, end) by `lines` (an insertion has row == end)."""

    row: int
    end: int
    lines: tuple[str, ...]
    note: str                                   # one line for the migration report


def locate(tree, path: Path_, value: bool = False) -> tuple[int, int] | None:
    """(row, column) of the element `path`, of its key or of its value; None if it does not exist.

    Walks like config.line_of, but returns the column as well and only for a *complete* path - a migration must
    not edit a parent because the child is missing.
    """
    node, where, last = tree, None, len(path) - 1
    for i, part in enumerate(path):
        try:
            if isinstance(node, CommentedMap) and part in node:
                where = (node.lc.value(part) if i == last and value else node.lc.key(part))
                node = node[part]
            elif isinstance(node, CommentedSeq) and isinstance(part, int) and -len(node) <= part < len(node):
                where = node.lc.item(part if part >= 0 else len(node) + part)
                node = node[part]
            else:
                return None
        except (AttributeError, KeyError, TypeError, IndexError):
            return None
    return (int(where[0]), int(where[1])) if where is not None else None


def get(tree, path: Path_, default=None):
    """The value at `path` in a round-trip document, `default` if any step of the path is missing."""
    node = tree
    for part in path:
        try:
            if isinstance(node, dict) and part in node:
                node = node[part]
            elif isinstance(node, list) and isinstance(part, int) and -len(node) <= part < len(node):
                node = node[part]
            else:
                return default
        except (KeyError, TypeError, IndexError):
            return default
    return node


# ------------------------------------------------------------------ scalars in a line

_BLOCK_SCALAR = re.compile(r"^[|>][+-]?\d*\s*(#.*)?$")


def _end_of_quoted(line: str, col: int) -> int:
    quote = line[col]
    i = col + 1
    while i < len(line):
        if line[i] == quote:
            if quote == "'" and i + 1 < len(line) and line[i + 1] == "'":
                i += 2                          # '' is an escaped quote in a single quoted scalar
                continue
            return i + 1
        i += 2 if (quote == '"' and line[i] == "\\") else 1
    raise EditError("the scalar is not closed on this line")


def _end_of_flow(line: str, col: int) -> int:
    pairs, stack, i = {"{": "}", "[": "]"}, [], col
    while i < len(line):
        c = line[i]
        if c in "'\"":
            i = _end_of_quoted(line, i)
            continue
        if c in pairs:
            stack.append(pairs[c])
        elif stack and c == stack[-1]:
            stack.pop()
            if not stack:
                return i + 1
        i += 1
    raise EditError("the flow collection does not end on this line")


def _end_of_plain(line: str, col: int, flow: bool) -> int:
    """End of a plain scalar: up to a trailing comment (' #') or the end of the line.

    Inside a flow collection (`{ name: A, path: swc/A }`) a plain scalar also ends at the next `,`, `}` or `]` -
    in a block mapping those are ordinary characters of the value.
    """
    rest = line[col:]
    cut = rest.find(" #")
    rest = rest[:cut] if cut >= 0 else rest
    if flow:
        stops = [i for i in (rest.find(c) for c in ",}]") if i >= 0]
        rest = rest[:min(stops)] if stops else rest
    return col + len(rest.rstrip())


def is_flow(node) -> bool:
    """True if a round-trip node is written in flow style (`{...}`, `[...]`).

    Asked wherever a line edit depends on how the text looks: whether a plain scalar ends at the next `,}]`
    (`set`), whether a collection has lines of its own to insert into (`add`) or to take away (`remove`). It is
    the only question this module asks about style - nothing here has to decide how *new* content should be
    written, so unlike `yamldoc` in the Studio it needs no second function for the author's intent: `add()`
    always writes a block line and refuses a flow parent either way.

    TOOL-95: for an EMPTY collection the length decides, not the marker. `ruamel` does not set the flow marker
    of one - measured against the ruamel of this tree (0.19.1):

        items: []     CommentedSeq, len 0, fa.flow_style() -> None      (not True)
        map: {}       CommentedMap, len 0, fa.flow_style() -> None      (not True)
        full: [a, b]  CommentedSeq, len 2, fa.flow_style() -> True
        fmap: {a: 1}  CommentedMap, len 1, fa.flow_style() -> True
        nothing:      NoneType, no `fa` at all

    `bool(node.fa.flow_style())` therefore took `map: {}` for a block mapping. That is the same weakness that
    destroyed files in tools/studio/core/yamldoc.py until TOOL-91; here it destroyed nothing, because `add()`
    stops earlier with "is empty" - it only made that message name the wrong reason.

    The reliable criterion is the length, because YAML has no block spelling for an empty collection: a key
    with nothing under it (`nothing:` above) loads as None, not as an empty mapping. A loaded collection of
    length 0 can only have come from `[]` or `{}`, so it is flow, whatever the marker says. A node that never
    came out of a document (a plain dict or list, no `fa`) keeps answering False.
    """
    style = getattr(node, "fa", None)
    if style is None:
        return False
    if isinstance(node, (CommentedMap, CommentedSeq)) and not len(node):
        return True
    return bool(style.flow_style())


def value_span(line: str, col: int, flow: bool = False) -> tuple[int, int]:
    """(start, end) of the scalar or flow collection that starts at `col`. EditError for a block scalar."""
    if col >= len(line):
        raise EditError("the value stands on its own line (a block); a line edit cannot replace it")
    if _BLOCK_SCALAR.match(line[col:]):
        raise EditError(f"the value is a block scalar ({line[col:col + 1]}); a line edit cannot replace it")
    if line[col] in "'\"":
        return col, _end_of_quoted(line, col)
    if line[col] in "{[":
        return col, _end_of_flow(line, col)
    return col, _end_of_plain(line, col, flow)


def quoted(text: str, quote: str) -> str:
    """`text` as a quoted YAML scalar in the given style ('' escapes in single, backslash in double quotes)."""
    if quote == "'":
        return "'" + str(text).replace("'", "''") + "'"
    return '"' + str(text).replace("\\", "\\\\").replace('"', '\\"') + '"'


# The same rule as `_quote_needed` in tools/studio/core/yamldoc.py, taken over word for word (TOOL-98). Not a
# second rule: the two modules write scalars into the same kind of file, so they have to agree on when a plain
# scalar is safe. What the old, shorter test here let through - measured by writing the value and loading it back:
#
#     'null'   was written as  null   and came back as None     (a different type)
#     '123'    was written as  123    and came back as int 123   (a different type)
#     'abc:'   was written as  abc:   and the file no longer loaded at all (ScannerError)
#     '- x'    was written as  - x    and the file no longer loaded at all (ScannerError)
#
# `-` and `?` were missing from the leading characters, a trailing `:` was not looked at, and nothing kept a
# string that spells a number, a boolean or a null from coming back as that type instead.
_RESOLVES_TO_A_NON_STRING = re.compile(
    r"[-+]?(\d+(\.\d*)?|\.\d+)([eE][-+]?\d+)?|0x[0-9a-fA-F]+|true|false|null|yes|no|on|off|~", re.I)


def _quote_needed(text: str) -> bool:
    if text == "" or text.strip() != text:
        return True
    if text[0] in "&*!|>%@`{}[],#\"'?-" or ": " in text or text.endswith(":"):
        return True
    return bool(_RESOLVES_TO_A_NON_STRING.fullmatch(text))


def represent(value) -> str:
    """A scalar as YAML text. Only what a migration writes: numbers, booleans, plain or quoted text.

    Text that would not read back as the same string is quoted, so a migration cannot write a file that loads
    differently - or not at all - than what it was given.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if value is None:
        return "null"
    text = str(value)
    # `" #"` is this module's one addition to the shared rule: `set()` and `add()` write into a line that may
    # carry a trailing comment, and an unquoted ` #` would start a second one.
    if _quote_needed(text) or " #" in text:
        return quoted(text, '"')
    return text


# ------------------------------------------------------------------ the file


@dataclass
class YamlFile:
    """A YAML file as text plus its round-trip document; changes are line edits against the original text."""

    file: Path
    text: str
    tree: object                                        # CommentedMap (round trip) - what a step reads
    newline: str = "\n"
    trailing_newline: bool = True
    edits: list[Edit] = field(default_factory=list)
    reformatted: bool = False                           # rewrite() was used: the whole file is dumped again

    @classmethod
    def read(cls, file: Path) -> "YamlFile":
        """Reads a YAML file. Raises OSError or ruamel's YAMLError; the caller turns those into messages."""
        file = Path(file)
        text = file.read_text(encoding="utf-8")
        return cls.of(file, text)

    @classmethod
    def of(cls, file: Path, text: str) -> "YamlFile":
        tree = yaml().load(text) if text.strip() else None
        newline = "\r\n" if "\r\n" in text else "\n"
        return cls(Path(file), text, tree, newline, text.endswith(("\n", "\r")))

    @property
    def lines(self) -> list[str]:
        return self.text.splitlines()

    @property
    def changed(self) -> bool:
        return bool(self.edits)

    # -------------------------------------------------------------- reading

    def get(self, *path, default=None):
        return get(self.tree, path, default)

    def has(self, *path) -> bool:
        marker = object()
        return get(self.tree, path, marker) is not marker

    # -------------------------------------------------------------- writing

    def set(self, path: Path_, value, note: str | None = None) -> None:
        """Replaces the value of an existing key (or sequence item) in its line; the trailing comment stays."""
        where = locate(self.tree, tuple(path), value=True)
        if where is None:
            raise EditError(f"{format_path(tuple(path))} does not exist in {self.file.name}")
        row, col = where
        line = self.lines[row]
        start, end = value_span(line, col, is_flow(get(self.tree, tuple(path)[:-1])))
        old = line[start:end]
        # a value that was quoted stays quoted, in the style the author chose
        new = (quoted(value, old[0]) if old[:1] in ("'", '"') and isinstance(value, str) else represent(value))
        if old == new:
            return
        rest = line[end:]
        if rest.strip().startswith("#"):
            # keep the comment in its column if the new value is not longer, otherwise one space before it
            pad = len(rest) - len(rest.lstrip(" "))
            rest = " " * max(1, pad - (len(new) - len(old))) + rest.lstrip(" ")
        self._edit(row, row + 1, [line[:start] + new + rest],
                   note or f"{format_path(tuple(path))}: {old} -> {new}")

    def add(self, path: Path_, value, note: str | None = None) -> None:
        """Adds a key to a mapping that exists. The new line goes first in the block, where a reader looks for it.

        `path` is the path of the *new* key; its parent must exist and must be a mapping written in block style.
        """
        path = tuple(path)
        if self.has(*path):
            raise EditError(f"{format_path(path)} already exists in {self.file.name}")
        parent = self.tree if len(path) == 1 else get(self.tree, path[:-1])
        if not isinstance(parent, CommentedMap):
            raise EditError(f"{format_path(path[:-1]) or 'the document'} is no mapping in {self.file.name}")
        # The flow check comes first, and it still refuses (TOOL-95). `add()` inserts a *line* into a block, and
        # no flow mapping has lines of its own - `{}` just as little as `{a: 1}`. Handling the empty one would
        # mean rewriting the whole entry, and it would leave an inconsistent contract behind: `map: {}` could
        # take a key, `map: {a: 1}` still could not. A step that has to write into a flow mapping restructures
        # the document and calls `rewrite()`, the escape hatch this module already has for exactly that.
        # So the refusal stays; what was wrong was only its *reason*. An empty flow mapping was reported as
        # "is empty", which sends the reader looking for a missing value instead of for the flow line.
        if is_flow(parent):
            raise EditError(f"{format_path(path[:-1]) or 'the document'} is written in flow style in "
                            f"{self.file.name}; a line edit cannot add a key to it")
        if not parent:
            # Unreachable for a loaded document - an empty mapping is always flow and was caught above (see
            # is_flow). Kept for a tree built in memory, and because `first` below needs a key to take its
            # column from.
            raise EditError(f"{format_path(path[:-1]) or 'the document'} is empty in {self.file.name}")
        first = next(iter(parent))
        where = locate(self.tree, path[:-1] + (first,))
        if where is None:
            raise EditError(f"cannot find where {format_path(path)} would go in {self.file.name}")
        row, col = where
        lines = [f"{' ' * col}{path[-1]}: {represent(value)}"]
        # A top-level key inserted below a comment block would read as a headline of the key it stands above;
        # a blank line keeps it on its own (the line before is already blank, that is where the block ended).
        if len(path) == 1 and row > 0 and not self.lines[row - 1].strip():
            lines.append("")
        self._edit(row, row, lines, note or f"{format_path(path)}: added, {represent(value)}")

    def set_or_add(self, path: Path_, value, note: str | None = None) -> None:
        (self.set if self.has(*path) else self.add)(path, value, note)

    def remove(self, path: Path_, note: str | None = None) -> None:
        """Removes a key with its whole block (the lines below it that are indented deeper)."""
        path = tuple(path)
        where = locate(self.tree, path)
        if where is None:
            raise EditError(f"{format_path(path)} does not exist in {self.file.name}")
        if is_flow(get(self.tree, path[:-1])):
            raise EditError(f"{format_path(path)} stands in a flow collection in {self.file.name}; a line edit "
                            "cannot remove it")
        row, col = where
        lines, end = self.lines, row + 1
        while end < len(lines):
            stripped = lines[end].strip()
            if stripped and (len(lines[end]) - len(lines[end].lstrip(" "))) <= col:
                break
            end += 1
        while end - 1 > row and not lines[end - 1].strip():       # blank lines belong to what follows
            end -= 1
        self._edit(row, end, [], note or f"{format_path(path)}: removed")

    def rename(self, path: Path_, key: str, note: str | None = None) -> None:
        """Renames a mapping key in place; its value and its comment stay."""
        path = tuple(path)
        where = locate(self.tree, path)
        if where is None:
            raise EditError(f"{format_path(path)} does not exist in {self.file.name}")
        row, col = where
        line = self.lines[row]
        old = str(path[-1])
        if line[col:col + len(old)] != old:
            raise EditError(f"{format_path(path)} is not written plainly in {self.file.name} (quoted or a merge key)")
        self._edit(row, row + 1, [line[:col] + key + line[col + len(old):]],
                   note or f"{format_path(path)}: renamed to {key}")

    def rewrite(self, note: str) -> None:
        """Escape hatch: dump the whole file from the ruamel document again (comments stay, the layout may not)."""
        self.reformatted = True
        self.edits.append(Edit(0, len(self.lines), (), note))

    def _edit(self, row: int, end: int, lines, note: str) -> None:
        for other in self.edits:
            if row < other.end and other.row < end and not (row == end == other.row):
                raise EditError(f"two changes of {self.file.name} touch line {row + 1}")
        self.edits.append(Edit(row, end, tuple(lines), note))

    # -------------------------------------------------------------- result

    def result(self) -> str:
        """The new file content. Without an edit it is the original text, byte for byte."""
        if not self.edits:
            return self.text
        if self.reformatted:
            buffer = io.StringIO()
            yaml().dump(self.tree, buffer)
            text = buffer.getvalue()
            return text.replace("\n", self.newline) if self.newline != "\n" else text
        lines = self.lines
        for edit in sorted(self.edits, key=lambda e: (e.row, e.end), reverse=True):
            lines[edit.row:edit.end] = list(edit.lines)
        text = self.newline.join(lines)
        return text + self.newline if self.trailing_newline and text else text


def read(file: Path) -> tuple[YamlFile | None, str | None]:
    """(file, None) or (None, reason) - reading never raises, the caller reports the reason."""
    try:
        return YamlFile.read(file), None
    except OSError as e:
        return None, f"cannot read {file}: {e.strerror or e}"
    except YAMLError as e:
        return None, f"invalid YAML: {e}"
