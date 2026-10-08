# SPDX-License-Identifier: Apache-2.0
"""Meaningfulness of the FlexIO I2C host test (IO-09): deliberately broken variants of the driver must fail it.

Each mutation is applied to a copy of platforms/s32k1xx/bsp/bspFlexioI2c (src and include), the host test
(test/host/FlexioI2cHostTest.cpp, unchanged) is compiled against the copy with zig c++ and run. A mutant counts as
detected if the test exits non-zero or hangs past the time limit; a mutant that does not compile is a fault of this
script. Runtime a few minutes.
Run: .venv/Scripts/python platforms/s32k1xx/bsp/bspFlexioI2c/test/host/test_flexio_i2c_mutations.py
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
ROOT = HERE.parents[5]
VENDOR = ROOT / "external/openbsw/platforms/s32k1xx/bsp/bspMcu/include/3rdparty/nxp"
SRC = "src/i2c/FlexioI2cMaster.cpp"
TIMING = "include/i2c/FlexioI2cTiming.h"
RUN_LIMIT_S = 60

TIMEOUT_CHECK = (
    "        if ((getSystemTimeUs32Bit() - start) > _config.timeoutUs)\n"
    "        {\n"
    "            return abortPacket(I2cResult::TIMEOUT);"
)

# name: [(file, old, new, occurrences), ...]; `old` must occur exactly `occurrences` times
MUTATIONS = {
    "SCL edge count: 18 x words + 3": [(TIMING, "((words * EDGES_PER_WORD) + 1U)", "((words * EDGES_PER_WORD) + 3U)", 1)],
    "no f/6 limit of the baud divider": [(TIMING, "MIN_BAUD_COMPARE = 2U", "MIN_BAUD_COMPARE = 0U", 1)],
    "baud divider rounded down (SCL faster than requested)": [
        (TIMING, "(((functionalClockHz % twoRequested) != 0U) ? 1U : 0U)", "0U", 1)],
    "14 data bytes per transfer (edge counter overflows)": [
        (TIMING, "MAX_WORDS_PER_PACKET - 1U;", "MAX_WORDS_PER_PACKET;", 1)],
    "no clock stretching (TIMRST never)": [(SRC, "FLEXIO_TIMCFG_TIMRST(2U)", "FLEXIO_TIMCFG_TIMRST(0U)", 1)],
    "trigger active high": [(SRC, "FLEXIO_TIMCTL_TRGPOL(1U)\n", "FLEXIO_TIMCTL_TRGPOL(0U)\n", 1)],
    "receiver samples on the other edge": [
        (SRC, "FLEXIO_SHIFTCTL_TIMSEL(shiftTimer) | FLEXIO_SHIFTCTL_TIMPOL(1U)",
         "FLEXIO_SHIFTCTL_TIMSEL(shiftTimer) | FLEXIO_SHIFTCTL_TIMPOL(0U)", 1)],
    "SDA output not inverted": [
        (SRC, "| FLEXIO_SHIFTCTL_PINSEL(_config.sdaPin) | FLEXIO_SHIFTCTL_PINPOL(1U)",
         "| FLEXIO_SHIFTCTL_PINSEL(_config.sdaPin) | FLEXIO_SHIFTCTL_PINPOL(0U)", 1)],
    "transmit LSB first (SHIFTBUF instead of SHIFTBUFBBS)": [
        (SRC, "io->SHIFTBUFBBS[_config.txShifter] = value;", "io->SHIFTBUF[_config.txShifter] = value;", 1)],
    "receive without bit swap (SHIFTBUF instead of SHIFTBUFBIS)": [
        (SRC, "io->SHIFTBUFBIS[_config.rxShifter]", "io->SHIFTBUF[_config.rxShifter]", 1)],
    "read: last byte acknowledged": [(SRC, "(loaded < (words - 1U))", "(loaded < words)", 1)],
    "read: no byte acknowledged": [(SRC, "bool const masterAck = isRead &&", "bool const masterAck = false &&", 1)],
    "NACK of address/data ignored": [
        (SRC, "return abortPacket((index == 0U) ? I2cResult::ADDRESS_NACK : I2cResult::DATA_NACK);", ";", 1)],
    "address NACK reported as data NACK": [
        (SRC, "(index == 0U) ? I2cResult::ADDRESS_NACK : I2cResult::DATA_NACK", "I2cResult::DATA_NACK", 1)],
    "no read back of written bits": [(SRC, "if (value != sent)", "if ((value != sent) && false)", 1)],
    "always repeated START word (no STOP)": [
        (SRC, "sendStop ? STOP_WORD : REPEATED_START_WORD", "REPEATED_START_WORD", 1)],
    "transmit underrun not checked": [(SRC, "if ((io->SHIFTERR & txBit) != 0U)", "if (false)", 1)],
    "no timeout while the packet runs": [(SRC, TIMEOUT_CHECK, TIMEOUT_CHECK.replace("> _config.timeoutUs", "> 0xFFFFFFFFU"), 2)],
    "no bus busy check before START": [
        (SRC, "    if ((!isPinHigh(_config.sdaPin)) || (!isPinHigh(_config.sclPin)))\n    {\n        ++_errorCount;\n"
              "        return I2cResult::BUS_BUSY;",
         "    if (false)\n    {\n        ++_errorCount;\n        return I2cResult::BUS_BUSY;", 1)],
    "end of packet not awaited (TIMSTAT)": [
        (SRC, "while (((io->TIMSTAT & sclBit) == 0U) ||", "while (((io->TIMSTAT & sclBit) == 0xFFFFFFFFU) ||", 1)],
    "abort without STOP word": [
        (SRC, "io->SHIFTBUFBBS[_config.txShifter] = STOP_WORD;", "io->SHIFTBUFBBS[_config.txShifter] = REPEATED_START_WORD;", 1)],
    "abort: no START/STOP when SDA is high": [(SRC, "            setTxStopBit(false);\n", "            setTxStopBit(true);\n", 1)],
    "abort: timers left running": [
        (SRC, "    io->TIMCTL[_config.sclTimer]      = 0U;\n    io->TIMCTL[_config.sclTimer + 1U] = 0U;\n    if (isPinHigh",
         "    if (isPinHigh", 1)],
    "flags of the previous transfer not cleared": [
        (SRC, "    releaseResources();\n    clearFlags();\n\n    // Word counts", "    releaseResources();\n\n    // Word counts", 1)],
    "error counter not incremented on abort": [(SRC, "    ++_errorCount;\n\n    // 54.5.5 on NACK", "\n    // 54.5.5 on NACK", 1)],
    "other FlexIO users reset (CTRL written without read)": [
        (SRC, "io->CTRL = io->CTRL | FLEXIO_CTRL_FLEXEN_MASK", "io->CTRL = FLEXIO_CTRL_FLEXEN_MASK", 1)],
    "PARAM pin count not checked": [
        (SRC, "(_config.sdaPin < pins) && (_config.sclPin < pins)",
         "(pins != 0U) && (_config.sdaPin < 8U) && (_config.sclPin < 8U)", 1)],
    "PARAM timer count not checked": [(SRC, "(shiftTimer < timers)", "(timers != 0U)", 1)],
}


def compile_and_run(module: Path, workdir: Path, name: str):
    """Returns (compiled, passed, output)."""
    exe = workdir / f"{name}.exe"
    cmd = [sys.executable, "-m", "ziglang", "c++", "-std=c++17", "-O1", "-Wall", "-Wextra", "-Wconversion", "-Werror",
           f"-I{HERE / 'stubs'}", f"-I{module / 'include'}", f"-isystem{VENDOR}", str(module / SRC),
           str(HERE / "FlexioI2cHostTest.cpp"), "-o", str(exe)]
    build = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=600)
    if build.returncode != 0:
        return False, False, build.stdout + build.stderr
    try:
        run = subprocess.run([str(exe)], cwd=ROOT, capture_output=True, text=True, timeout=RUN_LIMIT_S)
    except subprocess.TimeoutExpired:
        return True, False, "hangs"
    return True, run.returncode == 0, run.stdout[-2000:]


def apply(module: Path, changes):
    for rel, old, new, occurrences in changes:
        path = module / rel
        text = path.read_text(encoding="utf-8")
        found = text.count(old)
        if found != occurrences:
            raise AssertionError(f"{rel}: {old!r} found {found} times, expected {occurrences}")
        path.write_text(text.replace(old, new), encoding="utf-8")


class FlexioI2cMutationTest(unittest.TestCase):
    def test_mutants_are_detected(self):
        work = Path(tempfile.mkdtemp(prefix="nx_flexio_i2c_mut_"))
        try:
            compiled, passed, output = compile_and_run(MODULE, work, "original")
            self.assertTrue(compiled and passed, f"original does not pass:\n{output}")
            survivors, invalid = [], []
            for index, (name, changes) in enumerate(MUTATIONS.items()):
                module = work / f"m{index}"
                shutil.copytree(MODULE / "src", module / "src")
                shutil.copytree(MODULE / "include", module / "include")
                apply(module, changes)
                compiled, passed, output = compile_and_run(module, work, f"m{index}")
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
