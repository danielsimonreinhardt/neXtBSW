# SPDX-License-Identifier: Apache-2.0
"""CSEc way back to the factory state, device side (NVM-13, NVM-17): host test against a CSEc and watchdog model,
its meaningfulness, guards, the console command of the test build, and the object for the target.

  HostTest          test/host/CsecRecoveryHostTest.cpp: src/CsecRecovery.cpp against a model of the CSEc parameter
                    RAM (typed confirmation, only the first MASTER_ECU_KEY load, no key flag ever - WRITE_PROTECTION -,
                    chip preconditions, RUN mode, word/byte order, header last, CallSeq 0, interrupts/cache, INIT_RNG
                    separate from GET_ID/DBG_CHAL/DBG_AUTH, error bits) and of the MCU watchdog on simulated LPO time
                    (NVM-17: long commands without a reset, a hanging command ends in the watchdog reset after its
                    budget, window/prescaler/clock, duration, journal before the interrupts come back), hex fields;
                    NVM-21: CSE_PRAM address of the sub-series (the model answers only there)
  MutationTest      deliberately broken variants of the module must make the host test fail (or hang)
  AddressGuardTest  NVM-21: the CSE_PRAM table (include/csec/CsecPramAddress.h) against the FlexRAM of every derivative
                    of the database (mcudb/s32k1xx): directly behind it, never inside it
  GuardTest         CsecRecovery is called only by the console command of the test build (port/main
                    src/systems/CsecCommand.cpp, compiled and linked only with NX_FLEXNVM_PARTITION_COMMAND, the
                    library EXCLUDE_FROM_ALL); nothing at start-up but the requested INIT_RNG; no key flag
  ConsoleTest       NVM-17: the lines tools/csec prints fit the console (127 characters) and name the subcommands
                    and field lengths of the device command; the product check of tools/elfcheck knows the command
  TargetObjectTest  src/CsecRecovery.cpp compiled with the exact flags of the demo target build (-Werror): the launch
                    function lies in .launchFromRAM and branches nowhere outside it (needs the demo build, tools/test
                    -k target-demo; skipped without it)

No board, no CSEc command on any device. Run: .venv/Scripts/python -m unittest platforms/s32k1xx/bsp/bspCsec/test/test_csec.py
"""

from __future__ import annotations

import json
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
MODULE = HERE.parent
ROOT = HERE.parents[4]
OPENBSW = ROOT / "external" / "openbsw"
FLEXNVM_INCLUDE = ROOT / "platforms" / "s32k1xx" / "bsp" / "bspFlexNvm" / "include"
STUBS = HERE / "host" / "stubs"
TEST_CPP = HERE / "host" / "CsecRecoveryHostTest.cpp"
SOURCE = "src/CsecRecovery.cpp"
HEADER = "include/csec/CsecRecovery.h"
RUN_LIMIT_S = 30
STRICT = ["-std=c++17", "-O1", "-Wall", "-Wextra", "-Wconversion", "-Wno-sign-conversion", "-Werror",
          "-DNX_CSEC_HOST_TEST=1"]

sys.path.insert(0, str(ROOT / "tools" / "test"))
import build_dirs  # noqa: E402


def zig(args):
    return subprocess.run([sys.executable, "-m", "ziglang", "c++", *args], cwd=ROOT, capture_output=True, text=True,
                          timeout=600)


def build(module: Path, work: Path) -> tuple[bool, str, Path]:
    # stubs first: they replace mcu/mcu.h, csec/CsecPram.h, cache/cache.h and the interrupt lock
    includes = [f"-I{STUBS}", f"-I{module / 'include'}", f"-I{FLEXNVM_INCLUDE}"]
    objects = []
    for i, source in enumerate((TEST_CPP, module / SOURCE)):
        obj = work / f"{i}.o"
        r = zig([*STRICT, *includes, "-c", str(source), "-o", str(obj)])
        if r.returncode != 0:
            return False, r.stdout + r.stderr, obj
        objects.append(str(obj))
    exe = work / "CsecRecoveryHostTest.exe"
    r = zig([*objects, "-o", str(exe)])
    return r.returncode == 0, r.stdout + r.stderr, exe


def run(exe: Path) -> tuple[bool, str]:
    try:
        r = subprocess.run([str(exe)], capture_output=True, text=True, timeout=RUN_LIMIT_S)
    except subprocess.TimeoutExpired:
        return False, "hangs"
    return r.returncode == 0 and ", 0 failures" in r.stdout, r.stdout[-3000:]


