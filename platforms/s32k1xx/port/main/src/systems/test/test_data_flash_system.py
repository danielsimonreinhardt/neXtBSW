# SPDX-License-Identifier: Apache-2.0
"""NVM-23: data flash areas in the family port - host test, its meaningfulness and source guards.

  HostTest      test/host/DataFlashSystemHostTest.cpp: include/systems/DataFlashRunDown.h (gate, run-down step,
                advance) with the real dataflash::AreaService over a simulated data flash of its own profile
  MutationTest  deliberately broken variants of DataFlashRunDown.h must make the host test fail
  GuardTest     source checks of the integration: reads through read() or in place only under a hold (safety review
                NVM12-S1 b, NVM-25), new jobs only with the admission from the published ECU state (NVM23-S1 b,
                NVM-26), the run-down
                step wraps ShutdownStep::NVM and the gate is unlocked in run() and locked in shutdown() (NVM12-S2),
                the FlexNVM region of the code cache is made non-cacheable before the cache is switched on (NVM12-S4),
                the write hooks are the ROM check's, the cache hook only with NX_FEATURE_LMEM_CACHE, the console
                command only in test builds (NX_DFLASH_COMMAND, default OFF), the demo project has areas

No board. Run: .venv/Scripts/python -m unittest platforms/s32k1xx/port/main/src/systems/test/test_data_flash_system.py
"""

import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
PORT = HERE.parents[2]  # platforms/s32k1xx/port/main
ROOT = HERE.parents[6]
TEST = HERE / "host" / "DataFlashSystemHostTest.cpp"
RUN_DOWN_H = "systems/DataFlashRunDown.h"
STRICT = ["-std=c++17", "-O1", "-Wall", "-Wextra", "-Wconversion", "-Wno-sign-conversion", "-Werror"]
RUN_LIMIT_S = 30


def build_and_run(include: Path, work: Path) -> tuple[bool, str]:
    exe = work / "dflashsys.exe"
    r = subprocess.run(
        [sys.executable, "-m", "ziglang", "c++", *STRICT, f"-I{include}",
         f"-I{ROOT / 'libs' / 'bsw' / 'nvmDataFlash' / 'include'}", f"-I{ROOT / 'libs' / 'bsw' / 'ecuState' / 'include'}",
         str(TEST), "-o", str(exe)], cwd=ROOT, capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        return False, "build: " + r.stdout + r.stderr
    try:
        r = subprocess.run([str(exe)], capture_output=True, text=True, timeout=RUN_LIMIT_S)
    except subprocess.TimeoutExpired:
        return False, "hangs"
    return r.returncode == 0, r.stdout + r.stderr


class HostTest(unittest.TestCase):
    def test_run_down(self):
        with tempfile.TemporaryDirectory() as tmp:
            ok, text = build_and_run(PORT / "include", Path(tmp))
            self.assertTrue(ok, text)
            self.assertIn("0 failures", text)


# (name, original text, mutated text) in include/systems/DataFlashRunDown.h
MUTANTS = [
    ("run-down step does not lock", "        _gate.lock(); // first", "        // _gate.lock(); // first"),
    ("step ignores the command in flight", "        if (_gate.commandInFlight())\n        {\n            ++_waits;",
     "        if (false)\n        {\n            ++_waits;"),
    ("gate launches while locked (erase)", "        if (_locked)\n        {\n            ++_refused;\n"
     "            return ::dataflash::Status::NOT_AVAILABLE;\n        }\n        return launched(_flash.startErase",
     "        if (false)\n        {\n            ++_refused;\n"
     "            return ::dataflash::Status::NOT_AVAILABLE;\n        }\n        return launched(_flash.startErase"),
    ("gate launches while locked (program)", "        if (_locked)\n        {\n            ++_refused;\n"
     "            return ::dataflash::Status::NOT_AVAILABLE;\n        }\n        return launched(_flash.startProgram",
     "        if (false)\n        {\n            ++_refused;\n"
     "            return ::dataflash::Status::NOT_AVAILABLE;\n        }\n        return launched(_flash.startProgram"),
    ("in-flight flag not set at a launch", "            _inFlight = true;", "            _inFlight = false;"),
    ("in-flight flag kept after TIMEOUT", "        if (s != ::dataflash::Status::BUSY)\n        {\n            _inFlight = false;",
     "        if ((s != ::dataflash::Status::BUSY) && (s != ::dataflash::Status::TIMEOUT))\n        {\n"
     "            _inFlight = false;"),
    ("failed NvM step ends at once", "        if (!_innerDone)\n        {\n            return Result::RUNNING;\n        }\n"
     "        if (_gate.commandInFlight())",
     "        if (!_innerDone)\n        {\n            return Result::RUNNING;\n        }\n"
     "        if (_innerResult == Result::FAILED)\n        {\n            return _innerResult;\n        }\n"
     "        if (_gate.commandInFlight())"),
    ("NvM step not waited for", "            _innerDone = false;", "            _innerDone = true;"),
    ("advance one step per cycle", "        if ((!areas.busy()) || gate.commandInFlight())", "        if (true)"),
    ("failure reason not given", "? \"NvM step failed\" : \"-\"", "? \"-\" : \"-\""),
    # NVM-26: no new job from STANDBY on
    ("jobs admitted in every state", "return !::ecustate::isOnWayToPowerDown(state);",
     "return (static_cast<void>(state), true);"),
    ("jobs admitted in STANDBY", "return !::ecustate::isOnWayToPowerDown(state);",
     "return state != ::ecustate::State::RUNDOWN;"),
    # NVM-25: hold for a read in place through the gate
    ("gate grants holds in the run-down", "{ return !_locked && _flash.holdForDirectRead(); }",
     "{ return _flash.holdForDirectRead(); }"),
    ("gate grants holds without the driver", "{ return !_locked && _flash.holdForDirectRead(); }",
     "{ return !_locked; }"),
    ("gate does not release", "void releaseDirectRead() override { _flash.releaseDirectRead(); }",
     "void releaseDirectRead() override {}"),
]


class MutationTest(unittest.TestCase):
    def test_mutants_are_detected(self):
        original = (PORT / "include" / RUN_DOWN_H).read_text(encoding="utf-8")
        survivors = []
        with tempfile.TemporaryDirectory() as tmp:
            for i, (name, old, new) in enumerate(MUTANTS):
                self.assertEqual(original.count(old), 1, f"mutant '{name}': text not found once")
                include = Path(tmp) / f"m{i}"
                (include / "systems").mkdir(parents=True)
                (include / RUN_DOWN_H).write_text(original.replace(old, new), encoding="utf-8")
                ok, _ = build_and_run(include, Path(tmp))
                if ok:
                    survivors.append(name)
                shutil.rmtree(include)
        self.assertEqual(survivors, [], f"{len(MUTANTS) - len(survivors)} of {len(MUTANTS)} detected")


def code(path: Path) -> str:
    """Source without // and /* */ comments."""
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"//[^\n]*", "", text)


