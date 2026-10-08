# SPDX-License-Identifier: Apache-2.0
"""Export of the open tree (PLAT-50, decisions F5 and F11 of 2026-09-17).

The export writes a *fresh tree without git history*: only the files of the profile are copied, the files that carry
internal knowledge (CHANGELOG.md, doc/todo.yaml, CLAUDE.md) are written in a cleaned form, and LICENSE, NOTICE and
CONTRIBUTING.md are generated from the classification. Nothing is published here - the tree is written to a
directory the caller names, and the caller decides what happens with it.

Rule R9 of the concept is the proof that counts: build and test the exported tree (verify()). An export that does
not build is worthless, therefore verify() runs the host tests and at least one target build *in the exported tree*
and reports every skipped test.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Sequence

import yaml

from . import distcheck as core

HERE = Path(__file__).resolve().parent


class ExportError(Exception):
    """The export cannot be written (target not empty, template missing, clean rule unusable)."""


@dataclass
class ExportResult:
    out: str
    profile: str
    copied: int = 0
    cleaned: list[str] = field(default_factory=list)
    substituted: list[str] = field(default_factory=list)     # PLAT-114: export.substitute applied
    # DOC-73: replacements of the export per file - links to unpublished files and export.substitute - and the
    # report next to the exported tree that lists them (file, count, every single place) for the sample check
    links: dict[str, int] = field(default_factory=dict)
    substitutions: dict[str, int] = field(default_factory=dict)
    sections: dict[str, int] = field(default_factory=dict)   # DOC-74: private sections cut per file
    replacement_report: str = ""
    gitlinks: dict[str, str] = field(default_factory=dict)   # TOOL-236: submodule path -> commit of the source tree
    gitlink_file: str = ""                                    # the same as `git update-index --index-info` input
    overlaid: dict[str, str] = field(default_factory=dict)    # PLAT-119: export.overlay target -> source
    generated: list[str] = field(default_factory=list)
    dropped: int = 0
    notes: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, ensure_ascii=False)


# ------------------------------------------------------------------------------------------- export


def export(root: Path, rules: core.Rules, out: Path, profile: str = "open", *, use_git: bool = True,
           force: bool = False, git_init: bool = False, tree: core.Tree | None = None) -> ExportResult:
    """Write the tree of the profile to `out`. Existing content is only removed with force=True."""
    if profile not in core.PROFILES:
        raise ExportError(f"unbekanntes Profil {profile!r}")
    tree = tree if tree is not None else core.build_tree(root, rules, use_git=use_git)
    out = out.resolve()
    if out.exists() and any(out.iterdir()):
        if not force:
            raise ExportError(f"Zielordner {out} ist nicht leer (--force überschreibt ihn)")
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)

    inside = tree.exported(profile)
    result = ExportResult(out=str(out), profile=profile, dropped=len(tree.files) - len(inside))
    clean_rules = {str(entry.get("file")): entry for entry in (rules.export.get("clean") or [])}
    # PLAT-119: an overlay that cannot be applied stops the export before anything is written into the target
    problems = core.overlay_findings(tree, rules, profile, exported_tree=False)
    if problems:
        raise ExportError("export.overlay nicht anwendbar: " + "; ".join(f.text for f in problems))

    index = core.LinkIndex.of(tree, rules, profile)
    places: list[core.LinkReplacement] = []
    cuts: list[core.PrivateCut] = []

    def uncut(rel: str, text: str) -> str:
        """DOC-74: private sections (export:private-begin/-end) become one line naming them."""
        try:
            new, found = core.cut_private_sections(rel, text)
        except core.PrivateSectionError as exc:
            raise ExportError(f"{rel}:{exc.line}: Export-Schnitt nicht moeglich: {exc}") from exc
        if found:
            result.sections[rel] = len(found)
            cuts.extend(found)
        return new

    def unlinked(rel: str, text: str) -> str:
        """DOC-73: links to unpublished files become `text (not published, available on request)`."""
        new, found = core.rewrite_unpublished_links(rel, text, index)
        if found:
            result.links[rel] = len(found)
            places.extend(found)
        return new

    for rel in inside:
        if not (root / rel).is_file():
            # gitlink of a submodule: the content comes from upstream, only .gitmodules is exported
            result.notes.append(f"{rel}: keine Datei (Submodul-Verweis), nicht kopiert")
            continue
        target = out / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        entry = clean_rules.get(rel)
        if entry is None:
            original = tree.text(rel)
            replaced = core.substituted(rules, rel, original) if rules.substitutions else None
            if replaced is not None:
                result.substituted.append(rel)
                result.substitutions[rel] = core.substitution_count(rules, rel, original)
            text = replaced if replaced is not None else original
            if text is not None and rel.endswith(core.LINK_SUFFIXES):
                text = unlinked(rel, uncut(rel, text))
            if text is None or (replaced is None and rel not in result.links and rel not in result.sections):
                shutil.copy2(root / rel, target)
            else:
                # PLAT-114/DOC-73: only the matched text changes, line ends and the rest of the file stay as they are
                target.write_bytes(text.encode("utf-8"))
            result.copied += 1
            continue
        text = clean_file(rel, tree, entry, rules)
        target.write_text(unlinked(rel, uncut(rel, text)), encoding="utf-8", newline="\n")
        result.cleaned.append(rel)
        result.copied += 1
    for rel, entry in clean_rules.items():
        if rel not in inside:
            # the original is not part of the profile (CHANGELOG, todo.yaml, CLAUDE.md are private, decision F11);
            # only its cleaned form belongs into the export, marked in distribution.yaml with cleaned: true
            target = out / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(unlinked(rel, uncut(rel, clean_file(rel, tree, entry, rules))), encoding="utf-8",
                              newline="\n")
            result.cleaned.append(rel)
            result.copied += 1

    # PLAT-119 (F9 A): substitutes for a path the profile lacks (the Simulink code of swc/DemoSwc), byte for byte
    for overlay in core.active_overlays(tree, rules, profile):
        for target, source in overlay.files.items():
            (out / target).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(root / source, out / target)
            result.overlaid[target] = source
        result.notes.append(f"{overlay.replaces} fehlt im Profil {profile}: ersetzt durch "
                            + ", ".join(f"{s} -> {t}" for t, s in overlay.files.items()))

    for rel, text in generate_files(tree, rules, profile, inside).items():
        (out / rel).parent.mkdir(parents=True, exist_ok=True)
        (out / rel).write_text(text, encoding="utf-8", newline="\n")
        result.generated.append(rel)

    # DOC-85: what the export leaves out on purpose, for the consistency tests of the exported tree
    listing = unpublished_list(tree, rules, inside, out)
    if listing is not None:
        rel, text = listing
        (out / rel).parent.mkdir(parents=True, exist_ok=True)
        (out / rel).write_text(text, encoding="utf-8", newline="\n")
        result.generated.append(rel)

    # DOC-73: the list of replacements lies next to the tree, never inside it (the tree stays what is published)
    report = out.parent / f"{out.name}.replacements.md"
    report.write_text(render_replacements(result, places, cuts), encoding="utf-8")
    result.replacement_report = str(report)

    # TOOL-236: .gitmodules alone gives a clone no OpenBSW - `git submodule update --init` needs the gitlink (mode
    # 160000) with the commit the source tree pins. A folder cannot hold a gitlink, so it is recorded next to the tree
    # (index-info format) and put into the index of the one commit by --git-init.
    result.gitlinks = source_gitlinks(root, [rel for rel in inside if not (root / rel).is_file()])
    if result.gitlinks:
        link_file = out.parent / f"{out.name}.gitlinks"
        link_file.write_text(gitlink_index_info(result.gitlinks), encoding="utf-8", newline="\n")
        result.gitlink_file = str(link_file)

    if git_init:
        result.notes.append(init_repository(out, rules, result.gitlinks))
    else:
        result.notes.append("ohne .git - frischer Baum ohne Historie (Entscheidung F5)")
        if result.gitlinks:
            result.notes.append("Gitlinks " + ", ".join(f"{p}@{sha[:12]}" for p, sha in sorted(result.gitlinks.items()))
                                + f" nur in {result.gitlink_file}: vor dem Commit mit "
                                  "`git update-index --index-info < Datei` in den Index (--git-init macht das selbst)")
    return result


def _git(cwd: Path, *args: str, stdin: str | None = None) -> str:
    # stdin as bytes: in text mode Windows adds a carriage return to every line and git reads it as part of
    # the path
    done = subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True,
                          input=None if stdin is None else stdin.encode("utf-8"))
    return done.stdout.decode("utf-8", errors="replace")


def source_gitlinks(root: Path, paths: Sequence[str]) -> dict[str, str]:
    """TOOL-236: the commits the source tree pins for the submodules among `paths` (mode 160000 in its index).

    Only paths that are listed in the source are looked at, so an untracked or unpublished submodule never leaks into
    the export. A source that is no git repository has none."""
    if not paths or not (root / ".git").exists():
        return {}
    try:
        out = _git(root, "ls-files", "-s", "-z", "--", *paths)
    except (OSError, subprocess.CalledProcessError):
        return {}
    links: dict[str, str] = {}
    for entry in out.split("\0"):
        meta, _, path = entry.partition("\t")
        fields = meta.split()
        if len(fields) == 3 and fields[0] == "160000" and fields[2] == "0":
            links[path] = fields[1]
    return links


def gitlink_index_info(links: dict[str, str]) -> str:
    return "".join(f"160000 {sha}\t{path}\n" for path, sha in sorted(links.items()))


def gitlink_problems(repo: Path) -> list[str]:
    """TOOL-236: every submodule of .gitmodules needs a gitlink in HEAD and every gitlink a .gitmodules entry -
    otherwise `git submodule update --init` of a clone silently fetches nothing."""
    declared: set[str] = set()
    try:                                                      # the committed .gitmodules, not the working copy
        for line in _git(repo, "config", "--blob", "HEAD:.gitmodules", "--get-regexp",
                         r"^submodule\..*\.path$").splitlines():
            declared.add(line.split(None, 1)[1].strip())
    except subprocess.CalledProcessError:
        pass                                                  # no .gitmodules or no entry in it
    pinned = {line.split("\t", 1)[1] for line in _git(repo, "ls-tree", "-r", "HEAD").splitlines()
              if line.startswith("160000 ")}
    return ([f"{path}: in .gitmodules, aber kein Gitlink im Commit (Klon bekommt mit git submodule update --init "
             "nichts)" for path in sorted(declared - pinned)]
            + [f"{path}: Gitlink ohne Eintrag in .gitmodules" for path in sorted(pinned - declared)])


def unpublished_list(tree: core.Tree, rules: core.Rules, inside: Sequence[str], out: Path) -> tuple[str, str] | None:
    """DOC-85: (path, text) of the list export.unpublished.file of the exported tree at `out` (unpublished.py), or
    None without that rule. Files: the private files of the scope export.unpublished.files that the export neither
    copies nor writes - never NDA or workstation material, whose names are not published either. Folders: those of
    export.unpublished.folders, named once (each must be wholly private and unpublished). Cleaned: the files of the
    clean rules that the export wrote (published, but not with their original text). Tests and tasks:
    the entries of tools/test/tests.yaml and doc/todo.yaml (export.unpublished.tests/.tasks) that are in the
    development tree but not in their cleaned form under `out`."""
    from . import unpublished

    spec = rules.export.get("unpublished")
    if not spec:
        return None
    rel = str(spec.get("file") or unpublished.LIST)
    scope = [str(glob) for glob in spec.get("files") or []]
    present = set(inside)
    present |= {str(entry.get("file")) for entry in rules.export.get("clean") or []}
    present |= {str(name) for name in rules.export.get("generate") or {}}
    files = [p for p in tree.files if p not in present and tree.cls.get(p) == "private"
             and any(core._match(p, glob) for glob in scope)]
    # whole folders, named once: every file in them must be private and stay out, else the rule is wrong
    folders = []
    for folder in (str(f).rstrip("/") for f in spec.get("folders") or []):
        held = [p for p in tree.files if p.startswith(folder + "/")]
        wrong = [p for p in held if p in present or tree.cls.get(p) != "private"]
        if wrong:
            raise ExportError(f"export.unpublished.folders: {folder} haelt Dateien, die nicht privat oder "
                              f"veroeffentlicht sind: {', '.join(wrong[:3])}")
        if held:
            folders.append(folder)
    cleaned = sorted({str(entry.get("file")) for entry in rules.export.get("clean") or []
                      if (out / str(entry.get("file"))).is_file()})

    tests: list[str] = []
    tests_rel = str(spec.get("tests") or "")
    if tests_rel and (out / tests_rel).is_file():
        pattern = re.compile(r"^  - name:\s*(\S+)", re.MULTILINE)
        kept = set(pattern.findall((out / tests_rel).read_text(encoding="utf-8")))
        tests = [name for name in pattern.findall(tree.text(tests_rel) or "") if name not in kept]

    tasks: dict[str, str] = {}
    tasks_rel = str(spec.get("tasks") or "")
    if tasks_rel and (out / tasks_rel).is_file():
        def of(text: str | None) -> list[dict]:
            data = yaml.safe_load(text or "") or {}
            return [t for t in data.get("tasks") or [] if isinstance(t, dict) and t.get("id")]
        kept = {str(t["id"]) for t in of((out / tasks_rel).read_text(encoding="utf-8"))}
        tasks = {str(t["id"]): str(t.get("status", "open")) for t in of(tree.text(tasks_rel))
                 if str(t["id"]) not in kept}
    return rel, unpublished.render(files, tests, tasks, scope, folders, cleaned)


def render_replacements(result: ExportResult, places: Sequence[core.LinkReplacement],
                        cuts: Sequence[core.PrivateCut] = ()) -> str:
    """DOC-73: the replacements the export made - one row per file with the number of replaced links and of
    export.substitute texts, a total, then every replaced link with line and resolved target. DOC-74: then every
    private section the export cut, with the lines of its markers in the source and the number of anchors it held
    (links to them are among the replaced links, target `file#anchor`)."""
    rows = sorted(set(result.links) | set(result.substitutions))
    total_links, total_subs = sum(result.links.values()), sum(result.substitutions.values())
    lines = [f"# Replacements of the export (profile {result.profile})", "",
             f"Exported tree: `{result.out}`", "",
             f"Links to unpublished files are replaced by their link text plus \"({core.NOT_PUBLISHED})\"; "
             "export.substitute replaces single texts (tools/distcheck/distribution.yaml). The source tree is "
             "unchanged.", "",
             "| File | Links | Substitutions | Total |", "|---|---:|---:|---:|"]
    for rel in rows:
        links, subs = result.links.get(rel, 0), result.substitutions.get(rel, 0)
        lines.append(f"| {rel} | {links} | {subs} | {links + subs} |")
    lines += [f"| **total ({len(rows)} files)** | **{total_links}** | **{total_subs}** | "
              f"**{total_links + total_subs}** |", "",
              "## Replaced links", "",
              "| File | Line | Target (not published) | Link as written |", "|---|---:|---|---|"]
    for place in sorted(places, key=lambda p: (p.path, p.line)):
        written = place.link.replace("|", "\\|").replace("`", "'")
        lines.append(f"| {place.path} | {place.line} | {place.target} | `{written}` |")
    if cuts:
        lines += ["", "## Cut sections", "",
                  "Private sections (`<!-- export:private-begin -->` ... `<!-- export:private-end -->`) are replaced "
                  f"by one line with \"({core.NOT_PUBLISHED})\"; lines of the source tree (the lines of the replaced "
                  "links above are those of the exported file). "
                  f"Total: {len(cuts)} in {len(result.sections)} files.", "",
                  "| File | Lines | Label | Anchors |", "|---|---|---|---:|"]
        for cut in sorted(cuts, key=lambda c: (c.path, c.begin)):
            label = (cut.label or "-").replace("|", "\\|")
            lines.append(f"| {cut.path} | {cut.begin}-{cut.end} | {label} | {len(cut.anchors)} |")
    return "\n".join(lines) + "\n"


def clean_file(rel: str, tree: core.Tree, entry: dict, rules: core.Rules) -> str:
    """Apply one clean rule of distribution.yaml: replace, sections or todo."""
    mode = str(entry.get("mode", "replace"))
    original = tree.text(rel) or ""
    if mode == "replace":
        return _template(entry, rules)
    if mode == "sections":
        return _clean_sections(original, entry, rules)
    if mode == "todo":
        return _clean_todo(original, entry, tree, rules)
    if mode == "json_drop":
        return _clean_json(original, entry)
    if mode == "tests_yaml":
        return _clean_tests(original, entry, tree, set(tree.exported(str(entry.get("profile", "open")))))
    if mode == "reuse_toml":
        return _clean_reuse(original, set(tree.exported(str(entry.get("profile", "open")))))
    raise ExportError(f"{rel}: unbekannter clean-Modus {mode!r} "
                      f"(replace, sections, todo, json_drop, tests_yaml, reuse_toml)")


def _clean_json(text: str, entry: dict) -> str:
    """Drop entries of named JSON arrays (CMake presets of private projects) and everything inheriting them."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ExportError(f"kein lesbares JSON: {exc}") from exc
    drop = [re.compile(p) for p in entry.get("drop_matching") or []]
    removed: set[str] = set()
    for key in entry.get("arrays") or []:
        items = data.get(key)
        if not isinstance(items, list):
            continue
        while True:
            keep = []
            changed = False
            for item in items:
                blob = json.dumps(item, ensure_ascii=False)
                inherits = item.get("inherits") if isinstance(item, dict) else None
                inherits = [inherits] if isinstance(inherits, str) else list(inherits or [])
                if any(pattern.search(blob) for pattern in drop) or any(name in removed for name in inherits):
                    if isinstance(item, dict) and "name" in item:
                        removed.add(str(item["name"]))
                    changed = True
                    continue
                keep.append(item)
            items = keep
            if not changed:
                break
        data[key] = items
    return json.dumps(data, indent=4, ensure_ascii=False) + "\n"