class HostTest(unittest.TestCase):
    def test_recovery(self):
        work = Path(tempfile.mkdtemp())
        try:
            ok, text, exe = build(MODULE, work)
            self.assertTrue(ok, text[-3000:])
            passed, out = run(exe)
            print("\n" + out.strip(), file=sys.stderr)
            self.assertTrue(passed, out)
        finally:
            shutil.rmtree(work, ignore_errors=True)


# name: (file of the module, old, new); `old` must occur exactly once
MUTATIONS = {
    "load without confirmation": (SOURCE, "if (!sameText(confirmation, LOAD_KEY_CONFIRMATION))",
                                  "if (false && !sameText(confirmation, LOAD_KEY_CONFIRMATION))"),
    "confirmation prefix is enough": (SOURCE, "    return *a == *b;", "    return *a == '\\0';"),
    "any key id accepted": (SOURCE, "if (m1[15] != M1_MASTER_BY_MASTER)",
                            "if ((m1[15] != M1_MASTER_BY_MASTER) && false)"),
    "M2 flag block not checked": (SOURCE, "if (m2[i] != FIRST_LOAD_M2_BLOCK0[i])", "if (false)"),
    "M2 flag block checked in part": (SOURCE, "for (uint32_t i = 0U; i < 16U; ++i)\n    {\n        if (m2[i]",
                                      "for (uint32_t i = 1U; i < 16U; ++i)\n    {\n        if (m2[i]"),
    "wrong block constant (flag bit)": (SOURCE, "0x88U, 0x9BU, 0x71U, 0x64U", "0xC7U, 0x85U, 0xCDU, 0xDDU"),
    "part without CSEc accepted": (SOURCE, "if (!id.security)", "if (false)"),
    "FlexRAM state not checked": (SOURCE, "if (!FlexNvmChip::eeeReady())", "if (false)"),
    "running command not refused": (SOURCE, "    if ((FTFC->FSTAT & FTFC_FSTAT_CCIF_MASK) == 0U)\n    {\n"
                                            "        return Result::BUSY;",
                                    "    if (false)\n    {\n        return Result::BUSY;"),
    "no wait for completion": (SOURCE, "while ((FTFC->FSTAT & FTFC_FSTAT_CCIF_MASK) == 0U)",
                               "if ((FTFC->FSTAT & FTFC_FSTAT_CCIF_MASK) == 0U)"),
    "interrupts not suspended": (SOURCE, "uint32_t const oldInterruptStatus = "
                                         "getMachineStateRegisterValueAndSuspendAllInterrupts();",
                                 "uint32_t const oldInterruptStatus = 0U;"),
    "cache stays on": (SOURCE, "    cacheDisable();\n", "\n"),
    "header fields swapped": (SOURCE, "(static_cast<uint32_t>(command) << 24U)", "(static_cast<uint32_t>(command) << 16U)"),
    "little-endian words": (SOURCE, "(static_cast<uint32_t>(bytes[i]) << 24U) | (static_cast<uint32_t>(bytes[i + 1U]) << 16U)",
                            "static_cast<uint32_t>(bytes[i]) | (static_cast<uint32_t>(bytes[i + 1U]) << 16U)"),
    "error bits from the low half": (SOURCE, "csec::pramRead(_pram, WORD_ERROR) >> 16U",
                                     "csec::pramRead(_pram, WORD_ERROR)"),
    "answer read on error": (SOURCE, "    if (result == Result::DONE)\n    {\n        readBytes(_pram, WORD_M4",
                             "    if (true)\n    {\n        readBytes(_pram, WORD_M4"),
    "M3 into the wrong page": (SOURCE, "constexpr uint32_t WORD_M3        = 16U;", "constexpr uint32_t WORD_M3        = 20U;"),
    "INIT_RNG although RIN is set": (SOURCE, "if ((FTFC->FCSESTAT & FTFC_FCSESTAT_RIN_MASK) != 0U)\n    {\n"
                                             "        return Result::DONE;",
                                     "if (false)\n    {\n        return Result::DONE;"),
    "challenge without RIN": (SOURCE, "if ((FTFC->FCSESTAT & FTFC_FCSESTAT_RIN_MASK) == 0U)\n    {\n"
                                      "        return Result::RNG_NOT_READY;",
                              "if (false)\n    {\n        return Result::RNG_NOT_READY;"),
    # NVM-17: RUN mode, watchdog plan and refresh, budgets, duration, journal, CallSeq
    "HSRUN accepted": (SOURCE, "if ((IP_SMC->PMSTAT & SMC_PMSTAT_PMSTAT_MASK) != PMSTAT_RUN)",
                       "if (false && ((IP_SMC->PMSTAT & SMC_PMSTAT_PMSTAT_MASK) != PMSTAT_RUN))"),
    "no watchdog refresh": (SOURCE, "if ((count >= run.refreshAt) && (run.refreshesLeft != 0U))", "if (false)"),
    "refresh without budget": (SOURCE, "if ((count >= run.refreshAt) && (run.refreshesLeft != 0U))",
                               "if (count >= run.refreshAt)"),
    "refresh at any count": (SOURCE, "if ((count >= run.refreshAt) && (run.refreshesLeft != 0U))",
                             "if ((count >= 1U) && (run.refreshesLeft != 0U))"),
    "window ignored": (SOURCE, "        if (window > refreshAt)", "        if (false)"),
    "watchdog clock not checked": (SOURCE, "(((cs & WDOG_CS_CLK_MASK) >> WDOG_CS_CLK_SHIFT) != WDOG_CLK_LPO) || ",
                                   "(false && (((cs & WDOG_CS_CLK_MASK) >> WDOG_CS_CLK_SHIFT) != WDOG_CLK_LPO)) || "),
    "32-bit refresh not checked": (SOURCE, " || ((cs & WDOG_CS_CMD32EN_MASK) == 0U)", ""),
    "window without room accepted": (SOURCE, "if ((refreshAt == 0U) || (refreshAt >= timeout))",
                                     "if (refreshAt == 0U)"),
    "prescaler ignored": (SOURCE, "((cs & WDOG_CS_PRES_MASK) != 0U) ? (WATCHDOG_LPO_HZ / 256U) : WATCHDOG_LPO_HZ",
                          "WATCHDOG_LPO_HZ"),
    "DBG_AUTH with the short budget": (SOURCE, "        return BUDGET_DBG_AUTH_MS;", "        return BUDGET_OTHER_MS;"),
    "INIT_RNG with the long budget": (SOURCE, "        return BUDGET_INIT_RNG_MS;", "        return BUDGET_DBG_AUTH_MS;"),
    "budget never ends": (SOURCE, "static_cast<uint32_t>(budgetTicks / refreshAt) + 1U",
                          "static_cast<uint32_t>(budgetTicks / refreshAt) + 1000000U"),
    "duration not scaled": (SOURCE, "(static_cast<uint64_t>(run.ticks) * 1000U) / plan.ticksPerSecond",
                            "static_cast<uint64_t>(run.ticks) / plan.ticksPerSecond"),
    "duration from the start count on": (SOURCE, "    run.ticks = ticks - start;", "    run.ticks = ticks - (start & 0U);"),
    "no running mark": (SOURCE, "    journal(command, Journal::STATE_RUNNING, 0U, DURATION_UNKNOWN);\n", ""),
    "journal after the interrupts": (SOURCE, "    journal(command, Journal::STATE_DONE, _errorBits, _durationMs);\n"
                                             "    resumeAllInterrupts(oldInterruptStatus);",
                                     "    resumeAllInterrupts(oldInterruptStatus);\n"
                                     "    journal(command, Journal::STATE_DONE, _errorBits, _durationMs);"),
    "journal check word": (SOURCE, "_journal->check      = ~(Journal::TAG ^ word ^ durationMs);",
                           "_journal->check      = ~(Journal::TAG ^ word);"),
    "CallSeq 1 (Figure 36-42)": (SOURCE, "constexpr uint8_t CALL_FIRST  = 0x00U;", "constexpr uint8_t CALL_FIRST  = 0x01U;"),
    "hex: lower case refused": ("include/csec/CsecHex.h", "    if ((c >= 'a') && (c <= 'f'))", "    if (false)"),
    "hex: length not checked": ("include/csec/CsecHex.h", "(length != (2U * bytes))", "(length < (2U * bytes))"),
    "sequence stops not on error": (SOURCE, "    result = launch(CMD_GET_ID, 0U, plan);\n    if (result != Result::DONE)",
                                    "    result = launch(CMD_GET_ID, 0U, plan);\n    if (false)"),
    "challenge kept after a load": (SOURCE, "    _challengeValid = false; // any command", "    // any command"),
    "second attempt per challenge": (SOURCE, "    _challengeValid = false; // one attempt per challenge", ""),
    "auth without challenge": (SOURCE, "    if (!_challengeValid)\n    {\n        return Result::NO_CHALLENGE;",
                               "    if (false)\n    {\n        return Result::NO_CHALLENGE;"),
    "auth word not bound to the challenge": (SOURCE, "hexDigit(static_cast<uint8_t>(out.challenge[i] >> 4U))",
                                             "hexDigit(static_cast<uint8_t>(0U))"),
    "auth without confirmation": (SOURCE, "if (!sameText(confirmation, _authWord))",
                                  "if (false && !sameText(confirmation, _authWord))"),
    # NVM-21: the CSE_PRAM address (board 2026-09-30: S32K116 address on the S32K144)
    "S32K11x address on the S32K14x": ("include/csec/CsecPramAddress.h",
                                       "PRAM_BASE_S32K14X = 0x14001000U;", "PRAM_BASE_S32K14X = 0x14000800U;"),
    "S32K11x address wrong": ("include/csec/CsecPramAddress.h",
                              "PRAM_BASE_S32K11X = 0x14000800U;", "PRAM_BASE_S32K11X = 0x14001000U;"),
    "sub-series swapped": ("include/csec/CsecPramAddress.h", "(subseries == SUBSERIES_S32K14X) ? PRAM_BASE_S32K14X",
                           "(subseries == SUBSERIES_S32K11X) ? PRAM_BASE_S32K14X"),
    "unknown sub-series accepted": (SOURCE, "    if (pram == 0U)\n    {\n        return Result::UNKNOWN_PART;",
                                    "    if (false)\n    {\n        return Result::UNKNOWN_PART;"),
    "fixed address in the RAM function": (SOURCE, "csecLaunchFromRam(header, _pram, run);",
                                          "csecLaunchFromRam(header, csec::PRAM_BASE_S32K11X, run);"),
    "answer read at a fixed address": (SOURCE, "readBytes(_pram, WORD_M4, answer.m4, 32U);",
                                       "readBytes(csec::PRAM_BASE_S32K14X, WORD_M4, answer.m4, 32U);"),
    "challenge valid after a failed DBG_CHAL": (SOURCE, "    result   = launch(CMD_DBG_CHAL, 0U, plan); // DBG_AUTH must follow directly\n"
                                                        "    if (result != Result::DONE)\n    {\n        return result;",
                                                "    result   = launch(CMD_DBG_CHAL, 0U, plan); // DBG_AUTH must follow directly\n"
                                                "    if (result != Result::DONE)\n    {\n        _challengeValid = true;\n"
                                                "        return result;"),
}


