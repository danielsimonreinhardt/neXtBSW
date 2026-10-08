"""NVM-12: host test of the data flash areas (libs/bsw/nvmDataFlash) with its own test profile.

  DataFlashHostTest      test/host/DataFlashHostTest.cpp (zig c++, -Werror -Wconversion): dataflash::AreaService
                         against a simulated data flash (16 KB, 1 KB sectors, 8-byte program unit - no derivative)
  DataFlashMutationTest  the same test against DataFlashAreas.h (NVM-25: and IDataFlash.h) with deliberate faults -
                         every one must make it fail
  DataFlashGuardTest     the contract and the service are family neutral: no register, no family header, no Qt

The family implementation (s32k1xx flexnvm/FlexNvmDataFlash.h) has its own test against a register model:
platforms/s32k1xx/bsp/bspFlexNvm/test/test_flexnvm.py.

Run: .venv/Scripts/python -m unittest libs/bsw/nvmDataFlash/test/test_nvm_data_flash.py
"""

import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
MODULE = HERE.parent
INCLUDE = MODULE / "include"
AREAS_H = INCLUDE / "dataflash" / "DataFlashAreas.h"
CONTRACT_H = INCLUDE / "dataflash" / "IDataFlash.h"
TEST = HERE / "host" / "DataFlashHostTest.cpp"
ZIG = [sys.executable, "-m", "ziglang"]
CXX_FLAGS = ["-std=c++17", "-O1", "-Wall", "-Wextra", "-Wconversion", "-Wno-sign-conversion", "-Werror"]