class GuardTest(unittest.TestCase):
    def setUp(self):
        self.system = code(PORT / "src" / "systems" / "DataFlashSystem.cpp")
        self.integration = code(PORT / "src" / "integration" / "Integration.cpp")

    def test_reads_only_through_read(self):
        # NVM12-S1: no address of the data flash is handed out or dereferenced in the port - the generated addresses
        # (NX_DFLASH_AREA_<NAME>_ADDRESS) are not used, NX_DFLASH_START only for the region of the driver and the
        # cache hook, and nothing casts it to a pointer. NVM-25 (NVM12-S1 b): the one read in place (test command
        # "dflash hold") takes its address from areaService.areaAddress() of a held scope
        for path in sorted((PORT / "src").rglob("*.cpp")) + sorted((PORT / "include").rglob("*.h")):
            if "test" in path.parts:
                continue
            text = code(path)
            self.assertNotRegex(text, r"NX_DFLASH_AREA_\w+_ADDRESS", path)
            self.assertNotRegex(text, r"reinterpret_cast<[^>]*\*[^>]*>\s*\(\s*NX_DFLASH", path)
            self.assertNotRegex(text, r"\(\s*\w+\s*(const\s*)?(volatile\s*)?\*\s*\)\s*NX_DFLASH", path)
        self.assertEqual(len(re.findall(r"NX_DFLASH_START", self.system)), 3)  # cache hook, region, show line
        self.assertIn("areaService.read(", self.system)
        casts = re.findall(r"reinterpret_cast<[^>]*>\(\s*(\w+)\s*\)", self.system)
        self.assertEqual(casts, ["address"])
        self.assertIn("uintptr_t const address    = areaService.areaAddress(scope, index, 0U, a.size);", self.system)
        hold = re.search(r"#ifdef NX_DFLASH_COMMAND\s*enum Id", self.system)
        self.assertIsNotNone(hold)
        self.assertLess(hold.start(), self.system.index("uint32_t inPlaceSum("), "read in place only in test builds")

    def test_jobs_admitted_from_the_published_state(self):
        # NVM-26 (NVM23-S1 b): the area service asks the admission at every job; the admission is the published state
        self.assertRegex(self.system, r"WriteHooks\{&flashWriteBegin, &flashWriteEnd\},\s*&admitJob\);")
        self.assertIn("bool admitJob() { return ::systems::dflash::admitsJobs(::ecustate::rte::publishedState()); }",
                      self.system)
        # the test command's hold ends at once in the run-down and with the lifecycle
        self.assertIn("if ((elapsed < hold.durationUs) && !dataFlashGate.locked())", self.system)
        shutdown = re.search(r"void DataFlashSystem::shutdown\(\)\s*\{(.*?)\n\}", self.system, re.S).group(1)
        self.assertIn("hold.scope.reset();", shutdown)

    def test_run_down_and_lifecycle(self):
        self.assertRegex(self.integration, r"sequence\.set\(::ecustate::ShutdownStep::NVM,\s*"
                                           r"::systems::dataFlashRunDownStep\(nvm\)\)")
        self.assertIn('lifecycleManager.addComponent("dflash", dataFlashSystem.create(TASK_BSP), level);',
                      self.integration)
        run = re.search(r"void DataFlashSystem::run\(\)\s*\{(.*?)\n\}", self.system, re.S).group(1)
        shutdown = re.search(r"void DataFlashSystem::shutdown\(\)\s*\{(.*?)\n\}", self.system, re.S).group(1)
        self.assertIn("dataFlashGate.unlock();", run)
        self.assertIn("dataFlashGate.lock();", shutdown)
        # the console work also respects the lock (immediate answer) and runs in the owner task
        self.assertEqual(self.system.count("dataFlashGate.locked()"), 4)  # NVM-25: + end of a hold in the run-down
        self.assertEqual(self.system.count("inOwner("), 5)  # NVM-25: + dflash hold
        # every job goes through the gate: the service is built over the gate, not over the driver
        self.assertRegex(self.system, r"::dataflash::AreaService areaService\(\s*dataFlashGate,")
        self.assertRegex(self.system, r"WriteHooks\{&flashWriteBegin, &flashWriteEnd\}")
        self.assertIn("::safety::romCheckFlashWrites.begin();", self.system)
        self.assertIn("::safety::romCheckFlashWrites.end();", self.system)

    def test_cache(self):
        self.assertRegex(self.system, r"#if NX_FEATURE_LMEM_CACHE\s*#include \"cache/cache.h\"")
        self.assertIn("cacheInvalidate(NX_DFLASH_START, NX_DFLASH_SIZE)", self.system)
        main = code(PORT / "src" / "main.cpp")
        block = re.search(r"#if NX_FEATURE_LMEM_CACHE\s*(LMEM->PCCRMR[^;]*;)\s*cacheEnable\(\);", main)
        self.assertIsNotNone(block, "PCCRMR R2 = 00b right before cacheEnable() (NVM12-S4)")
        self.assertIn("~LMEM_PCCRMR_R2_MASK", block.group(1))
        self.assertIn("LMEM_PCCRMR_R2(0U)", block.group(1))

    def test_command_only_in_test_builds(self):
        cmake = (PORT / "CMakeLists.txt").read_text(encoding="utf-8")
        self.assertRegex(cmake, r'option\(NX_DFLASH_COMMAND\s+"[^"]*"\s+OFF\)')
        self.assertIn("target_compile_definitions(main PRIVATE NX_DFLASH_COMMAND=1)", cmake)
        header = code(PORT / "include" / "systems" / "DataFlashSystem.h")
        # the command class, its definition and its wrapper exist only with the option
        self.assertRegex(header, r"#ifdef NX_DFLASH_COMMAND\s*class Command :")
        self.assertEqual(self.system.count('DEFINE_COMMAND_GROUP_GET_INFO_BEGIN(DataFlashSystem::Command, "dflash"'), 1)
        guarded = re.search(r"#ifdef NX_DFLASH_COMMAND\s*DEFINE_COMMAND_GROUP_GET_INFO_BEGIN", self.system)
        self.assertIsNotNone(guarded)
        self.assertRegex(header, r"#ifdef NX_DFLASH_COMMAND\s*Command _command;\s*::console::AsyncCommandWrapper")

    def test_demo_project_has_areas(self):
        # PLAT-62: the own full test profile (data flash areas of the demo) - the eval board project is private
        project = (ROOT / "tools" / "nxconfig" / "test" / "profiles" / "projects" / "testproject_full"
                   / "project.yaml").read_text(encoding="utf-8")
        block = re.search(r"\n  data_flash:\n    areas:\n((?:      .*\n)+)", project)
        self.assertIsNotNone(block, "nvm.data_flash.areas in the demo project")
        self.assertIn("purpose: calibration", block.group(1))


if __name__ == "__main__":
    unittest.main()