class MutationTest(unittest.TestCase):
    def test_mutations_are_detected(self):
        work = Path(tempfile.mkdtemp())
        try:
            survivors = []
            for i, (name, (rel, old, new)) in enumerate(MUTATIONS.items()):
                copy = work / f"m{i}"
                shutil.copytree(MODULE / "include", copy / "include")
                shutil.copytree(MODULE / "src", copy / "src")
                path = copy / rel
                text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
                self.assertEqual(text.count(old), 1, f"{name}: {old!r} found {text.count(old)} times")
                path.write_text(text.replace(old, new), encoding="utf-8")
                ok, out, exe = build(copy, copy)
                self.assertTrue(ok, f"mutant '{name}' does not compile (fault of this script):\n{out[-2000:]}")
                passed, _ = run(exe)
                if passed:
                    survivors.append(name)
            print(f"\n{len(MUTATIONS) - len(survivors)} of {len(MUTATIONS)} mutants detected", file=sys.stderr)
            self.assertEqual(survivors, [])
        finally:
            shutil.rmtree(work, ignore_errors=True)


def code_of_text(text: str) -> str:
    """C++ without comments and string literals."""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return "\n".join(re.sub(r'"(?:\\.|[^"\\])*"', '""', line.split("//")[0]) for line in text.splitlines())