def build_and_run(include: Path, tmp: Path) -> subprocess.CompletedProcess:
    exe = tmp / "data_flash_host_test.exe"
    built = subprocess.run(ZIG + ["c++", *CXX_FLAGS, f"-I{include}", str(TEST), "-o", str(exe)],
                           capture_output=True, text=True, timeout=600)
    if built.returncode != 0:
        return built
    try:
        return subprocess.run([str(exe)], capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess([str(exe)], 1, "hangs", "")


class DataFlashHostTest(unittest.TestCase):
    def test_areas(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            result = build_and_run(INCLUDE, tmp)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        print("\n" + result.stdout.strip(), file=sys.stderr)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(" 0 failures", result.stdout)
        checks = int(result.stdout.strip().splitlines()[-1].split()[1])
        self.assertGreaterEqual(checks, 100)


class DataFlashMutationTest(unittest.TestCase):
    MUTATIONS = [
        ("no blank check before program", "if (!erased(_unit, unit))", "if (false)"),
        ("identical unit programmed again", "if (same(_unit, wants, unit))\n        {\n            _verifying = false;",
         "if (!_verifying && false)\n        {\n            _verifying = false;"),
        ("read-back not compared", "return finish(Status::FAILED); // read-back", "return finish(Status::OK); // read-back"),
        ("erase stops after one sector", "return (_position >= _end) ? finish(Status::OK) : Status::BUSY;\n        }\n        open();",
         "return finish(Status::OK);\n        }\n        open();"),
        ("erase error ignored", "            if (s != Status::OK)\n            {\n                return finish(s);\n            }\n            _launched = false;\n            _position += _flash.geometry().sectorSize;",
         "            _launched = false;\n            _position += _flash.geometry().sectorSize;"),
        ("busy block ends the job", "            return Status::BUSY; // another command runs on the block",
         "            return finish(Status::BUSY); // another command runs on the block"),
        ("two jobs at once", "        if (_job != Job::NONE)\n        {\n            return Status::BUSY;",
         "        if (false)\n        {\n            return Status::BUSY;"),
        ("area end not checked", "(length > (a.size - offset))", "false"),
        ("sector alignment of areas not checked", "if (((a.offset % g.sectorSize) != 0U) || ((a.size % g.sectorSize) != 0U))",
         "if (false)"),
        ("area outside the data flash accepted", "if ((a.size == 0U) || (a.offset >= g.size) || (a.size > (g.size - a.offset)))",
         "if (a.size == 0U)"),
        ("end hook missing", "            _hooks.end();", "            (void)0;"),
        ("write kept open while the block is busy",
         "        Status const s = _flash.startErase(_position);\n        if (s != Status::OK)\n        {\n            close();",
         "        Status const s = _flash.startErase(_position);\n        if (s != Status::OK)\n        {\n"),
        ("write opened only after the launch", "        open();\n        Status const s = _flash.startErase(_position);",
         "        Status const s = _flash.startErase(_position);\n        open();"),
        ("find by prefix", "        return *a == *b;", "        return *a == '\\0';"),
        ("program unit alignment not checked", "if (((offset % unit) != 0U) || ((length % unit) != 0U))", "if (unit == 0U)"),
        ("verify read busy counted as failure", "        if (r == Status::BUSY)\n        {\n            return Status::BUSY;\n        }\n        if (r != Status::OK)",
         "        if (r != Status::OK)"),
        # NVM-26: admission of new jobs (safety review NVM23-S1 b)
        ("admission ignored by erase", "        if (!admitted())\n        {\n            return Status::NOT_AVAILABLE;\n        }\n"
         "        Status const s = startable(index, 0U", "        Status const s = startable(index, 0U"),
        ("admission ignored by write", "        if (!admitted())\n        {\n            return Status::NOT_AVAILABLE;\n        }\n"
         "        if (data == nullptr)", "        if (data == nullptr)"),
        ("admission inverted", "if ((_admission != nullptr) && !_admission())", "if ((_admission != nullptr) && _admission())"),
        ("refused jobs not counted", "            ++_refusedJobs;", ""),
        # NVM-25: read in place of an area
        ("area address for a foreign scope", "if ((&scope.flash() != &_flash) || (inArea(index, offset, length) != Status::OK))",
         "if (inArea(index, offset, length) != Status::OK)"),
        ("area address outside the area", "if ((&scope.flash() != &_flash) || (inArea(index, offset, length) != Status::OK))",
         "if (&scope.flash() != &_flash)"),
        ("contract: address without a hold", "        if (!_held)\n        {\n            return 0U;", "        if (false)\n        {\n            return 0U;",
         CONTRACT_H),
        ("contract: address beyond the data flash", "if ((length == 0U) || (offset >= g.size) || (length > (g.size - offset)))",
         "if ((length == 0U) || (offset >= g.size))", CONTRACT_H),
        ("contract: scope never releases", "            _flash.releaseDirectRead();", "", CONTRACT_H),
        ("contract: scope releases a hold it did not get", "        if (_held)\n        {\n            _flash.releaseDirectRead();",
         "        if (true)\n        {\n            _flash.releaseDirectRead();", CONTRACT_H),
    ]

    def test_mutations_are_detected(self):
        tmp = Path(tempfile.mkdtemp())
        survivors = []
        try:
            for i, (name, old, new, *where) in enumerate(self.MUTATIONS):
                header = where[0] if where else AREAS_H
                original = header.read_text(encoding="utf-8").replace("\r\n", "\n")
                self.assertEqual(original.count(old), 1, f"{name}: {old!r} found {original.count(old)} times")
                include = tmp / f"m{i}" / "include"
                shutil.copytree(INCLUDE, include)
                (include / "dataflash" / header.name).write_text(original.replace(old, new), encoding="utf-8")
                result = build_and_run(include, tmp / f"m{i}")
                self.assertNotIn("error:", result.stderr, f"mutant '{name}' does not compile:\n{result.stderr[-2000:]}")
                if result.returncode == 0 and " 0 failures" in result.stdout:
                    survivors.append(name)
            print(f"\n{len(self.MUTATIONS) - len(survivors)} of {len(self.MUTATIONS)} mutants detected",
                  file=sys.stderr)
            self.assertEqual(survivors, [])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class DataFlashGuardTest(unittest.TestCase):
    def test_family_neutral(self):
        for header in (INCLUDE / "dataflash").glob("*.h"):
            text = header.read_text(encoding="utf-8")
            code = "\n".join(line.split("//")[0] for line in text.splitlines())
            includes = re.findall(r'#include\s+[<"]([^>"]+)[>"]', code)
            self.assertTrue(all(i in ("cstdint", "dataflash/IDataFlash.h") for i in includes), (header.name, includes))
            for forbidden in ("FTFC", "FSTAT", "FCCOB", "mcu.h", "S32K", "volatile", "reinterpret_cast"):
                self.assertNotIn(forbidden, code, header.name)


if __name__ == "__main__":
    unittest.main()
