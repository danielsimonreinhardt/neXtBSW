# SPDX-License-Identifier: Apache-2.0
"""Documentation website with MkDocs Material (DOC-71).

The source stays Markdown + Mermaid where it is today: `doc/`, `README.md`, the module documents next to the code
(`libs/**/doc`, `bootloader/doc`, port modules) and the tool READMEs. MkDocs needs all pages below one `docs_dir`,
so the hook `tools/docgen/mkdocs_hooks.py` stages the published pages - with their repository paths, so that every
relative link keeps working - into a temporary folder and points `docs_dir` there. Which pages are published is not
decided here but by the export classification (`tools/distcheck/distribution.yaml`, profile `open`): fail closed,
nothing outside the profile is staged, and `doc/confidential` is excluded explicitly on top.

The public site is built from the exported tree, not from the development tree (DOC-71 point 5): only the export
has the links to unpublished pages replaced by "not published, available on request" (DOC-73), the private sections
cut (DOC-74) and the internal review links substituted (PLAT-114). Building the development tree works for a local
preview, but its links to private pages are dead there and `--strict` fails on them - on purpose.

    python -m tools.docgen.site build --out <dir>            export to a temp folder, build the site there (strict);
                                                              in an exported tree (release repository): build it as is
    python -m tools.docgen.site build --tree <export> --out <dir>   build an already exported tree
    python -m tools.docgen.site pages [--tree <dir>]          list the pages and files that would be staged

Needs the packages of tools/requirements-site.txt (mkdocs, mkdocs-material, mkdocs-static-i18n, mike).
"""

from __future__ import annotations

import argparse
import fnmatch
import posixpath
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    __package__ = "tools.docgen"

from tools.distcheck import distcheck as core          # noqa: E402

REPO = Path(__file__).resolve().parents[2]
CONFIG = "mkdocs.yml"
RULES = Path("tools") / "distcheck" / "distribution.yaml"
# the marker of the full development tree (tools/test/tests.yaml tool `full_tree`): the export has no doc/TODO.md
FULL_TREE_MARKER = Path("doc") / "TODO.md"

# never staged, whatever the classification says: the folder of licensed standards and NDA material (it is
# .gitignore'd and private anyway; this is the explicit second barrier the task asks for)
NEVER = ("doc/confidential/**", "doc/confidential")
# open Markdown files that are no documentation pages: export templates and generated test references
NOT_PAGES = ("tools/distcheck/templates/**", "tools/*/test/reference/**")
# published in the repository (and export) but no page of the website (DOC-83): the project log and the agent rules
# stay in the release repository without a page, navigation entry or search hit; the STM32 family is not part of R1
# (K2: only the short page doc/architecture/stm32-nucleo.md, status planned). Links to these files become text plus
# repository path like any other link into the tree (rewrite_links). Mirrored by exclude_docs in mkdocs.yml.
SITE_EXCLUDE = ("CHANGELOG.md", "CLAUDE.md", "platforms/stm32/**", "mcudb/stm32/**", "boards/nucleo_f446re/**",
                # TOOL-243 (F8 b): not supported yet, private until supported
                "mcudb/rm4x/**", "mcudb/s32k3xx/**",
                # third-party code (README and licence pages of vendored sources) and the single-issue write-ups
                "platforms/**/3rdparty/**", "mcudb/*/sources/**", "doc/openbsw-beitraege/*/issue.md",
                "bootloader/test/layouts/README.md")


@dataclass
class Plan:
    root: Path
    exported: bool
    pages: list[str] = field(default_factory=list)      # Markdown pages, tree relative
    assets: list[str] = field(default_factory=list)     # other published files the pages link to (NOTICE, images)
    published: set[str] = field(default_factory=set)    # every file of the open profile
    dirs: set[str] = field(default_factory=set)         # every folder holding a published file
    submodules: list[str] = field(default_factory=list)  # published submodule paths (external/openbsw)

    @property
    def staged(self) -> set[str]:
        return set(self.pages) | set(self.assets)


def _matches(path: str, globs: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatchcase(path, glob) for glob in globs)


def is_export(root: Path) -> bool:
    """True if `root` is an exported tree (no full development state)."""
    return not (root / FULL_TREE_MARKER).exists()


def plan(root: Path) -> Plan:
    """The pages and linked files the site of `root` consists of: open profile only, never doc/confidential."""
    root = root.resolve()
    rules = core.load_rules(root / RULES)
    exported = is_export(root)
    tree = core.build_tree(root, rules, use_git=not exported, exported=exported)
    published = {p for p in tree.exported("open") if not _matches(p, NEVER)}
    result = Plan(root=root, exported=exported, published=published)
    for path in published:
        parts = path.split("/")
        result.dirs.update("/".join(parts[:depth]) for depth in range(1, len(parts)))
    # the gitlink is a file entry in the development tree only; the export keeps just .gitmodules
    result.submodules = _submodule_paths(root) if ".gitmodules" in published else []
    result.pages = sorted(p for p in published if p.endswith(".md") and not _matches(p, NOT_PAGES + SITE_EXCLUDE))
    assets: set[str] = set()
    for page in result.pages:
        text = tree.text(page) or ""
        lines = text.splitlines()
        fenced = core.fenced_lines(lines)
        for line, is_code in zip(lines, fenced):
            if is_code:
                continue
            hrefs = [m.group(3) for m in core.MD_LINK_RE.finditer(line)]
            definition = core.MD_REF_DEF_RE.match(line)
            if definition:
                hrefs.append(definition.group(2))
            for href in hrefs:
                target = core.link_target(page, href)
                if target and target in published and not target.endswith(".md") and not _matches(target, SITE_EXCLUDE):
                    assets.add(target)
    result.assets = sorted(assets)
    return result