def _template(entry: dict, rules: core.Rules) -> str:
    name = entry.get("template")
    if not name:
        return _fill(str(entry.get("text", "")), rules)
    path = HERE / str(name)
    if not path.is_file():
        raise ExportError(f"Vorlage fehlt: {path}")
    return _fill(path.read_text(encoding="utf-8"), rules)


def _fill(text: str, rules: core.Rules) -> str:
    values = {"COPYRIGHT": str(rules.export.get("copyright", "")),
              "LICENSE": str(rules.export.get("license", "Apache-2.0")),
              "PROJECT": str(rules.export.get("project", "neXtBSW")),
              "UPSTREAM": str(rules.export.get("upstream", ""))}
    for key, value in values.items():
        text = text.replace(f"%%{key}%%", value)
    return text


def _clean_sections(text: str, entry: dict, rules: core.Rules) -> str:
    """Markdown: drop sections by heading, whole list items (bullet plus continuation lines) and single lines by
    regex, prepend an optional note. Lines inside code fences are never taken for headings."""
    drop_headings = [str(h) for h in entry.get("drop_headings") or []]
    drop_bullets = [re.compile(p, re.IGNORECASE) for p in entry.get("drop_bullets") or []]
    drop_lines = [re.compile(p, re.IGNORECASE) for p in entry.get("drop_lines") or []]
    kept: list[str] = []
    skipping_level = 0
    in_fence = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
        heading = None if in_fence else re.match(r"^(#+)\s+(.*)$", line)
        if heading:
            level, title = len(heading.group(1)), heading.group(2).strip()
            if skipping_level and level <= skipping_level:
                skipping_level = 0
            if any(title.startswith(drop) for drop in drop_headings):
                skipping_level = level
                continue
        if not skipping_level:
            kept.append(line)
    # list items: a line starting with "- " or "* " at column 0 plus the indented lines that follow it
    blocks: list[list[str]] = []
    for line in kept:
        is_bullet = re.match(r"^[-*] ", line) is not None
        continues = bool(blocks) and re.match(r"^[-*] ", blocks[-1][0]) is not None \
            and line.startswith("  ") and bool(line.strip())
        if is_bullet or not continues:
            blocks.append([line])
        else:
            blocks[-1].append(line)
    out: list[str] = []
    for block in blocks:
        if re.match(r"^[-*] ", block[0]) and any(p.search(" ".join(block)) for p in drop_bullets):
            continue
        out += [line for line in block if not any(p.search(line) for p in drop_lines)]
    note = str(entry.get("note", ""))
    body = "\n".join(out).rstrip() + "\n"
    body = re.sub(r"\n{3,}", "\n\n", body)
    return (_fill(note, rules).rstrip() + "\n\n" + body) if note else body


