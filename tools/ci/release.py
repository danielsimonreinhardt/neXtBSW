# SPDX-License-Identifier: Apache-2.0
"""Release publication of neXtBSW (TOOL-69, doc/architecture/customer-project.md Abschnitt 7 gap N15).

Per release this module gathers the four pieces gap N15 asks for into one local plan:

  - the exact command for the **annotated tag** `vX.Y.Z` (SYS-68) - never run automatically, only ever printed or
    written into the plan. A real tag is created with `create_tag()`, and that function is only ever called
    against a temporary test repository (`tools/ci/test_release.py`); the coordinator/user runs the printed
    command by hand on the real tree, a deliberate act, not something this tool does on its own.
  - the **CI report**, archived under the tag as a copy plus `ci-out-<tag>.zip` with its SHA-256 (SYS-67,
    doc/safety/verification-plan.md section 7), after `report_binding` has checked that it is evidence of exactly
    this tree (verdict, --strict, clean, same tree hash - cm-plan.md gap G1).
  - **release notes** extracted from `CHANGELOG.md`: this tree's changelog is not split into one section per
    release (its "## " headings name tasks, not versions), so the notes are the lines the file gained since the
    previous release tag - what changed, regardless of where in the file it was inserted. The very first release
    takes the whole file.
  - an **export per distcheck profile** (`tools/distcheck`), with the target repository only ever a value in the
    plan: hosting is not decided yet (arch54-hosting, customer-project.md Q2), so a profile's target is `None`
    with a note unless `tools/distcheck/release-targets.yaml` names one. This module never pushes anything to any
    repository, under any flag - only local directories, an optional zip with its SHA-256 (variant 3,
    customer-project.md 4.4), and the plan describing what would go where.

Everything here reads or writes the local tree only; nothing calls `git push`, nothing opens a network connection.

    python -m tools.ci release --version 0.2.0 --report-dir build/ci-out --profile open --archive

Standard library plus `tools.distcheck` (which needs PyYAML); `tools/ci/ci.py` only imports this module inside its
`release` command, so `setup`/`check` keep working with the standard library alone before packages are installed.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import shutil
import subprocess
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
CHANGELOG_FILE = "CHANGELOG.md"
DISTRIBUTION_FILE = REPO / "tools" / "distcheck" / "distribution.yaml"
TARGETS_FILE = REPO / "tools" / "distcheck" / "release-targets.yaml"
GIT_TIMEOUT = 30
MASTER_REFS = ("master", "origin/master")
HOSTING_NOTE = "hosting/publication not decided yet (arch54-hosting, customer-project.md Q2)"


class ReleaseError(Exception):
    """A release step could not run at all (bad version, git failure, target not empty)."""


def git(tree: Path, *args: str) -> tuple[int, str]:
    """(exit code, stdout) of `git -C <tree> <args>`; (-1, reason) if git could not even be started."""
    try:
        done = subprocess.run(["git", "-C", str(tree), *args], capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=GIT_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as exc:
        return -1, str(exc)
    return done.returncode, done.stdout.strip()


def _version_module():
    """tools/nxconfig/version.py loaded on its own (no pydantic dependency), same trick as tools/ci/ci.py."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("nx_version_for_release", REPO / "tools" / "nxconfig" / "version.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _rctag_module():
    """tools/ci/rctag.py (TOOL-232: release candidate tags vX.Y.Z-rcN), loaded by path like the version module."""
    import importlib.util
    import sys

    module = sys.modules.get("nx_rctag")
    if module is None:
        spec = importlib.util.spec_from_file_location("nx_rctag", HERE / "rctag.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    return module


def release_tag(version: str, tag: str | None = None) -> str:
    """The tag of this release: `tag` if given (a release candidate `vX.Y.Z-rcN` of `version`, or `vX.Y.Z`
    itself), else `v<version>`. ReleaseError for a tag of another form or another version (TOOL-232)."""
    nxversion = _version_module()
    if not nxversion.is_version(version):
        raise ReleaseError(f"{version!r} is not MAJOR.MINOR.PATCH")
    if tag is None:
        return nxversion.tag_of(version)
    problem = _rctag_module().problem(tag, version)
    if problem:
        raise ReleaseError(problem)
    return tag


def default_archive_root(env: dict | None = None) -> Path:
    """Where archived CI reports of tagged baselines live. `NX_RELEASE_ARCHIVE`, else a per-user cache directory -
    the same convention as `tools/ci`'s tool cache (`default_cache` in `ci.py`). Never inside the tree: a report
    can be large, is per-machine, and archiving it is not what git history is for."""
    import os

    env = os.environ if env is None else env
    if env.get("NX_RELEASE_ARCHIVE"):
        return Path(env["NX_RELEASE_ARCHIVE"])
    if os.name == "nt":
        return Path(env.get("LOCALAPPDATA", Path.home())) / "nextbsw" / "release-archive"
    return Path(env.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "nextbsw" / "release-archive"


# ---------------------------------------------------------------------------- the tag (SYS-68)


@dataclass
class TagPlan:
    version: str
    tag: str
    exists: bool
    dirty: bool
    version_matches: bool
    on_master: bool
    head: str
    master_ref: str
    command: list[str]
    blocking: list[str] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return not self.blocking


def plan_tag(tree: Path, version: str, message: str | None = None, run=git, tag: str | None = None) -> TagPlan:
    """The exact `git tag -a` command for `version`, and every reason it is not ready yet. Read-only: no git
    state is ever changed here, regardless of what it finds. `tag`: a release candidate `vX.Y.Z-rcN` of `version`
    instead of `vX.Y.Z` (TOOL-232, Release R1 = v1.0.0-rc1)."""
    nxversion = _version_module()
    tag = release_tag(version, tag)
    tree = Path(tree)
    blocking: list[str] = []

    code, head = run(tree, "rev-parse", "HEAD")
    is_git = code == 0
    if not is_git:
        blocking.append(f"{tree} is not a git checkout")

    dirty = False
    if is_git:
        code, out = run(tree, "status", "--porcelain")
        dirty = code == 0 and bool(out)
        if dirty:
            blocking.append("working tree is dirty (git status)")

    exists = False
    if is_git:
        code, _ = run(tree, "rev-parse", "--verify", "--quiet", f"refs/tags/{tag}")
        exists = code == 0
        if exists:
            blocking.append(f"tag {tag} already exists")

    try:
        version_matches = nxversion.read_version(tree) == version
    except nxversion.VersionError as exc:
        version_matches = False
        blocking.append(str(exc))
    if is_git and not version_matches and f"tag {tag} already exists" not in blocking:
        blocking.append(f"VERSION does not read {version!r}")

    master_ref = ""
    on_master = False
    if is_git:
        for ref in MASTER_REFS:
            code, _ = run(tree, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
            if code == 0:
                master_ref = ref
                break
        if not master_ref:
            blocking.append("no master ref found (git fetch origin master)")
        else:
            code, master_head = run(tree, "rev-parse", master_ref)
            on_master = code == 0 and master_head == head
            if not on_master:
                shown = master_head[:12] if code == 0 else "?"
                blocking.append(f"HEAD ({head[:12] if head else '?'}) is not {master_ref} ({shown}); "
                                f"SYS-68 wants the release tag on master")

    command = ["git", "-C", str(tree), "tag", "-a", tag, "-m", message or f"neXtBSW {tag[1:]}"]
    return TagPlan(version=version, tag=tag, exists=exists, dirty=dirty, version_matches=version_matches,
                   on_master=on_master, head=head, master_ref=master_ref, command=command, blocking=blocking)


def create_tag(tree: Path, version: str, message: str | None = None, run=git, tag: str | None = None) -> TagPlan:
    """Actually create the annotated tag - refuses unless `plan_tag` says it is ready. Call this only against a
    temporary test repository; a release of the real tree is the user's deliberate act, run by hand from
    `plan_tag(...).command`, never something this tool does on its own."""
    plan = plan_tag(tree, version, message, run=run, tag=tag)
    if plan.blocking:
        raise ReleaseError("tag not created: " + "; ".join(plan.blocking))
    done = subprocess.run(plan.command, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=GIT_TIMEOUT)
    if done.returncode != 0:
        raise ReleaseError(f"git tag failed: {done.stderr.strip() or done.returncode}")
    return plan


# ---------------------------------------------------------------------------- release notes from CHANGELOG.md


def previous_release_tag(tree: Path, before_version: str | None = None, run=git) -> str | None:
    """The newest release tag `vX.Y.Z` reachable from HEAD, older than `before_version` (None: the newest at
    all). Asked before the new tag exists, so HEAD is an ordinary work-in-progress commit, not the master tip -
    this only names the *previous* baseline the release notes are measured against."""
    nxversion = _version_module()
    code, out = run(tree, "tag", "--list", "v*", "--merged", "HEAD")
    if code != 0 or not out:
        return None
    found = []
    for t in out.split():
        v = nxversion.version_of_tag(t)
        if v is not None:
            found.append((nxversion.parse(v), t))
    if not found:
        return None
    if before_version is not None:
        target = nxversion.parse(before_version)
        found = [(v, t) for v, t in found if v < target]
        if not found:
            return None
    found.sort()
    return found[-1][1]


def changelog_release_notes(tree: Path, version: str, changelog: str = CHANGELOG_FILE, since_tag: str | None = None,
                            run=git) -> tuple[str, str | None]:
    """The new content of `CHANGELOG.md` since `since_tag` (None: resolved via `previous_release_tag`) - the
    lines the file gained, independent of where in the file they were inserted, because this changelog's
    headings name tasks, not versions. The first release (no previous tag) takes the whole file. Returns
    (notes, since_tag actually used)."""
    tree = Path(tree)
    if since_tag is None:
        since_tag = previous_release_tag(tree, version, run=run)
    path = tree / changelog
    current = path.read_text(encoding="utf-8") if path.is_file() else ""
    if since_tag is None:
        return current.strip("\n"), None
    code, old = run(tree, "show", f"{since_tag}:{changelog}")
    old_text = (old + "\n") if code == 0 else ""
    old_lines = old_text.splitlines(keepends=True)
    new_lines = current.splitlines(keepends=True)
    added = [line[1:] for line in difflib.unified_diff(old_lines, new_lines, n=0)
             if line.startswith("+") and not line.startswith("+++")]
    notes = "".join(added).strip("\n")
    if not notes:
        notes = f"(no new content in {changelog} since {since_tag})"
    return notes, since_tag


# ---------------------------------------------------------------------------- CI report archive (SYS-67)


def archive_ci_report(report_dir: Path, tag: str, archive_root: Path | None = None, *, force: bool = False) -> Path | None:
    """Copy a finished CI report (`python -m tools.ci run --out <report_dir>`) into the archive keyed by the
    release tag. Returns the archived path, or None if `report_dir` does not exist (nothing to archive yet)."""
    report_dir = Path(report_dir)
    if not report_dir.is_dir():
        return None
    root = Path(archive_root) if archive_root is not None else default_archive_root()
    dest = root / tag
    if dest.exists():
        if not force:
            raise ReleaseError(f"{dest} already holds an archived report (force to overwrite)")
        shutil.rmtree(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(report_dir, dest)
    return dest


def report_binding(report_dir: Path, tree: Path, version: str | None = None, run=git) -> dict:
    """Is the CI report in `report_dir` evidence for the tree that is about to be tagged? (SYS-67, cm-plan.md G1)

    Bound means: `ci-result.json` exists, verdict pass, a `--strict` run of the tree (not of a workspace), the
    report records its provenance (`tools/ci/ci.py: provenance`), the working tree was clean at the start, HEAD did
    not move during the run, the recorded tree hash equals `HEAD^{tree}` of `tree` (the release merge commit on
    master carries the tree of the candidate on dev, so the same report binds to both), and VERSION matches.
    Every reason against it is named; the release decides what an unbound report means (verification-plan.md 7)."""
    report_dir = Path(report_dir)
    result_file = report_dir / "ci-result.json"
    binding = {"bound": False, "problems": [], "commit": None, "tree": None, "describe": None, "verdict": None,
               "strict": None, "slow": None}
    if not result_file.is_file():
        binding["problems"].append(f"{result_file} missing")
        return binding
    try:
        data = json.loads(result_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        binding["problems"].append(f"{result_file} unreadable: {exc}")
        return binding
    problems = binding["problems"]
    origin = data.get("provenance") or {}
    binding.update(commit=origin.get("commit"), tree=origin.get("tree"), describe=origin.get("describe"),
                   verdict=data.get("verdict"), strict=data.get("strict"), slow=data.get("slow"))
    if data.get("verdict") != "pass":
        problems.append(f"verdict {data.get('verdict')!r}, not 'pass'")
    if not data.get("strict"):
        problems.append("not a --strict run")
    if data.get("workspace"):
        problems.append("a workspace run, not a run of the tree")
    if not origin.get("commit") or not origin.get("tree"):
        problems.append("the report records no commit and tree hash (written before SYS-67, cm-plan.md G1)")
    else:
        if origin.get("dirty") is not False:
            problems.append(f"the working tree was not clean at the start of the run "
                            f"({origin.get('changes_total', '?')} paths)")
        end = origin.get("end") or {}
        if end and (end.get("commit"), end.get("tree")) != (origin["commit"], origin["tree"]):
            problems.append("HEAD moved during the run")
        code, head_tree = run(Path(tree), "rev-parse", "HEAD^{tree}")
        if code != 0 or not head_tree:
            problems.append(f"tree hash of {tree} unknown (no git checkout?)")
        elif head_tree != origin["tree"]:
            problems.append(f"the report ran on tree {origin['tree'][:12]}, HEAD of {tree} has tree {head_tree[:12]}")
        if version is not None and origin.get("version") not in (None, version):
            problems.append(f"the report ran on VERSION {origin.get('version')}, the release is {version}")
    binding["bound"] = not problems
    return binding


# ---------------------------------------------------------------------------- export target (arch54-hosting)


def load_release_targets(path: Path | None = None) -> dict:
    """Where an exported profile would be pushed, per profile - `tools/distcheck/release-targets.yaml` once the
    user decides (arch54-hosting, customer-project.md Q2); without the file, or without an entry for a profile,
    the target is `None` and the plan carries `HOSTING_NOTE` instead. The export itself always happens; only the
    push destination is undecided, and this tool never pushes regardless of what is configured here."""
    path = Path(path) if path is not None else TARGETS_FILE
    if not path.is_file():
        return {}
    import yaml

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return dict(data.get("targets") or {})


# ---------------------------------------------------------------------------- export per profile (distcheck)


@dataclass
class ProfileResult:
    profile: str
    out: str | None
    errors: int
    warnings: int
    exported: bool
    target: str | None
    target_note: str | None
    archive: str | None = None
    sha256: str | None = None


def export_profile(repo: Path, out_dir: Path, profile: str, *, rules_path: Path | None = None,
                    patterns_path: Path | None = None, allow_findings: bool = False,
                    targets: dict | None = None, use_git: bool = True) -> ProfileResult:
    """Check and (unless it has errors) export one distcheck profile, exactly like `python tools/distcheck
    export` - a profile blocked by findings is reported, not silently forced (`--allow-findings` is for trial
    runs there too). `use_git=False` lets host tests point this at a plain synthetic directory (no git checkout),
    the same switch `tools/distcheck/test_distcheck.py` uses for its own synthetic trees."""
    from tools.distcheck import distcheck as dc
    from tools.distcheck import exporter as dc_export

    repo = Path(repo)
    rules = dc.load_rules(Path(rules_path) if rules_path is not None else DISTRIBUTION_FILE)
    patterns = Path(patterns_path) if patterns_path is not None else (repo / rules.pattern_file if rules.pattern_file
                                                                       else None)
    tree = dc.build_tree(repo, rules, use_git=use_git)
    report = dc.check(repo, rules, profile, references=True, private_patterns=patterns, use_git=use_git)
    targets = targets if targets is not None else load_release_targets()
    target = targets.get(profile)
    note = None if target else HOSTING_NOTE

    if report.errors and not allow_findings:
        return ProfileResult(profile=profile, out=None, errors=len(report.errors), warnings=len(report.warnings),
                             exported=False, target=target, target_note=note)
    dc_export.export(repo, rules, Path(out_dir), profile, force=True, tree=tree)
    return ProfileResult(profile=profile, out=str(out_dir), errors=len(report.errors), warnings=len(report.warnings),
                         exported=True, target=target, target_note=note)


def archive_zip_with_sha256(export_dir: Path, out_zip: Path) -> tuple[Path, str]:
    """Variant 3 (customer-project.md 4.4): zip an exported profile as `nextbsw-<version>-<profile>.zip` and
    write its SHA-256 next to it as `<name>.sha256`, in the format R6 already checks (`sha256sum`: hex digest,
    two spaces, file name)."""
    export_dir = Path(export_dir)
    out_zip = Path(out_zip)
    out_zip.parent.mkdir(parents=True, exist_ok=True)
    if out_zip.exists():
        out_zip.unlink()
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(export_dir.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(export_dir).as_posix())
    digest = hashlib.sha256(out_zip.read_bytes()).hexdigest()
    sha_file = out_zip.with_name(out_zip.name + ".sha256")
    sha_file.write_text(f"{digest}  {out_zip.name}\n", encoding="utf-8")
    return out_zip, digest


# ---------------------------------------------------------------------------- the plan


@dataclass
class ReleasePlan:
    version: str
    tag: str
    tag_plan: dict
    since_tag: str | None
    release_notes: str
    release_notes_file: str
    ci_report: str | None
    ci_report_note: str | None
    profiles: list
    hosting_decision: str = HOSTING_NOTE
    # SYS-67: whether the report is evidence for this tree, and the zip of the archived report with its SHA-256
    # (the value the tag message records, cm-plan.md 5.3)
    ci_report_binding: dict | None = None
    ci_report_zip: str | None = None
    ci_report_sha256: str | None = None


def build_release_plan(repo: Path, version: str, *, out_dir: Path, profiles: list[str] = ("open",),
                       report_dir: Path | None = None, archive_root: Path | None = None, archive: bool = False,
                       allow_findings: bool = False, tag_message: str | None = None,
                       targets: dict | None = None, use_git: bool = True, tag: str | None = None) -> ReleasePlan:
    """Everything gap N15 asks for, in one local run: nothing here pushes, nothing here tags for real.

    `tag`: the release tag when it is not `v<version>` - a release candidate `vX.Y.Z-rcN` of `version` (TOOL-232);
    tag plan, archive folder, `ci-out-<tag>.zip` and the release notes file are named after it."""
    tag = release_tag(version, tag)      # before anything is written: a wrong tag is refused with no side effect
    repo = Path(repo).resolve()
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    tag_plan = plan_tag(repo, version, tag_message, tag=tag)

    notes, since_tag = changelog_release_notes(repo, version)
    notes_file = out_dir / f"RELEASE_NOTES-{tag}.md"
    notes_file.write_text(f"# neXtBSW {tag[1:]}\n\n{notes}\n", encoding="utf-8")

    ci_report = None
    ci_note = None
    binding = None
    report_zip = None
    report_sha = None
    if report_dir is None:
        ci_note = "no report_dir given - CI report not archived"
    else:
        if Path(report_dir).is_dir():
            binding = report_binding(report_dir, repo, version)
        try:
            dest = archive_ci_report(report_dir, tag, archive_root)
        except ReleaseError as exc:
            ci_note = str(exc)
        else:
            if dest is None:
                ci_note = f"{report_dir} does not exist yet - run `python -m tools.ci run --out {report_dir}` first"
            else:
                ci_report = str(dest)
                # SYS-67 (cm-plan.md 5.3, 7): one file per baseline, its SHA-256 goes into the tag message and the
                # same zip is the asset of the GitHub release; it lies next to the copy, outside the tree
                zip_path, report_sha = archive_zip_with_sha256(dest, dest.parent / f"ci-out-{tag}.zip")
                report_zip = str(zip_path)

    targets = targets if targets is not None else load_release_targets()
    results: list[ProfileResult] = []
    for profile in profiles:
        pdir = out_dir / "export" / profile
        result = export_profile(repo, pdir, profile, allow_findings=allow_findings, targets=targets,
                                use_git=use_git)
        if result.exported and archive:
            zip_path = out_dir / f"nextbsw-{version}-{profile}.zip"
            path, digest = archive_zip_with_sha256(pdir, zip_path)
            result.archive = str(path)
            result.sha256 = digest
        results.append(result)

    return ReleasePlan(version=version, tag=tag, tag_plan=asdict(tag_plan), since_tag=since_tag,
                       release_notes=notes, release_notes_file=str(notes_file), ci_report=ci_report,
                       ci_report_note=ci_note, profiles=[asdict(r) for r in results],
                       ci_report_binding=binding, ci_report_zip=report_zip, ci_report_sha256=report_sha)


def render_markdown(plan: ReleasePlan) -> str:
    lines = [f"# Release plan neXtBSW {plan.tag[1:]} ({plan.tag})", ""]
    lines.append("## Tag (SYS-68)")
    lines.append("Ready - not created automatically." if not plan.tag_plan["blocking"]
                else "Not ready yet:")
    lines += [f"- {b}" for b in plan.tag_plan["blocking"]]
    lines.append("\nCommand (never run by this tool):\n")
    lines.append("```\n" + " ".join(plan.tag_plan["command"]) + "\n```")
    lines.append("\n## CI report (SYS-67, doc/safety/verification-plan.md section 7)")
    lines.append(plan.ci_report or plan.ci_report_note or "-")
    if plan.ci_report_binding is not None:
        b = plan.ci_report_binding
        if b["bound"]:
            lines.append(f"\nBound to this tree: yes - commit `{b['commit']}`, tree `{b['tree']}`, verdict pass, "
                         f"strict, {'with' if b.get('slow') else 'without'} --slow.")
        else:
            lines.append("\nBound to this tree: **no** - the report is not evidence of this baseline "
                         "(a safety release needs a bound report):")
            lines += [f"- {p}" for p in b["problems"]]
    if plan.ci_report_zip:
        name = Path(plan.ci_report_zip).name
        lines.append(f"\nTag message line: `CI report: sha256 {plan.ci_report_sha256} ({name})`")
        lines.append("\nPublish as release asset after the tag is pushed (never run by this tool):\n")
        # TOOL-232: a release candidate is published as a GitHub pre-release under its own name
        prerelease = " --prerelease" if _rctag_module().is_candidate(plan.tag) else ""
        lines.append(f"```\ngh release create {plan.tag} \"{plan.ci_report_zip}\" \"{plan.ci_report_zip}.sha256\" "
                     f"--title \"neXtBSW {plan.tag[1:]}\"{prerelease} --notes-file \"{plan.release_notes_file}\"\n```")
    lines.append("\n## Release notes")
    lines.append(f"since {plan.since_tag or '(first release)'} -> `{plan.release_notes_file}`")
    lines.append("\n## Export per profile (distcheck)")
    lines.append("| profile | exported | errors | warnings | target | archive | sha256 |")
    lines.append("|---|---|---|---|---|---|---|")
    for r in plan.profiles:
        lines.append(f"| {r['profile']} | {r['exported']} | {r['errors']} | {r['warnings']} | "
                     f"{r['target'] or r['target_note'] or '-'} | {r['archive'] or '-'} | {r['sha256'] or '-'} |")
    lines.append(f"\nHosting/publication: {plan.hosting_decision}. This tool writes local artefacts and this "
                f"plan only; it never pushes anywhere.")
    return "\n".join(lines) + "\n"


def write_plan_files(plan: ReleasePlan, out_dir: Path) -> tuple[Path, Path]:
    out_dir = Path(out_dir)
    json_path = out_dir / "release-plan.json"
    json_path.write_text(json.dumps(asdict(plan), indent=2, ensure_ascii=False), encoding="utf-8")
    md_path = out_dir / "release-plan.md"
    md_path.write_text(render_markdown(plan), encoding="utf-8")
    return json_path, md_path
