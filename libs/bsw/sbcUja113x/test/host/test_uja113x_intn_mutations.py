# SPDX-License-Identifier: Apache-2.0
"""IO-68: the host test Uja113xHostTest.cpp detects deliberate faults in the INTN1 and Standby parts of the driver.

Each mutant replaces one unique piece of libs/bsw/sbcUja113x/src/sbc/Uja113x.cpp in a copy, the copy is compiled with
the host test through zig c++ (warnings off, a mutant may leave a variable unused) and run; it counts as detected if
the test fails or does not compile. The unchanged driver must pass.

Run: .venv/Scripts/python -m unittest libs/bsw/sbcUja113x/test/host/test_uja113x_intn_mutations.py
"""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[5]
SRC = REPO / "libs/bsw/sbcUja113x/src/sbc/Uja113x.cpp"
TEST = REPO / "libs/bsw/sbcUja113x/test/host/Uja113xHostTest.cpp"
INC = ["libs/bsw/sbcUja113x/include", "libs/bsp/bspSpi/include", "platforms/s32k1xx/bsp/bspLpspi/include"]

MUTANTS = [
    ("line ACTIVE ignored", "if (line == InterruptLine::ACTIVE)\n    {\n        ++_counters.interruptLineReads;\n        return true;",
     "if (false)\n    {\n        ++_counters.interruptLineReads;\n        return true;"),
    ("no fallback read", "if ((nowMs - _lastInterruptReadMs) >= INTERRUPT_FALLBACK_POLL_MS)",
     "if (false && ((nowMs - _lastInterruptReadMs) >= INTERRUPT_FALLBACK_POLL_MS))"),
    ("fallback read time not updated", "    _lastInterruptReadMs = nowMs;\n    // IO-68: compare", "    // IO-68: compare"),
    ("line fault never latched", "_lineFault = _lineFault || (_lineMissesInRow >= INTERRUPT_LINE_MISS_LIMIT);", "(void)0;"),
    ("line fault latched on first miss", "_lineFault = _lineFault || (_lineMissesInRow >= INTERRUPT_LINE_MISS_LIMIT);",
     "_lineFault = true;"),
    ("miss row not reset by clean read", "        else\n        {\n            _lineMissesInRow = 0U;\n        }",
     "        else\n        {\n        }"),
    ("faulty line still trusted", "if ((line == InterruptLine::UNKNOWN) || _lineFault)", "if (line == InterruptLine::UNKNOWN)"),
    ("release time ignored", "bool const settled = (nowMs - _lastInterruptClearMs) > INTERRUPT_RELEASE_MS;",
     "bool const settled = true;"),
    ("clear time not recorded", "    _lastInterruptClearMs = nowMs;\n    if (flags.any())", "    if (flags.any())"),
    ("bank 1 events dropped", "_events.wakeBank1    = static_cast<uint8_t>(_events.wakeBank1 | flags.wakeBank1);", "(void)0;"),
    ("standby: no CWIE", "uint8_t const trxStandby = static_cast<uint8_t>(wakeup.can ? (trxNormal | UJA113X_CWI_MASK) : trxNormal);\n    Status status ",
     "uint8_t const trxStandby = trxNormal;\n    Status status "),
    ("standby: no source accepted", "    // A Standby nothing can end would leave an MCU in a stop mode without supply of a wake-up (the SBC itself\n    // would not mind, unlike Sleep).\n    if (!wakeup.any())",
     "    if (false)"),
    ("standby: pending interrupt not checked", "    if ((status == Status::OK) && (global == 0U))\n    {\n        status = writeVerified(UJA113X_REG_MODE_CONTROL, UJA113X_MC_STANDBY, UJA113X_MC_MASK);\n        if (status == Status::OK)\n        {\n            status = writeLowPowerWatchdog(wakeup); // allowed",
     "    if (status == Status::OK)\n    {\n        status = writeVerified(UJA113X_REG_MODE_CONTROL, UJA113X_MC_STANDBY, UJA113X_MC_MASK);\n        if (status == Status::OK)\n        {\n            status = writeLowPowerWatchdog(wakeup); // allowed"),
    ("standby: second check missing", "            status = writeLowPowerWatchdog(wakeup); // allowed in Standby only\n        }\n        if (status == Status::OK)\n        {\n            status = read(UJA113X_REG_GLOBAL_IRQ_STATUS, global);\n        }",
     "            status = writeLowPowerWatchdog(wakeup); // allowed in Standby only\n        }"),
    ("standby: watchdog not switched", "            status = writeLowPowerWatchdog(wakeup); // allowed in Standby only", "            // nothing"),
    ("standby: canReady kept", "            _canReady               = false; // the transceiver is offline in Standby", "            // kept"),
    ("standby: watchdog not served", "    if (_standbyWatchdogService)\n    {", "    if (false)\n    {"),
    ("standby: served with cyclic too", "_standbyWatchdogService = _watchdogActive && !wakeup.cyclic;", "_standbyWatchdogService = true;"),
    ("standby: trigger with configured value",
     "Status status = write(UJA113X_REG_WATCHDOG_CONTROL, _standbyWatchdog);\n            if (status == Status::OK)\n            {\n                status = write(UJA113X_REG_WATCHDOG_CONTROL, _standbyWatchdog);",
     "Status status = write(UJA113X_REG_WATCHDOG_CONTROL, watchdogControlValue());\n            if (status == Status::OK)\n            {\n                status = write(UJA113X_REG_WATCHDOG_CONTROL, watchdogControlValue());"),
    ("standby: trigger only once", "            if (status == Status::OK)\n            {\n                status = write(UJA113X_REG_WATCHDOG_CONTROL, _standbyWatchdog);\n            }\n",
     ""),
    ("standby: no supervision of sources", "        intact = (status != Status::OK) || ((value & TRX_IRQ_MASK) == (trxStandby & TRX_IRQ_MASK));",
     "        intact = true;"),
    ("standby: interrupts not handled", "    (void)handleInterrupts(nowMs, line); // the wake-up interrupts", "    // (void)handleInterrupts(nowMs, line);"),
    ("leave: watchdog not restored", "            (void)configureWatchdog(nowMs); // restores WMC/NWP of the configuration while still in Standby\n        }\n        result = enterNormal();",
     "        }\n        result = enterNormal();"),
    ("leave: sources not removed", "    (void)writeVerified(UJA113X_REG_TRANSCEIVER_IRQ_ENABLE, trxNormal, TRX_IRQ_MASK);\n    for (uint8_t bank = 0U; bank < _hvioBanks; ++bank)",
     "    for (uint8_t bank = 0U; bank < _hvioBanks; ++bank)"),
    ("leave: no transceiver read", "    _lastModeCheckMs       = nowMs;\n    pollTransceiver();\n    return Status::OK;", "    _lastModeCheckMs       = nowMs;\n    return Status::OK;"),
    ("leave: unknown mode accepted", "        result = Status::NOT_READY; // neither Standby nor Normal: not reached", "        result = Status::OK;"),
    ("cyclic in standby runs normal path", "    if (_state == State::STANDBY)\n    {\n        cyclicStandby(nowMs, line);\n        return;\n    }", ""),
]


