"""NVM-02/NVM-18: host test of the NvM core (libs/bsw/nvm/include/nvm/Nvm.h) with its own test profile.

  NvmCoreHostTest     test/host/NvmHostTest.cpp (zig c++, -Werror -Wconversion, NX_NVM_FAULT_INJECTION): QM blocks
                      as since NVM-02, ASIL blocks with two copies (CRC-32, write counter, repair, events, quality)
  NvmCoreMutationTest the same test against Nvm.h with deliberate faults - every one of them must make it fail
  NvmCoreBuildTest    the header without NX_NVM_FAULT_INJECTION has no fault injection (product builds)

Run: .venv/Scripts/python -m unittest libs/bsw/nvm/test/test_nvm_core.py
"""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
MODULE = HERE.parent
REPO = MODULE.parents[2]
INCLUDE = MODULE / "include"
CORE = INCLUDE / "nvm" / "Nvm.h"
TEST = HERE / "host" / "NvmHostTest.cpp"
BSP_INCLUDE = REPO / "external" / "openbsw" / "libs" / "bsw" / "bsp" / "include"
ZIG = [sys.executable, "-m", "ziglang"]
CXX_FLAGS = ["-std=c++17", "-O1", "-Wall", "-Wextra", "-Wconversion", "-Wno-sign-conversion", "-Werror"]


def build_and_run(include: Path, tmp: Path, defines=("-DNX_NVM_FAULT_INJECTION=1",)) -> subprocess.CompletedProcess:
    exe = tmp / "nvm_host_test.exe"
    built = subprocess.run(ZIG + ["c++", *CXX_FLAGS, *defines, f"-I{include}", f"-I{HERE / 'host'}",
                                  "-isystem", str(BSP_INCLUDE), str(TEST), "-o", str(exe)],
                           capture_output=True, text=True)
    if built.returncode != 0:
        return built
    return subprocess.run([str(exe)], capture_output=True, text=True, timeout=120)


@unittest.skipUnless(BSP_INCLUDE.is_dir(), "external/openbsw is not checked out (git submodule update --init)")
class NvmCoreHostTest(unittest.TestCase):
    def test_core(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            result = build_and_run(INCLUDE, tmp)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(" 0 failures", result.stdout)
        checks = int(result.stdout.strip().splitlines()[-1].split()[1])
        self.assertGreaterEqual(checks, 400)


@unittest.skipUnless(BSP_INCLUDE.is_dir(), "external/openbsw is not checked out (git submodule update --init)")
class NvmCoreBuildTest(unittest.TestCase):
    def test_no_fault_injection_in_a_product(self):
        """Without NX_NVM_FAULT_INJECTION the test does not even compile: the injection is not there."""
        tmp = Path(tempfile.mkdtemp())
        try:
            result = build_and_run(INCLUDE, tmp, defines=())
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("injectCopyFault", result.stderr)


@unittest.skipUnless(BSP_INCLUDE.is_dir(), "external/openbsw is not checked out (git submodule update --init)")
class NvmCoreMutationTest(unittest.TestCase):
    MUTATIONS = [
        # QM (NVM-02)
        ("QM crc not checked", "if (crc16(_scratch, RECORD_HEADER + block.size) != get16(",
         "if (false && crc16(_scratch, RECORD_HEADER + block.size) != get16("),
        ("QM older slot wins", "|| !newer(seq1, seq0)", "|| newer(seq1, seq0)"),
        ("QM write to the current slot", "uint8_t const first   = static_cast<uint8_t>(s.slot ^ 1U);",
         "uint8_t const first   = static_cast<uint8_t>(s.slot);"),
        ("no read-back", "return verify(address, _scratch, length) ? WriteResult::OK : WriteResult::VERIFY;",
         "return WriteResult::OK;"),
        ("id not compared", "if ((id != block.id) || (size != block.size))", "if (size != block.size)"),
        # QM one place defective (NVM-30)
        ("QM defective place without event", "report(i, BlockEvent::PLACE_DEFECTIVE);", "(void)0;"),
        ("QM unreadable place not reported", "(other == Slot::CORRUPT) || (other == Slot::UNREADABLE)",
         "(other == Slot::CORRUPT)"),
        ("QM erased or foreign place reported", "(other == Slot::CORRUPT) || (other == Slot::UNREADABLE)",
         "(other != Slot::VALID)"),
        ("QM changed place taken as erased",
         "loadDefault(i, (slot == 0U) ? Slot::CORRUPT : s0, (slot == 1U) ? Slot::CORRUPT : s1);",
         "loadDefault(i, s0, s1);"),
        # ASIL (NVM-18)
        ("crc32 polynomial", "0xEDB88320U", "0xEDB88321U"),
        ("ASIL crc not checked", "if (crc32(_scratch, ASIL_HEADER + block.size) != get32(",
         "if (false && crc32(_scratch, ASIL_HEADER + block.size) != get32("),
        ("ASIL lower counter wins", "(counter[0] >= counter[1])", "(counter[0] <= counter[1])"),
        ("ASIL copies not compared on a tie", "same   = verify(placeAddress(blockConfig, loser), _scratch, length);",
         "same   = true;"),
        ("ASIL no repair", "if (putRecord(blockConfig, loser, length) == WriteResult::OK)", "if (true)"),
        ("ASIL repair without event", "report(i, BlockEvent::COPY_REPAIRED);", "(void)0;"),
        ("ASIL failed repair without event", "report(i, BlockEvent::COPY_REPAIR_FAILED);", "(void)0;"),
        ("default without event", "report(i, BlockEvent::DEFAULT_LOADED);", "(void)0;"),
        ("event for an erased block", "if (state == BlockState::CORRUPT)\n        {\n            report(",
         "if (state != BlockState::VALID)\n        {\n            report("),
        ("ASIL second copy not written",
         "result     = putRecord(blockConfig, static_cast<uint8_t>(first ^ 1U), length);",
         "result     = WriteResult::OK;"),
        ("ASIL both copies in the wrong order",
         "WriteResult result = putRecord(blockConfig, first, length);",
         "WriteResult result = putRecord(blockConfig, static_cast<uint8_t>(first ^ 1U), length);"),
        ("ASIL counter not incremented", "uint32_t const counter = s.sequence + 1U;",
         "uint32_t const counter = s.sequence;"),
        ("quality of a lost value", "case BlockState::FOREIGN: return Quality::UNSECURED;",
         "case BlockState::FOREIGN: return Quality::STORED;"),
        ("quality not handed to the RTE", "*_blocks[i].quality = static_cast<uint8_t>(_status[i].quality);",
         "(void)0;"),
        ("ASIL second copy outside the memory", "uint32_t const bytes = blockBytes(block.size, block.integrity);",
         "uint32_t const bytes = copyBytes(block.size, block.integrity);"),
    ]

    def test_mutations_are_found(self):
        source = CORE.read_text(encoding="utf-8")
        for name, old, new in self.MUTATIONS:
            with self.subTest(mutation=name):
                self.assertEqual(source.count(old), 1, f"mutation text not found once: {old}")
                tmp = Path(tempfile.mkdtemp())
                try:
                    shutil.copytree(INCLUDE, tmp / "include")
                    (tmp / "include" / "nvm" / "Nvm.h").write_text(source.replace(old, new), encoding="utf-8")
                    result = build_and_run(tmp / "include", tmp)
                finally:
                    shutil.rmtree(tmp, ignore_errors=True)
                self.assertNotEqual(result.returncode, 0, f"mutation '{name}' was not detected:\n{result.stdout}")


if __name__ == "__main__":
    unittest.main()