def code_of(path: Path) -> str:
    return code_of_text(path.read_text(encoding="utf-8"))


PORT = ROOT / "platforms" / "s32k1xx" / "port" / "main"
COMMAND_CPP = PORT / "src" / "systems" / "CsecCommand.cpp"
COMMAND_H = PORT / "include" / "systems" / "CsecCommand.h"
OPTION = "NX_FLEXNVM_PARTITION_COMMAND"


def ifdef_blocks(text: str, macro: str) -> list[str]:
    """Contents of every #ifdef <macro> ... #endif block (no nesting of the same macro)."""
    blocks, pos = [], 0
    while True:
        start = text.find(f"#ifdef {macro}", pos)
        if start < 0:
            return blocks
        depth, i = 0, start
        for m in re.finditer(r"^\s*#\s*(if|ifdef|ifndef|endif)\b", text[start:], re.M):
            depth += 1 if m.group(1) != "endif" else -1
            if depth == 0:
                i = start + m.end()
                break
        blocks.append(text[start:i])
        pos = i


class GuardTest(unittest.TestCase):
    def test_only_the_console_command_of_the_test_build_calls_it(self):
        callers = []
        for base in ("platforms", "ecu", "libs", "bootloader"):
            for path in (ROOT / base).rglob("*"):
                if path.suffix not in (".cpp", ".c", ".h", ".hpp") or MODULE in path.parents:
                    continue
                if "CsecRecovery" in code_of(path):
                    callers.append(path.relative_to(ROOT).as_posix())
        self.assertEqual(sorted(callers), ["platforms/s32k1xx/port/main/include/systems/CsecCommand.h",
                                           "platforms/s32k1xx/port/main/src/systems/CsecCommand.cpp"])
        # everything that names the command outside its own files sits behind the option
        for rel in ("include/systems/BspSystem.h", "src/systems/BspSystem.cpp", "src/integration/Integration.cpp"):
            text = (PORT / rel).read_text(encoding="utf-8")
            guarded = "".join(ifdef_blocks(text, OPTION))
            for name in ("CsecCommand", "csecInitRngAtStart", "_csecCommand"):
                self.assertEqual(code_of_text(text).count(name), code_of_text(guarded).count(name), f"{rel}: {name}")
        self.assertIn("csecInitRngAtStart", "".join(ifdef_blocks(
            (PORT / "src/integration/Integration.cpp").read_text(encoding="utf-8"), OPTION)))

    def test_library_and_command_only_with_the_option(self):
        cmake = (MODULE / "CMakeLists.txt").read_text(encoding="utf-8")
        self.assertIn("add_library(bspCsecRecovery EXCLUDE_FROM_ALL", cmake)
        # only files of this checkout: agent worktrees (.claude/worktrees) and build folders are not the repository
        users = sorted(p.relative_to(ROOT).as_posix() for p in ROOT.rglob("CMakeLists.txt")
                       if not {"external", ".claude", "build"} & set(p.relative_to(ROOT).parts)
                       and p.parent != MODULE
                       and ("bspCsec" in p.read_text(encoding="utf-8", errors="replace")))
        self.assertEqual(users, ["platforms/s32k1xx/port/main/CMakeLists.txt"])
        port = (PORT / "CMakeLists.txt").read_text(encoding="utf-8")
        self.assertRegex(port, r'option\(NX_FLEXNVM_PARTITION_COMMAND\s+"[^"]*"\s+OFF\)')
        start = port.index(f"if ({OPTION})")
        block = port[start:port.index("endif ()", start)]
        for line in ('add_subdirectory("${NX_ROOT}/platforms/s32k1xx/bsp/bspCsec" nxBspCsec)',
                     "target_sources(main PRIVATE src/systems/CsecCommand.cpp)",
                     "target_link_libraries(main PRIVATE bspCsecRecovery)"):
            self.assertIn(line, block)
            self.assertEqual(port.count(line), 1, line)
        self.assertEqual(port.count("CsecCommand"), 1)

    def test_start_up_runs_only_the_requested_init_rng(self):
        code = code_of(COMMAND_CPP)
        hook = code[code.index("void csecInitRngAtStart()"):]
        self.assertEqual(re.findall(r"csec\.(\w+)\(", hook), ["setJournal", "initRng", "errorBits",
                                                               "lastDurationMs", "lastWatchdogRefreshes",
                                                               "errorBits", "lastDurationMs"])
        request = hook.index("RNG_REQUEST"), hook.index("csecRngRequest[0] = 0U"), hook.index("initRng()")
        self.assertLess(request[0], request[1])
        self.assertLess(request[1], request[2], "request cleared before INIT_RNG (a reset does not repeat it)")
        for rel in ("src/main.cpp", "src/lifecycle/StaticBsp.cpp"):
            self.assertNotIn("csec", code_of(PORT / rel).lower(), rel)
        # the request and the journal are no-init RAM (they survive the reset)
        text = COMMAND_CPP.read_text(encoding="utf-8")
        self.assertIn('__attribute__((section(".noinit.csec"))) CsecRecovery::Journal volatile csecJournal;', text)
        self.assertIn('__attribute__((section(".noinit.csec"))) uint32_t volatile csecRngRequest[2];', text)

    def test_long_commands_check_the_external_watchdog(self):
        code = code_of(COMMAND_CPP)
        body = code[code.index("void CsecCommand::executeCommand("):code.index("void csecInitRngAtStart()")]
        for call, command in (("_csec.loadMasterKey(", "CMD_LOAD_KEY"), ("_csec.challenge(", "CMD_DBG_CHAL"),
                              ("_csec.authorize(", "CMD_DBG_AUTH"), ("csecRngRequest[0] = RNG_REQUEST", "CMD_INIT_RNG")):
            at = body.index(call)
            check = body.rindex("externalWatchdogAllows(out, CsecRecovery::", 0, at)
            self.assertIn(command, body[check:check + 80], call)
            self.assertNotIn("if (idx ==", body[check:at], f"{call}: check in the same branch")

    def test_no_key_flag_in_the_code(self):
        # the device builds no M2 and knows no flag: the only WRITE_PROT* name is the error bit it reports
        for rel in (SOURCE, HEADER, "include/csec/CsecPram.h"):
            names = set(re.findall(r"\w*WRITE_PROT\w*", code_of(MODULE / rel)))
            self.assertTrue(names <= {"ERC_KEY_WRITE_PROTECTED"}, f"{rel}: {names}")
        code = code_of(MODULE / SOURCE)
        # every CSEc command the device can launch; no key update other than the checked first load
        launched = set(re.findall(r"launch\((CMD_\w+)", code))
        self.assertEqual(launched, {"CMD_LOAD_KEY", "CMD_INIT_RNG", "CMD_GET_ID", "CMD_DBG_CHAL", "CMD_DBG_AUTH"})
        self.assertEqual(code.count("launch(CMD_LOAD_KEY"), 1)
        load = code[code.index("CsecRecovery::loadMasterKey("):code.index("CsecRecovery::challenge(")]
        guard, rest = load.split("writeBytes(_pram, WORD_PAGE1", 1)
        for check in ("sameText(confirmation, LOAD_KEY_CONFIRMATION)", "m1[15] != M1_MASTER_BY_MASTER",
                      "m2[i] != FIRST_LOAD_M2_BLOCK0[i]", "ready(CMD_LOAD_KEY, plan)"):
            self.assertIn(check, guard, "checked before the first write to the parameter RAM")
        auth = code[code.index("CsecRecovery::authorize("):code.index("CsecRecovery::resultText(")]
        guard = auth.split("writeBytes(_pram, WORD_PAGE1", 1)[0]
        for check in ("_challengeValid", "sameText(confirmation, _authWord)", "ready(CMD_DBG_AUTH, plan)"):
            self.assertIn(check, guard)

    def test_launch_function_in_ram_section(self):
        code = code_of(MODULE / SOURCE)
        self.assertIn('void csecLaunchFromRam(uint32_t header, uintptr_t pram, WatchdogRun& run) '
                      '__attribute__((section("")));', code)
        text = (MODULE / SOURCE).read_text(encoding="utf-8")
        self.assertIn('__attribute__((section(".launchFromRAM")))', text)
        self.assertIn('KEEP(*(.launchFromRAM))',
                      (ROOT / "platforms/s32k1xx/port/main/linkerscript/application.dld.in").read_text(encoding="utf-8"))
        pram = code_of(MODULE / "include/csec/CsecPram.h")
        self.assertEqual(pram.count("__attribute__((always_inline)) inline"), 3)
        # NVM-21: no fixed address in the register view, the caller hands it in
        self.assertNotRegex(pram, r"0x1400[0-9A-Fa-f]{4}U")