def _clean_tests(text: str, entry: dict, tree: core.Tree, inside: set[str]) -> str:
    """tools/test/tests.yaml of the export: drop every test entry that names a file or directory which is not
    exported (NDA driver, Studio, private projects), so the exported tree runs its own test list. Text based, the
    comments of the list stay."""
    drop = [re.compile(p) for p in entry.get("drop_matching") or []]
    # split into blocks: one per test entry, one per line outside entries (comments, keys)
    blocks: list[list[str]] = []
    in_entry = False
    for line in text.splitlines():
        starts_entry = line.startswith("  - name:")
        outside = (bool(line) and not line.startswith(" ")) or line.startswith("  #")
        if starts_entry:
            blocks.append([line])
            in_entry = True
        elif outside or not in_entry:
            blocks.append([line])
            in_entry = False
        else:
            blocks[-1].append(line)

    def name_of(block: list[str]) -> str | None:
        return block[0].split(":", 1)[1].strip() if block[0].startswith("  - name:") else None

    # DOC-73: an entry may read files it names neither in args nor in covers (a test that loads a document of the
    # tree). `reads` lists them per entry: the entry goes when one of them is not exported. `narrow` keeps an entry
    # but replaces a text in its block (e.g. a narrower -k selection) when the named file is not exported.
    reads = {str(name): [str(p) for p in paths or []] for name, paths in (entry.get("reads") or {}).items()}
    narrow = list(entry.get("narrow") or [])

    def unpublished(path: str) -> bool:
        """Not in the export - also when the path is not in the tree at all: an exported tree lacks it already, and
        its test list no longer has the entry. That the rules still fit the development tree is checked by
        tests_yaml_rule_problems() in the repository test."""
        path = path.rstrip("/")
        return not any(p in inside for p in tree.files if p == path or p.startswith(path + "/"))

    # narrowed first (PLAT-119): a `narrow` rule may also take a private file out of an entry's covers, so the entry
    # stays in the export with a smaller selection instead of being dropped as a whole
    for rule in narrow:
        test, old, new = str(rule.get("test", "")), str(rule.get("replace", "")), str(rule.get("with", ""))
        if not unpublished(str(rule.get("reads", ""))):
            continue
        for block in blocks:
            if name_of(block) == test:
                block[:] = [line.replace(old, new) for line in block]
    dropped: set[str] = set()
    for block in blocks:
        name = name_of(block)
        blob = "\n".join(block)
        if name and (any(p.search(blob) for p in drop) or _names_private(blob, tree, inside)
                     or any(unpublished(p) for p in reads.get(name, []))):
            dropped.add(name)
    # entries that run after a dropped one depend on its output: drop them too, until nothing changes
    changed = True
    while changed:
        changed = False
        for block in blocks:
            name = name_of(block)
            if not name or name in dropped:
                continue
            after = re.search(r"^\s*after:\s*\[([^\]]*)\]", "\n".join(block), re.MULTILINE)
            needs = {n.strip() for n in after.group(1).split(",")} if after else set()
            if needs & dropped:
                dropped.add(name)
                changed = True
    # the test files of dropped entries stay in the tree (they are open files); the completeness check of the test
    # runner would report them as unlisted, so they go to inventory.ignore with the reason in a comment
    orphans: list[str] = []
    for block in blocks:
        if name_of(block) in dropped:
            blob = "\n".join(block)
            inline = re.search(r"^\s*covers:\s*\[([^\]]*)\]", blob, re.MULTILINE)
            listed = re.search(r"^\s*covers:\s*\n((?:\s+(?:-\s*\S+|#.*)\n?)+)", blob, re.MULTILINE)
            names = inline.group(1).split(",") if inline else re.findall(r"-\s*(\S+)", listed.group(1)) if listed else []
            orphans += [n.strip() for n in names if n.strip() and n.strip() in inside]
    # a group whose last entry was dropped (pytest: only the Studio) leaves the group list, the runner checks it
    used_groups = {m.group(1) for block in blocks if name_of(block) and name_of(block) not in dropped
                   for m in [re.search(r"^\s*group:\s*(\S+)", "\n".join(block), re.MULTILINE)] if m}
    out: list[str] = []
    for block in blocks:
        name = name_of(block)
        groups_line = re.match(r"^groups:\s*\[([^\]]*)\]\s*$", block[0])
        if groups_line and dropped:
            keep = [g.strip() for g in groups_line.group(1).split(",") if g.strip() in used_groups]
            out.append(f"groups: [{', '.join(keep)}]")
            continue
        if name in dropped:
            out.append(f"  # (Eintrag {name} beim Export entfernt: Inhalt nicht im Profil)")
        elif orphans and re.match(r"^  ignore:\s*\[\s*\]\s*$", block[0]):
            out.append("  # Testdateien, deren Eintrag der Export entfernt hat (tools/distcheck)")
            out.append("  ignore: [" + ", ".join(sorted(set(orphans))) + "]")
        else:
            out.extend(block)
    return "\n".join(out) + "\n"


