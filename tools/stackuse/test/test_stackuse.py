"""Host tests of tools/stackuse (SYS-65).

Run: .venv/Scripts/python -m unittest tools/stackuse/test/test_stackuse.py

Test profile: test/profile_test.yaml with the made-up image below (FIXTURE_DISASSEMBLY, FIXTURE_SU) - two tasks,
four interrupt handlers on three levels, a dispatch chain, resolved and unresolved indirect calls, bounded and
open recursion, a tail call, a switch table, an assembly helper with a profile frame. Nothing here depends on the
eval board (review question 7). DemoImageTest reads the real demo build when one exists (tools/test -k target-demo
or the CMake preset) and is skipped otherwise.
"""

from __future__ import annotations

import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOL = HERE.parent
REPO = TOOL.parents[1]
if str(TOOL.parent) not in sys.path:
    sys.path.insert(0, str(TOOL.parent))

from stackuse import cli  # noqa: E402
from stackuse import stackuse as su  # noqa: E402

PROFILE = HERE / "profile_test.yaml"

FIXTURE_DISASSEMBLY = """
app.elf:     file format elf32-littlearm


Disassembly of section .text:

00001000 <task_entry>:
    1000:\tpush\t{r4, lr}
    1002:\tbl\t1100 <helper_small>
    1006:\tldr\tr3, [r4, #4]
    1008:\tblx\tr3
    100a:\tpop\t{r4, pc}

00001100 <helper_small>:
    1100:\tsub\tsp, #16
    1102:\tadd\tsp, #16
    1104:\tbx\tlr

00001200 <loop_one>:
    1200:\tpush\t{r4, r5, r6, lr}
    1202:\tpop\t{r4, r5, r6, pc}

00001210 <loop_two>:
    1210:\tpush\t{r3, lr}
    1212:\tsub\tsp, #24
    1214:\tadd\tsp, #24
    1216:\tpop\t{r3, pc}

00001300 <runnable>:
    1300:\tpush\t{r4, r5, r6, r7, lr}
    1302:\tsub\tsp, #20
    1304:\tbl\t1400 <leaf>
    1308:\tbl\t1410 <dispatcher>
    130c:\tadd\tsp, #20
    130e:\tpop.w\t{r4, r5, r6, r7, lr}
    1312:\tb.w\t1500 <deep_leaf>

00001400 <leaf>:
    1400:\tbx\tlr

00001410 <dispatcher>:
    1410:\tpush\t{r3, lr}
    1412:\tldr\tr3, [r0, #0]
    1414:\tldr\tr3, [r3, #8]
    1416:\tblx\tr3
    1418:\tpop\t{r3, pc}

00001420 <job_a>:
    1420:\tpush\t{r3, lr}
    1422:\tbl\t1400 <leaf>
    1426:\tpop\t{r3, pc}

00001430 <job_b>:
    1430:\tpush\t{r4, lr}
    1432:\tpop\t{r4, pc}

00001500 <deep_leaf>:
    1500:\tsub\tsp, #24
    1502:\tadd\tsp, #24
    1504:\tbx\tlr

00001600 <comms_main>:
    1600:\tpush\t{r4, lr}
    1602:\tbl\t1700 <chain_a>
    1606:\tldr\tr2, [r4, #12]
    1608:\tblx\tr2
    160a:\tpop\t{r4, pc}

00001700 <chain_a>:
    1700:\tpush\t{r3, lr}
    1702:\tbl\t1710 <chain_b>
    1706:\tpop\t{r3, pc}

00001710 <chain_b>:
    1710:\tpush\t{r4, r5, r6, lr}
    1712:\tbl\t1700 <chain_a>
    1716:\tpop\t{r4, r5, r6, pc}

00001800 <Fault_Handler>:
    1800:\tmrs\tr0, MSP
    1804:\tb.w\t1810 <fault_final>

00001810 <fault_final>:
    1810:\tpush\t{r4, lr}
    1812:\tvpush\t{d8-d9}
    1816:\tb.n\t1816 <fault_final+0x6>

00001900 <Timer_IRQHandler>:
    1900:\tpush\t{r3, lr}
    1902:\tldr\tr3, [pc, #8]\t; (190c <Timer_IRQHandler+0xc>)
    1904:\tblx\tr3
    1906:\tpop\t{r3, pc}

00001910 <Timer2_IRQHandler>:
    1910:\tpush\t{r3, lr}
    1912:\tbl\t1920 <rec>
    1916:\tpop\t{r3, pc}

00001920 <rec>:
    1920:\tpush\t{r3, lr}
    1922:\tbl\t1920 <rec>
    1926:\tpop\t{r3, pc}

00001a00 <Can_IRQHandler>:
    1a00:\tpush\t{r4, r5, r6, r7, lr}
    1a02:\tsub.w\tsp, sp, #12
    1a06:\tldr.w\tpc, [r2, r3, lsl #2]
    1a0a:\tb.n\t1a0e <Can_IRQHandler+0xe>
    1a0c:\tbl\t1b00 <asm_helper>
    1a10:\tbl\t1b10 <libfunc>
    1a14:\tadd\tsp, #12
    1a16:\tpop\t{r4, r5, r6, r7, pc}

00001b00 <asm_helper>:
    1b00:\tbx\tlr

00001b10 <libfunc>:
    1b10:\tpush\t{r4, lr}
    1b12:\tpop\t{r4, pc}
"""

# .su lines as GCC writes them; `job_b` deliberately larger than its prologue and `loop_two` smaller (the larger value
# counts in both directions), one name broken over two lines, one name GCC prints as ")" only (it cannot be matched and is ignored).
FIXTURE_SU = {
    "a.su": (
        "/src/a.c:1:6:task_entry\t8\tstatic\n"
        "/src/a.c:5:13:helper_small\t16\tstatic\n"
        "/src/a.c:9:6:loop_one\t16\tstatic\n"
        "/src/a.c:12:6:loop_two\t16\tstatic\n"
        "/src/a.c:20:6:runnable\t40\tstatic\n"
        "/src/a.c:30:6:leaf\t0\tstatic\n"
        "/src/a.c:31:6:dispatcher\t8\tstatic\n"
        "/src/a.c:40:6:job_a\t8\tstatic\n"
        "/src/a.c:44:6:job_b\t64\tstatic\n"
        "/src/a.c:50:6:deep_leaf\t24\tstatic\n"
    ),
    "sub/b.su": (
        "C:/src/b.c:1:6:comms_main\t8\tstatic\n"
        "C:/src/b.c:5:6:chain_a\t8\tstatic\n"
        "C:/src/b.c:9:6:chain_b\t16\tstatic\n"
        "C:/src/b.c:20:6:Fault_Handler\t0\tstatic\n"
        "C:/src/b.c:22:6:fault_final\t24\tstatic\n"
        "C:/src/b.c:30:6:Timer_IRQHandler\t8\tstatic\n"
        "C:/src/b.c:34:6:Timer2_IRQHandler\t8\tstatic\n"
        "C:/src/b.c:38:6:rec\t8\tstatic\n"
        "C:/src/b.c:40:6:Can_IRQHandler\t32\tstatic\n"
        "C:/src/b.h:7:1:)\t20\tstatic\n"
        "C:/src/b.c:90:1:_GLOBAL__sub_I_x(\n"
        "static initializers for b.c)\t8\tstatic\n"
    ),
}