MCUDB = ROOT / "mcudb" / "s32k1xx"
ADDRESS_H = MODULE / "include" / "csec" / "CsecPramAddress.h"


class AddressGuardTest(unittest.TestCase):
    """NVM-21: CSE_PRAM of each sub-series right behind the FlexRAM of every derivative ([MAP] Overview Memory Map)."""

    def table(self) -> dict[str, int]:
        code = code_of(ADDRESS_H)
        return {name: int(value, 16) for name, value in
                re.findall(r"constexpr uintptr_t (PRAM_BASE_\w+)\s*=\s*(0x[0-9A-Fa-f]+)U;", code)}

    def flexram(self) -> dict[str, tuple[int, int]]:
        out = {}
        for path in sorted(p for p in MCUDB.glob("S32K1*.yaml") if re.fullmatch(r"S32K1\d\dW?", p.stem)):
            m = re.search(r"\{\s*name:\s*FLEXRAM,\s*kind:\s*eeprom_ram,\s*start:\s*(0x[0-9A-Fa-f]+),\s*"
                          r"size:\s*(0x[0-9A-Fa-f]+)", path.read_text(encoding="utf-8"))
            self.assertIsNotNone(m, f"{path.name}: region FLEXRAM")
            out[path.stem] = (int(m.group(1), 16), int(m.group(2), 16))
        return out

    def test_behind_the_flexram_of_every_derivative(self):
        table = self.table()
        self.assertEqual(set(table), {"PRAM_BASE_S32K14X", "PRAM_BASE_S32K11X"})
        derivatives = self.flexram()
        self.assertGreaterEqual(len(derivatives), 5)
        for name, (start, size) in derivatives.items():
            family = "S32K14X" if name.startswith("S32K14") else "S32K11X" if name.startswith("S32K11") else None
            self.assertIsNotNone(family, name)
            base = table[f"PRAM_BASE_{family}"]
            self.assertEqual(base, start + size, f"{name}: CSE_PRAM right behind the FlexRAM")
            self.assertFalse(start <= base < start + size, f"{name}: CSE_PRAM inside the FlexRAM (EEPROM!)")
        self.assertEqual(table["PRAM_BASE_S32K14X"], 0x14001000)  # [MAP] rows CSE_PRAM
        self.assertEqual(table["PRAM_BASE_S32K11X"], 0x14000800)

    def test_sub_series_codes(self):
        code = code_of(ADDRESS_H)
        self.assertIn("SUBSERIES_S32K11X = 1U;", code)
        self.assertIn("SUBSERIES_S32K14X = 4U;", code)
        # the same codes as the part names of flexnvm/FlexNvmChip.h
        chip = code_of(FLEXNVM_INCLUDE / "flexnvm" / "FlexNvmChip.h")
        self.assertIn("if (id.subseries == 4U)", chip)
        self.assertIn("else if (id.subseries == 1U)", chip)