def tests_yaml_rule_problems(text: str, entry: dict, files: Sequence[str]) -> list[str]:
    """DOC-73: `reads`/`narrow` of a tests_yaml clean rule against the development tree - every named entry exists,
    every named path lies in the tree, every `narrow` text is in its entry. The cleaner itself is lenient (it also
    runs on an exported tree, where entries and files are gone), so a renamed test or file shows up here."""
    names = set(re.findall(r"^  - name:\s*(\S+)", text, re.MULTILINE))
    problems = []

    def in_tree(path: str) -> bool:
        path = path.rstrip("/")
        return bool(path) and any(p == path or p.startswith(path + "/") for p in files)

    for name, paths in (entry.get("reads") or {}).items():
        if name not in names:
            problems.append(f"reads: Eintrag {name} fehlt in der Testliste")
        problems += [f"reads {name}: {p} liegt nicht im Baum" for p in paths or [] if not in_tree(str(p))]
    for rule in entry.get("narrow") or []:
        test, old = str(rule.get("test", "")), str(rule.get("replace", ""))
        if not in_tree(str(rule.get("reads", ""))):
            problems.append(f"narrow {test}: {rule.get('reads')} liegt nicht im Baum")
        block = re.search(rf"^  - name:\s*{re.escape(test)}\s*\n((?:(?!  - name:).*\n?)*)", text, re.MULTILINE)
        if block is None or not old or old not in block.group(1):
            problems.append(f"narrow {test}: Text {old!r} nicht im Eintrag")
    return problems