# SYS-97: the same image with a start-up path on the interrupt stack (reset handler -> constructors through a table,
# main), a default handler, a weak stub branching to it and a handler no context covers.
FIXTURE_MSP_DISASSEMBLY = FIXTURE_DISASSEMBLY + """
00002000 <Reset_Handler>:
    2000:\tpush\t{r3, lr}
    2002:\tbl\t2100 <run_init_array>
    2006:\tbl\t2200 <startup_main>
    200a:\tpop\t{r3, pc}

00002100 <run_init_array>:
    2100:\tpush\t{r4, lr}
    2102:\tldr\tr3, [r4, #0]
    2104:\tblx\tr3
    2106:\tpop\t{r4, pc}

00002120 <ctor_a>:
    2120:\tsub\tsp, #240
    2122:\tadd\tsp, #240
    2124:\tbx\tlr

00002140 <ctor_b>:
    2140:\tpush\t{r4, lr}
    2142:\tpop\t{r4, pc}

00002200 <startup_main>:
    2200:\tpush\t{r4, r5, r6, lr}
    2202:\tsub\tsp, #200
    2204:\tbl\t1400 <leaf>
    2208:\tadd\tsp, #200
    220a:\tpop\t{r4, r5, r6, pc}

00002300 <Default_Handler>:
    2300:\tb.n\t2300 <Default_Handler>

00002310 <Spare_IRQHandler>:
    2310:\tldr\tr0, [pc, #0]\t; (2314 <Spare_IRQHandler+0x4>)
    2312:\tbx\tr0
    2314:\t.word\t0x00002301

00002320 <New_IRQHandler>:
    2320:\tpush\t{r3, lr}
    2322:\tpop\t{r3, pc}
"""

# objdump -s of the vector table (initial SP, reset, four handlers, default, stub, empty) and of .init_array
FIXTURE_MSP_SECTIONS = """
app.elf:     file format elf32-littlearm

Contents of section .interrupts:
 0000 00100020 01200000 01180000 01190000  ... . ..........
 0010 11190000 011a0000 01230000 11230000  .........#...#..
 0020 00000000                             ....
Contents of section .init_array:
 3000 21210000 41210000                    !!..A!..
"""


def _write_su(root: Path) -> None:
    for name, text in FIXTURE_SU.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


class NameKeyTest(unittest.TestCase):
    def test_su_and_objdump_names_meet(self):
        pairs = [
            ("void safety::SafetyManager::cyclic()", "safety::SafetyManager::cyclic()"),
            ("static void util::logger::Logger::debug(uint8_t, const char*, ...)",
             "util::logger::Logger::debug(unsigned char, char const*, ...)"),
            ("virtual void systems::SafetySystem::execute()", "non-virtual thunk to systems::SafetySystem::execute()"),
            ("uint32_t {anonymous}::fill(uint8_t)", "(anonymous namespace)::fill(unsigned char) [clone .constprop.0]"),
            ("static void etl::delegate<TReturn(TParams ...)>::method_stub(...) [with T = X]",
             "void etl::delegate<void ()>::method_stub<X, &X::f>(etl::delegate<void ()>::invocation_element const&)"),
            ("xTaskIncrementTick", "xTaskIncrementTick.part.0"),
            ("bool ns::Id::operator==(const ns::Id&) const", "ns::Id::operator==(ns::Id const&) const"),
            ("const char* ns::f<T>::name() const [with T = int]", "ns::f<int>::name() const"),
        ]
        for su_name, dump_name in pairs:
            with self.subTest(su_name):
                self.assertEqual(su.key(su_name), su.key(dump_name))

    def test_different_functions_stay_apart(self):
        self.assertNotEqual(su.key("void a::B::run()"), su.key("void a::C::run()"))
        self.assertNotEqual(su.key("ns::Id::operator==(ns::Id const&) const"), su.key("ns::Id::operator<(ns::Id)"))


class ParseTest(unittest.TestCase):
    def test_su_entries(self):
        frames = su.parse_su(FIXTURE_SU["sub/b.su"] + "/x.c:1:1:grow\t16\tdynamic,bounded\n")
        names = [f.name for f in frames]
        self.assertIn("comms_main", names)
        self.assertIn("_GLOBAL__sub_I_x( static initializers for b.c)", names)   # joined over the line break
        self.assertEqual(frames[0].location, "C:/src/b.c:1:6")                   # drive letter kept in the path
        self.assertEqual(frames[-1].qualifier, "dynamic,bounded")
        self.assertEqual(len(frames), 12)

    def test_su_rejects_garbage(self):
        with self.assertRaises(ValueError):
            su.parse_su("no location here\t8\tstatic\n")

    def test_sp_decrement(self):
        self.assertEqual(su.sp_decrement("push", "{r4, r5, r6, lr}"), 16)
        self.assertEqual(su.sp_decrement("push.w", "{r4, r5, r6, r7, r8, r9, sl, fp, lr}"), 36)
        self.assertEqual(su.sp_decrement("stmdb", "sp!, {r4, r5, lr}"), 12)
        self.assertEqual(su.sp_decrement("vpush", "{d8-d15}"), 64)
        self.assertEqual(su.sp_decrement("vpush", "{s16-s31}"), 64)
        self.assertEqual(su.sp_decrement("sub", "sp, #20"), 20)
        self.assertEqual(su.sp_decrement("sub.w", "sp, sp, #264"), 264)
        self.assertEqual(su.sp_decrement("subw", "sp, sp, #1028"), 1028)
        self.assertEqual(su.sp_decrement("str.w", "lr, [sp, #-4]!"), 4)
        self.assertEqual(su.sp_decrement("strd", "r4, r5, [sp, #8]"), 0)
        self.assertEqual(su.sp_decrement("sub", "r3, sp, #8"), 0)
        with self.assertRaises(ValueError):
            su.sp_decrement("sub", "sp, sp, r3")

    def test_objdump(self):
        fns = su.parse_objdump(FIXTURE_DISASSEMBLY)
        self.assertEqual(fns["runnable"].sp_bytes, 40)
        self.assertEqual([c for _, c in fns["runnable"].calls], ["leaf", "dispatcher"])
        self.assertEqual([c for _, c in fns["runnable"].tail_calls], ["deep_leaf"])
        self.assertEqual(fns["fault_final"].tail_calls, [])        # branch inside the function
        self.assertEqual(fns["fault_final"].sp_bytes, 24)
        self.assertEqual(fns["Can_IRQHandler"].indirect, [])       # switch table, no call
        self.assertEqual(fns["Can_IRQHandler"].sp_bytes, 32)
        self.assertEqual(fns["leaf"].indirect, [])                 # bx lr is a return
        self.assertEqual(len(fns["dispatcher"].indirect), 1)
        self.assertIn("virtual, vtable offset 8", fns["dispatcher"].indirect[0][1])
        self.assertNotIn("virtual", fns["Timer_IRQHandler"].indirect[0][1])
        self.assertNotIn("virtual", fns["comms_main"].indirect[0][1])


class AnalysisTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        _write_su(self.tmp)
        self.profile = su.load_profile(PROFILE)
        self.analysis, results = su.analyse(self.tmp, FIXTURE_DISASSEMBLY, self.profile)
        self.results = {r.context.name: r for r in results}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_task_with_dispatch_chain(self):
        r = self.results["control"]
        # 100 context + task_entry 8 + loop_two 32 (larger alternative) + runnable 40 + dispatcher 8 + job_b 64
        self.assertEqual(r.depth, 252)
        self.assertTrue(r.complete, r.findings)
        self.assertEqual(r.margin, 260)
        self.assertEqual([k for k, _ in r.path][-3:], ["runnable", "dispatcher", "job_b"])

    def test_su_larger_than_prologue_wins(self):
        self.assertEqual(self.analysis.frame("job_b"), 64)
        self.assertEqual(self.analysis.frame_source("job_b"), ".su")
        self.assertEqual(self.analysis.frame("loop_two"), 32)
        self.assertEqual(self.analysis.frame_source("loop_two"), "disassembly")
        self.assertEqual(self.analysis.frame("libfunc"), 8)
        self.assertEqual(self.analysis.frame_source("libfunc"), "disassembly")
        self.assertEqual(self.analysis.frame("asm_helper"), 12)
        self.assertEqual(self.analysis.frame_source("asm_helper"), "profile")

    def test_cross_check(self):
        both, equal, larger = self.analysis.cross_check()
        self.assertEqual(both, 19)
        self.assertEqual(equal, 17)          # job_b (.su 64, prologue 8) and loop_two (.su 16, prologue 32) differ
        self.assertEqual(larger, 1)

    def test_context_resolution_and_bounded_recursion(self):
        r = self.results["comms"]
        # comms_main 8 + max(job_b 64, chain_a 8 + chain_b 2 x 16 = 40)
        self.assertEqual(r.depth, 72)
        self.assertTrue(r.complete, r.findings)

    def test_bounded_recursion_counts_times(self):
        prof = su.load_profile(PROFILE)
        prof.contexts[1].indirect = {"comms_main": []}
        prof.bounded_recursion["chain_b"] = 5
        analysis = su.Analysis(su.load_su_dir(self.tmp), su.parse_objdump(FIXTURE_DISASSEMBLY), prof)
        r = analysis.context(prof.contexts[1])
        self.assertEqual(r.depth, 8 + 8 + 5 * 16)

    def test_open_items_make_a_lower_bound(self):
        timer = self.results["timer"]
        self.assertFalse(timer.complete)
        self.assertEqual([f.kind for f in timer.findings], ["unresolved-indirect"])
        timer2 = self.results["timer2"]
        self.assertEqual([f.kind for f in timer2.findings], ["recursion"])
        self.assertEqual(timer2.depth, 16)

    def test_tail_call_and_isr(self):
        self.assertEqual(self.results["fault"].depth, 24)
        self.assertEqual(self.results["can"].depth, 44)

    def test_isr_nesting(self):
        total, levels = self.analysis.isr_nesting(list(self.results.values()))
        # (24 + 32) + (max(8, 16) + 32) + (44 + 32)
        self.assertEqual(total, 180)
        self.assertEqual([lvl for lvl, _ in levels], [-1, 3, 7])
        self.assertEqual(levels[1][1].context.name, "timer2")

    def test_unknown_entry(self):
        prof = su.load_profile(PROFILE)
        prof.contexts[0].entries = ["runnable", "no_such_function"]
        analysis = su.Analysis(su.load_su_dir(self.tmp), su.parse_objdump(FIXTURE_DISASSEMBLY), prof)
        r = analysis.context(prof.contexts[0])
        self.assertIn("unknown-entry", [f.kind for f in r.findings])

    def test_unresolved_without_profile(self):
        prof = su.load_profile(PROFILE)
        prof.indirect = {}
        analysis = su.Analysis(su.load_su_dir(self.tmp), su.parse_objdump(FIXTURE_DISASSEMBLY), prof)
        r = analysis.context(prof.contexts[0])
        self.assertFalse(r.complete)
        # without the jobs the dispatcher is a leaf: runnable 40 + deep_leaf 24 on top of the dispatch chain (140)
        self.assertEqual(r.depth, 204)


class ProfileTest(unittest.TestCase):
    def _load(self, text: str):
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8") as f:
            f.write(text)
        try:
            return su.load_profile(Path(f.name))
        finally:
            os.unlink(f.name)

    def test_errors_name_the_path(self):
        cases = {
            "contexts: [{name: a}]": "contexts[0].entries",
            "contexts: [{name: a, kind: thread, entries: [f]}]": "contexts[0].kind",
            "contexts: [{name: a, entries: [f], stack_bytes: -1}]": "contexts[0].stack_bytes",
            "contexts: [{name: a, entries: [f]}, {name: a, entries: [g]}]": "names used twice",
            "safety_contexts: [x]\ncontexts: [{name: a, entries: [f]}]": "safety_contexts",
            "bounded_recursion: [{times: 2}]\ncontexts: [{name: a, entries: [f]}]": "bounded_recursion[0]",
        }
        for text, expected in cases.items():
            with self.subTest(text):
                with self.assertRaises(su.ProfileError) as ctx:
                    self._load(text)
                self.assertIn(expected, str(ctx.exception))


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        _write_su(self.tmp / "build")
        self.dis = self.tmp / "app.dis"
        self.dis.write_text(FIXTURE_DISASSEMBLY, encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, *extra, profile=PROFILE):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = cli.main(["--build", str(self.tmp / "build"), "--disassembly", str(self.dis),
                           "--profile", str(profile), *extra])
        return rc, out.getvalue(), err.getvalue()

    def test_report_and_exit_codes(self):
        rc, out, _ = self._run()
        self.assertEqual(rc, 0)
        self.assertIn("| control (safety) | task | - | 512 | 252 | 260 | 49 % | yes |", out)
        self.assertIn("Interrupt stack 400 B, worst case **180 B**, margin 220 B", out)   # SYS-97 wording
        rc, _, err = self._run("--strict")
        self.assertEqual(rc, 1)                     # timer and timer2 are incomplete
        self.assertIn("timer: incomplete", err)

    def test_overflow_fails(self):
        text = PROFILE.read_text(encoding="utf-8").replace("stack_bytes: 512", "stack_bytes: 200")
        profile = self.tmp / "small.yaml"
        profile.write_text(text, encoding="utf-8")
        rc, _, err = self._run(profile=profile)
        self.assertEqual(rc, 1)
        self.assertIn("control: worst case 252 B exceeds the stack of 200 B", err)

    def test_json(self):
        import json
        rc, out, _ = self._run("--json")
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["isr_nesting_bytes"], 180)
        self.assertEqual({c["name"]: c["worst_case_bytes"] for c in data["contexts"]}["comms"], 72)

    def test_bad_input(self):
        rc, _, err = self._run(profile=self.tmp / "missing.yaml")
        self.assertEqual(rc, 2)
        self.assertIn("stackuse:", err)


