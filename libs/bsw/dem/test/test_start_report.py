"""SYS-113: host test of the start report of the fault memory (libs/bsw/dem/include/dem/StartReport.h) and of the cause
of a safety reset as BSW sources (dem/SafetyResetReport.h), with its own test profile (six safety DTCs in the real
fault memory core, a second profile without the deadman DTC) and the real reset-loop rule of the safe supervisor
(ecu/safety/safeSupervisor/include/safeSupervisor/ResetLoopLimit.h, read only).

  StartReportHostTest      test/host/StartReportHostTest.cpp (zig c++, -Werror -Wconversion)
  StartReportTableTest     the event names and cause codes of SafetyResetReport.h against SafeSupervisor::Event (in
                           lower case, same number) and the standard description tools/diaggen/standard_diag.yaml
                           (bsw_instance of every safety DTC = its cause number)
  StartReportMutation      the same host test against both headers with deliberate faults - every one must fail it
                           (SYS-196: also against dem/OccurrenceReport.h, the run-time occurrences of a BSW source)

Run: .venv/Scripts/python -m unittest libs/bsw/dem/test/test_start_report.py
"""

import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
DEM_INCLUDE = HERE.parent / "include"
SAFETY_INCLUDE = REPO / "ecu" / "safety" / "safeSupervisor" / "include"
START_REPORT = DEM_INCLUDE / "dem" / "StartReport.h"
SAFETY_REPORT = DEM_INCLUDE / "dem" / "SafetyResetReport.h"
OCCURRENCE_REPORT = DEM_INCLUDE / "dem" / "OccurrenceReport.h"   # SYS-196
SUPERVISOR_H = SAFETY_INCLUDE / "safeSupervisor" / "SafeSupervisor.h"
STANDARD_DIAG = REPO / "tools" / "diaggen" / "standard_diag.yaml"
TEST = HERE / "host" / "StartReportHostTest.cpp"
ZIG = [sys.executable, "-m", "ziglang"]
CXX_FLAGS = ["-std=c++17", "-O1", "-Wall", "-Wextra", "-Wconversion", "-Wno-sign-conversion", "-Werror"]

# events of the supervisor that are no cause of a safety reset report (see the header of SafetyResetReport.h): they
# hold the safe state or do not reset, or their name belongs to another monitor (DEVICE_FAULT)
NOT_REPORTED = {"WATCHDOG_STARTUP_CHECK_FAILURE", "ROM_CHECK_FAILURE", "CHECK_REGISTER_ERROR", "RTE_TIMING_VIOLATION",
                "DEVICE_REPORT", "DEVICE_FAULT", "RESET_LOOP_LIMIT"}
# sources that are no event of the supervisor: name -> low byte of the DTC number (bsw_instance)
FIXED = {"safety.reset_loop": 0xFF, "safety.mcu_watchdog": 0xFD, "safety.fault_exception": 0xFC,
         "safety.reset_other": 0xFB}


def build_and_run(dem_include: Path, tmp: Path) -> subprocess.CompletedProcess:
    exe = tmp / "start_report_test.exe"
    built = subprocess.run(ZIG + ["c++", *CXX_FLAGS, f"-I{dem_include}", f"-I{SAFETY_INCLUDE}", str(TEST),
                                  "-o", str(exe)], capture_output=True, text=True)
    if built.returncode != 0:
        return built
    return subprocess.run([str(exe)], capture_output=True, text=True, timeout=60)


def supervisor_events() -> list[str]:
    """The enumerators of SafeSupervisor::Event in their order (comments removed)."""
    text = SUPERVISOR_H.read_text(encoding="utf-8")
    body = text.split("enum class Event : uint8_t", 1)[1].split("};", 1)[0]
    body = re.sub(r"/\*.*?\*/", "", body, flags=re.S)
    body = re.sub(r"//[^\n]*", "", body)
    return [name.strip() for name in body.strip().strip("{").split(",") if name.strip()]