def build_and_run(source: str, work: Path) -> bool:
    src = work / "Uja113x.cpp"
    src.write_text(source, encoding="utf-8")
    exe = work / "t.exe"
    cmd = [sys.executable, "-m", "ziglang", "c++", "-std=c++17", "-O1", "-w"]
    for i in INC:
        cmd += ["-I", str(REPO / i)]
    cmd += [str(src), str(TEST), "-o", str(exe)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print("   compile error:", r.stderr[-400:])
        return False
    try:
        r = subprocess.run([str(exe)], capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        return False                        # a hang counts as detected
    return r.returncode == 0


class Uja113xIntnMutationTest(unittest.TestCase):
    def test_every_mutant_is_detected(self):
        original = SRC.read_text(encoding="utf-8")
        with tempfile.TemporaryDirectory(prefix="io68mut") as folder:
            work = Path(folder)
            self.assertTrue(build_and_run(original, work), "the unchanged driver must pass its host test")
            survived = []
            for name, old, new in MUTANTS:
                with self.subTest(mutant=name):
                    self.assertEqual(original.count(old), 1, f"{name}: pattern not unique in Uja113x.cpp")
                    passed = build_and_run(original.replace(old, new), work)
                    print(("SURVIVED " if passed else "detected ") + name, flush=True)
                    if passed:
                        survived.append(name)
            self.assertEqual(survived, [], f"mutants the host test does not detect: {survived}")


if __name__ == "__main__":
    unittest.main()