def _names_private(blob: str, tree: core.Tree, inside: set[str]) -> bool:
    for token in re.findall(r"[A-Za-z0-9_.\-]+(?:/[A-Za-z0-9_.\-*]+)+", blob):
        token = token.rstrip("/").split("*", 1)[0].rstrip("/")
        if not token:
            continue
        hits = [p for p in tree.files if p == token or p.startswith(token + "/")]
        if hits and all(h not in inside for h in hits):
            return True
    return False


def _clean_reuse(text: str, inside: set[str]) -> str:
    """PLAT-114: REUSE.toml of the export - an annotation whose paths match no exported file is dropped together with
    the comment lines directly above it (NDA module, Studio, vendor documents), so the exported tree names only
    licences whose text it carries. Text based: kept blocks and the head of the file stay as written."""
    import tomllib

    lines = text.splitlines()
    starts = [i for i, line in enumerate(lines) if line.strip() == "[[annotations]]"]
    if not starts:
        return text
    begins = []
    for start in starts:
        begin = start
        while begin > 0 and lines[begin - 1].lstrip().startswith("#"):
            begin -= 1
        begins.append(begin)
    out = lines[:begins[0]]
    for index, begin in enumerate(begins):
        end = begins[index + 1] if index + 1 < len(begins) else len(lines)
        block = lines[begin:end]
        try:
            data = tomllib.loads("\n".join(line for line in block if not line.lstrip().startswith("#")))
            paths = data["annotations"][0]["path"]
        except (tomllib.TOMLDecodeError, KeyError, IndexError) as exc:
            raise ExportError(f"REUSE.toml: Annotation ab Zeile {starts[index] + 1} nicht lesbar ({exc})") from exc
        paths = [paths] if isinstance(paths, str) else list(paths)
        if any(core._match(path, glob) for glob in paths for path in inside):
            out.extend(block)
    return "\n".join(out).rstrip("\n") + "\n"


