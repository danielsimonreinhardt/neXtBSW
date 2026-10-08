"""Memory report per build profile as a test (PLAT-29).

The measuring link of PLAT-12 (tools/test/memreport.py): per profile the application is built, linked once more
with `--noinhibit-exec --print-memory-usage` next to the real image, and the usage of every linker region is held
against its size (the budget of the profile, from derivative + project) and against the reserve of
tools/test/memory_budget.yaml. The biggest items per library, object file and symbol go into the report; the test is
red as soon as a region overflows, also if the real link fails for that reason (then the report is the result).

Three layers:
  * parsers and budget logic on synthetic text - no toolchain, always run (MemoryReportParsersTest),
  * the table and the presets agree (MemoryBudgetTableTest),
  * the real profiles (ProfileMemoryReportTest): needs cmake, ninja, arm-none-eabi-gcc (PATH or NX_ARM_GCC_BIN)
    and external/openbsw; builds below NX_MEMORY_BUILD_ROOT (set by tools/test to {build}, else the build root of
    tools/test) in the same sub-directories as the target-* entries, so that an earlier target build is reused.
    NX_MEMORY_PRESETS=<preset>,<preset> limits the run, NX_MEMORY_REPORT_JSON=<file> writes the numbers as JSON
    (trend of the biggest items between two runs).

Run: .venv/Scripts/python -m pytest tools/test/test_memory_report.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
import build_dirs  # noqa: E402
import memreport  # noqa: E402

TABLE = HERE / "memory_budget.yaml"
CMAKE = shutil.which("cmake")
NINJA = shutil.which("ninja")
GCC_PATH = build_dirs.compiler_search_path()
ARM_GCC = shutil.which("arm-none-eabi-gcc", path=GCC_PATH)
OPENBSW = REPO / "external" / "openbsw" / "platforms" / "s32k1xx" / "CMakeLists.txt"


def profiles() -> list[dict]:
    data = yaml.safe_load(TABLE.read_text(encoding="utf-8"))["profiles"]
    wanted = [p for p in os.environ.get("NX_MEMORY_PRESETS", "").split(",") if p]
    return [p for p in data if not wanted or p["preset"] in wanted]


# ---------------------------------------------------------------------------- synthetic linker output

MAP = """\
Discarded input sections

 .text.unused   0x0000000000000000       0x40 old.obj

Memory Configuration

Name             Origin             Length             Attributes
Isr              0x0000000000002800 0x0000000000000400 xr
Application      0x0000000000002c30 0x000000000001c3d0 xr
MainRAM          0x0000000020002300 0x0000000000001300 rw
NoInit           0x0000000020003600 0x0000000000000200 rw
*default*        0x0000000000000000 0xffffffffffffffff

Linker script and memory map

LOAD ecu/application/CMakeFiles/app.ecu.dir/RelWithDebInfo/src/app/app.cpp.obj
LOAD libs/libutil.a

.isr_vector     0x0000000000002800      0x0c0
 *(.isr_vector)
 .isr_vector    0x0000000000002800      0x0c0 platforms/startup/startUp.S.obj

.text           0x0000000000002c30    0x10000
 *(.text*)
 .text.main     0x0000000000002c30      0x100 ecu/application/CMakeFiles/app.ecu.dir/RelWithDebInfo/src/app/app.cpp.obj
 .text.a_very_long_function_name_of_the_util_library
                0x0000000000002d30      0x300 libs/libutil.a(Format.cpp.obj)
 .text.zero     0x0000000000003030        0x0 libs/libutil.a(Empty.cpp.obj)

.data           0x0000000020002300       0x20 load address 0x0000000000012c30
 .data.counter  0x0000000020002300       0x20 ecu/application/CMakeFiles/app.ecu.dir/RelWithDebInfo/src/app/app.cpp.obj

.bss            0x0000000020002320      0x400
 .bss.stack     0x0000000020002320      0x400 ecu/application/CMakeFiles/app.ecu.dir/RelWithDebInfo/src/app/app.cpp.obj
 .bss.buffer    0x0000000020002720       0x80 libs/libutil.a(Format.cpp.obj)

