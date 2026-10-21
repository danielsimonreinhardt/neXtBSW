# SPDX-License-Identifier: Apache-2.0
"""Site sanitizer: internal traces out of the rendered pages of the documentation website (SITE-CLEAN).

The sources keep their internal references for now (a source cleanup follows later); only the Markdown that MkDocs
renders is cleaned, in the hook tools/docgen/mkdocs_hooks.py (on_page_markdown, after site.rewrite_links).

Removed:
1. Internal task IDs of the planning list: the prefixes in PREFIXES, pattern ``\\b(PREFIX)-\\d+[a-z]?\\b``, with the
   short forms ``ARCH-24/25``, ranges ``SYS-237..240`` and finding suffixes ``SYS-188-P2``.
2. References to the agent rules file ``CLAUDE.md`` (not published): reason brackets go, a mention in a sentence
   becomes "the project rules", a link to it loses its target.

Rules, only outside fenced code (Mermaid diagrams are cleaned in their labels, they are visible text); inline code
only when it is exactly one ID or ``CLAUDE.md``; link targets, HTML tags and URLs are never touched:

- a bracket of IDs, separators, ranges and optionally a date or filler word ("decision", "task") goes with the space
  before it; in a mixed bracket only the ID items go: "(PLAT-53, C2)" -> "(C2)";
- table cells lose their IDs; an emptied cell shows "–"; a column whose body held only IDs is removed;
- label IDs at the start of a line or list item ("**SYS-19:**", "SYS-75: ") go;
- IDs left in running text go with their separator; after a preposition they become a neutral phrase
  ("since SYS-40" -> "since an earlier change", "until IO-54" -> "until a follow-up task");
- a changed heading keeps its old anchor ({#...}), so links into the page stay valid.

Other identifiers (SSR-, SM-, AoU-, DF-, AN-, CM-, CR-, EW-, F-, BT-, B16, versions, registers) are never touched.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

PREFIXES = ("ARCH", "BL", "COM", "DOC", "IO", "NVM", "PLAT", "RTE", "SYS", "TOOL", "VER")
_P = "|".join(PREFIXES)
_P_LOWER = _P.lower()

# the pattern of the check (tests, report): exactly the task's definition
ID_RE = re.compile(rf"\b(?:{_P})-\d+[a-z]?\b")
# the same, without the hyphenated document identifiers that only contain a prefix (FM-RTE-01, FM-BL-04, FM-NVM-02:
# failure modes of the architecture safety analysis) - the website check counts with this one
CHECK_RE = re.compile(rf"(?<![\w-])(?:{_P})-\d+[a-z]?\b")
CLAUDE_RE = re.compile(r"claude", re.IGNORECASE)


def _test_names() -> set[str]:
    """Names of the test entries (tools/test/tests.yaml): `io67-mutations` is a test, no decision board item."""
    path = Path(__file__).resolve().parents[2] / "tools" / "test" / "tests.yaml"
    try:
        return set(re.findall(r"(?m)^\s*-\s+name:\s*([\w.-]+)", path.read_text(encoding="utf-8")))
    except OSError:
        return set()


_TESTS = sorted(_test_names(), key=len, reverse=True)
_NOT_A_TEST = (rf"(?!(?:{'|'.join(re.escape(t) for t in _TESTS)})(?![\w-]))" if _TESTS else "")

_CORE = rf"(?<![\w-])(?:{_P})-(?:\d+[a-z]?|\*)"
# the decision letter of a review point ("S202-2 a", "K-2 a"): only before punctuation or the end of the phrase
_LETTER = r"(?:[ \t][a-e](?=[ \t]*(?:[,;:)\]./…]|$|-[ \t]|and\b|of\b|\()))"
# finding IDs derived from a task (SYS188-P2, IO86-1, TOOL257-1, S211-1, RTE43-2, P106-S1, DOC99-5)
_DER = (rf"(?<![\w-])(?:(?:{_P})\d+|[SP]\d{{3}}|B-(?:{_P})\d+)-[A-Z]{{0,3}}\d+[a-z]?(?:-\d+)?(?![\w-]){_LETTER}?")
# review points of a review package, always with their decision letter ("K-2 a", "S-1 a", "E2-1 a", "D5-3 a")
_REV = rf"(?<![\w-])[A-Z]\d?-\d{{1,2}}[ \t][a-e](?![\w-])"
# lower-case IDs of the decision board and of work waves (cloud-1001-sys-62, review-doc19, tool229-cm-regeln-1011,
# sys37-postfach-abschaltend, q-sys115-116-117, review-welle-0210-tag, night12, WAVE-1001); test names stay
_LOW = (rf"(?<![\w./-]){_NOT_A_TEST}(?:"
        rf"(?:q-)?(?:{_P_LOWER})\d+(?:-[a-z0-9]+)+"
        rf"|cloud-\d{{4}}(?:-[a-z]+-\d+[a-z]?)?"
        rf"|abn-[a-z0-9]+(?:-[a-z0-9]+)*"
        rf"|board-\d{{4}}(?:-[a-z]+)*"
        rf"|review-(?=[a-z-]*\d)[a-z0-9]+(?:-[a-z0-9]+)*"
        rf"|[a-z][a-z0-9]*(?:-[a-z0-9]+)*-(?:0[1-9]|1[0-2])[0-3]\d"
        rf"|(?i:welle|wave)-\d{{4}}(?:-[A-Za-z]+)*"
        rf"|night\d+"
        rf"|NXBOARDITEM)(?![\w/-]){_LETTER}?")
_ATOM = rf"(?:{_CORE}(?:-\d+\b|-[A-Z][A-Za-z0-9]*)*(?:/(?:\d+[a-z]?|[a-z])\b)*(?![\w])|{_DER}|{_REV}|{_LOW})"
_ONE = rf"{_ATOM}(?:[ \t]?(?:\.\.|…|–)[ \t]?(?:{_ATOM}|\d+[a-z]?\b))?(?![\w])"
_SEP = r"(?:[ \t]*[,;/+&][ \t]*(?:(?:and|und|or|oder)[ \t]+)?|[ \t]+(?:and|und|or|oder|to|bis)[ \t]+)"
_LIST = rf"{_ONE}(?:{_SEP}{_ONE})*"
_CLAUDE = r"CLAUDE\.md"
_PLAN = "NXPLANFILE"                   # stands for an internal planning file (doc/todo.yaml, doc/TODO.md, ...)
_NAMED = rf"{_CLAUDE}|{_PLAN}"
LIST_RE = re.compile(_LIST)
ONE_RE = re.compile(_ONE)
LOW_RE = re.compile(_LOW)
TOK_RE = re.compile(rf"(?:{_LIST}|{_NAMED})")
_ANY = re.compile(rf"{_CORE}|{_DER}|{_REV}|{_LOW}|{_NAMED}")
_TRIGGER = re.compile(rf"{_CORE}|{_DER}|{_REV}|{_LOW}|{_CLAUDE}|{_PLAN}|\b(?:pre|vor)-(?:{_P})-\d")
_DATE = r"\d{4}-\d{2}-\d{2}"
_FILLER = {
    "task", "tasks", "aufgabe", "aufgaben", "see", "siehe", "cf", "vgl", "decision", "entscheidung", "follow-up",
    "follow-ups", "followup", "closed", "by", "done", "open", "since", "seit", "with", "mit", "via", "from", "after",
    "nach", "in", "im", "and", "und", "or", "oder", "also", "auch", "todo", "ticket", "item", "items", "part", "teil",
    "of", "für", "for", "e.g", "z.b", "i.e", "the", "a", "an", "implemented", "fixed", "introduced", "added", "moved",
    "user", "nutzer", "update", "updates", "board", "entry", "decisions", "entscheidungsboard", "board-eintrag",
    "order", "wave", "welle", "night", "point", "points", "decided", "answer", "answered", "artefact", "artifact",
}
_MINOR_RE = re.compile(rf"^(?:(?:the\s+)?(?:user|nutzer|decision|entscheidung),?\s*)?{_DATE}$", re.IGNORECASE)

# prepositions in front of an ID that need a phrase instead of nothing; (singular, plural)
_PHRASES = {
    "en": {
        "earlier": ("an earlier change", "earlier changes"),
        "later": ("a later change", "later changes"),
        "follow": ("a follow-up task", "follow-up tasks"),
        "generic": ("a separate task", "separate tasks"),
        "subject": ("a related task", "related tasks"),
        "start": ("A related task", "Related tasks"),
        "start_past": ("An earlier change", "Earlier changes"),
    },
    "de": {
        "earlier": ("einer früheren Änderung", "früheren Änderungen"),
        "later": ("einer späteren Änderung", "späteren Änderungen"),
        "follow": ("zu einer Folgeaufgabe", "zu Folgeaufgaben"),
        "generic": ("einer eigenen Aufgabe", "eigenen Aufgaben"),
        "akk": ("eine eigene Aufgabe", "eigene Aufgaben"),
        "subject": ("eine zugehörige Aufgabe", "zugehörige Aufgaben"),
        "start": ("Eine zugehörige Aufgabe", "Zugehörige Aufgaben"),
        "start_past": ("Eine frühere Änderung", "Frühere Änderungen"),
    },
}
_PREP = {
    "en": {**dict.fromkeys(("since", "by", "was", "were"), "earlier"),
           **dict.fromkeys(("after", "before"), "later"),
           **dict.fromkeys(("until", "till", "pending", "awaiting", "then"), "follow"),
           **dict.fromkeys(("when", "if", "once", "unless", "whether"), "subject"),
           **dict.fromkeys(("with", "in", "of", "from", "per", "under", "see", "via", "through", "for", "on", "at",
                            "like", "as", "into", "to", "is", "are", "within", "than"), "generic")},
    "de": {**dict.fromkeys(("seit", "von"), "earlier"),
           **dict.fromkeys(("nach",), "later"),
           **dict.fromkeys(("bis",), "follow"),
           **dict.fromkeys(("wenn", "sobald", "falls", "ob"), "subject"),
           "vor": "later",
           **dict.fromkeys(("mit", "aus", "bei", "in", "laut", "gemäß", "zu", "unter"), "generic"),
           **dict.fromkeys(("durch", "für", "siehe", "ohne", "um"), "akk")},
}
# a noun in front of the ID already says what it was: the ID just goes ("follow-up task IO-97" -> "follow-up task")
_NOUNS = {"task", "tasks", "aufgabe", "aufgaben", "follow-up", "ticket", "package", "item", "implementation",
          "fix", "proposal", "report", "umsetzung", "point", "points", "decision", "decisions", "entscheidung",
          "review", "entry", "board", "artefact", "artifact", "items", "decided", "wave"}
_PAST = {"made", "did", "had", "brought", "took", "found", "put", "built", "wrote", "ran", "left", "kept", "gave",
         "split", "drew", "began", "became", "chose", "held", "brought", "showed"}

_PH0 = 0xE000                        # private-use characters stand for protected spans
_PH_RE = re.compile("[\ue000-\uf8ff]")


class _Protect:
    """Inline code, link targets, HTML tags and URLs replaced by single placeholder characters."""

    SPAN = re.compile(r"(`+)((?s:.+?))(?<!`)\1(?!`)|\]\([^)\s]*(?:\s+\"[^\"]*\")?\)|<[^>\n]+>|https?://[^\s)>\]]+")

    def __init__(self) -> None:
        self.parts: list[str] = []
        self.links: set[str] = set()

    def _hold(self, text: str, link: bool = False) -> str:
        char = chr(_PH0 + len(self.parts))
        self.parts.append(text)
        if link:
            self.links.add(char)
        return char

    def apply(self, text: str) -> str:
        # a link to CLAUDE.md: the target is not published, only the label stays
        text = re.sub(r"\[([^\]\n]*)\]\([^)\s]*CLAUDE\.md[^)\s]*\)", r"\1", text)
        # links to the private artefact pages of the project: the label stays, the link goes
        text = re.sub(r"\[([^\]\n]*)\]\(https?://claude\.ai/[^)\s]*\)", r"\1", text)
        # an item of the decision board named in code: `x` after "decision", "decision board", "entry"
        text = re.sub(r"(\b(?:[Dd]ecisions?|[Ee]ntscheidung|[Ee]ntry|[Ee]intrag|item)(?:[ \t]+board)?(?:[ \t]+(?:items?|entry))?"
                      r"[ \t]+)`([a-z][a-z0-9_]*(?:-[a-z0-9_]+)+(?::[ \t]*[a-z0-9_]+)?)`",
                      lambda m: m.group(1) + ("NXBOARDITEM" if not re.fullmatch(r"v?\d[\w.-]*", m.group(2))
                                              and m.group(2) not in _TESTS else f"`{m.group(2)}`"), text)

        def span(match: re.Match) -> str:
            whole = match.group(0)
            if match.group(1):
                inner = match.group(2).strip()
                if ONE_RE.fullmatch(inner):
                    return inner
                if re.fullmatch(r"(?:\.\./|doc/)?(?:todo\.yaml(?:::[\w.-]+)?|TODO\.md|koordinator-stand\.md)", inner):
                    return _PLAN
                if inner == "dev-motor":
                    return "the development branch"
                if re.fullmatch(r"integ/[\w.-]+|night\d+", inner):
                    return "an integration branch"
                if re.fullmatch(r"agent/<?[\w-]+>?", inner):
                    return "a task branch"
                if re.fullmatch(r"(?:\.\.?/)*CLAUDE\.md", inner):
                    return "CLAUDE.md"
                if LIST_RE.fullmatch(inner.strip("[](){} ")):     # `[SYS-19, SYS-20]`: a list of IDs only
                    return inner.strip("[](){} ")
                if _ANY.search(inner) and "/" in inner:             # a path named after a task
                    if "changelog.d" in inner:
                        return "a change-log fragment"
                    if "proposals/" in inner:
                        return "a design note"
                    if re.match(r"agent/", inner):
                        return "a task branch"
                if re.fullmatch(r"claude/<\w+>", inner):           # branch name of a cloud session
                    return "a session branch"
                if re.match(r"\.claude/worktrees", inner):          # worktree folder of the agent tooling
                    return "a worktree folder"
                anchor = re.fullmatch(r"([\w./-]+\.md)::([a-z][\w-]*)", inner)
                if anchor and LOW_RE.fullmatch(anchor.group(2)):       # `concept.md::sys23-halt`: board item
                    return self._hold(f"`{anchor.group(1)}`")
                if re.search(r"\bcoordinator\b|agent/<ID>", inner):    # a format or command naming a role
                    return self._hold(whole.replace("coordinator", "maintainer").replace("agent/<ID>", "<task branch>"))
                if re.search(r"agent-<id>|<worktree name>", inner):  # build folder of a task worktree
                    return "a worktree build folder"
                return self._hold(whole)
            if re.match(r"https?://claude\.ai/", whole):              # a private artefact page
                return "NXBOARDITEM"
            return self._hold(whole, link=whole.startswith("]("))
        return self.SPAN.sub(span, text)

    def restore(self, text: str) -> str:
        # a link whose label went: the whole link goes
        for char in self.links:
            text = re.sub(rf"\[[ \t]*{char}", "", text)
        return _PH_RE.sub(lambda m: self.parts[ord(m.group(0)) - _PH0]
                          if ord(m.group(0)) - _PH0 < len(self.parts) else m.group(0), text)


def _count(listing: str) -> int:
    return len(ONE_RE.findall(listing)) or 1


def _last_word(before: str) -> str:
    stripped = re.sub(r"[\s*_`]+$", "", before)
    match = re.search(r"([\wäöüÄÖÜß-]+)$", stripped)
    return match.group(1).lower() if match else ""


def _is_id_item(item: str) -> bool:
    if not _ANY.search(item):
        return False
    rest = TOK_RE.sub(" ", item)
    rest = re.sub(_DATE, " ", rest)
    words = [w for w in re.split(r"[\s,;:/+&*_`.()\-–—]+", rest) if w]
    words = [w for w in words if w.lower().rstrip(".") not in _FILLER]
    return all(w.isdigit() for w in words)


def _is_minor(item: str) -> bool:
    return bool(_MINOR_RE.match(item.strip().strip("*_")))


def _group(lead: str, content: str, lang: str) -> str:
    pieces = re.split(r"(\s*[,;]\s*)", content)
    items, seps = pieces[0::2], [""] + pieces[1::2]
    kinds = ["id" if _is_id_item(i) else "user" if _USER_ITEM.match(i.strip().strip("*_"))
             else "minor" if _is_minor(i) else "real" for i in items]
    if "real" not in kinds:
        return ""                          # IDs, a person, dates: internal evidence only
    dated_user = any(k == "user" and _USER_ITEM.match(i.strip().strip("*_")).group(1) for i, k in zip(items, kinds))
    if dated_user or "id" in kinds and "user" in kinds:
        kinds = ["id" if k == "minor" else k for k in kinds]       # the date of a personal decision goes with it
    full = "(" + content + ")"
    out: list[str] = []
    for item, sep, kind in zip(items, seps, kinds):
        if kind == "id" or (kind == "user" and _USER_ITEM.match(item.strip().strip("*_")).group(1)):
            continue
        if kind == "user":                 # "(user, D-01, ...)": the role
            context = _SAFETY_CONTEXT if _PAGE.get("safety", True) else _SAFETY_CONTEXT_STRICT
            item = ("safety manager" if context.search(full) else "maintainer") if lang == "en" else "Maintainer"
        if _ANY.search(item):
            item = _strip_tokens_in_group(item, lang)
        if out:
            out.append(sep if sep.strip() else ", ")
        out.append(item.strip() if not out else item)
    body = "".join(out).strip()
    return f"{lead}({body})" if body else ""


def _strip_tokens_in_group(item: str, lang: str) -> str:
    """A mixed item of a bracket: a label ID goes, CLAUDE.md after 'rules of' becomes 'the project', other IDs go
    with the prose rules."""
    item = re.sub(rf"^[ \t]*(?:\*\*)?(?:{_LIST})(?:\*\*)?[ \t]*:[ \t]*", "", item)
    item = re.sub(rf"\b(rules?|Regeln)\s+(of|in|aus)\s+{_CLAUDE}", r"\1 of the project", item)
    item = re.sub(rf"\b(in|of|from|see|per|under|im|aus|laut)\s+{_CLAUDE}", r"\1 the project rules", item)
    item = re.sub(rf"[ \t]*{_CLAUDE}[ \t]*", " ", item)
    item = re.sub(rf"\b(in|into|of|from|see|per|under|to|im|aus|laut|in die)\s+{_PLAN}", r"\1 the task list", item)
    return _prose(item, lang, in_group=True).strip()


def _drop(text: str, start: int, end: int) -> tuple[str, int]:
    """Remove text[start:end] with one adjacent list separator; returns the new text and the cut position."""
    before, after = text[:start], text[end:]
    sep_before = re.search(r"[ \t]*(?:[,;/+&]|(?<=[ \t])(?:and|und|or|oder|-|–|—))[ \t]*$", before)
    if sep_before and re.search(r"[\w-)`*]\s*$", before[:sep_before.start()] or ""):
        word = sep_before.group(0).strip()
        before = before[:sep_before.start()]
        if word in ("and", "und", "or", "oder"):
            # "a, b and <ID>" -> "a and b"
            comma = before.rfind(", ")
            if comma >= 0 and "(" not in before[comma:] and "\n" not in before[comma:] and comma > len(before) - 80 \
                    and f" {word} " not in before[comma:]:
                before = f"{before[:comma]} {word} {before[comma + 2:]}"
        return before + after, len(before)
    sep_after = re.match(r"[ \t]*(?:[,;/+&][ \t]*|(?:and|und|or|oder)[ \t]+)(?=[\w-`*])", after)
    if sep_after and not re.search(r"[(\[][ \t]*$", before):
        return before + after[sep_after.end():], len(before)
    if (not before or not before.endswith((" ", "\t"))) and after.startswith(" "):
        after = after[1:]
    return before + after, len(before)


_START_BEFORE = re.compile(r"(?:\A[ \t]*(?:>[ \t]*)*(?:[-*+][ \t]+|\d+[.)][ \t]+)?"
                           r"|\n[ \t]*(?:>[ \t]*)*(?:[-*+][ \t]+|\d+[.)][ \t]+)|[.!?][ \t]+)$")
_VERBISH = {"will", "can", "must", "may", "should", "shall", "has", "is", "was", "does", "did", "had", "would",
            "wird", "ist", "hat", "war", "kann", "muss", "soll"}


def _capitalize_at(text: str, pos: int) -> str:
    match = re.compile(r"[ \t]*([a-zäöü])").match(text, pos)
    if match and not re.match(r"[a-zäöü]+[A-Z_(.]", text[match.start(1):]):
        index = match.start(1)
        return text[:index] + text[index].upper() + text[index + 1:]
    return text


def _verbish(word: str) -> str | None:
    """'past' for a past-tense verb, 'present' for another verb form, None for anything else."""
    if (word.endswith("ed") and len(word) > 4) or word in _PAST:
        return "past"
    if word in _VERBISH or (len(word) > 3 and word.endswith("s") and not word.endswith("ss")):
        return "present"
    return None


def _plan_file(text: str, start: int, end: int, before: str, lang: str, in_group: bool) -> tuple[str, int]:
    """An internal planning file (doc/todo.yaml, doc/TODO.md, koordinator-stand.md): gone from lists and brackets,
    'the task list' where a sentence needs it."""
    after = text[end:]
    if in_group or re.search(r"[-)][ \t]*(?:,|and|und|or)[ \t]*$", before) \
            or re.match(r"[ \t]*(?:,|and|und)[ \t]*[-]", after) \
            or re.match(r"[ \t]*(?:,[ \t]*)?(?:and|und)?[ \t]*" + _PLAN, after):
        return _drop(text, start, end)
    return before + ("the task list" if lang == "en" else "die Aufgabenliste") + after, start + 1


def _claude(text: str, start: int, end: int, before: str, word: str, lang: str, in_group: bool) -> tuple[str, int]:
    after = text[end:]
    if re.match(r"[ \t]*(?:\([^()\n]*\))?[ \t]+is\b", after) and not in_group and word not in ("rules", "rule"):
        return before + "the project rules file" + after, start + 1     # "... `CLAUDE.md` (EW-01) is exported"
    rule =re.search(r"\b(rules?|Regeln)[ \t]+(?:in|of|from|aus)[ \t]*$", before)
    if rule:                                                   # "a project rule in CLAUDE.md" -> "a project rule"
        return text[:rule.end(1)] + after, rule.end(1)
    if word in ("rules", "rule", "regeln") and not in_group:     # "the project rules CLAUDE.md and" -> "... rules and"
        stripped = before.rstrip(" \t`")
        return stripped + after, len(stripped)
    if in_group:
        return _drop(text, start, end)
    if re.search(r",[ \t]*$", before) and re.match(r"[ \t]*[);]", after):
        return _drop(text, start, end)                         # last reason item of a bracket over two lines
    verb = re.match(r"[ \t]+requires\b", after)
    if verb:
        return before + "the project rules require" + after[verb.end():], start + 1
    if re.search(r"[-][ \t]*(?:,|and|und)[ \t]*$", before) or re.match(r"[ \t]*,[ \t]*[-]", after):
        return _drop(text, start, end)                         # one item of a list of files
    return before + ("the project rules" if lang == "en" else "den Projektregeln") + after, start + 1


def _prose(text: str, lang: str, in_group: bool = False) -> str:
    phrases, preps = _PHRASES[lang], _PREP[lang]
    pos = 0
    while True:
        match = TOK_RE.search(text, pos)
        if not match:
            return text
        token, start, end = match.group(0), match.start(), match.end()
        before = text[:start]
        # emphasis wrapped around the token goes with it
        if before.endswith("**") and text[end:end + 2] == "**":
            start, end = start - 2, end + 2
        elif re.search(r"(?<!\*)\*$", before) and text[end:end + 1] == "*" and text[end:end + 2] != "**":
            start, end = start - 1, end + 1
        before, after = text[:start], text[end:]
        word = _last_word(before)
        if token.startswith("CLAUDE"):
            text, pos = _claude(text, start, end, before, word, lang, in_group)
            continue
        if token == _PLAN:
            text, pos = _plan_file(text, start, end, before, lang, in_group)
            continue
        plural = _count(token) > 1
        # "Implementation: PLAT-54 (move of the port), PLAT-55 (...)." -> "Implementation: move of the port, ..."
        desc = re.match(r"[ \t]+\(([^()]{3,120})\)(?=[,;.)]|[ \t]+(?:and|und)\b|$)", after)
        if desc and not _ANY.search(desc.group(1)) and (re.search(r"(?:^|[:,;(])[ \t]*$", before)) \
                and word not in preps:
            text, pos = before + desc.group(1) + after[desc.end():], start + 1
            continue
        if not in_group and re.search(r":[ \t]+$", before) and re.match(r"[ \t]+[a-z]", after) \
                and _verbish((re.match(r"[ \t]+(\w+)", after) or [None, ""])[1]):
            text, pos = before + phrases["subject"][plural] + after, start + 1   # "descriptors: SYS-21 must fit"
            continue
        if not in_group and _START_BEFORE.search(before):
            label = re.match(r"[ \t]*(?:[:–—]|-(?=[ \t]))[ \t]*", after)
            if label:
                text = before + after[label.end():]
                text, pos = _capitalize_at(text, start), start
                continue
            nxt = re.match(r"[ \t]+(?:\([^()\n]*\)[ \t]+)?([A-Za-zäöüß][\w-]*)(:?)", after)
            form = _verbish(nxt.group(1)) if nxt and not nxt.group(2) and nxt.group(1).islower() else None
            if nxt and not nxt.group(2) and preps.get(nxt.group(1)) and nxt.group(1) not in ("is", "are", "was", "were"):
                form = "present"                 # "TOOL-57 for the safety impact" -> "A related task for ..."
            if form:
                phrase = phrases["start_past" if form == "past" else "start"][plural]
                text, pos = before + phrase + after, start + 1
                continue
            text, pos = _drop(text, start, end)
            text = _capitalize_at(text, pos)
            continue
        if word in _NOUNS:                       # "follow-up task IO-97; ..." -> "follow-up task; ..."
            stripped = before.rstrip(" \t")
            text, pos = stripped + after, len(stripped)
            continue
        kind = preps.get(word)
        if kind:
            phrase = phrases[kind][plural]
            # "of SYS-60 (safety plan)" -> "of the safety plan"
            desc = re.match(r"[ \t]+\(([a-z][^()\n,;]{0,60})\)", after)
            if desc and lang == "en" and len(desc.group(1).split()) <= 6 and kind == "generic" and \
                    not re.match(rf"(?:the|a|an|see|{_DATE})\b", desc.group(1)):
                text = before + "the " + desc.group(1) + after[desc.end():]
            else:
                text = before + phrase + after
            pos = start + 1
            continue
        text, pos = _drop(text, start, end)


_LINE_START = r"(?m)^([ \t]*(?:>[ \t]*)*(?:[-*+][ \t]+|\d+[.)][ \t]+)?)"
_LABEL_RE = re.compile(_LINE_START + rf"(\*\*|\*)?(?:{_LIST})(?:\*\*|\*)?[ \t]*"
                                     rf"(?:[:–—](?:\*\*|\*)?|(?<=\*)(?=[ \t]))[ \t]*")


def _sentence_start(match: re.Match) -> bool:
    """First line of the block, or a line that opens a list item or quote (no continuation line)."""
    return match.start() == 0 or bool(re.search(r"[-*+>]|\d[.)]", match.group(1)))


def _labels(text: str) -> str:
    """'**SYS-19:** text', 'SYS-75: text' at the start of a line or list item: the label goes."""
    def label(match: re.Match) -> str:
        if not _sentence_start(match):
            return match.group(0)
        return match.group(1) + "\x00CAP"
    text = _LABEL_RE.sub(label, text)
    return re.sub(r"\x00CAP([a-zäöü])(?![a-zäöü]*[A-Z_(.])", lambda m: m.group(1).upper(), text).replace("\x00CAP", "")


def _tidy(text: str) -> str:
    text = re.sub(r"\(\s*[,;:–—]\s*", "(", text)
    text = re.sub(r"\s*[,;]\s*\)", ")", text)
    text = re.sub(r"[ \t]+[-–—][ \t]*\)", ")", text)
    text = re.sub(r"[ \t]*\(\s*\)", "", text)
    text = re.sub(r"\*\*\s*\*\*", "", text)
    text = re.sub(r"(?<=[^\s])[ \t]+([,.;:)!?])(?!\w)", r"\1", text)
    text = re.sub(r"\([ \t]+", "(", text)
    text = re.sub(r"([,;])[ \t]*[,;]", r"\1", text)
    text = re.sub(r"[,;][ \t]*([.:])(?=\s|$)", r"\1", text)
    text = re.sub(r"(?<=\S)[ \t]{2,}(?=\S)", " ", text)
    text = re.sub(r"(?m)^([ \t]*(?:[-*+]|\d+\.)[ \t])[ \t]+", r"\1", text)
    text = re.sub(r"(?m)^([ \t]*(?:[-*+]|\d+\.)[ \t]+)[,;:][ \t]*", r"\1", text)
    text = re.sub(r"(?<=\S)[ \t]+$", "", text, flags=re.M)
    text = re.sub(r"(?m)^([ \t]*)[:;,][ \t]+(?=\S)", r"\1", text)
    text = re.sub(r"([—–])[ \t]*[—–]", r"\1", text)
    return text


def clean_text(text: str, lang: str = "en", cell: bool = False) -> str:
    """Sanitize one Markdown text block (no code fence, no table row, may span lines)."""
    if not _TRIGGER.search(text) and "claude" not in text.lower() and not _ROLE_TRIGGER.search(text):
        return text
    guard = _Protect()
    work = guard.apply(text)
    if not _TRIGGER.search(work) and not _ROLE_TRIGGER.search(work):
        return guard.restore(work)
    original = work
    work = _mark(work)
    # "the pre-SYS-37 code" -> "the earlier code"
    work = re.sub(rf"\b(?:pre|vor)-(?:{_P})-\d+[a-z]?\b", lambda m: "earlier" if lang == "en" else "frühere", work)
    # brackets: innermost first, a few rounds for nesting
    group = re.compile(r"([ \t]*)\(([^()]*)\)")
    for _ in range(3):
        new = group.sub(lambda m: _group(m.group(1), m.group(2), lang)
                        if _ANY.search(m.group(2)) or _USER_IN_GROUP.search(m.group(2)) else m.group(0), work)
        if new == work:
            break
        work = new
    work = _labels(work)
    work = _prose(work, lang)
    work = _roles(work, lang)
    if work != original:
        lines_new, lines_old = work.split("\n"), original.split("\n")
        if len(lines_new) == len(lines_old):
            work = "\n".join(_tidy(n) if n != o else n for n, o in zip(lines_new, lines_old))
        else:
            work = _tidy(work)
    out = guard.restore(work).replace(_PLAN, "the task list").replace("NXBOARDITEM", "")
    if lang == "de":
        for english, german in (("the development branch", "der Entwicklungszweig"), ("an integration branch",
                                "ein Integrationszweig"), ("a task branch", "ein Aufgabenzweig"), ("the task list",
                                "die Aufgabenliste"), ("a change-log fragment", "ein Changelog-Fragment"),
                                ("a design note", "eine Entwurfsnotiz"), ("the project rules", "die Projektregeln")):
            out = out.replace(english, german)
    if cell:
        out = re.sub(r"^[ \t]*[,;:][ \t]*", " ", out)
    return out


# ----------------------------------------------------------------------------- stage 2: roles, persons, boards

# words that show the internal work organisation, persons and boards (stage 2); a text without them is left alone
_ROLE_TRIGGER = re.compile(r"(?i)coordinator|koordinator|\bagent|cloud[- ]session|\bwaves?\b|\bwelle|\bnight|integ/|"
                           r"dev-motor|\buser|\bnutzer|decision board|entscheidungsboard|scratchpad|claude\.ai|"
                           r"todo\.yaml|todo\.md|koordinator-stand|internal working|artefact|artifact|\(order \d+\)|"
                           r"session vscode|[A-Za-z0-9]{22}|branch (?:an?|the) \w+ branch|\b(?:by|per|after|following|under|with) decision\b|\b[Dd]ecisions? [a-z][a-z0-9]*-|\[Decided[ \t]+\]")
_SAFETY_CONTEXT = re.compile(r"(?i)review|accept|approv|decid|decision|releas|confirm|re-check|\bD-01\b|\bCR-\d|"
                             r"\bCM-\d|closure|responsib|escalat|residual|safety|sign")
# outside the safety documents only the safety process itself makes "the user" the safety manager
_SAFETY_CONTEXT_STRICT = re.compile(r"\bD-01\b|safety (?:manager|responsible|review)|confirmation review|"
                                    r"\bCR-\d|\bCM-\d")
_PAGE: dict[str, bool] = {}          # what clean_page knows about the page being cleaned
_USER_ITEM = re.compile(rf"(?i)^(?:the\s+)?(?:user|nutzer)(?:'s)?(?:\s+(?:decision|wish|request|entscheidung))?"
                        rf"(?:[ ,]+(?:of\s+|am\s+|on\s+)?({_DATE}))?$")
_USER_IN_GROUP = re.compile(rf"(?i)(?:^|[,;]\s*)(?:the\s+)?(?:user|nutzer)\b")


def _is_artefact_id(word: str) -> bool:
    """A 22-character page ID of the private artefacts (VUvnHk7QPZNqDUgAAYoMjr), not a camelCase identifier."""
    return (bool(re.search(r"\d", word)) and len(re.findall(r"[A-Z]", word)) >= 4 and len(re.findall(r"[a-z]", word)) >= 3
            and len(re.findall(r"[A-Z][a-z]{2,}", word)) < 3)


def _mark(text: str) -> str:
    """Before the brackets: artefact IDs and (order NNN) become board items, so the ID rules take them."""
    text = re.sub(r"\b[A-Za-z0-9]{22}\b", lambda m: "NXBOARDITEM" if _is_artefact_id(m.group(0)) else m.group(0), text)
    text = re.sub(r"[ \t]*\(order \d+\)", "", text)
    # "Decision rtos-u1-anfrage: ...": a board item named in plain text (with a digit or of three parts)
    text = re.sub(r"\b([Dd]ecisions?|[Ee]ntscheidung)[ \t]+([a-z][a-z0-9]*(?:-[a-z0-9]+)+)\b(?![\w./-])",
                  lambda m: m.group(1) + " NXBOARDITEM" if (re.search(r"\d", m.group(2)) or m.group(2).count("-") >= 2)
                  and m.group(2) not in _TESTS else m.group(0), text)
    text = re.sub(r"(?<![\w/.-])doc/(?:todo\.yaml|TODO\.md|koordinator-stand\.md)\b", _PLAN, text)
    # "(decision board, F1/F3)": the board as a bracket item is a source note only
    text = re.sub(r"(?<=\()[ \t]*decision board(?=[ \t]*[,;)])|(?<=[,;] )decision board(?=[ \t]*[,;)])", "NXBOARDITEM", text)
    # where a text came from (a session of a work wave) is no information for the reader
    text = re.sub(r"\b(?:agent|cloud) sessions? of (?:the )?(?:wave|night)\b[ \t]*(?:[\w-]*\d[\w-]*)?,?[ \t]*", "", text)
    text = re.sub(r"[ \t]*\bsession vscode-workspace-[\w-]+", "", text)
    # a work wave named by its date or board ID: "of wave cloud-0930", "the wave 0929"
    text = re.sub(r"\b(?:of |the |of the )?wave[ \t]+(?:cloud-\d{4}|\d{4}\b|[A-Za-z]*-?\d[\w-]*)", lambda m: ("of " if m.group(0).startswith("of") else "") + "the work step", text)
    return text


def _role(text: str, start: int, end: int, lang: str) -> str:
    """The role a 'user' of the project documents stands for, by the words around it."""
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    window = text[max(line_start, start - 160):(line_end if line_end >= 0 else len(text))][:320]
    if lang == "de":
        return "Maintainer"
    context = _SAFETY_CONTEXT if _PAGE.get("safety", True) else _SAFETY_CONTEXT_STRICT
    return "safety manager" if context.search(window) else "maintainer"


def _roles(text: str, lang: str) -> str:
    """Roles of the work organisation, persons and boards in running text (after the IDs went)."""
    if not _ROLE_TRIGGER.search(text):
        return text
    sub = re.sub
    # (a) roles and sessions of the work organisation
    text = sub(r"\b(?:agent|cloud) sessions? of (?:the )?(?:wave|night)\b,?[ \t]*", "", text)
    text = sub(r"[ \t]*\bsession vscode-workspace-[\w-]+", "", text)
    text = sub(r"\b([Tt])he coordinator session\b", lambda m: m.group(1) + "he maintainer", text)
    text = sub(r"\bcoordinator session\b", "maintainer", text)
    text = sub(r"\bcoordinator(s?)\b", r"maintainer\1", text)
    text = sub(r"\bCoordinator(s?)\b", r"Maintainer\1", text)
    text = sub(r"\bKoordinator(?:-Sitzung|sitzung)?\b", "Maintainer", text)
    text = sub(r"\bthe agent briefs\b", "the task briefs", text)
    text = sub(r"\ban agent worktree", "a task worktree", text)
    text = sub(r"\bagent worktree", "task worktree", text)
    text = sub(r"\b(?:local |cloud )?agent session(s?)\b", r"contributor session\1", text)
    text = sub(r"\bcloud[- ]session(s?)\b", r"contributor session\1", text)
    text = sub(r"\ban agent\b", "a contributor", text)
    text = sub(r"\bagents\b", "contributors", text)
    text = sub(r"\bagent\b", "contributor", text)
    text = sub(r"\bAgents\b", "Contributors", text)
    text = sub(r"\bAgent\b", "Contributor", text)
    text = sub(r"\bAgenten\b", "Beitragenden", text)
    text = sub(r"\b(?:the )?night (?=\d{4}-\d{2}-\d{2})", "", text)
    text = sub(r"\bin the night of\b", "on", text)
    text = sub(r"\bthe night (state|merges?|run)\b", r"the \1", text)
    text = sub(r"[ \t]*\b(?:in\s+|of\s+)?the\s+night\b", "", text)
    text = sub(r"(?<=\d{4}-\d{2}-\d{2})[ \t]+night\b", "", text)
    text = sub(r"\bcloud waves\b", "earlier work steps", text)
    text = sub(r"\b(?:running\s+|current\s+)?cloud\s+wave\b", "current work step", text)
    text = sub(r"\bthis wave\b", "this work step", text)
    text = sub(r"\bWelle\b", "Arbeitsschritt", text)
    text = sub(r"\b(?:the )?branch dev-motor\b|\bdev-motor\b", "the development branch", text)
    text = sub(r"\bbranch the development branch\b", "the development branch", text)
    text = sub(r"\bbranch (an?|the) (task|session|integration|development) branch\b", r"\1 \2 branch", text)
    text = sub(r"\bthe the development branch\b", "the development branch", text)
    text = sub(r"(?<![\w/])integ/[\w.-]+", "an integration branch", text)
    text = sub(r"\bthe scratchpad\b", "a private folder", text)
    # (c) the decision board
    text = sub(r"\b(an? )?item on the decision board\b", lambda m: ("a " if m.group(1) else "") + "decision item", text)
    text = sub(r"[ \t]+(?:on|in|to|into) the decision board\b", "", text)
    text = sub(r"\b[Dd]ecision board (items?|entry|entries)\b", lambda m: "decision " + ("items" if m.group(1) in
                                                                                         ("items", "entries") else "item"), text)
    text = sub(r"\b(by|per|after|following|under|with) decision\b(?![ \t]+[A-Z]\w*\d)", r"\1 a project decision", text)
    text = sub(r"\bdecision board\b", "decision log", text)
    text = sub(r"\bDecision board\b", "Decision log", text)
    text = sub(r"\bEntscheidungsboard\w*\b", "Entscheidungsprotokoll", text)
    text = sub(r"\[Decided[ \t]+(?:[a-e][ \t]*)?\]", "[Decided]", text)
    # (b) persons: "the user" is the safety manager or the maintainer; the date of a personal decision goes
    text = sub(r"\bNutzerentscheidung(en)?\b", r"Projektentscheidung\1", text)
    text = sub(r"\b(der|Der|dem|den|Dem|Den) Nutzer\b", r"\1 Maintainer", text)
    text = sub(r"\b(des|Des) Nutzers\b", r"\1 Maintainers", text)
    text = sub(r"\bNutzers\b", "Maintainers", text)
    text = sub(r"\bNutzer\b", "Maintainer", text)
    text = sub(r"\buser (?=(?:for|at) the board\b)", "maintainer ", text)
    text = sub(r"\b(\*\*)?user's decision\b", r"\1safety manager's decision", text)
    text = sub(r"\bthe \*\*user's\*\*", lambda m: f"the **{_role(text, m.start(), m.end(), lang)}'s**", text)
    text = sub(r"(?<![\w-])user as (?:the )?(safety \w+)", r"\1", text)
    def decision(match: re.Match) -> str:
        article = match.group(1) or ""
        if article.lower().startswith("the"):
            return article + "project decision"
        return ("A " if article.startswith("A") else "a ") + "project decision"
    text = sub(rf"\b([Aa] |[Tt]he )?(?:decision of the\s+user|user decision|decision by the\s+user)"
               rf"(?:[ \t]+(?:of|on)[ \t]+{_DATE}|[ \t]+{_DATE})?", decision, text)
    text = sub(rf"\b([Tt])he\s+user as (?:the )?(safety manager|safety responsible|project manager|safety owner|architect"
               rf" and safety owner|reviewer|approver)\b", lambda m: m.group(1) + "he " + m.group(2), text)
    text = sub(rf"\b([Tt])he\s+user's (\w+)(?:[ \t]+of[ \t]+{_DATE})?",
               lambda m: f"{m.group(1)}he {_role(text, m.start(), m.end(), lang)}'s {m.group(2)}", text)
    text = sub(rf"\buser,?[ \t]+{_DATE}\b", lambda m: _role(text, m.start(), m.end(), lang), text)

    def the_user(match: re.Match) -> str:
        return f"{match.group(1)}he {_role(text, match.start(), match.end(), lang)}"
    text = sub(r"\b([Tt])he\s+user\b(?![ \t]+(?:test|guide|interface|manual|code|space|mode|data|program)\b|-)",
               the_user, text)

    def credit(match: re.Match) -> str:                     # "| user (CR-02-1, ...)", "; user (", "issue 2: user ("
        return match.group(1) + _role(text, match.start(), match.end(), lang)
    text = sub(r"((?:^|[|(;:,]|\bissue \d+:)[ \t]*)user(?=[ \t]*(?:\(|,|;|\)|$|\|))", credit, text, flags=re.M)
    text = sub(r"\b(only |)with the user\b", lambda m: m.group(1) + "with the maintainer", text)
    return text


# ----------------------------------------------------------------------------------------- page level

_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")


def _split_row(row: str) -> tuple[str, list[str], str]:
    """Indentation, cells and trailing part of a table row; pipes in code spans and escaped pipes stay."""
    guard = re.compile(r"(`+).+?\1|\\\|")
    held: list[str] = []

    def hold(m: re.Match) -> str:
        held.append(m.group(0))
        return f"\x01{len(held) - 1}\x02"
    work = guard.sub(hold, row)
    indent = re.match(r"\s*", work).group(0)
    body = work.strip()
    lead = body.startswith("|")
    trail = body.endswith("|") and len(body) > 1
    if lead:
        body = body[1:]
    if trail:
        body = body[:-1]
    cells = body.split("|")
    back = lambda s: re.sub(r"\x01(\d+)\x02", lambda m: held[int(m.group(1))], s)  # noqa: E731
    return indent + ("|" if lead else ""), [back(c) for c in cells], "|" if trail else ""


def _join_row(lead: str, cells: list[str], trail: str) -> str:
    return lead + "|".join(cells) + trail


def _clean_cell(cell: str, lang: str) -> tuple[str, bool]:
    """(new cell, emptied by the cleanup)."""
    if not cell.strip():
        return cell, False
    new = clean_text(cell, lang, cell=True)
    if new == cell:
        return cell, False
    if not re.sub(r"[\s*_`–—-]", "", new):
        return " – ", True
    new = " " + new.strip() + " "
    return new, False


def _clean_table(rows: list[str], lang: str) -> list[str]:
    split = [_split_row(r) for r in rows]
    width = len(split[0][1])
    has_sep = len(rows) > 1 and _TABLE_SEP.match(rows[1])
    cleaned: list[list[str]] = []
    emptied: list[list[bool]] = []
    for index, (_, cells, _) in enumerate(split):
        if index == 1 and has_sep:
            cleaned.append(cells)
            emptied.append([False] * len(cells))
            continue
        pairs = [_clean_cell(c, lang) for c in cells]
        cleaned.append([p[0] for p in pairs])
        emptied.append([p[1] for p in pairs])
    drop: set[int] = set()
    if has_sep and all(len(s[1]) == width for s in split) and width > 2:
        body = range(2, len(rows))
        for col in range(width):
            if not len(body):
                break
            if all(emptied[r][col] or not split[r][1][col].strip() or split[r][1][col].strip() in "–—-"
                   for r in body) and any(emptied[r][col] for r in body):
                drop.add(col)
        if len(drop) >= width - 1:
            drop = set()
    out = []
    for (lead, _, trail), cells in zip(split, cleaned):
        out.append(_join_row(lead, [c for i, c in enumerate(cells) if i not in drop], trail))
    return out


def _heading_id(text: str) -> str:
    import markdown
    from pymdownx.slugs import slugify
    md = markdown.Markdown(extensions=["toc", "attr_list"],
                           extension_configs={"toc": {"slugify": slugify(case="lower")}})
    md.convert("# " + text)
    tokens = getattr(md, "toc_tokens", [])
    base = tokens[0]["id"] if tokens else ""
    return base


def _unique(slug: str, used: set[str]) -> str:
    candidate = slug
    while candidate in used or not candidate:
        match = re.match(r"^(.*)_([0-9]+)$", candidate)
        candidate = f"{match.group(1)}_{int(match.group(2)) + 1}" if match else f"{candidate}_1"
    used.add(candidate)
    return candidate


def _clean_heading(line: str, lang: str, used: set[str]) -> str:
    match = re.match(r"^(#{1,6}[ \t]+)(.*?)([ \t]+#+)?[ \t]*$", line)
    if not match:
        return line
    head, text = match.group(1), match.group(2)
    attr = re.search(r"[ \t]*\{[^{}]*\}$", text)
    original_id = None if attr else _unique(_heading_id(text), used)
    new = clean_text(text, lang)
    if new == text:
        return line
    new = re.sub(r"^([\d.]+)[ \t]+[—–:-][ \t]+", r"\1 ", new.strip())
    new = re.sub(r"^[—–:-][ \t]+", "", new)
    new = re.sub(r":[ \t]*\(([^()]*)\)$", r": \1", new)
    new = re.sub(r"^([\d.]+[ \t]+)([a-zäöü])", lambda m: m.group(1) + m.group(2).upper(), new)
    if not re.search(r"[A-Za-zÄÖÜäöü]{2,}", re.sub(r"[\ue000-\uf8ff]", "", new)):
        new = (new + " " if new else "") + ("Further items" if lang == "en" else "Weitere Punkte")
    if original_id and not attr:
        new = f"{new} {{#{original_id}}}"
    return head + new


def _clean_mermaid(line: str, lang: str) -> str:
    if not _ANY.search(line):
        return line

    def quoted(match: re.Match) -> str:
        return '"' + clean_text(match.group(1), lang).strip() + '"'
    new = re.sub(r'"([^"\n]*)"', quoted, line)
    new = re.sub(r'(-\.|--|==)[ \t]*""[ \t]*(\.->|-->|==>|---)', lambda m: m.group(2) if m.group(1) != "-." else "-.->", new)
    keyword = re.match(r"^(\s*(?:opt|alt|else|loop|par|and|rect|critical|break|Note|note)\b[^:\n]*?)(.*)$", new)
    if keyword and _ANY.search(new):
        new = clean_text(new, lang)
    # a message of a sequence diagram: "R-->>H: lock released (..., no line - SYS-175)" - only the text after ':'
    message = re.match(r"^(\s*[\w ]+?\s*(?:--?>>?|--?x|--?\)|-->|->)[+-]?\s*[\w ]+?\s*:)(.*)$", new)
    if message and _ANY.search(message.group(2)):
        new = message.group(1) + " " + clean_text(message.group(2).strip(), lang)
    return new


def page_language(text: str) -> str:
    german = len(re.findall(r"\b(?:und|der|die|das|nicht|ist|mit|für|wird)\b", text))
    english = len(re.findall(r"\b(?:the|and|is|with|not|for|are)\b", text))
    return "de" if german > english else "en"


def _drop_internal_items(lines: list[str], fenced: list[bool]) -> tuple[list[str], list[bool]]:
    """A list item that only explains the internal working files ('... are internal working files') goes."""
    keep_lines, keep_fenced = [], []
    index = 0
    while index < len(lines):
        line = lines[index]
        item = re.match(r"^([ \t]*)[-*+][ \t]", line)
        if item and not fenced[index]:
            end = index + 1
            while end < len(lines) and lines[end].strip() and not fenced[end] and \
                    not re.match(r"^[ \t]*(?:[-*+]|\d+\.)[ \t]", lines[end]) and \
                    len(re.match(r"^[ \t]*", lines[end]).group(0)) > len(item.group(1)):
                end += 1
            if re.search(r"internal\s+working\s+files", " ".join(lines[index:end])):
                index = end
                continue
        keep_lines.append(line)
        keep_fenced.append(fenced[index])
        index += 1
    return keep_lines, keep_fenced


def clean_page(markdown: str, fenced_lines: Callable[[list[str]], list[bool]] | None = None, path: str = "") -> str:
    """Sanitize one page (Markdown source as MkDocs renders it); `path` (tree relative) tells a safety document."""
    _PAGE["safety"] = not path or path.startswith("doc/safety/")
    if not _TRIGGER.search(markdown) and "claude" not in markdown.lower() and not _ROLE_TRIGGER.search(markdown):
        return markdown
    if fenced_lines is None:
        from tools.distcheck import distcheck as core
        fenced_lines = core.fenced_lines
    lang = page_language(markdown)
    ending = "\n" if markdown.endswith("\n") else ""
    lines = markdown.split("\n")
    if ending:
        lines = lines[:-1]
    fenced = fenced_lines(lines)
    lines, fenced = _drop_internal_items(lines, fenced)
    out: list[str] = []
    block: list[str] = []
    table: list[str] = []
    used: set[str] = set()
    mermaid = False

    def flush() -> None:
        if block:
            out.extend(clean_text("\n".join(block), lang).split("\n"))
            block.clear()
        if table:
            out.extend(_clean_table(table, lang))
            table.clear()

    for line, is_code in zip(lines, fenced):
        if is_code:
            flush()
            fence = re.match(r"^\s*(```+|~~~+)\s*(\w*)", line)
            if fence:
                mermaid = fence.group(2) == "mermaid" if not mermaid else False
                out.append(line)
            else:
                out.append(_clean_mermaid(line, lang) if mermaid else line)
            continue
        mermaid = False
        stripped = line.strip()
        if re.fullmatch(r"<!--.*-->", stripped) and (_ANY.search(stripped) or "claude" in stripped.lower()):
            flush()                    # an HTML comment is invisible but in the page source: it goes
            continue
        if stripped.startswith("|") or (table and stripped and "|" in stripped):
            if block:
                flush()
            table.append(line)
            continue
        if table:
            flush()
        if re.match(r"^#{1,6}[ \t]", line):
            flush()
            out.append(_clean_heading(line, lang, used))
            continue
        if not stripped or re.match(r"^\s*\[(?!\^)[^\]]+\]:\s", line):
            flush()
            out.append(line)
            continue
        block.append(line)
    flush()
    return "\n".join(out) + ending