def _clean_todo(text: str, entry: dict, tree: core.Tree, rules: core.Rules) -> str:
    """Public roadmap out of doc/todo.yaml: only tasks of the profile, only public fields, no internal wording."""
    raw = yaml.safe_load(text) or {}
    keep_fields = [str(f) for f in entry.get("keep_fields") or ["id", "title", "prio", "status", "area", "depends"]]
    drop = [re.compile(p, re.IGNORECASE) for p in entry.get("drop_tasks_matching") or []]
    drop_status = {str(s) for s in entry.get("drop_status") or []}
    profile_rank = core.RANK[str(entry.get("profile", "open"))]
    tasks_out = []
    dropped = 0
    for task in raw.get("tasks") or []:
        if not isinstance(task, dict):
            continue
        blob = yaml.safe_dump(task, allow_unicode=True)
        touched_private = any(core.RANK.get(cls, 0) > profile_rank
                              for cls in _classes_of(tree, [str(p) for p in task.get("touches") or []]))
        if touched_private or str(task.get("status", "")) in drop_status \
                or any(pattern.search(blob) for pattern in drop):
            dropped += 1
            continue
        tasks_out.append({key: task[key] for key in keep_fields if key in task})
    header = _fill(str(entry.get("note", "")), rules)
    body = yaml.safe_dump({"areas": raw.get("areas") or {}, "tasks": tasks_out}, allow_unicode=True, sort_keys=False,
                          width=120)
    return f"{header.rstrip()}\n# {dropped} interne Aufgaben entfernt.\n\n{body}" if header else body