.noinit         0x0000000020003600      0x150
 .noinit.resetLoop
                0x0000000020003744       0x10 platforms/main.cpp.obj

.debug_info     0x0000000000000000     0x9000
 .debug_info    0x0000000000000000     0x9000 ecu/application/CMakeFiles/app.ecu.dir/RelWithDebInfo/src/app/app.cpp.obj
"""

READELF = """\
Section Headers:
  [Nr] Name              Type            Addr     Off    Size   ES Flg Lk Inf Al
  [ 0]                   NULL            00000000 000000 000000 00      0   0  0
  [ 1] .isr_vector       PROGBITS        00002800 010800 0000c0 00   A  0   0  4
  [ 2] .text             PROGBITS        00002c30 010c30 010000 00  AX  0   0  4
  [ 3] .data             PROGBITS        20002300 022300 000020 00  WA  0   0  4
  [ 4] .bss              NOBITS          20002320 022320 000400 00  WA  0   0  4
  [ 5] .noinit           NOBITS          20003600 023600 000150 00  WA  0   0  4
  [ 6] .debug_info       PROGBITS        00000000 024000 009000 00      0   0  1
"""

USAGE = """\
Memory region         Used Size  Region Size  %age Used
             Isr:         192 B         1 KB     18.75%
     Application:       67992 B     112960 B     60.19%
         MainRAM:        4544 B       4864 B     93.42%
          NoInit:         512 B         512 B    100.00%
