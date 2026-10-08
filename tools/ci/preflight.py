# SPDX-License-Identifier: Apache-2.0
"""Pre-flight before the release CI (TOOL-209, doc/safety/verification-plan.md section 8.2, task proposal T-VP3).

The release procedure of v0.2.0 needed three runs of `tools.ci run --strict` of several hours each, because faults
(calgen, mcudb, stackuse, safe-outputs-table, misra, logcheck) showed only there. The plan names a pre-flight
(PF-1 .. PF-5) of about one hour; this module runs it in that order and writes a short report:

    python -m tools.ci preflight [--since v0.2.0] [--jobs 4] [--slow] [--out DIR]

    PF-1 Freeze          clean tree, build root and report folder outside the tree, optional --commit check
    PF-2 Tools+profiles  python -m tools.ci check --strict
    PF-3 Increment       tools.test --since <previous release> --list, then the same run (--jobs, --slow)
    PF-4 CI-only stages  tools.test -k target-demo (compile database), tools.misra run, tools/test/coverage.py
    PF-5 Document state  tools.test -k safety

Everything here only starts the commands the plan names; it decides nothing of its own beyond "did the command end
green". The release CI itself (`tools.ci run`) is not started. Timeouts are a first-class result: a command that
exceeds its step limit is killed with its whole process tree and reported as a timeout, a test of tools.test that
timed out is repeated once on its own (a loaded workstation is the usual cause, see agent rules of the night wave)
and only a second timeout counts as red. `--timeout-factor` scales every step limit.

The module reads and runs commands; it never changes the tree. Standard library only (like `setup` and `check`).
Command execution is injected (`Runner`), so the tests drive the whole sequence with invented commands.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

OK, FAIL, SKIP = "ok", "fail", "skip"
PF_IDS = ("PF-1", "PF-2", "PF-3", "PF-4", "PF-5")
TITLES = {
    "PF-1": "Freeze",
    "PF-2": "Werkzeuge und Profile",
    "PF-3": "Inkrement seit dem letzten Release",
    "PF-4": "Nur-CI-Stufen (Target-Build, Statik, Abdeckung)",
    "PF-5": "Dokumentenstand (Sicherheitsdokumente)",
}
# Step limits in seconds, scaled by --timeout-factor. Orders of magnitude from verification-plan.md 8.2 with room
# for a loaded machine; the single tests keep their own limit from tools/test/tests.yaml (logcheck takes ~20 min).
STEP_TIMEOUT = {"check": 600, "since-list": 300, "increment": 7200, "target-demo": 3600, "misra": 3600,
                "coverage": 5400, "safety": 3600, "retry": 3600}
RETRY_TIMEOUTS = 1


@dataclass
class Command:
    argv: list[str]
    exit_code: int | None = None
    timed_out: bool = False
    duration_s: float = 0.0
    log: str = ""

    @property
    def green(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


@dataclass
class Step:
    id: str
    title: str
    status: str = SKIP
    duration_s: float = 0.0
    findings: list[str] = field(default_factory=list)   # what is red (or why the step did not run)
    notes: list[str] = field(default_factory=list)      # what to know about a green or red step
    commands: list[Command] = field(default_factory=list)

    def fail(self, text: str) -> None:
        self.status = FAIL
        self.findings.append(text)


@dataclass
class Context:
    repo: Path
    work: Path                       # build root and report folder both live below it, outside the tree
    since: str | None
    jobs: int = 4
    slow: bool = False
    toolchain: str = "pinned"
    cache: Path | None = None
    release: bool = False
    commit: str | None = None
    allow_dirty: bool = False
    clean: bool = False
    timeout_factor: float = 1.0
    env: dict[str, str] = field(default_factory=dict)
    python: str = sys.executable
    tests_file: Path | None = None
    only: list[str] = field(default_factory=list)
    skip: list[str] = field(default_factory=list)
    fail_fast: bool = False
    misra_env: dict[str, str] = field(default_factory=dict)

    @property
    def build_root(self) -> Path:
        return self.work / "build"

    @property
    def out(self) -> Path:
        return self.work / "out"


# Runner(argv, env, cwd, timeout_s, log_path) -> (exit code, timed_out). The default one streams to the console
# and a log file and kills the whole process tree on a timeout.
Runner = Callable[[list[str], dict[str, str], Path, float, Path], "tuple[int, bool]"]


def kill_tree(proc: subprocess.Popen) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, check=False)
    else:
        proc.kill()


def run_streaming(argv: list[str], env: dict[str, str], cwd: Path, timeout: float, log_path: Path) -> tuple[int, bool]:
    import threading

    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8", buffering=1) as log:
        proc = subprocess.Popen(argv, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace")
        timed = {"hit": False}

        def fire() -> None:
            timed["hit"] = True
            log.write(f"\n[preflight] Zeitueberschreitung nach {timeout:.0f} s, Prozessbaum beendet\n")
            kill_tree(proc)

        timer = threading.Timer(timeout, fire)
        timer.daemon = True
        timer.start()
        try:
            for line in proc.stdout:
                sys.stdout.write(line)
                sys.stdout.flush()
                log.write(line)
            code = proc.wait()
        finally:
            timer.cancel()
    return code, timed["hit"]


class Preflight:
    def __init__(self, ctx: Context, runner: Runner = run_streaming, clock: Callable[[], float] = time.monotonic,
                 provenance: Callable[[Path], dict] | None = None) -> None:
        self.ctx = ctx
        self.runner = runner
        self.clock = clock
        self.provenance = provenance
        self.steps: list[Step] = []
        self.started = clock()
        self._counter = 0

    # ------------------------------------------------------------------ helpers

    def limit(self, key: str) -> float:
        return STEP_TIMEOUT[key] * self.ctx.timeout_factor

    def run(self, step: Step, label: str, argv: list[str], limit_key: str, env: dict[str, str] | None = None) -> Command:
        self._counter += 1
        log = self.ctx.out / f"{self._counter:02d}-{step.id.lower()}-{label}.log"
        print(f"\n--- {step.id} {label} ---\n$ {subprocess.list2cmdline(argv)}", flush=True)
        began = self.clock()
        code, timed = self.runner(argv, env or self.ctx.env, self.ctx.repo, self.limit(limit_key), log)
        cmd = Command(argv, code, timed, round(self.clock() - began, 1), str(log))
        step.commands.append(cmd)
        if timed:
            step.fail(f"{label}: Zeitueberschreitung nach {self.limit(limit_key):.0f} s (Schritt abgebrochen; "
                      f"Rechner ausgelastet? --timeout-factor erhoeht die Grenzen)")
        return cmd

    def tools_test(self, *extra: str) -> list[str]:
        argv = [self.ctx.python, "-m", "tools.test", "--build-root", str(self.ctx.build_root)]
        if self.ctx.tests_file:
            argv += ["--tests", str(self.ctx.tests_file)]
        return argv + list(extra)

    def load_json(self, path: Path) -> dict | None:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def judge_tests_json(self, step: Step, path: Path, cmd: Command, label: str) -> dict | None:
        """Red entries of a tools.test result file into findings; a missing file is red, too."""
        data = self.load_json(path)
        if data is None:
            if not cmd.timed_out:
                step.fail(f"{label}: tools.test ohne Ergebnisdatei (Exit-Code {cmd.exit_code}, Log {cmd.log})")
            return None
        for test in data.get("tests", []):
            if test["status"] in ("fail", "timeout"):
                step.fail(f"{test['name']} ({test['group']}): {test['reason']}")
        for rel in data.get("unlisted_test_files", []):
            step.fail(f"{rel} steht nicht in tools/test/tests.yaml")
        if cmd.exit_code not in (0, None) and step.status != FAIL and not cmd.timed_out:
            step.fail(f"{label}: tools.test endete mit Exit-Code {cmd.exit_code}")
        summary = data.get("summary", {})
        step.notes.append(f"{label}: {summary.get('pass', 0)} gruen, {summary.get('fail', 0)} rot, "
                          f"{summary.get('timeout', 0)} Zeitueberschreitung, {summary.get('skip', 0)} "
                          f"uebersprungen von {summary.get('total', 0)}; {data.get('duration_s', 0):.0f} s")
        return data

    def retry_timeouts(self, step: Step, data: dict) -> None:
        """A test that timed out under load is repeated once on its own; only a second miss stays red."""
        timed = [t["name"] for t in data.get("tests", []) if t["status"] == "timeout"]
        if not timed or RETRY_TIMEOUTS < 1:
            return
        # the finding of the first pass is replaced by the verdict of the repetition
        step.findings = [f for f in step.findings if not any(f.startswith(f"{n} (") for n in timed)]
        if not step.findings and step.status == FAIL:
            step.status = OK
        for name in timed:
            # a glob with one bracketed character matches the exact name only (plain -k is a substring)
            exact = "[" + name[0] + "]" + name[1:] if not any(c in name for c in "*?[") else name
            out = self.ctx.out / f"retry-{name}.json"
            cmd = self.run(step, f"wiederholung-{name}", self.tools_test("-k", exact, "--json", str(out)), "retry")
            again = self.load_json(out)
            result = next((t for t in (again or {}).get("tests", []) if t["name"] == name), None)
            if cmd.green and result and result["status"] == "pass":
                step.notes.append(f"{name}: Zeitueberschreitung im Lauf, einzeln wiederholt gruen "
                                  f"({result['duration_s']:.0f} s) - Limit in tests.yaml pruefen")
            else:
                reason = result["reason"] if result else f"Exit-Code {cmd.exit_code}"
                step.fail(f"{name}: Zeitueberschreitung, auch einzeln wiederholt nicht gruen: {reason}")

    # ------------------------------------------------------------------ steps

    def pf1(self, step: Step) -> None:
        ctx = self.ctx
        repo = ctx.repo.resolve()
        info = self.provenance(repo) if self.provenance else {}
        commit = info.get("commit")
        if not commit:
            step.fail("kein git-Checkout: der Kandidat ist nicht benannt (PF-1 braucht einen Commit)")
        else:
            step.notes.append(f"Kandidat: {info.get('describe') or commit} (Commit {commit}, Baum {info.get('tree')})")
            if ctx.commit and not commit.startswith(ctx.commit):
                step.fail(f"HEAD ist {commit[:12]}, erwartet {ctx.commit}")
        if info.get("dirty"):
            text = (f"Arbeitskopie nicht sauber ({info.get('changes_total')} Aenderungen, z. B. "
                    f"{'; '.join(info.get('changes', [])[:3])}): ein eigener sauberer Worktree des Kandidaten "
                    f"(git worktree add <Ordner> <Commit>) verhindert Aenderungen waehrend des Laufs")
            if ctx.allow_dirty:
                step.notes.append("--allow-dirty: " + text)
            else:
                step.fail(text)
        for label, folder in (("Bau-Wurzel", ctx.build_root), ("Berichtsordner", ctx.out)):
            if is_inside(folder, repo):
                step.fail(f"{label} {folder} liegt im Baum - Dateien dort verfaelschen Statik und Klassifikation "
                          f"(--work-dir ausserhalb waehlen)")
        stray = repo / "build"
        if stray.is_dir():
            msg = (f"Ordner {stray} im Baum: Dateien darunter sind das Muster der v0.2.0-Statik-Befunde "
                   f"(Klassifikation von build/-Pfaden)")
            if ctx.allow_dirty:
                step.notes.append("--allow-dirty: " + msg)
            else:
                step.fail(msg)
        if step.status != FAIL:
            step.status = OK

    def pf2(self, step: Step) -> None:
        ctx = self.ctx
        argv = [ctx.python, "-m", "tools.ci"]
        if ctx.cache:
            argv += ["--cache", str(ctx.cache)]
        argv += ["check", "--strict", "--toolchain", ctx.toolchain]
        if ctx.release:
            argv.append("--release")
        cmd = self.run(step, "check", argv, "check")
        if not cmd.timed_out and not cmd.green:
            step.fail(f"tools.ci check --strict endete mit Exit-Code {cmd.exit_code} (Log {cmd.log})")
        if step.status != FAIL:
            step.status = OK

    def pf3(self, step: Step) -> None:
        ctx = self.ctx
        if not ctx.since:
            step.fail("kein vorheriger Release-Tag gefunden: --since v<vorher> angeben")
            return
        listing = ctx.out / "increment-list.json"
        cmd = self.run(step, "liste", self.tools_test("--since", ctx.since, "--list", "--json", str(listing)),
                       "since-list")
        if cmd.timed_out:
            return
        if not cmd.green:
            step.fail(f"tools.test --since {ctx.since} --list endete mit Exit-Code {cmd.exit_code} (Log {cmd.log})")
            return
        data = self.load_json(listing)
        if data is not None:
            names = [t["name"] for t in data.get("tests", [])]
            step.notes.append(f"{len(names)} Eintraege seit {ctx.since} betroffen")
            orphans = (data.get("since") or {}).get("files_without_entry") or []
            if orphans:
                step.notes.append(f"{len(orphans)} geaenderte Dateien ohne Eintrag, z. B. {', '.join(orphans[:3])}")
        result = ctx.out / "increment.json"
        extra = ["--since", ctx.since, "--jobs", str(ctx.jobs), "--json", str(result)]
        if ctx.slow:
            extra.append("--slow")
        if ctx.clean:
            extra.append("--clean")
        cmd = self.run(step, "lauf", self.tools_test(*extra), "increment")
        if cmd.timed_out:
            # the result file of a killed run does not exist; the log shows how far it got
            return
        done = self.judge_tests_json(step, result, cmd, "Inkrement")
        if done:
            self.retry_timeouts(step, done)
        if step.status != FAIL:
            step.status = OK

    def pf4(self, step: Step) -> None:
        ctx = self.ctx
        built = ctx.out / "target-demo.json"
        cmd = self.run(step, "target-demo", self.tools_test("-k", "target-demo", "--jobs", str(ctx.jobs),
                                                            "--json", str(built)), "target-demo")
        data = None if cmd.timed_out else self.judge_tests_json(step, built, cmd, "Target-Build")
        if data:
            self.retry_timeouts(step, data)
        compile_db = ctx.build_root / "demo" / "compile_commands.json"
        if not compile_db.is_file():
            step.fail(f"Compile-Datenbank fehlt: {compile_db} (Target-Build nicht gruen) - Statik nicht gelaufen")
        else:
            misra_env = {**ctx.env, **ctx.misra_env}
            cmd = self.run(step, "misra", [ctx.python, "-m", "tools.misra", "run", "--compile-db", str(compile_db),
                                           "--out", str(ctx.out / "misra"), "--jobs", str(ctx.jobs)],
                           "misra", misra_env)
            judged = self.load_json(ctx.out / "misra" / "result.json")
            if not cmd.timed_out:
                self.judge_misra(step, cmd, judged)
        coverage_json = ctx.out / "coverage.json"
        argv = [ctx.python, str(ctx.repo / "tools" / "test" / "coverage.py"), "--jobs", str(ctx.jobs),
                "--json", str(coverage_json), "--markdown", str(ctx.out / "coverage.md"),
                "--build-root", str(ctx.build_root)]
        if ctx.tests_file:
            argv += ["--tests", str(ctx.tests_file)]
        cmd = self.run(step, "coverage", argv, "coverage")
        if not cmd.timed_out:
            self.judge_coverage(step, cmd, self.load_json(coverage_json))
        if step.status != FAIL:
            step.status = OK

    def judge_misra(self, step: Step, cmd: Command, result: dict | None) -> None:
        if result is None:
            step.fail(f"tools.misra ohne Ergebnis (Exit-Code {cmd.exit_code}, Log {cmd.log})")
            return
        new = result.get("new", [])
        if new:
            first = "; ".join(f"{f['file']}:{f['line']} {f['rule']}" for f in new[:5])
            step.fail(f"Statik: {len(new)} neue Befunde weder in der Baseline noch in der Abweichungsliste: {first}"
                      + (" ..." if len(new) > 5 else ""))
        for tool, why in result.get("skipped_tools", {}).items():
            step.fail(f"Statik: {tool} nicht gelaufen: {why}")
        if cmd.exit_code not in (0, 1, 3, None) and step.status != FAIL:
            step.fail(f"tools.misra endete mit Exit-Code {cmd.exit_code}")
        step.notes.append(f"Statik: {len(new)} neue Befunde")

    def judge_coverage(self, step: Step, cmd: Command, result: dict | None) -> None:
        if result is None:
            step.fail(f"coverage.py ohne Ergebnisdatei (Exit-Code {cmd.exit_code}, Log {cmd.log}): "
                      f"Messlauf gescheitert")
            return
        for item in result.get("entries", {}).get("items", []):
            if item["status"] == "fail":
                step.fail(f"Abdeckung {item['name']}: {item['reason'].splitlines()[0][:160]}")
        under = [m["module"] for m in result.get("modules", []) if m.get("verdict") == "unter Schwelle"]
        if under:
            # reported, not enforced - exactly like tools.ci run with enforce = false
            step.notes.append(f"Abdeckung unter Schwelle (nicht erzwungen): {', '.join(under[:6])}")
        step.notes.append(f"Abdeckung: {len(result.get('modules', []))} Module gemessen")

    def pf5(self, step: Step) -> None:
        out = self.ctx.out / "safety.json"
        cmd = self.run(step, "safety", self.tools_test("-k", "safety", "--jobs", str(self.ctx.jobs),
                                                       "--json", str(out)), "safety")
        data = None if cmd.timed_out else self.judge_tests_json(step, out, cmd, "Sicherheitsdokumente")
        if data:
            self.retry_timeouts(step, data)
        if step.status != FAIL:
            step.status = OK

    # ------------------------------------------------------------------ sequence

    def selected(self, step_id: str) -> bool:
        short = step_id.lower()
        wanted = [w.lower() for w in self.ctx.only]
        skipped = [w.lower() for w in self.ctx.skip]
        return (not wanted or short in wanted) and short not in skipped

    def execute(self) -> list[Step]:
        self.ctx.out.mkdir(parents=True, exist_ok=True)
        self.ctx.build_root.mkdir(parents=True, exist_ok=True)
        methods = {"PF-1": self.pf1, "PF-2": self.pf2, "PF-3": self.pf3, "PF-4": self.pf4, "PF-5": self.pf5}
        stop = False
        for step_id in PF_IDS:
            step = Step(step_id, TITLES[step_id])
            self.steps.append(step)
            if not self.selected(step_id):
                step.notes.append("nicht gewaehlt (--only/--skip)")
                continue
            if stop:
                step.notes.append("nicht gelaufen: --fail-fast nach rotem Schritt")
                continue
            print(f"\n=== {step_id} {step.title} ===", flush=True)
            began = self.clock()
            try:
                methods[step_id](step)
            except Exception as exc:     # a tool defect must show up as a red step, not as a lost report
                step.fail(f"Ausnahme im Vorlauf: {type(exc).__name__}: {exc}")
            step.duration_s = round(self.clock() - began, 1)
            if step.status == SKIP:
                step.status = OK if not step.findings else FAIL
            print(f"{step_id}: {'OK' if step.status == OK else 'FEHLER'} ({step.duration_s:.0f} s)", flush=True)
            if step.status == FAIL and self.ctx.fail_fast:
                stop = True
        return self.steps

    @property
    def green(self) -> bool:
        return any(s.status == OK for s in self.steps) and not any(s.status == FAIL for s in self.steps)

    # ------------------------------------------------------------------ report

    def payload(self) -> dict:
        info = self.provenance(self.ctx.repo) if self.provenance else {}
        return {
            "schema": "nxpreflight/1",
            "since": self.ctx.since,
            "candidate": {k: info.get(k) for k in ("commit", "tree", "describe", "version", "dirty")},
            "work_dir": str(self.ctx.work),
            "duration_s": round(self.clock() - self.started, 1),
            "verdict": "gruen" if self.green else "rot",
            "steps": [{"id": s.id, "title": s.title, "status": s.status, "duration_s": s.duration_s,
                       "findings": s.findings, "notes": s.notes,
                       "commands": [{"argv": c.argv, "exit_code": c.exit_code, "timed_out": c.timed_out,
                                     "duration_s": c.duration_s, "log": c.log} for c in s.commands]}
                      for s in self.steps],
        }

    def markdown(self) -> str:
        data = self.payload()
        cand = data["candidate"]
        lines = [f"# Vorlauf vor der Release-CI: {data['verdict'].upper()}", "",
                 f"- Kandidat: {cand.get('describe') or cand.get('commit') or 'kein git-Checkout'}",
                 f"- Seit: {data['since'] or '-'}", f"- Dauer: {data['duration_s']:.0f} s",
                 f"- Arbeitsordner: {data['work_dir']}", "",
                 "| Schritt | Ergebnis | Dauer |", "|---|---|---|"]
        for s in data["steps"]:
            mark = {"ok": "OK", "fail": "FEHLER", "skip": "-"}[s["status"]]
            lines.append(f"| {s['id']} {s['title']} | {mark} | {s['duration_s']:.0f} s |")
        for s in data["steps"]:
            if s["findings"] or s["notes"]:
                lines += ["", f"## {s['id']} {s['title']}"]
                lines += [f"- FEHLER: {f}" for f in s["findings"]] + [f"- {n}" for n in s["notes"]]
        lines += ["", "Ist der Vorlauf gruen, kann die Release-CI starten (`python -m tools.ci run --strict`)."
                  if data["verdict"] == "gruen" else
                  "Rot: erst beheben, dann Vorlauf wiederholen (nur die roten Schritte: `--only pf-3` usw.); "
                  "die Release-CI startet erst bei gruenem Vorlauf.", ""]
        return "\n".join(lines)

    def write_report(self) -> tuple[Path, Path]:
        json_path = self.ctx.out / "preflight.json"
        md_path = self.ctx.out / "preflight.md"
        json_path.write_text(json.dumps(self.payload(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        md_path.write_text(self.markdown(), encoding="utf-8")
        return json_path, md_path


def is_inside(folder: Path, root: Path) -> bool:
    try:
        folder.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def previous_release(repo: Path, git: Callable[..., str | None] | None = None) -> str | None:
    """Newest release tag v<X.Y.Z> that is not on HEAD itself (the baseline the increment is measured from); None
    without a tag. Not limited to tags reachable from HEAD: a release is merged into master only, so `dev` after a
    release does not contain its tag (v0.2.0 on 2026-10-01); tools.test --since works from the merge base anyway."""
    def run(*args: str) -> str | None:
        try:
            done = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=60)
        except (OSError, subprocess.SubprocessError):
            return None
        return done.stdout.strip() if done.returncode == 0 else None

    runner = git or run
    tags = runner("tag", "--list", "v[0-9]*", "--sort=-version:refname")
    head_tags = set((runner("tag", "--points-at", "HEAD") or "").split())
    for tag in (tags or "").split():
        if tag not in head_tags:
            return tag
    return None


def default_work_dir(repo: Path, commit: str | None) -> Path:
    return Path(tempfile.gettempdir()) / "nx-preflight" / (commit or "tree")[:8]
