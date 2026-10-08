# SPDX-License-Identifier: Apache-2.0
"""Meaningfulness of the FlexCAN host test (COM-26): deliberately broken variants of the driver copy must fail it.

Each mutation is applied to a copy of platforms/s32k1xx/port/bspConfiguration/bspFlexCan (src and include), the host
test (test/host/FlexCanDeviceHostTest.cpp, unchanged) is compiled against the copy with zig c++ and run. A mutant
counts as detected if the test exits non-zero or hangs past the time limit; a mutant that does not compile is a fault
of this script. Several mutants restore a behaviour of the OpenBSW original (fixed split for 32 buffers, MAXMB ORed
into the reset value, transmit mask of buffer 15, transmit callback on every interrupt). Runtime a few minutes.
Run: .venv/Scripts/python platforms/s32k1xx/port/bspConfiguration/bspFlexCan/test/host/test_flexcan_device_mutations.py
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
OPENBSW = ROOT / "external/openbsw"
SRC = "src/can/FlexCANDevice.cpp"
HDR = "include/can/FlexCANDevice.h"
RUN_LIMIT_S = 60

# name: [(file, old, new, occurrences), ...]; `old` must occur exactly `occurrences` times
MUTATIONS = {
    "OpenBSW: transmit callback on every interrupt of the vector": [
        (SRC, "if ((fpDevice->IFLAG1 & fpDevice->IMASK1 & fTxInterruptMask0) == 0U)", "if (false)", 1)],
    "OpenBSW: transmit mask of buffer 15 until the first callback frame": [
        (SRC, "fTxInterruptMask0(1UL << fLayout.callback)", "fTxInterruptMask0(1UL << 15U)", 2)],
    "OpenBSW: MAXMB ORed into its reset value": [
        (SRC, "fpDevice->MCR = (fpDevice->MCR & ~FLEXCAN_MCR_MAXMB_MASK)", "fpDevice->MCR = fpDevice->MCR", 1)],
    "OpenBSW: MAXMB 32 (number of buffers instead of the last one)": [
        (SRC, "FLEXCAN_MCR_MAXMB(static_cast<uint32_t>(fLayout.count) - 1U)",
         "FLEXCAN_MCR_MAXMB(static_cast<uint32_t>(fLayout.count))", 1)],
    "OpenBSW: numMessageBuffers ignored (always 32)": [
        (HDR, "(config.numMessageBuffers == 0U) ? DEFAULT_MESSAGE_BUFFERS\n"
              "                                                               : config.numMessageBuffers",
         "(config.numMessageBuffers == 0U) ? DEFAULT_MESSAGE_BUFFERS : DEFAULT_MESSAGE_BUFFERS", 1)],
    "transmit from buffer 16 on every instance": [
        (HDR, "return {count, static_cast<uint8_t>(count / 2U), static_cast<uint8_t>(count - 1U)};",
         "return {count, 16U, static_cast<uint8_t>(count - 1U)};", 1)],
    "callback buffer 31 on every instance": [
        (HDR, "return {count, static_cast<uint8_t>(count / 2U), static_cast<uint8_t>(count - 1U)};",
         "return {count, static_cast<uint8_t>(count / 2U), 31U};", 1)],
    "layout check without the receive buffers": [
        (HDR, "               && ((static_cast<uint32_t>(config.numRxBufsStd) + config.numRxBufsExt)\n"
              "                   <= layout.firstTransmit)\n", "", 1)],
    "layout check without the transmit buffers": [
        (HDR, "               && ((static_cast<uint32_t>(layout.firstTransmit) + config.numTxBufsApp)\n"
              "                   <= layout.callback)", "", 1)],
    "odd numbers of buffers accepted": [(HDR, "               && ((layout.count % 2U) == 0U)\n", "", 1)],
    "constructor does not check the transmit buffers": [
        (SRC, "        endOfApplicationBuffers() <= static_cast<uint32_t>(fLayout.callback),",
         "        true,", 1)],
    "application buffers run into the callback buffer": [
        (SRC, "if (fLastMessageBuffer < (endOfApplicationBuffers() - 1U))",
         "if (fLastMessageBuffer < endOfApplicationBuffers())", 2)],
    "transmit does not check the buffer index": [
        (SRC, "= ((bufIdx >= fLayout.firstTransmit) &&", "= true || ((bufIdx >= fLayout.firstTransmit) &&", 1)],
    "mute leaves the callback buffer transmitting": [
        (SRC, "    messageBuffer(fLayout.callback).FLAGS.B.CODE = CANTxBuffer::CODE_INACTIVE;\n", "", 1)],
    "callback buffer not set up in init": [
        (SRC, "    setupMessageBuffer(fLayout.callback, CANTxBuffer::CODE_INACTIVE, false);\n", "", 1)],
    "transmit interrupt not disabled after the callback frame": [
        (SRC, "    disableTransmitInterrupt();\n    fFrameSentCallback();", "    fFrameSentCallback();", 1)],
    "extended receive buffers set up as standard": [
        (SRC, "setupMessageBuffer(i, CANRxBuffer::CODE_EMPTY, true);", "setupMessageBuffer(i, CANRxBuffer::CODE_EMPTY, false);",
         1)],
}


def compile_and_run(module: Path, workdir: Path, name: str):
    """Returns (compiled, passed, output)."""
    exe = workdir / f"{name}.exe"
    cmd = [sys.executable, "-m", "ziglang", "c++", "-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror",
           "-DETL_USE_ASSERT_FUNCTION", f"-I{HERE / 'stubs'}", f"-I{module / 'include'}",
           f"-isystem{OPENBSW / 'libs/bsw/bsp/include'}", f"-isystem{OPENBSW / 'libs/bsw/cpp2can/include'}",
           f"-isystem{OPENBSW / 'libs/3rdparty/etl/include'}",
           f"-isystem{OPENBSW / 'platforms/s32k1xx/bsp/bspMcu/include/3rdparty/nxp'}",
           str(module / SRC), str(OPENBSW / "libs/bsw/cpp2can/src/can/canframes/CANFrame.cpp"),
           str(HERE / "FlexCanDeviceHostTest.cpp"), "-o", str(exe)]
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
        text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
        found = text.count(old)
        if found != occurrences:
            raise AssertionError(f"{rel}: {old!r} found {found} times, expected {occurrences}")
        path.write_text(text.replace(old, new), encoding="utf-8")


class FlexCanDeviceMutationTest(unittest.TestCase):
    def test_mutants_are_detected(self):
        work = Path(tempfile.mkdtemp(prefix="nx_flexcan_mut_"))
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
                if not compiled and "FlexCanDeviceHostTest.cpp" in output and "static assertion failed" in output:
                    state = "detected (static_assert of the host test)"
                elif not compiled:
                    invalid.append(name)
                    state = "does not compile"
                elif passed:
                    survivors.append(name)
                    state = "SURVIVED"
                else:
                    state = "detected" + (" (hangs)" if output == "hangs" else "")
                print(f"{state:>18}: {name}", flush=True)
                if name in invalid:
                    print(output[-1500:])
            print(f"{len(MUTATIONS)} mutants, {len(survivors)} survived, {len(invalid)} did not compile")
            self.assertEqual(invalid, [], "mutants must compile")
            self.assertEqual(survivors, [], "mutants not detected by the host test")
        finally:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    os.environ.setdefault("PYTHONUTF8", "1")
    unittest.main()
