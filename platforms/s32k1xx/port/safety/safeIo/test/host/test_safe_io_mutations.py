# SPDX-License-Identifier: Apache-2.0
"""Meaningfulness of the SafeIo host test (SYS-42, SYS-24, SM-21/SM-22): deliberately broken variants must fail it.

Each mutation is applied to a copy of src/safeIo/SafeIo.cpp or of include/safeIo/RegisterCheck.h (the engine of the
generated register list, SYS-24), the host test (SafeIoHostTest.cpp, unchanged) is compiled against the copy with
zig c++ exactly as the tools/test entry safe-io does, and run. A mutated header is put into an include directory
between the stubs and the module's own include directory. A mutant counts as detected if the test exits non-zero or
hangs; a mutant that does not compile is a fault of this script.
SYS-138: the host test compiles the real SafeSupervisor.cpp with the read-back (NX_SAFE_SUPERVISOR_READBACK), so the
mutants of the read-back for the hold, the degraded groups and the S1 + S2 sequence are judged by the supervisor's use.
SYS-153: mutants of the access width (8-bit entry PMC_LVDSC1) and of its static checks in RegisterCheck.h.
Run: .venv/Scripts/python platforms/s32k1xx/port/safety/safeIo/test/host/test_safe_io_mutations.py (about 1 min)
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
ROOT = HERE.parents[6]
SRC = "src/safeIo/SafeIo.cpp"
ENGINE = "include/safeIo/RegisterCheck.h"
READBACK = "include/safeIo/SafeIoRegisters.h"   # SYS-30: acceptance rule of the read-back
RUN_LIMIT_S = 60

# name: (file, [(old, new, occurrences), ...]); `old` must occur exactly `occurrences` times in that file
MUTATIONS = {
    # SM-21, SafeIo::init (SYS-24: the generated list instead of one demo pin)
    "SM-21: lock not written": (SRC, [
        ("    registers::lock(registers::safeIoRegisters, registers::safeIoRegisterCount);\n", "", 1),
    ]),
    "SM-21: read-back after the lock skipped": (SRC, [
        ("registers::safeIoRegisterCount, 0U, true);",
         "registers::safeIoRegisterCount, registers::safeIoRegisterCount, true);", 1),
    ]),
    "SM-21: read-back judges checked-only registers (reset for a timer fault)": (SRC, [
        ("registers::safeIoRegisterCount, 0U, true);", "registers::safeIoRegisterCount, 0U, false);", 1),
    ]),
    "SM-21: report without the index of the entry": (SRC, [
        ("registers::context(registers::safeIoRegisters[deviating].registerClass, deviating));",
         "registers::context(registers::safeIoRegisters[deviating].registerClass, 0U));", 1),
    ]),
    # SYS-30 (G1): read-back of the safe state while S1 is latched
    "SYS-30: read-back never done": (SRC, [
        ("if ((outputFault == 0U) && (::nxSafeStateIsLatched() != 0))", "if (false)", 1),
    ]),
    "SYS-30: read-back also outside S1 (PWM running is reported)": (SRC, [
        ("(::nxSafeStateIsLatched() != 0))", "(true))", 1),
    ]),
    "SYS-30: read-back fault not latched (reported every cycle)": (SRC, [
        ("if ((outputFault == 0U) && (::nxSafeStateIsLatched()", "if ((true) && (::nxSafeStateIsLatched()", 1),
    ]),
    "SYS-30: mask ignored (other channels of the timer judged)": (READBACK, [
        ("    uint32_t const value = readValue & r.mask;", "    uint32_t const value = readValue;", 1),
    ]),
    "SYS-30: short accepted without an active short": (READBACK, [
        ("    if (selected != freewheel)\n", "    if (selected != (freewheel + 1U))\n", 1),
    ]),
    "SYS-30: clock gate not checked (gated timer read)": (SRC, [
        ("if ((registers::read(r.gate) & r.gateMask) == 0U)", "if (false)", 1),
    ]),
    "SYS-30: report without the entry": (SRC, [
        ("registers::context(registers::RegisterClass::SAFE_OUTPUT, index)",
         "registers::context(registers::RegisterClass::SAFE_OUTPUT, 0U)", 1),
    ]),
    "SYS-30: only the first entry read back": (SRC, [
        ("index < registers::safeOutputReadbackCount; ++index", "index < 1U; ++index", 1),
    ]),
    "SYS-30: S1 not effective without reset": (SRC, [
        ("supervisor.lockRegisterMonitor.trigger(outputFault);", "supervisor.checkRegisterMonitor.trigger(false);", 1),
    ]),
    # SYS-138 (review K6): the read-back SafeIo implements for the supervisor (safeSupervisor/SafeStateReadback.h)
    "SYS-138: read-back never armed by init() (nothing judged)": (SRC, [
        ("    readbackArmed = true;\n", "", 1),
    ]),
    "SYS-138: read-back judges before init() (hold before the BSP resets)": (SRC, [
        ("    if (!readbackArmed)\n    {\n        return 0U;\n    }\n", "", 1),
    ]),
    "SYS-138: gated entry confirmed (as up to SYS-30)": (SRC, [
        ("        return false;\n    }\n    uint32_t const value = registers::read(r.address);",
         "        return true;\n    }\n    uint32_t const value = registers::read(r.address);", 1),
    ]),
    "SYS-138: degraded read-back ignores its entry mask": (SRC, [
        ("if ((!all) && (", "if ((!all) && false && (", 1),
    ]),
    "SYS-138: degraded read-back shifts the entry mask": (SRC, [
        ("(static_cast<uint32_t>(1U) << index)", "(static_cast<uint32_t>(2U) << index)", 1),
    ]),
    "SYS-138: read-back of S1 for the supervisor confirms everything": (SRC, [
        ("uint32_t safeStateReadback() { return firstUnconfirmed(true, 0U); }",
         "uint32_t safeStateReadback() { return 0U; }", 1),
    ]),
    "SYS-138: read-back of the degraded groups confirms everything": (SRC, [
        ("{ return firstUnconfirmed(false, entries); }", "{ return (entries & 0U); }", 1),
    ]),
    # SM-22, SafeIo::cyclic - the register list
    "SM-22: register list not checked in the cycle": (SRC, [
        ("registers::safeIoRegisterCount, 0U, false);",
         "registers::safeIoRegisterCount, registers::safeIoRegisterCount, false);", 1),
    ]),
    "SM-22: register fault not latched (drifting back releases S1)": (SRC, [
        ("    if (registerFault == 0U)\n    {\n        size_t const deviating",
         "    registerFault = 0U;\n    {\n        size_t const deviating", 1),
    ]),
    "SM-22: register fault does not enter the safe state": (SRC, [
        ("(pinConfigSafetyTest.dir == ::bios::Io::_IN) && (registerFault == 0U);",
         "(pinConfigSafetyTest.dir == ::bios::Io::_IN);", 1),
    ]),
    "SM-22: fault context without the class": (SRC, [
        ("= registers::context(registers::safeIoRegisters[deviating].registerClass, deviating);",
         "= registers::context(registers::RegisterClass::PIN_FUNCTION, deviating);", 1),
    ]),
    "init does not clear the latch of the last run": (SRC, [
        ("    registerFault   = 0U;\n", "", 1),
    ]),
    # SM-22, the demonstration pin of the OpenBSW demo (SYS-42)
    "SM-22 demo: only 'output' counts as a deviation (disabled input passes)": (SRC, [
        ("(pinConfigSafetyTest.dir == ::bios::Io::_IN)", "(pinConfigSafetyTest.dir != ::bios::Io::_OUT)", 1),
    ]),
    "SM-22 demo: wrong pin checked": (SRC, [
        ("getConfiguration(::bios::Io::SAFETY_TEST, pinConfigSafetyTest)",
         "getConfiguration(::bios::Io::SAFETY_LOCKED_PIN, pinConfigSafetyTest)", 1),
    ]),
    "SM-22 demo: last state not remembered (report in every cycle)": (SRC, [
        ("        _lastCheckState = checkState;\n", "", 1),
    ]),
    "SM-22 demo: context inverted": (SRC, [
        ("checkState ? safety::SafeSupervisor::CHECK_REGISTER_CONTEXT_OK : registerFault",
         "!checkState ? safety::SafeSupervisor::CHECK_REGISTER_CONTEXT_OK : registerFault", 1),
    ]),
    # SYS-169: the context of the report names the entry of the list
    "SYS-169: report without the entry (context 0 as the demo pin)": (SRC, [
        ("checkState ? safety::SafeSupervisor::CHECK_REGISTER_CONTEXT_OK : registerFault",
         "checkState ? safety::SafeSupervisor::CHECK_REGISTER_CONTEXT_OK : 0U", 1),
    ]),
    "SM-22 demo: start state 'deviating' (first healthy cycle reports)": (SRC, [
        ("    _lastCheckState = true;\n", "    _lastCheckState = false;\n", 1),
    ]),
    # the engine (RegisterCheck.h)
    "engine: mask ignored (bits the list does not judge count)": (ENGINE, [
        ("return (value & entry.mask) != entry.expected;", "return value != entry.expected;", 1),
    ]),
    "engine: lock write copies the write-1-to-clear flag back": (ENGINE, [
        ("(readEntry(e) & ~e.lockClear) | e.lockBits", "readEntry(e) | e.lockBits", 1),
    ]),
    "engine: last entry of the list never checked": (ENGINE, [
        ("for (size_t i = from; i < count; ++i)", "for (size_t i = from; (i + 1U) < count; ++i)", 1),
    ]),
    "engine: read-back judges the checked-only entries instead of the locked ones": (ENGINE, [
        ("(!lockedOnly || (e.lockBits != 0U))", "(!lockedOnly || (e.lockBits == 0U))", 1),
    ]),
    "engine: class in the wrong bits of the context": (ENGINE, [
        ("constexpr uint32_t CONTEXT_CLASS_SHIFT = 24U;", "constexpr uint32_t CONTEXT_CLASS_SHIFT = 16U;", 1),
    ]),
    "engine: a lock bit outside the expected value passes the static check": (ENGINE, [
        ("|| ((e.expected & e.lockBits) != e.lockBits)", "", 1),
    ]),
    # SYS-153: access width of an entry (PMC_LVDSC1 is a byte register)
    "SYS-153: byte entry read with 32 bits": (ENGINE, [
        ("    if (entry.width == WIDTH_8)\n    {\n        return read8(", "    if (false)\n    {\n        return read8(", 1),
    ]),
    "SYS-153: word entry read with 8 bits": (ENGINE, [
        ("    if (entry.width == WIDTH_8)\n    {\n        return read8(",
         "    if (entry.width != 0U)\n    {\n        return read8(", 1),
    ]),
    "SYS-153: byte entry written with 32 bits": (ENGINE, [
        ("    if (entry.width == WIDTH_8)\n    {\n        write8(", "    if (false)\n    {\n        write8(", 1),
    ]),
    "SYS-153: byte write takes the wrong byte": (ENGINE, [
        ("write8(entry.address, static_cast<uint8_t>(value));",
         "write8(entry.address, static_cast<uint8_t>(value >> 8U));", 1),
    ]),
    "SYS-153: static check accepts any width": (ENGINE, [
        ("if ((e.width != WIDTH_32) && (e.width != WIDTH_8))", "if (false)", 1),
    ]),
    "SYS-153: static check accepts a misaligned word": (ENGINE, [
        ("((e.width == WIDTH_32) && ((e.address % 4U) != 0U))", "(false)", 1),
    ]),
    "SYS-153: static check accepts a byte mask beyond its byte": (ENGINE, [
        ("& ~static_cast<uint32_t>(0xFFU)) != 0U)", "& ~static_cast<uint32_t>(0xFFFFU)) != 0U)", 1),
    ]),
    "SYS-153: static check ignores the lock clear bits of a byte entry": (ENGINE, [
        ("(((e.mask | e.lockClear) & ~static_cast", "(((e.mask) & ~static_cast", 1),
    ]),
}


def compile_and_run(source: Path, header_dir: Path | None, workdir: Path, name: str):
    """Returns (compiled, passed, output)."""
    exe = workdir / f"{name}.exe"
    cmd = [sys.executable, "-m", "ziglang", "c++", "-std=c++17", "-O1", "-Wall", "-Wextra", "-Wconversion",
           "-Wno-sign-conversion", "-Werror", "-DPLATFORM_SUPPORT_IO",
           f"-I{HERE / 'ioStubs'}",
           f"-I{ROOT / 'ecu/safety/safeSupervisor/test/host/stubs'}",
           *([f"-I{header_dir}"] if header_dir is not None else []),
           f"-I{MODULE / 'include'}",
           f"-I{ROOT / 'ecu/safety/safeSupervisor/include'}",
           f"-I{ROOT / 'platforms/s32k1xx/hardFaultHandler/include'}",   # SYS-30: latch of S1
           f"-isystem{ROOT / 'external/openbsw/platforms/s32k1xx/bsp/bspIo/include'}",
           f"-isystem{ROOT / 'external/openbsw/libs/bsw/bsp/include'}",
           f"-isystem{ROOT / 'external/openbsw/libs/safety/safeMonitor/include'}",
           str(HERE / "SafeIoHostTest.cpp"), str(source), "-o", str(exe)]
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


class SafeIoMutationTest(unittest.TestCase):
    def test_mutants_are_detected(self):
        work = Path(tempfile.mkdtemp(prefix="nx_safe_io_mut_"))
        try:
            compiled, passed, output = compile_and_run(MODULE / SRC, None, work, "original")
            self.assertTrue(compiled and passed, f"original does not pass:\n{output}")
            survivors, invalid = [], []
            for index, (name, (rel, changes)) in enumerate(MUTATIONS.items()):
                if rel == SRC:
                    source, header_dir = work / f"SafeIo_m{index}.cpp", None
                    mutate(rel, source, changes)
                else:
                    source, header_dir = MODULE / SRC, work / f"include_m{index}"
                    mutate(rel, header_dir / "safeIo" / Path(rel).name, changes)
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