def _submodule_paths(root: Path) -> list[str]:
    gitmodules = root / ".gitmodules"
    if not gitmodules.is_file():
        return []
    return [line.split("=", 1)[1].strip() for line in gitmodules.read_text(encoding="utf-8").splitlines()
            if line.strip().startswith("path") and "=" in line]


def in_repository(result: Plan, target: str) -> bool:
    """True if a link target lies in the published tree without being a page or file of the site: a folder, a file
    that is no page (export template, test reference) or a file inside a submodule (external/openbsw/...)."""
    target = target.rstrip("/")
    if not target or target in result.staged:
        return False
    if target in result.published or target in result.dirs:
        return True
    return any(target.startswith(sub + "/") for sub in result.submodules)


def rewrite_links(page: str, text: str, result: Plan, source_url: str = "") -> str:
    """Links of `page` that work on the code host but not in the site, rewritten for the site (DOC-71).

    A link to a published folder with a README.md page goes to that page. Any other link into the published tree that
    is no page or file of the site (a folder of code, external/openbsw/...) goes to `source_url`/<path> if the site
    has one (set on publication), otherwise it becomes its text plus the repository path. Links to paths that do not
    exist or are not published stay untouched, so MkDocs still reports them as dead (strict build fails)."""
    lines = text.splitlines(keepends=True)
    fenced = core.fenced_lines(lines)
    base = page.rsplit("/", 1)[0] if "/" in page else "."

    def inline(match: re.Match) -> str:
        bang, label, href = match.group(1), match.group(2), match.group(3)
        target = core.link_target(page, href)
        if bang or target is None or not in_repository(result, target):
            return match.group(0)
        target = target.rstrip("/")
        anchor = href.split("#", 1)[1] if "#" in href else ""
        readme = f"{target}/README.md"
        if target in result.dirs and readme in result.staged:
            return f"[{label}]({posixpath.relpath(readme, base)}{'#' + anchor if anchor else ''})"
        if source_url:
            return f"[{label}]({source_url.rstrip('/')}/{target})"
        plain = label.strip("` ")
        if plain and target.endswith(plain):
            return label
        return f"{label} (repository: `{target}`)"

    out = []
    for line, is_code in zip(lines, fenced):
        out.append(line if is_code else core.MD_LINK_RE.sub(inline, line))
    return "".join(out)


def stage(root: Path, dest: Path) -> Plan:
    """Copy the pages and linked files of `root` to `dest`, keeping their tree relative paths."""
    result = plan(root)
    for rel in (*result.pages, *result.assets):
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(result.root / rel, target)
    return result


# ------------------------------------------------------------------------------------------- build


def build(tree: Path, out: Path, *, strict: bool = True) -> subprocess.CompletedProcess:
    """`mkdocs build` of `tree` (its mkdocs.yml) into `out`. Returns the finished process (output in stdout)."""
    cmd = [sys.executable, "-m", "mkdocs", "build", "--clean", "-f", str(tree / CONFIG), "-d", str(out)]
    if strict:
        cmd.append("--strict")
    return subprocess.run(cmd, cwd=tree, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          stdin=subprocess.DEVNULL)


def export_tree(repo: Path, out: Path) -> Path:
    """Write the open export of `repo` to `out` (tools/distcheck, without the check gate and without .git)."""
    from tools.distcheck import exporter
    rules = core.load_rules(repo / RULES)
    tree = core.build_tree(repo, rules)
    exporter.export(repo, rules, out, "open", force=True, tree=tree)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tools.docgen.site", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build", help="build the site (strict)")
    b.add_argument("--tree", type=Path, default=None,
                   help="exported tree to build; default: export this repository to a temp folder first")
    b.add_argument("--out", type=Path, required=True, help="site folder")
    b.add_argument("--no-strict", action="store_true", help="warnings do not fail the build (preview only)")
    p = sub.add_parser("pages", help="list what would be staged")
    p.add_argument("--tree", type=Path, default=REPO)
    args = parser.parse_args(argv)

    if args.command == "pages":
        result = plan(args.tree)
        print(f"{'export' if result.exported else 'development tree'}: {len(result.pages)} page(s), "
              f"{len(result.assets)} linked file(s)")
        for rel in result.pages:
            print(f"  {rel}")
        for rel in result.assets:
            print(f"  (file) {rel}")
        return 0

    with tempfile.TemporaryDirectory(prefix="nx-site-export-") as tmp:
        # in the release repository the checkout already is the export: exporting it again would apply
        # export.overlay a second time and fail (DemoSwc already replaced) - build it as it is
        if args.tree:
            tree = args.tree.resolve()
        elif is_export(REPO):
            tree = REPO
        else:
            tree = export_tree(REPO, Path(tmp) / "export")
        done = build(tree, args.out.resolve(), strict=not args.no_strict)
        sys.stdout.write(done.stdout)
        sys.stderr.write(done.stderr)
        return done.returncode


if __name__ == "__main__":
    sys.exit(main())