class ConsoleTest(unittest.TestCase):
    """NVM-17: the host lines of tools/csec against the device command (no device)."""

    @classmethod
    def setUpClass(cls):
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from tools.csec import cli, she
        cls.cli, cls.she = cli, she
        cls.code = COMMAND_CPP.read_text(encoding="utf-8")

    def lines(self) -> list[str]:
        key = bytes(range(0x10, 0x20))  # a made-up key of this test only
        update = self.she.first_master_key_load(key, self.she.WILDCARD_UID)
        auth = self.she.debug_authorization(key, bytes(16), bytes(15))
        return [*self.cli.load_lines(update), self.cli.auth_line(auth, bytes(16))]

    def test_lines_fit_the_console(self):
        self.assertEqual(self.cli.CONSOLE_LINE_MAX, 127)
        for line in self.lines():
            self.assertLessEqual(len(line), self.cli.CONSOLE_LINE_MAX, line)

    def test_lines_name_the_device_subcommands_and_lengths(self):
        table = dict(re.findall(r'COMMAND_GROUP_COMMAND\((ID_\w+), "(\w+)"', self.code))
        self.assertIn('DEFINE_COMMAND_GROUP_GET_INFO_BEGIN(CsecCommand, "csec"', self.code)
        self.assertEqual(set(table.values()), {"status", "m1", "m2", "m3", "loadkey", "rng", "challenge", "dbgauth"})
        lengths = {"m1": 32, "m2": 64, "m3": 32}
        for line in self.lines():
            group, sub, *args = line.split()
            self.assertEqual(group, "csec")
            self.assertIn(sub, table.values(), line)
            if sub in lengths:
                self.assertEqual([len(a) for a in args], [lengths[sub]], line)
            elif sub == "loadkey":
                self.assertEqual(args, ["LOADKEY-MASTER-ECU-KEY"])
            else:
                self.assertEqual([len(a) for a in args], [32, 16], line)
                self.assertTrue(args[1].startswith("DBGAUTH-"))
        # the device parses exactly these lengths (bytes x 2)
        self.assertIn("size_t const bytes    = (idx == ID_M2) ? 32U : 16U;", self.code)
        self.assertIn("scanHex(context, authorization, 16U)", self.code)

    def test_product_check_knows_the_command(self):
        sys.path.insert(0, str(ROOT / "tools" / "elfcheck"))
        import product
        marker = [m for m in product.MARKERS if m.command.startswith("csec ")]
        self.assertEqual(len(marker), 1)
        self.assertEqual(marker[0].option, OPTION)
        for text in marker[0].texts:
            self.assertIn(text.decode(), self.code, "marker text is a text of the command")