def report_table() -> list[tuple[str, str]]:
    """(event name, cause expression) of SafetyResetReport.h in the order of EVENTS / CAUSES."""
    text = SAFETY_REPORT.read_text(encoding="utf-8")
    events = re.findall(r'^\s+"(safety\.[a-z_]+)",$', text.split("EVENTS[] = {", 1)[1].split("};", 1)[0], re.M)
    causes = re.findall(r"^\s+(CAUSE_[A-Z_]+(?: \| 0x[0-9A-F]+U)?),", text.split("CAUSES[] = {", 1)[1].split("};", 1)[0],
                        re.M)
    return list(zip(events, causes, strict=True))


class StartReportHostTest(unittest.TestCase):
    def test_host(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            result = build_and_run(DEM_INCLUDE, tmp)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(" 0 failures", result.stdout)
        checks = int(result.stdout.strip().splitlines()[-1].split()[0])
        self.assertGreaterEqual(checks, 120)


class StartReportTableTest(unittest.TestCase):
    def test_every_resetting_event_has_its_name_and_number(self):
        events = supervisor_events()
        self.assertEqual(len(events), 24)               # a new event must be decided here (report or not)
        table = report_table()
        by_cause = {cause: name for name, cause in table}
        for number, event in enumerate(events):
            with self.subTest(event=event):
                cause = f"CAUSE_EVENT | 0x{number:02X}U"
                if event in NOT_REPORTED:
                    self.assertNotIn(cause, by_cause)
                    self.assertNotIn(f"safety.{event.lower()}", [n for n, _c in table])
                else:
                    self.assertEqual(by_cause.get(cause), f"safety.{event.lower()}")
        self.assertEqual(len(table), len(events) - len(NOT_REPORTED) + len(FIXED))
        # the fixed sub-types stay clear of the event numbers
        self.assertTrue(all(low >= len(events) for low in FIXED.values()))

    def test_fixed_numbers_of_the_header(self):
        text = SAFETY_REPORT.read_text(encoding="utf-8")
        for name, low in FIXED.items():
            with self.subTest(name=name):
                self.assertIn(f"{name}", text)
                constant = "INSTANCE_" + name.split(".", 1)[1].upper()
                self.assertRegex(text, rf"{constant}\s*=\s*0x{low:02X}U;")

    def test_standard_description_uses_the_numbers(self):
        # tools/diaggen/standard_diag.yaml: every safety DTC of the template carries the cause number of its source
        from ruamel.yaml import YAML
        desc = YAML(typ="safe").load(STANDARD_DIAG.read_text(encoding="utf-8"))
        events = supervisor_events()
        names = {name for name, _cause in report_table()}
        safety = [d for d in desc["dtcs"] if d["event"].get("bsw", "").startswith("safety.")]
        self.assertTrue(safety)
        for dtc in safety:
            event = dtc["event"]["bsw"]
            with self.subTest(dtc=dtc["name"]):
                self.assertIn(event, names)
                if event in FIXED:
                    self.assertNotIn("bsw_instance", dtc)      # fixed sub-type of the numbering plan (bswdtc.PLAN)
                else:
                    self.assertEqual(dtc["bsw_instance"], events.index(event.split(".", 1)[1].upper()))


class StartReportMutation(unittest.TestCase):
    # (what, file, original, replacement)
    MUTATIONS = [
        ("PASSED before the fault memory took the failure", START_REPORT,
         "if (((_reported & bit(i)) != 0U) && !failureTaken(*memory, dtc))",
         "if (((_reported & bit(i)) != 0U) && false)"),
        ("a status of the cycle before taken for the hand-over", START_REPORT,
         "(info.status & status::TEST_FAILED_THIS_OPERATION_CYCLE) != 0U",
         "(info.status & status::TEST_FAILED_SINCE_LAST_CLEAR) != 0U"),
        ("FAILED forever (never PASSED, a clear does not hold)", START_REPORT,
         "if (((_reported & bit(i)) != 0U) && !failureTaken(*memory, dtc))",
         "if ((_reported & bit(i)) != 0U)"),
        ("a source that did not fail is not reported", START_REPORT,
         "                memory->report(dtc, Result::PASSED);\n                _pending &= ~bit(i);",
         "                _pending &= ~bit(i);"),
        ("a new fault memory is not served", START_REPORT, "if (memory != _memory)", "if (_memory == nullptr)"),
        ("fallback not applied", START_REPORT, "if ((_fallback < _count) && ((_failed & _covers & ~_found) != 0U))",
         "if (false)"),
        ("fallback also when the cause has its DTC", START_REPORT, "((_failed & _covers & ~_found) != 0U)",
         "((_failed & _covers) != 0U)"),
        ("an old cause reported after a start without safety reset", SAFETY_REPORT,
         "    if (!counted)\n    {\n        return 0U;", "    if (false)\n    {\n        return 0U;"),
        ("reset loop from the first safety reset on", SAFETY_REPORT,
         "(count >= RESET_LOOP_MIN_COUNT)", "(count >= 1U)"),
        ("reset loop only above the minimum", SAFETY_REPORT,
         "(count >= RESET_LOOP_MIN_COUNT)", "(count > RESET_LOOP_MIN_COUNT)"),
        ("unknown cause lost", SAFETY_REPORT, "return known ? failed : (failed | StartReport::bit(RESET_OTHER));",
         "return failed;"),
        ("watchdog and fault exception swapped", SAFETY_REPORT,
         "    CAUSE_WATCHDOG,           // mcu_watchdog\n    CAUSE_FAULT_EXCEPTION,    // fault_exception",
         "    CAUSE_FAULT_EXCEPTION,    // mcu_watchdog\n    CAUSE_WATCHDOG,           // fault_exception"),
        ("fallback without covers", SAFETY_REPORT, "_report.setFallback(RESET_OTHER, otherCovers());",
         "_report.setFallback(RESET_OTHER, 0U);"),
        # SYS-196: the occurrence report
        ("occurrence: PASSED before the fault memory took the failure", OCCURRENCE_REPORT,
         "        if (failureTaken(*memory, _dtc))", "        if (true)"),
        ("occurrence: FAILED forever (never PASSED, a clear does not hold)", OCCURRENCE_REPORT,
         "        if (failureTaken(*memory, _dtc))", "        if (false)"),
        ("occurrence: a status of the cycle before taken for the hand-over", OCCURRENCE_REPORT,
         "status::TEST_FAILED | status::TEST_FAILED_THIS_OPERATION_CYCLE", "status::TEST_FAILED"),
        ("occurrence: reported without an occurrence (no counter-check)", OCCURRENCE_REPORT,
         "            if (_occurred == _covered)\n            {\n                return;",
         "            if (false)\n            {\n                return;"),
        ("occurrence: occurrences during a hand-over lost", OCCURRENCE_REPORT,
         "            _covered     = _inFlight;", "            _covered     = _occurred;"),
        ("occurrence: never counted", OCCURRENCE_REPORT, "void occurred() { ++_occurred; }", "void occurred() {}"),
        ("occurrence: a new fault memory is not served", OCCURRENCE_REPORT,
         "        if (memory != _memory)", "        if (_memory == nullptr)"),
    ]

    def test_every_fault_is_found(self):
        for what, file, original, replacement in self.MUTATIONS:
            with self.subTest(fault=what):
                tmp = Path(tempfile.mkdtemp())
                try:
                    dem_inc = tmp / "dem_include"
                    shutil.copytree(DEM_INCLUDE, dem_inc)
                    target = dem_inc / file.parent.name / file.name
                    text = target.read_text(encoding="utf-8")
                    self.assertIn(original, text, "mutation does not apply any more")
                    target.write_text(text.replace(original, replacement, 1), encoding="utf-8")
                    result = build_and_run(dem_inc, tmp)
                finally:
                    shutil.rmtree(tmp, ignore_errors=True)
                self.assertNotEqual(result.returncode, 0, f"fault not found: {what}\n{result.stdout}")


if __name__ == "__main__":
    unittest.main()