# PLAT-107: own stack manifest of the test (task names of the test profile, not of the ECU application)
TEST_TASK_STACKS = """
schema_version: 1
default: { stack_bytes: 1024, min_stack_bytes: 128 }
tasks:
  idle: { stack_bytes: 512, min_stack_bytes: 128 }
  timer: { stack_bytes: 512, min_stack_bytes: 128 }
  control: { stack_bytes: 512, min_stack_bytes: 256, multiple_of: 32 }
"""


class TaskFloorTest(unittest.TestCase):
    """PLAT-107 (decision PLAT-26/S1): a context linked to a BSW task takes the stack of the build and its worst case
    must fit below the floor of the task in the stack manifest."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        _write_su(self.tmp / "build")
        self.dis = self.tmp / "app.dis"
        self.dis.write_text(FIXTURE_DISASSEMBLY, encoding="utf-8")
        self.manifest = self.tmp / "taskStacks.yaml"
        self.manifest.write_text(TEST_TASK_STACKS, encoding="utf-8")
        self.profile = self.tmp / "profile.yaml"
        text = PROFILE.read_text(encoding="utf-8")
        text = text.replace("  - name: control\n    kind: task\n", "  - name: control\n    kind: task\n"
                                                                   "    bsw_task: control\n")
        text = text.replace("  - name: comms\n    kind: task\n", "  - name: comms\n    kind: task\n"
                                                               "    bsw_task: comms\n")
        self.profile.write_text(text, encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, *extra):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = cli.main(["--build", str(self.tmp / "build"), "--disassembly", str(self.dis),
                           "--profile", str(self.profile), "--task-stacks", str(self.manifest), *extra])
        return rc, out.getvalue(), err.getvalue()

    def _header(self, path: Path, **stacks):
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = ["#pragma once"] + [f"#define NX_BSW_TASK_STACK_{k.upper()} {v}U /* {k} */" for k, v in stacks.items()]
        lines += ["#define NX_BSW_TASK_OWN_CONTROL 1", f"#define NX_BSW_TASK_STACK_BYTES {sum(stacks.values())}U"]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def test_header(self):
        text = self._header(self.tmp / "h.h", control=1600, idle=1024).read_text(encoding="utf-8")
        self.assertEqual(su.parse_bsw_tasks_header(text), {"control": 1600, "idle": 1024})

    def test_floor_holds_with_the_profile_stack(self):
        rc, out, err = self._run()
        self.assertEqual(rc, 0, err)
        self.assertIn("## Floors of the BSW task stacks", out)
        self.assertIn("| control | control | 512 | profile | 256 | 252 | holds |", out)
        # comms has no entry in the manifest: the default floor
        self.assertIn("| comms | comms | 256 | profile | 128 | 72 | holds |", out)

    def test_stack_of_the_build(self):
        # the header below the build directory is found without an option; the stack of the build replaces the
        # profile's 512 and the margin follows it
        self._header(self.tmp / "build" / su.BSW_TASKS_HEADER, control=384, comms=256)
        rc, out, err = self._run()
        self.assertEqual(rc, 0, err)
        self.assertIn("| control (safety) | task | - | 384 | 252 | 132 |", out)
        self.assertIn("| control | control | 384 | BswTasks.h | 256 | 252 | holds |", out)
        # a smaller stack of the build than the worst case: overflow as before
        self._header(self.tmp / "build" / su.BSW_TASKS_HEADER, control=224, comms=256)
        rc, _, err = self._run()
        self.assertEqual(rc, 1)
        self.assertIn("control: worst case 252 B exceeds the stack of 224 B", err)

    def test_floor_below_the_worst_case_fails(self):
        self.manifest.write_text(TEST_TASK_STACKS.replace("min_stack_bytes: 256", "min_stack_bytes: 224"),
                                 encoding="utf-8")
        rc, out, err = self._run()
        self.assertEqual(rc, 1)
        self.assertIn("control: worst case 252 B exceeds the floor 224 B of task control (taskStacks.yaml "
                      "min_stack_bytes): raise the floor", err)
        self.assertIn("| control | control | 512 | profile | 224 | 252 | BELOW THE WORST CASE |", out)

    def test_merged_task_and_missing_input(self):
        header = self._header(self.tmp / "other.h", comms=256)            # control merged: no stack of its own
        rc, _, err = self._run("--bsw-tasks-header", str(header))
        self.assertEqual(rc, 2)
        self.assertIn("bsw_task control has no stack of its own in BswTasks.h", err)
        rc, _, err = self._run("--bsw-tasks-header", str(self.tmp / "missing.h"))
        self.assertEqual(rc, 2)
        self.manifest.write_text("schema_version: 1\ntasks: {}\n", encoding="utf-8")
        rc, _, err = self._run()
        self.assertEqual(rc, 2)
        self.assertIn("default.min_stack_bytes", err)

    def test_json(self):
        import json
        rc, out, _ = self._run("--json")
        self.assertEqual(rc, 0)
        floors = {f["context"]: f for f in json.loads(out)["floors"]}
        self.assertEqual((floors["control"]["floor_bytes"], floors["control"]["worst_case_bytes"],
                          floors["control"]["ok"]), (256, 252, True))

    def test_lower_bound(self):
        """A lower bound below the floor shows nothing; above it, it already shows the floor too small."""
        check = su.FloorCheck("c", "t", 256, 200, False, 512, "profile")
        self.assertEqual((check.ok, check.status), (True, "not shown (lower bound)"))
        check = su.FloorCheck("c", "t", 256, 300, False, 512, "profile")
        self.assertEqual((check.ok, check.status), (False, "BELOW THE WORST CASE"))

    def test_profile_errors(self):
        for text, fragment in (("    bsw_task: control\n", "    bsw_task: Control\n"),
                               ("  - name: fault\n    kind: isr\n", "  - name: fault\n    kind: isr\n"
                                                                    "    bsw_task: fault\n")):
            with self.subTest(fragment):
                bad = self.tmp / "bad.yaml"
                bad.write_text(self.profile.read_text(encoding="utf-8").replace(text, fragment), encoding="utf-8")
                with self.assertRaisesRegex(su.ProfileError, r"\.bsw_task: "):
                    su.load_profile(bad)

    def test_demo_profile_and_report(self):
        """Every safety context of the demo profile is linked to its BSW task, and the committed report shows the floor
        of the repository's manifest holding the worst case (regenerated with the demo build)."""
        profile = su.load_profile(TOOL / "profiles" / "evalboard_s32k144_demo.yaml")
        linked = {c.name: c.bsw_task for c in profile.contexts if c.bsw_task}
        self.assertLessEqual(set(profile.safety_contexts), set(linked))
        self.assertEqual(set(linked.values()), {"safety"})
        floors = su.load_task_floors(REPO / su.TASK_STACKS_MANIFEST)
        report = (TOOL / "reports" / "evalboard_s32k144_demo.md").read_text(encoding="utf-8")
        rows = [line.split("|")[1:-1] for line in report.splitlines()
                if line.startswith("| safety") and line.count("|") == 8]
        self.assertEqual(sorted(r[0].strip() for r in rows), sorted(linked))
        for row in rows:
            name, task, _, _, floor, depth, status = (c.strip() for c in row)
            with self.subTest(name):
                self.assertEqual(int(floor), floors.floor(task))
                self.assertLessEqual(int(depth), floors.floor(task))
                self.assertEqual(status, "holds")


