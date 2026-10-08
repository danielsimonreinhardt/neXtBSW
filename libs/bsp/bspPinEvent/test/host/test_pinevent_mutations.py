# SPDX-License-Identifier: Apache-2.0
"""Meaningfulness of the pinEvent host test (IO-53): deliberately broken variants must fail it.

Each mutation is applied to a copy of src/pinEvent/PinEvent.cpp or of include/pinEvent/PinEvent.h, the host test
(PinEventHostTest.cpp, unchanged) is compiled against the copy with zig c++ exactly as the tools/test entry
`pinevent` does, and run. A mutated header is put into an include directory in front of the module's own one.
A mutant counts as detected if the test exits non-zero or hangs; a mutant that does not compile is a fault of
this script.

The mutations undo the promises of the module one by one: consistent hand-over between ISR and task, debounce
with level confirmation, rate limit with automatic re-arm, and the expiry that keeps a quiet input from aliasing
into an old lock-out or window after a wrap of the 32-bit time.

Run: .venv/Scripts/python libs/bsp/bspPinEvent/test/host/test_pinevent_mutations.py (about 3 min)
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
MODULE = HERE.parents[1]
ROOT = HERE.parents[4]
SRC = "src/pinEvent/PinEvent.cpp"
HDR = "include/pinEvent/PinEvent.h"
RUN_LIMIT_S = 60

# name: (file, [(old, new, occurrences), ...]); `old` must occur exactly `occurrences` times in that file
MUTATIONS = {
    # ---- hand-over ISR -> task
    "the reader does not notice an interrupt during its copy": (SRC, [
        ("if (((before & 1U) == 0U) && (load(_seq) == before))", "if ((before & 1U) == 0U)", 1),
    ]),
    "the reader copies while a writer is inside": (SRC, [
        ("if (((before & 1U) == 0U) && (load(_seq) == before))", "if (load(_seq) == before)", 1),
    ]),
    "the writer does not mark itself (sequence unchanged)": (SRC, [
        ("void PinEvent::beginWrite()\n{\n    store(_seq, load(_seq) + 1U);",
         "void PinEvent::beginWrite()\n{\n    store(_seq, load(_seq) + 0U);", 1),
        ("    barrier();\n    store(_seq, load(_seq) + 1U);", "    barrier();\n    store(_seq, load(_seq) + 0U);", 1),
    ]),
    # ---- debounce and level confirmation
    "a glitch is confirmed instead of taken back": (SRC, [
        ("    if (levelAtEnd != expected)\n", "    if ((levelAtEnd != expected) && (s.glitches == 0xFFFFFFFFU))\n", 1),
    ]),
    "bounces inside the lock-out are not ignored": (SRC, [
        ("if (s.pending && (!passed(nowUs, s.edgeUs, _config.debounceUs)))", "if (false)", 1),
    ]),
    "a bounce does not update the level seen in the lock-out": (SRC, [
        ("        ++s.lockBounces;\n        s.lastSeen = level;\n", "        ++s.lockBounces;\n", 1),
    ]),
    "the time stamp is the last activity, not the opening edge": (SRC, [
        ("        s.timeUs   = s.edgeUs;", "        s.timeUs   = s.lastUs;", 1),
    ]),
    "read() does not show a confirmed edge before the next interrupt": (SRC, [
        ("    if (s.pending && ((load(_idleMark) == snap.seq) || passed(snap.nowUs, s.edgeUs, _config.debounceUs)))",
         "    if (s.pending && (load(_idleMark) == snap.seq))", 1),
    ]),
    "releases count for edge activate": (SRC, [
        ("        case Edge::ACTIVATE:   return newLevel;", "        case Edge::ACTIVATE:   return true;", 1),
    ]),
    "the detector reports only the configured edge while debouncing": (HDR, [
        ("return (c.debounceUs != 0U) ? Detect::BOTH : static_cast<Detect>",
         "return (c.debounceUs != 1U) ? Detect::BOTH : static_cast<Detect>", 1),
    ]),
    # ---- rate limit
    "the limit allows one edge more": (SRC, [
        ("keep = (s.windowCount <= _config.maxEvents);", "keep = (s.windowCount <= (_config.maxEvents + 1U));", 1),
    ]),
    "the limit never trips": (SRC, [
        ("keep = (s.windowCount <= _config.maxEvents);", "keep = true;", 1),
    ]),
    "the window never ends": (SRC, [
        ("if ((s.windowCount == 0U) || passed(nowUs, s.windowUs, _config.windowUs))", "if (s.windowCount == 0U)", 1),
    ]),
    "glitches do not count towards the limit (only confirmed edges)": (SRC, [
        ("        if (counts)\n        {\n            if ((s.windowCount == 0U)",
         "        if (counts && (_config.debounceUs == 0U))\n        {\n            if ((s.windowCount == 0U)", 1),
    ]),
    "a bounce storm inside one lock-out does not trip": (SRC, [
        ("if (s.lockBounces > MAX_BOUNCES)", "if (false)", 1),
    ]),
    "a tripped input keeps counting": (SRC, [
        ("if ((!s.started) || s.tripped)", "if (!s.started)", 1),
    ]),
    "a trip does not report QUALITY_ERROR": (SRC, [
        ("    if (s.tripped)\n    {\n        quality = QUALITY_ERROR;", "    if (false)\n    {\n        quality = QUALITY_ERROR;", 1),
    ]),
    "re-armed at once, without hold-off": (SRC, [
        ("return passed(snap.nowUs, s.tripUs, _config.rearmUs);", "return true;", 1),
    ]),
    "never re-armed": (SRC, [
        ("return passed(snap.nowUs, s.tripUs, _config.rearmUs);", "return false;", 1),
    ]),
    "the re-arm keeps the old window": (SRC, [
        ("    s.pending     = false;\n    s.windowCount = 0U;\n    s.lockBounces = 0U;\n    s.tripped     = false;",
         "    s.pending     = false;\n    s.lockBounces = 0U;\n    s.tripped     = false;", 1),
    ]),
    "the re-arm does not program the edge detector": (SRC, [
        ("            _inputs[i].rearm(_hardware.level(i), _now());\n            _hardware.arm(i, detectorEdges(_inputs[i].config()));",
         "            _inputs[i].rearm(_hardware.level(i), _now());", 1),
    ]),
    "start does not arm": (SRC, [
        ("        _inputs[i].start(_hardware.level(i), _now());\n        _hardware.arm(i, detectorEdges(_inputs[i].config()));",
         "        _inputs[i].start(_hardware.level(i), _now());", 1),
    ]),
    # ---- expiry of a quiet history (32-bit wrap)
    "the interrupt ignores the expiry mark": (SRC, [
        # seq + 1 is odd after the start, and the only odd mark (1) needs seq 0: the mark never matches
        ("    if (load(_idleMark) == seq)\n", "    if (load(_idleMark) == (seq + 1U))\n", 1),
    ]),
    "the expiry closes the window but leaves the lock-out open": (SRC, [
        ("        if (s.pending)\n        {\n            settle(s, s.lastSeen);\n        }\n        s.windowCount = 0U;",
         "        s.windowCount = 0U;", 1),
    ]),
    "the task side never marks a quiet history": (SRC, [
        ("        store(_idleMark, snap.seq); // valid", "        (void)0; // valid", 1),
    ]),
    "the task side drops a current mark once the wrapped time looks young": (SRC, [
        ("    else if (load(_idleMark) != snap.seq)", "    else if (true)", 1),
    ]),
    # ---- configuration rule
    "the debounce is not checked against the spacing of the limit": (HDR, [
        ("           && ((static_cast<uint64_t>(c.debounceUs) * c.maxEvents) < static_cast<uint64_t>(c.windowUs));",
         "           && true;", 1),
    ]),
}


def compile_and_run(source: Path, header_dir: Path | None, workdir: Path, name: str):
    """Returns (compiled, passed, output)."""
    exe = workdir / f"{name}.exe"
    cmd = [sys.executable, "-m", "ziglang", "c++", "-std=c++17", "-O1", "-Wall", "-Wextra", "-Wconversion",
           "-Wno-sign-conversion", "-Werror",
           *([f"-I{header_dir}"] if header_dir is not None else []),
           f"-I{MODULE / 'include'}",
           str(source), str(HERE / "PinEventHostTest.cpp"), "-o", str(exe)]
    build = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=600)
    if build.returncode != 0:
        return False, False, build.stdout + build.stderr
    try:
        run = subprocess.run([str(exe)], cwd=ROOT, capture_output=True, text=True, timeout=RUN_LIMIT_S)
    except subprocess.TimeoutExpired:
        return True, False, "hangs"
    return True, run.returncode == 0, run.stdout[-2000:]


def mutate(rel: str, target: Path, changes):
    text = (MODULE / rel).read_text(encoding="utf-8")
    for old, new, occurrences in changes:
        found = text.count(old)
        if found != occurrences:
            raise AssertionError(f"{rel}: {old!r} found {found} times, expected {occurrences}")
        text = text.replace(old, new)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


class PinEventMutationTest(unittest.TestCase):
    def test_mutants_are_detected(self):
        work = Path(tempfile.mkdtemp(prefix="nx_pinevent_mut_"))
        try:
            compiled, passed, output = compile_and_run(MODULE / SRC, None, work, "original")
            self.assertTrue(compiled and passed, f"original does not pass:\n{output}")
            survivors, invalid = [], []
            for index, (name, (rel, changes)) in enumerate(MUTATIONS.items()):
                if rel == SRC:
                    source, header_dir = work / f"PinEvent_m{index}.cpp", None
                    mutate(rel, source, changes)
                else:
                    # the source includes "pinEvent/PinEvent.h": the mutated copy comes first on the include path
                    source, header_dir = MODULE / SRC, work / f"include_m{index}"
                    mutate(rel, header_dir / "pinEvent" / Path(rel).name, changes)
                compiled, passed, output = compile_and_run(source, header_dir, work, f"m{index}")
                if not compiled:
                    invalid.append(name)
                    state = "does not compile"
                elif passed:
                    survivors.append(name)
                    state = "SURVIVED"
                else:
                    state = "detected" + (" (hangs)" if output == "hangs" else "")
                print(f"{state:>18}: {name}", flush=True)
                if not compiled:
                    print(output[-1500:])
            print(f"{len(MUTATIONS)} mutants, {len(survivors)} survived, {len(invalid)} did not compile")
            self.assertEqual(invalid, [], "mutants must compile")
            self.assertEqual(survivors, [], "mutants not detected by the host test")
        finally:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUTF8", "1")
    unittest.main()