def _classes_of(tree: core.Tree, prefixes: Sequence[str]) -> list[str]:
    out = []
    for prefix in prefixes:
        prefix = prefix.rstrip("/")
        for path in tree.files:
            if path == prefix or path.startswith(prefix + "/"):
                out.append(tree.cls.get(path, core.UNCLASSIFIED))
    return out


def generate_files(tree: core.Tree, rules: core.Rules, profile: str, inside: Sequence[str]) -> dict[str, str]:
    """LICENSE, NOTICE and CONTRIBUTING.md of the export (rule R10, decision DCO)."""
    out: dict[str, str] = {}
    for rel, spec in (rules.export.get("generate") or {}).items():
        if str(spec.get("kind", "template")) == "notice":
            out[rel] = render_notice(tree, rules, profile, inside)
        else:
            out[rel] = _template(spec, rules)
    return out


def render_notice(tree: core.Tree, rules: core.Rules, profile: str, inside: Sequence[str]) -> str:
    """Third party notice from the classification: which licence covers how many files, and where it comes from."""
    project_license = str(rules.export.get("license", "Apache-2.0"))
    by_license: dict[str, dict[str, int]] = {}          # licence -> classifying glob -> number of files
    for path in inside:
        lic = tree.license_of.get(path)
        if not lic:
            continue
        rule = tree.rule_of.get(path)
        key = rule.glob if rule is not None else path
        by_license.setdefault(lic, {}).setdefault(key, 0)
        by_license[lic][key] += 1
    lines = [f"{rules.export.get('project', 'neXtBSW')}", "",
             f"Copyright {rules.export.get('copyright', '')}", "",
             f"This work is licensed under the {project_license} licence (see LICENSE).", "",
             "It contains parts that come from third parties or are derived from their work. This overview is",
             "generated from the classification of the tree (tools/distcheck/distribution.yaml); the header of each",
             "file takes precedence over it.", ""]
    for lic in sorted(by_license, key=lambda name: (name != project_license, name)):
        groups = by_license[lic]
        lines.append(f"## {lic} ({sum(groups.values())} files)")
        if lic == project_license:
            lines.append("  the rest of the tree, among it files derived from Eclipse OpenBSW that keep their")
            lines.append("  original copyright header (Accenture, BMW AG)")
        else:
            lines += [f"  - {glob} ({count} files)" for glob, count in sorted(groups.items())]
        lines.append("")
    for note in rules.export.get("notice_notes") or []:
        lines.append(str(note))
    return "\n".join(lines).rstrip() + "\n"