def _demo_compile_command(demo: Path) -> list[str] | None:
    try:
        entries = json.loads((demo / "compile_commands.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    for entry in entries:
        command = entry.get("command", "")
        if entry.get("file", "").endswith("src/systems/FlexNvmCommand.cpp") and "RelWithDebInfo" in command:
            compiler, rest = command.split(" ", 1)
            args = shlex.split(rest, posix=True)
            out, skip = [], False
            for a in args:
                if skip:
                    skip = False
                    continue
                if a in ("-o", "-c", "-MF", "-MT", "-MQ"):
                    skip = True
                    continue
                if a in ("-MD", "-MMD") or a.endswith("FlexNvmCommand.cpp"):
                    continue
                out.append(a)
            return [compiler, *out]
    return None


class TargetObjectTest(unittest.TestCase):
    def test_object_for_the_target(self):
        demo = build_dirs.demo_build_dir(ROOT)
        command = _demo_compile_command(demo)
        if command is None:
            self.skipTest(f"no demo build with compile_commands.json at {demo} (tools/test -k target-demo)")
        compiler = Path(command[0])
        objdump = compiler.with_name(compiler.name.replace("g++", "objdump"))
        work = Path(tempfile.mkdtemp())
        try:
            obj = work / "CsecRecovery.o"
            extra = [f"-I{MODULE / 'include'}", f"-I{OPENBSW / 'platforms/s32k1xx/bsp/bspCore/include'}"]
            r = subprocess.run([*command, *extra, "-Wconversion", "-c", str(MODULE / SOURCE), "-o", str(obj)],
                               capture_output=True, text=True, timeout=300)
            self.assertEqual(r.returncode, 0, (r.stdout + r.stderr)[-3000:])
            self.assertEqual(r.stderr.strip(), "", "warning-free")
            dump = subprocess.run([str(objdump), "-d", "-j", ".launchFromRAM", str(obj)], capture_output=True,
                                  text=True, timeout=60)
            self.assertEqual(dump.returncode, 0, dump.stderr)
            self.assertRegex(dump.stdout, r"<[^>]*csecLaunchFromRam[^>]*>:", "launch function in .launchFromRAM")
            body = dump.stdout.split("Disassembly of section .launchFromRAM:", 1)[1]
            calls = re.findall(r"\t(bl|blx)\s", body)
            self.assertEqual(calls, [], "no call out of the RAM function")
            # branches only within the section (the polling loop), never to a symbol elsewhere
            for target in re.findall(r"\tb(?:\.[nw])?\s+[0-9a-f]+ <([^>]+)>", body) + \
                    re.findall(r"\tb(?:eq|ne|cs|cc|mi|pl|hi|ls|ge|lt|gt|le)(?:\.[nw])?\s+[0-9a-f]+ <([^>]+)>", body):
                self.assertIn("csecLaunchFromRam", target)
            # the header store happens inside the section, and the section refers to nothing else (no relocation:
            # register addresses are immediates or its own literals)
            self.assertRegex(body, r"\tstr(?:\.w)?\s")
            relocs = subprocess.run([str(objdump), "-r", "-j", ".launchFromRAM", str(obj)], capture_output=True,
                                    text=True, timeout=60).stdout
            self.assertNotRegex(relocs, r"R_ARM_\w+", relocs)
        finally:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