"""

NM = """\
00002c30 00000100 T main
00002d30 00000300 t Format(char const*, int)
20002300 00000020 d counter
20002320 00000400 b stack
20002720 00000080 B buffer
00002d00 00000000 T zero_size
"""


class MemoryReportParsersTest(unittest.TestCase):
    """Text in, numbers out - the part of the memory report that needs no toolchain."""

    def regions(self):
        return memreport.with_usage(memreport.parse_map_regions(MAP), memreport.parse_memory_usage(USAGE))

    def test_regions_from_the_memory_configuration(self):
        regions = memreport.parse_map_regions(MAP)
        self.assertEqual([(r.name, r.origin, r.size, r.kind) for r in regions], [
            ("Isr", 0x2800, 0x400, "flash"), ("Application", 0x2C30, 0x1C3D0, "flash"),
            ("MainRAM", 0x20002300, 0x1300, "ram"), ("NoInit", 0x20003600, 0x200, "ram")])

    def test_memory_usage_units_are_exact(self):
        self.assertEqual(memreport.parse_memory_usage(USAGE),
                         {"Isr": 192, "Application": 67992, "MainRAM": 4544, "NoInit": 512})
        self.assertEqual(memreport.parse_memory_usage("   X:   2 KB   4 KB  50.00%\n   Y: 1 MB 2 MB 50.00%"),
                         {"X": 2048, "Y": 1048576})

    def test_within_budget_is_green(self):
        self.assertEqual(memreport.check_budget(self.regions()), [])
        self.assertEqual(memreport.check_budget(self.regions(), {"Application": 4096, "MainRAM": 128}), [])

    def test_overflow_is_red_and_names_region_and_bytes(self):
        over = memreport.with_usage(memreport.parse_map_regions(MAP), {"Isr": 192, "Application": 118000,
                                                                       "MainRAM": 4544, "NoInit": 512})
        findings = memreport.check_budget(over)
        self.assertEqual(len(findings), 1)
        self.assertIn("Application (flash): 118000 of 115664 bytes used, 2336 bytes too much", findings[0])

    def test_reserve_makes_it_red_before_the_overflow(self):
        findings = memreport.check_budget(self.regions(), {"MainRAM": 512, "Isr": 100})
        self.assertEqual(len(findings), 1)
        self.assertIn("MainRAM (ram): 320 bytes free, at least 512 must stay free", findings[0])

    def test_no_usage_line_is_a_finding(self):
        """A region the linker did not report must not pass as 'fits'."""
        regions = memreport.with_usage(memreport.parse_map_regions(MAP), {"Isr": 192})
        self.assertEqual(sum("no usage reported" in f for f in memreport.check_budget(regions)), 3)

    def test_reserve_of_an_unknown_region_is_a_finding(self):
        findings = memreport.check_budget(self.regions(), {"Nope": 1})
        self.assertEqual(findings, ["Nope: reserve in memory_budget.yaml names no linker region"])

    def test_sections_flash_ram_and_both(self):
        kinds = memreport.parse_sections(READELF)
        self.assertEqual(kinds[".isr_vector"], memreport.Kind(flash=True, ram=False))
        self.assertEqual(kinds[".text"], memreport.Kind(flash=True, ram=False))
        self.assertEqual(kinds[".data"], memreport.Kind(flash=True, ram=True))        # image + RAM
        self.assertEqual(kinds[".bss"], memreport.Kind(flash=False, ram=True))
        self.assertEqual(kinds[".noinit"], memreport.Kind(flash=False, ram=True))
        self.assertNotIn(".debug_info", kinds)                                          # not allocated

    def test_inputs_by_library_and_object(self):
        totals = memreport.parse_map_inputs(MAP, memreport.parse_sections(READELF))
        self.assertEqual(totals, {
            "startUp.S": [0xC0, 0],                         # .isr_vector, flash
            "app.cpp": [0x100 + 0x20, 0x20 + 0x400],        # code, data (both), bss
            "libutil.a": [0x300, 0x80],                     # wrapped long name, zero size and debug left out
            "main.cpp": [0, 0x10],                          # wrapped name in .noinit
        })
        self.assertEqual(memreport.biggest(totals, 1)[:2], [("app.cpp", 0x420), ("libutil.a", 0x80)])
        self.assertEqual(memreport.biggest(totals, 0)[0], ("libutil.a", 0x300))

    def test_symbols(self):
        symbols = memreport.parse_symbols(NM)
        self.assertEqual(memreport.biggest_symbols(symbols, ram=False),
                         [("Format(char const*, int)", 0x300), ("main", 0x100), ("counter", 0x20)])
        self.assertEqual(memreport.biggest_symbols(symbols, ram=True),
                         [("stack", 0x400), ("buffer", 0x80), ("counter", 0x20)])

    def test_report_text_marks_the_overflow(self):
        over = memreport.with_usage(memreport.parse_map_regions(MAP), {"Isr": 192, "Application": 118000,
                                                                       "MainRAM": 4544, "NoInit": 512})
        report = memreport.Report("demo", over, memreport.parse_map_inputs(MAP, memreport.parse_sections(READELF)),
                                  memreport.parse_symbols(NM), linked=False)
        text = report.text()
        self.assertIn("linkt NICHT", text)
        self.assertIn("-2336 (UEBER)", text)
        self.assertIn("libutil.a", text)
        self.assertEqual(report.as_dict()["regions"][1]["free"], -2336)
        json.dumps(report.as_dict())


class MemoryBudgetTableTest(unittest.TestCase):
    def test_every_entry_names_a_preset_and_the_reference_profiles_are_in(self):
        presets = {p["name"] for p in json.loads((REPO / "CMakePresets.json").read_text(encoding="utf-8"))[
            "configurePresets"]}
        table = [p["preset"] for p in yaml.safe_load(TABLE.read_text(encoding="utf-8"))["profiles"]]
        self.assertEqual(len(table), len(set(table)), "preset twice in memory_budget.yaml")
        # PLAT-114: the exported open tree has neither the eval board projects nor their presets (PLAT-62) - a preset
        # may only be missing together with its project folder; TOOL-243c: the same for the S32K116EVB profile
        # (private until its levels are measured, PLAT-13)
        missing = {p for p in set(table) - presets if (REPO / "projects" / p.replace("-", "_")).is_dir()
                   or not p.startswith(("evalboard-", "s32k116evb-"))}
        self.assertEqual(missing, set())
        # the smallest profile (S32K116) and the eval board are what the report is for
        self.assertGreaterEqual(set(table), {"s32k116evb-minimal", "evalboard-s32k144-demo",
                                             "buildtest-s32k148-min"})
        dirs = [p["build"] for p in yaml.safe_load(TABLE.read_text(encoding="utf-8"))["profiles"]]
        self.assertEqual(len(dirs), len(set(dirs)), "build directory twice in memory_budget.yaml")


# ---------------------------------------------------------------------------- the real profiles


@unittest.skipIf(None in (CMAKE, NINJA, ARM_GCC) or not OPENBSW.is_file(),
                 "cmake, ninja, arm-none-eabi-gcc (NX_ARM_GCC_BIN) or external/openbsw missing")
class ProfileMemoryReportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = os.environ.get("NX_MEMORY_BUILD_ROOT")
        cls.root = Path(root) if root else build_dirs.default_build_root(REPO)
        cls.env = {**os.environ, "PATH": GCC_PATH, "PYTHONUTF8": "1"}
        cls.env.setdefault("CC", "arm-none-eabi-gcc")
        cls.env.setdefault("CXX", "arm-none-eabi-g++")
        cls.entries: dict[str, dict] = {}
        for profile in profiles():
            cls.entries[profile["preset"]] = cls.measure(profile)
        cls.print_report()

    @classmethod
    def build(cls, preset: str, directory: Path) -> subprocess.CompletedProcess | None:
        """Configure (if the directory is new) and build the application of the profile; returns the failed step
        or None. A failing link is expected for a profile that overflows - the measuring link follows anyway."""
        if not (directory / "CMakeCache.txt").is_file() or build_dirs.stale_compiler(directory, GCC_PATH):
            fresh, _ = build_dirs.fresh_args(directory, GCC_PATH)
            done = subprocess.run([CMAKE, "--preset", preset, *fresh, "-B", str(directory),
                                   f"-DPython3_EXECUTABLE={sys.executable}"], cwd=REPO, capture_output=True,
                                  text=True, env=cls.env)
            if done.returncode != 0:
                return done
        done = subprocess.run([CMAKE, "--build", str(directory), "--config", "RelWithDebInfo", "--target", "app.ecu"],
                              capture_output=True, text=True, env=cls.env)
        return done if done.returncode != 0 else None

    @classmethod
    def measure(cls, profile: dict) -> dict:
        directory = cls.root / profile["build"]
        failed = cls.build(profile["preset"], directory)
        entry = {"profile": profile, "dir": directory, "build_failure": failed, "report": None, "error": None}
        if failed is not None and not (directory / "build-RelWithDebInfo.ninja").is_file():
            entry["error"] = "configure failed:\n" + (failed.stdout + failed.stderr)[-3000:]
            return entry
        try:
            entry["report"] = memreport.measure(profile["preset"], directory, directory / "memreport",
                                                gcc_path=GCC_PATH, linked=failed is None)
        except memreport.MeasureError as error:
            entry["error"] = str(error)
        return entry

    @classmethod
    def print_report(cls):
        reports = [e["report"] for e in cls.entries.values() if e["report"] is not None]
        print("\n" + "\n\n".join(r.text() for r in reports), file=sys.stderr)
        target = os.environ.get("NX_MEMORY_REPORT_JSON")
        if target:
            Path(target).write_text(json.dumps([r.as_dict() for r in reports], indent=2), encoding="utf-8")

    def test_every_profile_is_measured(self):
        self.assertTrue(self.entries, "no profile selected")
        for preset, entry in self.entries.items():
            with self.subTest(preset=preset):
                self.assertIsNone(entry["error"], entry["error"])
                self.assertIsNotNone(entry["report"])

    def test_budget_per_profile(self):
        """Red as soon as a region overflows or falls below its reserve; the report above has the numbers."""
        for preset, entry in self.entries.items():
            report = entry["report"]
            if report is None:
                continue
            with self.subTest(preset=preset):
                findings = memreport.check_budget(report.regions, entry["profile"].get("min_free"))
                self.assertEqual(findings, [], "\n" + report.text())

    def test_failed_build_is_not_hidden_by_the_measuring_link(self):
        """A failing build is accepted only because a region overflows (budget test names it); any other failure
        (compiler, generator, a link error that is no overflow) is red here."""
        for preset, entry in self.entries.items():
            failed, report = entry["build_failure"], entry["report"]
            if failed is None or report is None:
                continue
            with self.subTest(preset=preset):
                overflow = memreport.check_budget(report.regions)
                self.assertTrue(overflow, "build failed without a region overflow:\n"
                                + (failed.stdout + failed.stderr)[-3000:])

    def test_the_biggest_items_are_reported(self):
        for preset, entry in self.entries.items():
            report = entry["report"]
            if report is None:
                continue
            with self.subTest(preset=preset):
                text = report.text()
                for title in ("Flash nach Bibliothek/Objekt", "RAM nach Bibliothek/Objekt", "Flash nach Symbol",
                              "RAM nach Symbol"):
                    self.assertIn(title, text)
                self.assertEqual(len(memreport.biggest(report.inputs, 0)), memreport.TOP)
                self.assertTrue(any(r.kind == "flash" and r.used for r in report.regions))
                self.assertTrue(any(r.kind == "ram" and r.used for r in report.regions))

    def test_noinit_region_agrees_with_nxconfig(self):
        """PLAT-29: the linker places the hard fault dump and the records of the modules in NoInit and wants
        8 + 0x10 bytes for failed-start record and boot request above them; the minimum nxconfig demands is the
        smallest size the linker accepts, and what is used leaves that room."""
        sys.path.insert(0, str(REPO / "tools"))
        import nxconfig
        from nxconfig import memory
        for preset, entry in self.entries.items():
            report = entry["report"]
            if report is None:
                continue
            with self.subTest(preset=preset):
                presets = json.loads((REPO / "CMakePresets.json").read_text(encoding="utf-8"))["configurePresets"]
                variables = next(p for p in presets if p["name"] == preset)["cacheVariables"]
                project = Path(variables["NX_PROJECT"].replace("${sourceDir}", str(REPO)))
                config, messages = nxconfig.check(project)
                self.assertEqual([str(m) for m in messages if m.severity == "error"], [])
                minimum, _ = memory.noinit_minimum(config)
                region = next(r for r in report.regions if r.name == "NoInit")
                self.assertGreaterEqual(region.size, minimum)
                self.assertEqual(region.size, config.memory.noinit_size)
                self.assertLessEqual(region.used + memory.FAILED_START_RECORD_SIZE + memory.BOOT_REQUEST_FROM_END,
                                     region.size, "linker: .noinit overlaps the boot request or failed-start record")
                self.assertGreaterEqual(region.used + 8 + 0x10, minimum, "less used than the dump alone: layout "
                                        "header and linker script disagree")

    def test_overflow_turns_up_in_the_measuring_link(self):
        """Negative control, end to end: the smallest profile linked with a RAM region made too small gives a
        complete report and the budget check is red - the proof that 'green' means something."""
        entry = next((e for e in self.entries.values() if e["report"] is not None
                      and any(r.name == "MainRAM" for r in e["report"].regions)), None)
        if entry is None:
            self.skipTest("no measured profile with a MainRAM region")
        small = memreport.measure(entry["profile"]["preset"], entry["dir"], entry["dir"] / "memreport-small",
                                  gcc_path=GCC_PATH, linked=False, shrink=("MainRAM", 0x100))
        findings = memreport.check_budget(small.regions)
        self.assertTrue(any(f.startswith("MainRAM (ram):") and "too much" in f for f in findings), findings)
        self.assertEqual([r.name for r in small.regions], [r.name for r in entry["report"].regions])
        self.assertIn("UEBER", small.text())
        self.assertTrue(small.inputs and small.symbols)


if __name__ == "__main__":
    unittest.main()