PROFILE_MSP = HERE / "profile_msp_test.yaml"


class IsrStackAnalysisTest(unittest.TestCase):   # selected by "-k AnalysisTest" (tests.yaml stackuse)
    """SYS-97: start-up context on the interrupt stack, table references, vector coverage, guard check."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        _write_su(self.tmp)
        self.sections = su.parse_section_dump(FIXTURE_MSP_SECTIONS)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _analyse(self, profile=None, disassembly=FIXTURE_MSP_DISASSEMBLY, sections=None):
        profile = profile or su.load_profile(PROFILE_MSP)
        analysis = su.Analysis(su.load_su_dir(self.tmp), su.parse_objdump(disassembly), profile,
                               self.sections if sections is None else sections)
        results = [analysis.context(c) for c in profile.contexts]
        return analysis, {r.context.name: r for r in results}, results

    def test_section_dump(self):
        self.assertEqual(self.sections[".init_array"], [(0x3000, 0x2121), (0x3004, 0x2141)])
        table = self.sections[".interrupts"]
        self.assertEqual(len(table), 9)
        self.assertEqual(table[0], (0x0, 0x20001000))
        self.assertEqual(table[7], (0x1C, 0x2311))

    def test_literal_words_and_ops(self):
        fn = su.parse_objdump(FIXTURE_MSP_DISASSEMBLY)["Spare_IRQHandler"]
        self.assertEqual(fn.words, [0x2301])
        self.assertEqual(fn.ops, ["ldr", "bx", ".word"])
        self.assertEqual(fn.sp_bytes, 0)

    def test_table_reference_reaches_the_constructors(self):
        _, r, _ = self._analyse()
        start = r["startup"]
        # Reset_Handler 8 + run_init_array 8 + ctor_a 240 (deeper than startup_main 216)
        self.assertEqual(start.depth, 256)
        self.assertTrue(start.complete, start.findings)
        self.assertEqual([k for k, _ in start.path], ["Reset_Handler", "run_init_array", "ctor_a"])
        self.assertEqual(start.largest_frame, ("ctor_a", 240))

    def test_missing_table_section_is_an_error(self):
        with self.assertRaises(su.ProfileError) as ctx:
            self._analyse(sections={".interrupts": self.sections[".interrupts"]})
        self.assertIn("table:.init_array", str(ctx.exception))

    def test_budget_is_start_up_plus_nested_levels(self):
        analysis, _, results = self._analyse()
        budget = analysis.isr_stack_budget(results)
        # nested: (24 + 32) + (max(8, 8 + 3 x 8) + 32) + (44 + 32) = 196; start-up 256 below it
        self.assertEqual(budget.nested, 196)
        self.assertEqual(budget.below.context.name, "startup")
        self.assertEqual(budget.total, 452)
        self.assertTrue(budget.complete)

    def test_vector_coverage(self):
        analysis, _, results = self._analyse()
        rows = {row.index: row for row in analysis.vector_rows(results)}
        self.assertEqual(rows[0].status, "skipped")
        self.assertEqual(rows[1].status, "startup")
        self.assertEqual([rows[i].status for i in (2, 3, 4, 5)], ["fault", "timer", "timer2", "can"])
        self.assertEqual(rows[6].status, "default")
        self.assertEqual(rows[7].status, "trampoline")
        self.assertEqual(rows[8].status, "empty")
        self.assertEqual(analysis.isr_stack_budget(results).uncovered_vectors, [])

    def test_a_handler_without_context_is_uncovered(self):
        # the stub at vector 7 replaced by a real handler (a module linked later, e.g. pin events)
        text = FIXTURE_MSP_SECTIONS.replace("01230000 11230000", "01230000 21230000")
        analysis, _, results = self._analyse(sections=su.parse_section_dump(text))
        budget = analysis.isr_stack_budget(results)
        self.assertEqual([(v.index, v.handler) for v in budget.uncovered_vectors], [(7, "New_IRQHandler")])
        self.assertFalse(budget.complete)

    def test_stub_to_a_non_default_handler_is_uncovered(self):
        # a stub is only harmless when it branches to a default handler
        text = FIXTURE_MSP_DISASSEMBLY.replace("2314:\t.word\t0x00002301", "2314:\t.word\t0x00002321")
        analysis, _, results = self._analyse(disassembly=text)
        self.assertEqual([v.index for v in analysis.isr_stack_budget(results).uncovered_vectors], [7])

    def test_generated_wrapper_of_a_default_handler(self):
        """SYS-170: Safe_<handler> of an unused vector (pre hook, default handler, post hook) is no handler of its own;
        a wrapper around a real handler, or one without the hooks named in the profile, stays UNCOVERED."""
        wrappers = (
            "\n00002330 <Pre_Hook>:\n    2330:\tbx\tlr\n"
            "\n00002340 <Post_Hook>:\n    2340:\tbx\tlr\n"
            "\n00002350 <Safe_Spare_IRQHandler>:\n    2350:\tpush\t{r3, lr}\n    2352:\tbl\t2330 <Pre_Hook>\n"
            "    2356:\tbl\t2300 <Default_Handler>\n    235a:\tldmia.w\tsp!, {r3, lr}\n"
            "    235e:\tb.w\t2340 <Post_Hook>\n"
            "\n00002370 <Safe_New_IRQHandler>:\n    2370:\tpush\t{r3, lr}\n    2372:\tbl\t2330 <Pre_Hook>\n"
            "    2376:\tbl\t2320 <New_IRQHandler>\n    237a:\tldmia.w\tsp!, {r3, lr}\n"
            "    237e:\tb.w\t2340 <Post_Hook>\n")
        profile_text = PROFILE_MSP.read_text(encoding="utf-8").replace(
            "  default_handlers: [Default_Handler]\n",
            "  default_handlers: [Default_Handler]\n  wrapper_hooks: [Pre_Hook, Post_Hook]\n")
        profile_path = self.tmp / "profile_wrapper.yaml"
        profile_path.write_text(profile_text, encoding="utf-8")
        for word, status in (("51230000", "wrapper"), ("71230000", "UNCOVERED")):
            with self.subTest(word=word):
                text = FIXTURE_MSP_SECTIONS.replace("01230000 11230000", "01230000 " + word)
                analysis, _, results = self._analyse(profile=su.load_profile(profile_path),
                                                     disassembly=FIXTURE_MSP_DISASSEMBLY + wrappers,
                                                     sections=su.parse_section_dump(text))
                self.assertEqual({row.index: row for row in analysis.vector_rows(results)}[7].status, status)
        # without wrapper_hooks the wrapper of the default handler is not recognised
        text = FIXTURE_MSP_SECTIONS.replace("01230000 11230000", "01230000 51230000")
        analysis, _, results = self._analyse(disassembly=FIXTURE_MSP_DISASSEMBLY + wrappers,
                                             sections=su.parse_section_dump(text))
        self.assertEqual([v.index for v in analysis.isr_stack_budget(results).uncovered_vectors], [7])

    def test_guard_check(self):
        analysis, _, results = self._analyse()
        guard = analysis.guard_check(results)
        # largest frame on the interrupt stack ctor_a 240: 240 + max(240, exception frame 32)
        self.assertEqual(guard.largest, ("ctor_a", 240))
        self.assertEqual(guard.required, 480)
        self.assertTrue(guard.ok)
        profile = su.load_profile(PROFILE_MSP)
        profile.isr_stack_guard_bytes = 256
        analysis, _, results = self._analyse(profile)
        self.assertFalse(analysis.guard_check(results).ok)
        # the exception frame counts when it is larger than every frame
        profile = su.load_profile(PROFILE_MSP)
        profile.exception_frame_bytes = 300
        analysis, _, results = self._analyse(profile)
        self.assertEqual(analysis.guard_check(results).required, 540)

    def test_lower_bound_accepted(self):
        profile = su.load_profile(PROFILE_MSP)
        profile.contexts[0].indirect = {}          # constructors unresolved: start-up is a lower bound
        analysis, r, results = self._analyse(profile)
        self.assertFalse(r["startup"].complete)
        self.assertFalse(analysis.isr_stack_budget(results).complete)
        profile.contexts[0].lower_bound_accepted = "runs once before the scheduler, guarded"
        analysis, r, results = self._analyse(profile)
        self.assertTrue(analysis.isr_stack_budget(results).complete)
        self.assertEqual(r["startup"].depth, 8 + 216)   # without the constructors: main 216
        # the guard check cannot trust the reached functions of a lower bound: the largest frame of the image counts
        self.assertEqual(r["startup"].largest_frame, ("startup_main", 216))
        self.assertEqual(analysis.guard_check(results).largest, ("ctor_a", 240))

    def test_profile_errors(self):
        base = PROFILE_MSP.read_text(encoding="utf-8")
        cases = {
            base.replace("    kind: isr\n    level: -1", "    kind: isr\n    on_isr_stack: true\n    level: -1"):
                "on_isr_stack: only a task-kind context",
            base.replace("    on_isr_stack: true\n", "    on_isr_stack: yes please\n"): "on_isr_stack: true or false",
            base.replace("    level: -1\n", "    level: -1\n    lower_bound_accepted: why not\n"):
                "an interrupt level must be complete",
            base.replace("vectors:\n  section: .interrupts\n", "vectors:\n  skip: {}\n"): "vectors: needs section",
        }
        for text, expected in cases.items():
            with self.subTest(expected):
                path = self.tmp / "p.yaml"
                path.write_text(text, encoding="utf-8")
                with self.assertRaises(su.ProfileError) as ctx:
                    su.load_profile(path)
                self.assertIn(expected, str(ctx.exception))

    def _cli(self, *extra, profile=PROFILE_MSP, sections=FIXTURE_MSP_SECTIONS):
        dis = self.tmp / "app.dis"
        dis.write_text(FIXTURE_MSP_DISASSEMBLY, encoding="utf-8")
        dump = self.tmp / "app.sections"
        dump.write_text(sections, encoding="utf-8")
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = cli.main(["--build", str(self.tmp), "--disassembly", str(dis), "--sections-dump", str(dump),
                           "--profile", str(profile), *extra])
        return rc, out.getvalue(), err.getvalue()

    def test_cli_strict(self):
        rc, out, err = self._cli("--strict")
        self.assertEqual(rc, 0, err)
        self.assertIn("256 + 196 = **452 B**", out)
        self.assertIn("margin 148 B", out)
        self.assertIn("**sufficient**", out)
        self.assertIn("| 1 | `Reset_Handler` | startup |", out)
        rc, _, err = self._cli("--strict", "--min-margin", "200")
        self.assertEqual(rc, 1)
        self.assertIn("interrupt stack: margin 148 B below 200 B", err)

    def test_cli_fails_on_uncovered_vector_overflow_and_guard(self):
        rc, _, err = self._cli("--strict", sections=FIXTURE_MSP_SECTIONS.replace("11230000", "21230000"))
        self.assertEqual(rc, 1)
        self.assertIn("vector 7 (New_IRQHandler) is covered by no interrupt context", err)
        rc, _, _ = self._cli(sections=FIXTURE_MSP_SECTIONS.replace("11230000", "21230000"))
        self.assertEqual(rc, 0)                      # without --strict: reported, not failed
        small = self.tmp / "small.yaml"
        small.write_text(PROFILE_MSP.read_text(encoding="utf-8").replace("isr_stack_bytes: 600", "isr_stack_bytes: 400")
                         .replace("isr_stack_guard_bytes: 480", "isr_stack_guard_bytes: 256"), encoding="utf-8")
        rc, _, err = self._cli(profile=small)
        self.assertEqual(rc, 1)
        self.assertIn("nested worst case 452 B exceeds 400 B", err)
        self.assertIn("guard below the interrupt stack: 256 B, required 480 B", err)

    def test_json(self):
        import json
        rc, out, _ = self._cli("--json")
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual(data["isr_stack_worst_case_bytes"], 452)
        self.assertEqual(data["guard"]["required"], 480)
        self.assertEqual(data["uncovered_vectors"], [])


# SYS-132: nm output of an image linked with the test profile's sizes (stack 600 B, guard 480 B directly below)
FIXTURE_NM = """\
20000e20 B __StackGuardStart
20001000 B __StackGuardEnd
20001000 B __StackLimit
20001258 B __StackTop
20000400 B __HeapLimit
00002101 T Reset_Handler
"""


def _nm(stack: int, guard: int, gap: int = 0) -> str:
    """nm output with an interrupt stack of `stack` bytes and a guard of `guard` bytes `gap` bytes below it."""
    limit = 0x20001000
    return (f"{limit - gap - guard:08x} B __StackGuardStart\n{limit - gap:08x} B __StackGuardEnd\n"
            f"{limit:08x} B __StackLimit\n{limit + stack:08x} B __StackTop\n")


class ImageStackAnalysisTest(unittest.TestCase):   # selected by "-k AnalysisTest" (tests.yaml stackuse)
    """SYS-132: interrupt stack and guard as linked (project value memory.application.isr_stack_size /
    isr_stack_guard_size or template default) replace the profile numbers; own test profile profile_msp_test.yaml."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        _write_su(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_parse_symbols(self):
        symbols = su.parse_symbols(FIXTURE_NM)
        self.assertEqual(sorted(symbols), sorted(su.IMAGE_STACK_SYMBOLS))
        linked = su.image_stack(symbols)
        self.assertEqual((linked.stack, linked.guard, linked.guard_below), (600, 480, True))
        self.assertEqual(su.image_stack({}), su.ImageStack(None, None, False))

    def test_image_replaces_the_profile(self):
        profile = su.load_profile(PROFILE_MSP)
        notes = su.apply_image_stack(profile, su.image_stack(su.parse_symbols(_nm(0x400, 0x200))), "app.elf")
        self.assertEqual((profile.isr_stack_bytes, profile.isr_stack_guard_bytes), (0x400, 0x200))
        self.assertEqual(profile.isr_stack_source, "app.elf")
        startup = next(c for c in profile.contexts if c.on_isr_stack)
        self.assertEqual(startup.stack_bytes, 0x400)            # the start-up runs on the same stack
        self.assertEqual(len(notes), 2)
        self.assertIn("1024 B linked, the profile says 600 B", notes[0])
        # equal values: no note; no symbols: the profile stays
        profile = su.load_profile(PROFILE_MSP)
        self.assertEqual(su.apply_image_stack(profile, su.image_stack(su.parse_symbols(FIXTURE_NM)), "x"), [])
        profile = su.load_profile(PROFILE_MSP)
        su.apply_image_stack(profile, su.image_stack({}), "")
        self.assertEqual((profile.isr_stack_bytes, profile.isr_stack_source), (600, "profile"))

    def test_guard_not_below_the_stack_is_refused(self):
        profile = su.load_profile(PROFILE_MSP)
        with self.assertRaises(su.ProfileError) as ctx:
            su.apply_image_stack(profile, su.image_stack(su.parse_symbols(_nm(600, 480, gap=32))), "app.elf")
        self.assertIn("does not end at the limit of the interrupt stack", str(ctx.exception))

    def _cli(self, nm: str | None, *extra):
        dis = self.tmp / "app.dis"
        dis.write_text(FIXTURE_MSP_DISASSEMBLY, encoding="utf-8")
        dump = self.tmp / "app.sections"
        dump.write_text(FIXTURE_MSP_SECTIONS, encoding="utf-8")
        args = ["--build", str(self.tmp), "--disassembly", str(dis), "--sections-dump", str(dump),
                "--profile", str(PROFILE_MSP), *extra]
        if nm is not None:
            (self.tmp / "app.nm").write_text(nm, encoding="utf-8")
            args += ["--image-symbols", str(self.tmp / "app.nm")]
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = cli.main(args)
        return rc, out.getvalue(), err.getvalue()

    def test_cli_checks_against_the_linked_value(self):
        # worst case 452 B (start-up 256 + nested 196), required guard 480 B (IsrStackAnalysisTest)
        rc, out, err = self._cli(_nm(600, 480), "--strict")
        self.assertEqual(rc, 0, err)
        self.assertIn("Interrupt stack 600 B (linked: app.nm), worst case **452 B**", out)
        # a project that linked a smaller stack fails although the profile says 600 B
        rc, _, err = self._cli(_nm(448, 480))
        self.assertEqual(rc, 1)
        self.assertIn("nested worst case 452 B exceeds 448 B", err)
        self.assertIn("note: interrupt stack 448 B linked, the profile says 600 B", err)
        # a larger stack passes a margin the profile value would not
        rc, _, err = self._cli(_nm(0x400, 480), "--strict", "--min-margin", "200")
        self.assertEqual(rc, 0, err)
        # a smaller guard than the frames need fails
        rc, _, err = self._cli(_nm(600, 256))
        self.assertEqual(rc, 1)
        self.assertIn("guard below the interrupt stack: 256 B, required 480 B", err)

    def test_cli_without_symbols_keeps_the_profile(self):
        import json
        rc, out, _ = self._cli(None, "--json")
        self.assertEqual(rc, 0)
        data = json.loads(out)
        self.assertEqual((data["isr_stack_bytes"], data["isr_stack_source"]), (600, "profile"))
        rc, out, _ = self._cli(_nm(0x400, 0x200), "--json")
        data = json.loads(out)
        self.assertEqual((data["isr_stack_bytes"], data["isr_stack_guard_bytes"], data["isr_stack_source"]),
                         (0x400, 0x200, "app.nm"))


