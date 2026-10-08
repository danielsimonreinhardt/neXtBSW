"""NVM-05: host test of the operating time and reset statistics of the application with its own test profile.

  EcuStatisticsHostTest     host/EcuStatisticsHostTest.cpp (zig c++, -Werror -Wconversion): stats/EcuStatistics.h,
                            stats/EcuStatisticsNvm.h on the NvM core of libs/bsw/nvm with a virtual EEPROM, several
                            power cycles, write budget, console text (stats/EcuStatisticsConsole.h)
  EcuStatisticsMutationTest the same test with deliberate faults in the headers - each must fail

Run: .venv/Scripts/python -m unittest ecu/application/src/systems/test/test_ecu_statistics.py
"""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SYSTEMS = HERE.parent
REPO = SYSTEMS.parents[3]
HOST = HERE / "host"
TEST = HOST / "EcuStatisticsHostTest.cpp"
NVM_INCLUDE = REPO / "libs" / "bsw" / "nvm" / "include"
ECUSTATE_INCLUDE = REPO / "libs" / "bsw" / "ecuState" / "include"
BSP_INCLUDE = REPO / "external" / "openbsw" / "libs" / "bsw" / "bsp" / "include"
ZIG = [sys.executable, "-m", "ziglang"]
CXX_FLAGS = ["-std=c++17", "-O1", "-Wall", "-Wextra", "-Wconversion", "-Wno-sign-conversion", "-Werror"]


def build_and_run(systems: Path, tmp: Path) -> subprocess.CompletedProcess:
    """Builds the test with the headers of \\p systems (the real ecu/application/src/systems or a mutated copy)."""
    exe = tmp / "ecu_statistics.exe"
    includes = [systems, HOST, NVM_INCLUDE, ECUSTATE_INCLUDE]
    built = subprocess.run(ZIG + ["c++", *CXX_FLAGS, *[f"-I{i}" for i in includes], "-isystem", str(BSP_INCLUDE),
                                  str(TEST), "-o", str(exe)],
                           capture_output=True, text=True)
    if built.returncode != 0:
        return built
    return subprocess.run([str(exe)], capture_output=True, text=True, timeout=300)


def last_counts(stdout: str) -> tuple[int, int]:
    words = stdout.strip().splitlines()[-1].replace(",", "").split()
    return int(words[-4]), int(words[-2])


# (file below systems/stats, original text, faulty text, what the fault is)
MUTATIONS = [
    ("EcuStatistics.h", "if ((!known) || ((reasons & wakeup::UNKNOWN) != 0U))",
     "if ((reasons & wakeup::UNKNOWN) != 0U)", "a source that cannot tell counts nothing"),
    ("EcuStatistics.h", "if (counter != 0xFFFFU)", "if (true)", "16-bit counters wrap instead of saturating"),
    ("EcuStatistics.h", "{wakeup::CAN_BUSES, Cause::CAN},", "{wakeup::canBus(0U), Cause::CAN},",
     "only CAN bus 0 counts as CAN"),
    ("EcuStatistics.h", "countReasons(_record.supply, _supplyReasons);", "countReasons(_record.mcu, _supplyReasons);",
     "supply share counted as MCU"),
    ("EcuStatistics.h", "if (_supplyKnown || ((nowMs - _startMs) >= START_SETTLE_MS))", "if (true)",
     "start counted before the supply share: two writes per start"),
    ("EcuStatistics.h", "((nowMs - _imageMs) >= IMAGE_PERIOD_MS) && ", "",
     "operating time handed over every second"),
    ("EcuStatistics.h", "        if (_record.operatingSeconds == _imageSeconds)\n        {\n            return false;",
     "        if (true)\n        {\n            return false;", "run-down does not store the operating time"),
    ("EcuStatistics.h", "uint32_t const total   = _remainderMs + (nowMs - _lastMs);",
     "uint32_t const total   = _remainderMs + ((nowMs > _lastMs) ? (nowMs - _lastMs) : 0U);",
     "operating time stops at the wrap of the millisecond time"),
    ("EcuStatistics.h", "_remainderMs           = total % 1000U;", "_remainderMs           = 0U;",
     "fractions of a second dropped every cycle"),
    ("EcuStatistics.h", "if (stored.format == FORMAT)", "if (stored.format != 0U)",
     "record of another format taken over"),
    ("EcuStatisticsNvm.h", "((b.size == RECORD_BYTES) && (b.image != nullptr)", "((b.image != nullptr)",
     "block of another size used"),
    ("EcuStatisticsNvm.h", "*b.changed = true;", "*b.changed = false;", "hand-over does not mark the block changed"),
    ("EcuStatisticsNvm.h", "if ((b.name != nullptr) && (std::strcmp(b.name, BLOCK_NAME) == 0))",
     "if (b.name != nullptr)", "first block taken whatever its name"),
    ("EcuStatisticsConsole.h", "static_cast<unsigned>(r.mcu[c])", "static_cast<unsigned>(r.supply[c])",
     "console shows the supply row twice"),
    # NVM-34: an MCU-only start (eval board: lc reboot with JP1 open) must not count in the supply row
    ("EcuStatistics.h", "            if (_supplyRestarted)\n", "            if (true)\n",
     "MCU-only start counted in the supply row"),
    ("EcuStatistics.h", "_supplyReasons   = _supplyRestarted ? reasons : 0U;", "_supplyReasons   = reasons;",
     "marker of the MCU-only start taken as reasons"),
    ("StartReasonHandover.h", "    if (!restarted)\n", "    if (false)\n",
     "hand-over ignores that the supply did not restart"),
]


@unittest.skipUnless(BSP_INCLUDE.is_dir(), "external/openbsw is not checked out (git submodule update --init)")
class EcuStatisticsHostTest(unittest.TestCase):
    def test_statistics(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            result = build_and_run(SYSTEMS, tmp)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        print("\n" + result.stdout.strip(), file=sys.stderr)
        checks, failures = last_counts(result.stdout)
        self.assertEqual(failures, 0)
        self.assertGreater(checks, 100)


@unittest.skipUnless(BSP_INCLUDE.is_dir(), "external/openbsw is not checked out (git submodule update --init)")
class EcuStatisticsMutationTest(unittest.TestCase):
    def test_mutations_fail(self):
        caught = 0
        for name, original, faulty, what in MUTATIONS:
            with self.subTest(what):
                tmp = Path(tempfile.mkdtemp())
                try:
                    systems = tmp / "systems"
                    shutil.copytree(SYSTEMS / "stats", systems / "stats")
                    path = systems / "stats" / name
                    text = path.read_text(encoding="utf-8")
                    self.assertEqual(text.count(original), 1, f"mutation anchor not unique in {name}: {original!r}")
                    path.write_text(text.replace(original, faulty), encoding="utf-8")
                    result = build_and_run(systems, tmp)
                finally:
                    shutil.rmtree(tmp, ignore_errors=True)
                self.assertNotEqual(result.returncode, 0, f"fault not detected: {what}")
                caught += 1
        print(f"\n{caught}/{len(MUTATIONS)} deliberate faults detected", file=sys.stderr)


if __name__ == "__main__":
    unittest.main()