def init_repository(out: Path, rules: core.Rules, gitlinks: dict[str, str] | None = None) -> str:
    """One commit, no history (decision F5). Only called with --git-init. `gitlinks` (TOOL-236) go into the index."""
    message = str(rules.export.get("first_commit", "Erste Veroeffentlichung"))
    try:
        subprocess.run(["git", "init", "-q", str(out)], check=True, capture_output=True, text=True)
        subprocess.run(["git", "-C", str(out), "add", "-A"], check=True, capture_output=True, text=True)
        if gitlinks:
            _git(out, "update-index", "--add", "--index-info", stdin=gitlink_index_info(gitlinks))
        subprocess.run(["git", "-C", str(out), "commit", "-q", "-m", message], check=True, capture_output=True,
                       text=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        return f"git init fehlgeschlagen: {exc}"
    problems = gitlink_problems(out)
    if problems:
        raise ExportError("Export-Repository inkonsistent: " + "; ".join(problems))
    links = f", Gitlink {', '.join(f'{p}@{sha[:12]}' for p, sha in sorted((gitlinks or {}).items()))}" if gitlinks else ""
    return f"frisches Repository mit genau einem Commit: {message}{links}"


# ------------------------------------------------------------------------------------------- R9 build proof


@dataclass
class VerifyStep:
    name: str
    command: list[str]
    status: str = "skip"                 # pass | fail | skip
    seconds: float = 0.0
    tail: str = ""
    log: str = ""                        # full output of the step

    def __str__(self) -> str:
        mark = {"pass": "OK  ", "fail": "FEHL", "skip": "UEBS"}.get(self.status, "????")
        return f"{mark} {self.name} ({self.seconds:.0f} s)"


#: Limit of one verify step in seconds and parallel test groups. The full host suite needs about 100 min with
#: four jobs on a loaded workstation, so a release export may pass a larger limit (0 = none, PLAT-114).
VERIFY_TIMEOUT = 5400
VERIFY_JOBS = 4


def verify(out: Path, rules: core.Rules, *, openbsw: Path | None = None, groups: str = "python,host",
           preset: str | None = None, timeout: int | None = VERIFY_TIMEOUT, python: str | None = None,
           jobs: int = VERIFY_JOBS) -> list[VerifyStep]:
    """R9: run the host tests and one target build *in the exported tree*.

    A folder export has no submodule checkout (the gitlink only lives in the commit, TOOL-236), so a checkout of
    Eclipse OpenBSW has to be handed in with --openbsw. Without it the target build is reported as skipped, never
    silently left out.
    """
    import time

    timeout = timeout or None                         # 0 or None: no limit
    steps: list[VerifyStep] = []
    python = python or sys.executable
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    preset = preset or str(rules.export.get("verify_preset", ""))

    if openbsw is not None:
        link = out / "external" / "openbsw"
        link.parent.mkdir(parents=True, exist_ok=True)
        if not link.exists():
            _link_or_copy(Path(openbsw).resolve(), link)

    def run(name: str, command: list[str], skip: str = "") -> None:
        step = VerifyStep(name=name, command=command)
        if skip:
            step.status, step.tail = "skip", skip
            steps.append(step)
            return
        start = time.time()
        try:
            done = subprocess.run(command, cwd=str(out), env=env, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=timeout)
            step.status = "pass" if done.returncode == 0 else "fail"
            step.tail = (done.stdout or "")[-4000:] + (done.stderr or "")[-2000:]
            # the full output next to the tree, not inside it (the tree stays what was exported)
            log = out.parent / f"{out.name}.verify.{len(steps)}.log"
            log.write_text((done.stdout or "") + "\n" + (done.stderr or ""), encoding="utf-8")
            step.log = str(log)
        except (OSError, subprocess.TimeoutExpired) as exc:
            step.status, step.tail = "fail", str(exc)
        step.seconds = time.time() - start
        steps.append(step)

    group_args = [arg for group in groups.split(",") if group.strip() for arg in ("--group", group.strip())]
    run("Host-Tests", [python, "tools/test", *group_args, "--jobs", str(jobs),
                       "--json", str(out.parent / f"{out.name}.tests.json")])
    if not preset:
        run("Target-Build", [], skip="kein verify_preset in distribution.yaml")
    elif openbsw is None or not (out / "external" / "openbsw" / "CMakeLists.txt").exists():
        run("Target-Build", [], skip="Submodul external/openbsw nicht im Export (--openbsw <Pfad> angeben)")
    else:
        # the export has no .venv of its own: hand the interpreter of this run to the configure step
        run(f"Target-Build {preset}", ["cmake", "--preset", preset, f"-DPython3_EXECUTABLE={python}"])
        if steps[-1].status == "pass":
            run(f"Target-Build {preset} (bauen)", ["cmake", "--build", "--preset", preset])
    return steps


def _link_or_copy(source: Path, link: Path) -> None:
    """Directory junction if the platform allows it, otherwise a copy."""
    try:
        os.symlink(source, link, target_is_directory=True)
        return
    except (OSError, NotImplementedError, AttributeError):
        pass
    if os.name == "nt":
        try:
            subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(source)], check=True, capture_output=True)
            return
        except (OSError, subprocess.CalledProcessError):
            pass
    shutil.copytree(source, link, ignore=shutil.ignore_patterns(".git"))