def _demo_build() -> Path | None:
    sys.path.insert(0, str(REPO / "tools" / "test"))
    try:
        import build_dirs  # noqa: E402
        build = build_dirs.demo_build_dir(REPO)
    finally:
        sys.path.pop(0)
    elf = build / "ecu" / "application" / "RelWithDebInfo" / "app.ecu.elf"
    return build if elf.exists() and su.objdump_tool() else None


@unittest.skipIf(_demo_build() is None, "no demo build with app.ecu.elf, or arm-none-eabi-objdump not found")
class DemoImageTest(unittest.TestCase):
    """The demo profile against the real image: the safety task is complete and fits; frames agree."""

    @classmethod
    def setUpClass(cls):
        build = _demo_build()
        cls.build = build
        cls.elf = elf = build / "ecu" / "application" / "RelWithDebInfo" / "app.ecu.elf"
        cls.profile = su.load_profile(TOOL / "profiles" / "evalboard_s32k144_demo.yaml")
        cls.analysis, results = su.analyse(build, su.disassemble(elf), cls.profile,
                                           su.dump_sections(elf, cls.profile.sections()))
        cls.result_list = results
        cls.results = {r.context.name: r for r in results}

    def test_safety_task_complete_and_fits(self):
        for name in self.profile.safety_contexts:
            r = self.results[name]
            self.assertTrue(r.complete, [f"{f.kind} {f.function}" for f in r.findings])
            self.assertGreaterEqual(r.margin, 0)

    def test_frames_mostly_agree(self):
        both, equal, larger = self.analysis.cross_check()
        self.assertGreater(both, 500)
        # the rest are mostly template instances merged under one key (.su maximum above the single prologue)
        self.assertGreater(equal / both, 0.85)
        self.assertLess(larger / both, 0.02)

    def test_every_profile_reference_names_a_symbol(self):
        missing = []
        for ctx in self.profile.contexts:
            for ref in ctx.entries + [a for seg in ctx.dispatch for a in seg] + list(ctx.indirect):
                if not self.analysis.resolve(ref):
                    missing.append(f"{ctx.name}: {ref}")
        for ref, targets in self.profile.indirect.items():
            for r in [ref] + targets:
                if not self.analysis.resolve(r):
                    missing.append(f"indirect: {r}")
        # SYS-97: the per-context targets as well; a table reference must name at least one function
        for ctx in self.profile.contexts:
            for targets in ctx.indirect.values():
                for r in targets:
                    if r.startswith(su.TABLE_PREFIX):
                        if not self.analysis.table_functions(r[len(su.TABLE_PREFIX):]):
                            missing.append(f"{ctx.name}: {r} holds no function")
                    elif not self.analysis.resolve(r):
                        missing.append(f"{ctx.name}: target {r}")
        for handler in self.profile.vectors.default_handlers + list(self.profile.vectors.not_interrupts):
            if not self.analysis.resolve(handler):
                missing.append(f"vectors: {handler}")
        self.assertEqual(missing, [])

    # ------------------------------------------------------------------ SYS-97: interrupt stack of the demo

    def test_interrupt_levels_complete_and_the_can_level_resolved(self):
        """AN-084 (STK-6): every interrupt level is complete; the CAN transmit interrupt ends in canFrameSentCallback,
        which only queues the transmit completion for the CAN task - the DoCAN callbacks are not on the MSP."""
        for r in self.result_list:
            if r.context.kind == "isr":
                self.assertTrue(r.complete, (r.context.name, [f"{f.kind} {f.function}" for f in r.findings]))
        can = self.results["isr-can0"]
        self.assertFalse([k for k, _ in can.path if "docan::" in k or "canFrameSentAsyncCallback" in k], can.path)

    def test_every_vector_is_covered(self):
        rows = self.analysis.vector_rows(self.result_list)
        self.assertGreater(len(rows), 16)
        self.assertEqual([(r.index, r.handler) for r in rows if r.status == "UNCOVERED"], [])

    def test_interrupt_stack_fits_with_reserve_and_the_guard_suffices(self):
        budget = self.analysis.isr_stack_budget(self.result_list)
        self.assertTrue(budget.complete)
        # SYS-97: the start-up context is the only accepted lower bound, the levels themselves are complete
        self.assertTrue(budget.below.context.lower_bound_accepted)
        self.assertLessEqual(budget.total, self.profile.isr_stack_bytes)
        guard = self.analysis.guard_check(self.result_list)
        self.assertTrue(guard.ok, guard)

    def test_profile_matches_the_linker_symbols(self):
        """isr_stack_bytes and isr_stack_guard_bytes of the profile are the sizes the image was linked with."""
        out = subprocess.run([str(Path(su.objdump_tool()).with_name(Path(su.objdump_tool()).name.replace(
            "objdump", "nm"))), str(self.elf)], capture_output=True, text=True, check=True).stdout
        sym = {p[2]: int(p[0], 16) for p in (line.split() for line in out.splitlines()) if len(p) == 3}
        self.assertEqual(sym["__StackTop"] - sym["__StackLimit"], self.profile.isr_stack_bytes)
        self.assertEqual(sym["__StackGuardEnd"] - sym["__StackGuardStart"], self.profile.isr_stack_guard_bytes)
        self.assertEqual(sym["__StackGuardEnd"], sym["__StackLimit"])

    def test_stack_sizes_come_from_the_image(self):
        """SYS-132: stackuse reads the linked interrupt stack and guard (demo: template default, no project value)."""
        linked = su.image_stack(su.image_symbols(self.elf))
        self.assertTrue(linked.guard_below)
        self.assertEqual((linked.stack, linked.guard), (self.profile.isr_stack_bytes,
                                                        self.profile.isr_stack_guard_bytes))

    def test_floors_hold_with_the_stack_of_the_build(self):
        """PLAT-107: the safety contexts take the stack of the build (BswTasks.h) and fit below the floor of the task."""
        import json
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = cli.main(["--build", str(self.build), "--elf", str(self.elf), "--json",
                           "--profile", str(TOOL / "profiles" / "evalboard_s32k144_demo.yaml")])
        self.assertEqual(rc, 0, err.getvalue())
        data = json.loads(out.getvalue())
        self.assertLessEqual(set(self.profile.safety_contexts), {f["context"] for f in data["floors"]})
        self.assertTrue(all(f["ok"] and f["complete"] for f in data["floors"]), data["floors"])
        sources = {c["name"]: c["stack_source"] for c in data["contexts"] if c["bsw_task"]}
        self.assertEqual(set(sources.values()), {"BswTasks.h"})

    def test_strict_run_is_green(self):
        """The CI step: stackuse --strict on the demo image (tests.yaml stackuse-demo runs this class)."""
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = cli.main(["--build", str(self.build), "--elf", str(self.elf), "--strict",
                           "--profile", str(TOOL / "profiles" / "evalboard_s32k144_demo.yaml")])
        self.assertEqual(rc, 0, err.getvalue())


if __name__ == "__main__":
    unittest.main()
