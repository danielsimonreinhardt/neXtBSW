"""NVM-19: host test of the NvM integration of the application with its own test profile (host/nvmstubs).

  NvmIntegrationHostTest    host/NvmIntegrationHostTest.cpp (zig c++, -Werror -Wconversion, NX_NVM_FAULT_INJECTION):
                            nvm::NvmRteBlocks (libs/bsw/nvm with integrity and quality from rte::NVM_BLOCKS) and
                            nvm/NvmEvents.h (events of the start to a fake fault memory) over several power cycles
  NvmIntegrationMutationTest the same test with deliberate faults in NvmRteBlocks.h / NvmEvents.h - each must fail
  NvmFlashWriteBracketTest  host/FlashWriteBracketHostTest.cpp (SYS-44) against the core of libs/bsw/nvm, QM and ASIL

Run: .venv/Scripts/python -m unittest ecu/application/src/systems/test/test_nvm_integration.py
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
TEST = HOST / "NvmIntegrationHostTest.cpp"
BRACKET_TEST = HOST / "FlashWriteBracketHostTest.cpp"
NVM_INCLUDE = REPO / "libs" / "bsw" / "nvm" / "include"
DEM_INCLUDE = REPO / "libs" / "bsw" / "dem" / "include"
BSP_INCLUDE = REPO / "external" / "openbsw" / "libs" / "bsw" / "bsp" / "include"
ZIG = [sys.executable, "-m", "ziglang"]
CXX_FLAGS = ["-std=c++17", "-O1", "-Wall", "-Wextra", "-Wconversion", "-Wno-sign-conversion", "-Werror"]


def build_and_run(systems: Path, tmp: Path, test: Path = TEST) -> subprocess.CompletedProcess:
    """Builds \\p test with the headers of \\p systems (the real ecu/application/src/systems or a mutated copy)."""
    exe = tmp / "nvm_integration.exe"
    includes = [HOST / "nvmstubs", systems, HOST, NVM_INCLUDE, DEM_INCLUDE]
    built = subprocess.run(ZIG + ["c++", *CXX_FLAGS, "-DNX_NVM_FAULT_INJECTION=1", *[f"-I{i}" for i in includes],
                                  "-isystem", str(BSP_INCLUDE), str(test), "-o", str(exe)],
                           capture_output=True, text=True)
    if built.returncode != 0:
        return built
    return subprocess.run([str(exe)], capture_output=True, text=True, timeout=120)


def last_counts(stdout: str) -> tuple[int, int]:
    words = stdout.strip().splitlines()[-1].replace(",", "").split()
    return int(words[-4]), int(words[-2])


@unittest.skipUnless(BSP_INCLUDE.is_dir(), "external/openbsw is not checked out (git submodule update --init)")
class NvmIntegrationHostTest(unittest.TestCase):
    def test_integration(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            result = build_and_run(SYSTEMS, tmp)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        print("\n" + result.stdout.strip(), file=sys.stderr)
        checks, failures = last_counts(result.stdout)
        self.assertEqual(failures, 0)
        self.assertGreaterEqual(checks, 80)


@unittest.skipUnless(BSP_INCLUDE.is_dir(), "external/openbsw is not checked out (git submodule update --init)")
class NvmFlashWriteBracketTest(unittest.TestCase):
    def test_bracket_with_the_library_core(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            result = build_and_run(SYSTEMS, tmp, BRACKET_TEST)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(" 0 failures", result.stdout)


@unittest.skipUnless(BSP_INCLUDE.is_dir(), "external/openbsw is not checked out (git submodule update --init)")
class NvmIntegrationMutationTest(unittest.TestCase):
    # (description, file under systems/nvm, text, replacement)
    MUTATIONS = [
        ("ASIL block of the RTE stored as QM", "NvmRteBlocks.h",
         "(b.integrity == ::rte::NvmIntegrity::ASIL) ? Integrity::ASIL : Integrity::QM", "Integrity::QM"),
        ("every block ASIL", "NvmRteBlocks.h",
         "(b.integrity == ::rte::NvmIntegrity::ASIL) ? Integrity::ASIL : Integrity::QM", "Integrity::ASIL"),
        ("quality not handed to the RTE", "NvmRteBlocks.h", "                b.quality};", "                nullptr};"),
        ("restore before the read", "NvmRteBlocks.h",
         "        _manager.readAll();\n        ::rte::nvmRestore();",
         "        ::rte::nvmRestore();\n        _manager.readAll();"),
        ("FAILED and PASSED swapped", "NvmEvents.h",
         "log.raised(i, e.event) ? ::dem::Result::FAILED : ::dem::Result::PASSED",
         "log.raised(i, e.event) ? ::dem::Result::PASSED : ::dem::Result::FAILED"),
        ("result for a block without memory", "NvmEvents.h",
         "if ((quality == Quality::NOT_READ) || (quality == Quality::NO_MEMORY))", "if (quality == Quality::NOT_READ)"),
        ("nothing reported", "NvmEvents.h", "            if (dtc >= 0)\n", "            if (dtc >= 0x10000)\n"),
        ("event names of two kinds swapped", "NvmEvents.h",
         '{BlockEvent::COPY_REPAIR_FAILED, "nvm.copy_repair_failed"},\n    {BlockEvent::COPY_REPAIRED, '
         '"nvm.copy_repaired"},',
         '{BlockEvent::COPY_REPAIR_FAILED, "nvm.copy_repaired"},\n    {BlockEvent::COPY_REPAIRED, '
         '"nvm.copy_repair_failed"},'),
        ("event of the block not recorded", "NvmEvents.h",
         "            _raised[index] = static_cast<uint8_t>(_raised[index] | (1U << kind));\n",
         "            _raised[index] = 0U;\n"),
        ("event recorded for another block", "NvmEvents.h",
         "            _raised[index] = static_cast<uint8_t>(_raised[index] | (1U << kind));\n",
         "            _raised[0] = static_cast<uint8_t>(_raised[0] | (1U << kind));\n"),
        ("events not counted", "NvmEvents.h", "        ++_count[kind];\n", "\n"),
        ("last block instead of the first", "NvmEvents.h", "        if (_count[kind] == 0U)\n", "        if (true)\n"),
        ("other separator in the event name", "NvmEvents.h", "    out[p] = '.';\n", "    out[p] = '_';\n"),
        ("name one byte too long accepted", "NvmEvents.h", "    if ((p + 1U + b) >= size)\n",
         "    if ((p + 1U + b) > size)\n"),
    ]

    def test_mutations_are_found(self):
        for name, file, old, new in self.MUTATIONS:
            with self.subTest(mutation=name):
                source = (SYSTEMS / "nvm" / file).read_text(encoding="utf-8")
                self.assertEqual(source.count(old), 1, f"mutation text not found once in {file}: {old}")
                tmp = Path(tempfile.mkdtemp())
                try:
                    shutil.copytree(SYSTEMS / "nvm", tmp / "systems" / "nvm")
                    (tmp / "systems" / "nvm" / file).write_text(source.replace(old, new), encoding="utf-8")
                    result = build_and_run(tmp / "systems", tmp)
                finally:
                    shutil.rmtree(tmp, ignore_errors=True)
                # a mutation has to build and then fail the test, not fail the compiler
                self.assertIn("NvmIntegrationHostTest:", result.stdout,
                              f"mutation '{name}' did not build:\n{result.stderr[-2000:]}")
                self.assertNotEqual(result.returncode, 0,
                                    f"mutation '{name}' was not detected:\n{result.stdout}{result.stderr}")


if __name__ == "__main__":
    unittest.main()
