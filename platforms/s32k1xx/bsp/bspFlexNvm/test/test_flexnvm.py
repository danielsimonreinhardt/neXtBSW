# SPDX-License-Identifier: Apache-2.0
"""FlexNVM of the family s32k1xx (NVM-02, NVM-07, NVM-10): host tests against a register model, their meaningfulness, and the
guards around the IRREVERSIBLE partition command.

  FlexNvmEepromTest     test/host/FlexNvmEepromHostTest.cpp: the EEPROM driver of the NvM (include/flexnvm/
                        FlexNvmEeprom.h) - states, sizes, only changed bytes, a store bounded in time (not in polls)
  FlexNvmPartitionTest  test/host/FlexNvmPartitionHostTest.cpp: src/FlexNvmPartition.cpp together with OpenBSW's
                        unchanged EepromDriver.cpp against a model of the flash module - confirmation word, erased IFR,
                        FCCOB bytes of PGMPART for the decision nvm01-eeesize (EEESIZE 0010b, DEPART 1011b, FCCOB3 = 0);
                        NVM-10: include/flexnvm/FlexNvmChip.h - the mixed state of the board (DEPART 0xF, EEERAMSIZE
                        0x2) is a chip as delivered, FlexRAM flags, a programmed IFR EEESIZE byte (ACCERR), SIM SDID
  FlexNvmDataFlashTest  test/host/FlexNvmDataFlashHostTest.cpp (NVM-12): include/flexnvm/FlexNvmDataFlash.h against a
                        model of the flash module - Erase Flash Sector / Program Phrase on the data flash (address bit
                        23), launch only with the module idle and no EEPROM store running, poll with a time limit,
                        cache invalidate hook; the EEPROM driver waits for the end of a data flash command;
                        NVM-25: the block lock include/flexnvm/FlexNvmBlockLock.h (read in chunks under the lock, a
                        hold for a read in place keeps EEPROM stores and launches out, "another task" played where
                        interrupts are enabled again)
  MutationTest          deliberately broken variants of the module files must make their host test fail (or hang)
  GuardTest             source checks: the EEPROM driver and the chip view launch no flash command, the data flash
                        driver only Erase Flash Sector and Program Phrase on the data flash (NVM-12); the partition is
                        reachable only
                        from the console command of a test build (option NX_FLEXNVM_PARTITION_COMMAND, default OFF),
                        never from the start-up (StaticBsp.cpp, main.cpp, Integration.cpp)

No board. Run: .venv/Scripts/python -m unittest platforms/s32k1xx/bsp/bspFlexNvm/test/test_flexnvm.py
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
ROOT = HERE.parents[4]
OPENBSW = ROOT / "external" / "openbsw"
PORT = ROOT / "platforms" / "s32k1xx" / "port" / "main"
RUN_LIMIT_S = 30

EEPROM_H = "include/flexnvm/FlexNvmEeprom.h"
PARTITION_CPP = "src/FlexNvmPartition.cpp"
CHIP_H = "include/flexnvm/FlexNvmChip.h"
DATA_FLASH_H = "include/flexnvm/FlexNvmDataFlash.h"
LOCK_H = "include/flexnvm/FlexNvmBlockLock.h"
DATA_FLASH_CONTRACT = ROOT / "libs" / "bsw" / "nvmDataFlash" / "include"
STUBS = MODULE / "test" / "host" / "stubs"
BSP_INCLUDE = OPENBSW / "libs" / "bsw" / "bsp" / "include"
ETL_INCLUDE = OPENBSW / "libs" / "3rdparty" / "etl" / "include"
OPENBSW_EEPROM = OPENBSW / "platforms" / "s32k1xx" / "bsp" / "bspEepromDriver"
STRICT = ["-std=c++17", "-O1", "-Wall", "-Wextra", "-Wconversion", "-Wno-sign-conversion", "-Werror"]
# OpenBSW's EepromDriver.cpp is compiled unchanged; its narrowing of the FSTAT/FCCOB values is not ours to fix
RELAXED = ["-std=c++17", "-O1", "-DUNIT_TEST"]


def zig(args, cwd=ROOT):
    return subprocess.run([sys.executable, "-m", "ziglang", "c++", *args], cwd=cwd, capture_output=True, text=True,
                          timeout=600)


def build(module: Path, test: str, work: Path) -> tuple[bool, str, Path]:
    """Compiles one host test with the module files from `module` (a copy with a mutation); False + text on failure."""
    includes = [f"-I{module / 'include'}", f"-I{STUBS}", f"-I{OPENBSW_EEPROM / 'include'}", f"-I{ETL_INCLUDE}",
                f"-I{DATA_FLASH_CONTRACT}", "-isystem", str(BSP_INCLUDE)]
    objects = []
    sources = [(MODULE / "test" / "host" / test, STRICT)]
    if test == "FlexNvmPartitionHostTest.cpp":
        sources += [(module / PARTITION_CPP, STRICT), (OPENBSW_EEPROM / "src" / "eeprom" / "EepromDriver.cpp", RELAXED)]
    for i, (source, flags) in enumerate(sources):
        obj = work / f"{i}.o"
        r = zig([*flags, *includes, "-c", str(source), "-o", str(obj)])
        if r.returncode != 0:
            return False, r.stdout + r.stderr, obj
        objects.append(str(obj))
    exe = work / (test.replace(".cpp", ".exe"))
    r = zig([*objects, "-o", str(exe)])
    return r.returncode == 0, r.stdout + r.stderr, exe


def run(exe: Path) -> tuple[bool, str]:
    try:
        r = subprocess.run([str(exe)], capture_output=True, text=True, timeout=RUN_LIMIT_S)
    except subprocess.TimeoutExpired:
        return False, "hangs"
    return r.returncode == 0 and ", 0 failures" in r.stdout, r.stdout[-3000:]


class HostTest(unittest.TestCase):
    def setUp(self):
        self.work = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.work, ignore_errors=True)

    def check(self, test: str):
        ok, text, exe = build(MODULE, test, self.work)
        self.assertTrue(ok, text[-3000:])
        passed, out = run(exe)
        print("\n" + out.strip(), file=sys.stderr)
        self.assertTrue(passed, out)


class FlexNvmEepromTest(HostTest):
    def test_eeprom_driver(self):
        self.check("FlexNvmEepromHostTest.cpp")


class FlexNvmPartitionTest(HostTest):
    def test_partition(self):
        self.check("FlexNvmPartitionHostTest.cpp")


class FlexNvmDataFlashTest(HostTest):
    def test_data_flash(self):
        self.check("FlexNvmDataFlashHostTest.cpp")


# name: (file of the module, old, new, host test); `old` must occur exactly once
MUTATIONS = {
    "store waits without time limit": (EEPROM_H, "if (elapsed > _writeLimitUs)", "if (false)",
                                       "FlexNvmEepromHostTest.cpp"),
    "limit one poll early (>=)": (EEPROM_H, "if (elapsed > _writeLimitUs)", "if (elapsed >= _writeLimitUs)",
                                  "FlexNvmEepromHostTest.cpp"),
    "elapsed time never measured": (EEPROM_H, "uint32_t const elapsed = getSystemTimeUs32Bit() - start;",
                                    "uint32_t const elapsed = start - start;", "FlexNvmEepromHostTest.cpp"),
    "longest write not kept": (EEPROM_H, "_longestWriteUs = (elapsed > _longestWriteUs) ? elapsed : _longestWriteUs;",
                               "", "FlexNvmEepromHostTest.cpp"),
    "time-outs not counted": (EEPROM_H, "            if (elapsed > _writeLimitUs)\n            {\n                ++_timeouts;",
                              "            if (elapsed > _writeLimitUs)\n            {\n", "FlexNvmEepromHostTest.cpp"),
    "no confirmation needed": (PARTITION_CPP, "!sameText(confirmation, plan.confirmWord)",
                               "(!sameText(confirmation, plan.confirmWord) && false)",
                               "FlexNvmPartitionHostTest.cpp"),
    "confirmation prefix is enough": (PARTITION_CPP, "    return *a == *b;", "    return *a == '\\0';",
                                      "FlexNvmPartitionHostTest.cpp"),
    "partitioned chip not refused": (PARTITION_CPP, "if (!isErased())", "if (false)",
                                     "FlexNvmPartitionHostTest.cpp"),
    # NVM-10: chip as delivered = DEPART erased and FlexRAM plain RAM; EEERAMSIZE no IFR code (board: 0x2)
    "erased check on DEPART only": (PARTITION_CPP,
                                    "FlexNvmChip::partition() == FlexNvmChip::Partition::NOT_PARTITIONED",
                                    "FlexNvmChip::departCode() == 0xFU", "FlexNvmPartitionHostTest.cpp"),
    "EEERAMSIZE required erased (old rule, refuses the board)": (
        CHIP_H, "(ram && !eee && definedEeeRamSizeCode(eeeRamSizeCode()))",
        "(ram && !eee && (eeeRamSizeCode() == ERASED_CODE))", "FlexNvmPartitionHostTest.cpp"),
    "RAMRDY ignored": (CHIP_H, "bool const ram = ramReady();", "bool const ram = true;",
                       "FlexNvmPartitionHostTest.cpp"),
    "EEERDY ignored": (CHIP_H, "ram && !eee && definedEee", "ram && definedEee", "FlexNvmPartitionHostTest.cpp"),
    "reserved EEERAMSIZE accepted": (CHIP_H, "definedEeeRamSizeCode(eeeRamSizeCode())", "true",
                                     "FlexNvmPartitionHostTest.cpp"),
    "EEERAMSIZE range too wide": (CHIP_H, "(code <= 0x9U)", "(code <= 0xEU)", "FlexNvmPartitionHostTest.cpp"),
    "EEPROM ready shown as RAM": (CHIP_H, "return Partition::EEPROM_READY;", "return Partition::FLEXRAM_AS_RAM;",
                                  "FlexNvmPartitionHostTest.cpp"),
    "display calls the chip as delivered partitioned": (CHIP_H, '"not partitioned (IFR', '"partitioned (IFR',
                                                        "FlexNvmPartitionHostTest.cpp"),
    "CSEc from the wrong FEATURES bit": (CHIP_H, "& 0x80U) != 0U}", "& 0x01U) != 0U}",
                                         "FlexNvmPartitionHostTest.cpp"),
    "144W named 144": (CHIP_H, 'case 5U: return "S32K144W";', 'case 5U: return "S32K144";',
                       "FlexNvmPartitionHostTest.cpp"),
    "running command not refused": (PARTITION_CPP, "if ((FTFC->FSTAT & FTFC_FSTAT_CCIF_MASK) == 0U)", "if (false)",
                                    "FlexNvmPartitionHostTest.cpp"),
    "load byte inverted (OpenBSW naming taken literally)": (PARTITION_CPP, "plan.loadCode != 0U", "plan.loadCode == 0U",
                                                            "FlexNvmPartitionHostTest.cpp"),
    "size and partition code swapped": (PARTITION_CPP, "        plan.eeeSizeCode,\n        plan.partitionCode};",
                                        "        plan.partitionCode,\n        plan.eeeSizeCode};",
                                        "FlexNvmPartitionHostTest.cpp"),
    "refused command reported as done": (PARTITION_CPP, "if ((FTFC->FSTAT & FAILURE_FLAGS) != 0U)",
                                         "if (((FTFC->FSTAT & FAILURE_FLAGS) != 0U) && false)",
                                         "FlexNvmPartitionHostTest.cpp"),
    "no reset hint after a command SIM does not show yet": (
        PARTITION_CPP, "(result == ::bsp::BSP_OK) ? Result::DONE : Result::DONE_RESET_NEEDED",
        "(static_cast<void>(result), Result::DONE)",
        "FlexNvmPartitionHostTest.cpp"),
    "invalid load byte accepted": (PARTITION_CPP, "|| (plan.loadCode > 1U)", "", "FlexNvmPartitionHostTest.cpp"),
    # NVM-11: CSEc key space from the confirmation word
    "key space fixed at 0 again": (PARTITION_CPP, "        codes.keySize, //", "        0U, //",
                                   "FlexNvmPartitionHostTest.cpp"),
    "old word without key space written": (PARTITION_CPP, "if (!codes.keyNamed)", "if (false)",
                                           "FlexNvmPartitionHostTest.cpp"),
    "key size beyond 20 slots accepted": (PARTITION_CPP, "(codes.keySize > KEY_SIZE_MAX)", "(codes.keySize > (KEY_SIZE_MAX + 12U))",
                                          "FlexNvmPartitionHostTest.cpp"),
    "SFE accepted": (PARTITION_CPP, "|| (codes.sfe != 0U) ", "", "FlexNvmPartitionHostTest.cpp"),
    "key space without load on reset accepted": (PARTITION_CPP,
                                                 " || ((codes.keySize != 0U) && (codes.loadCode != 0U))", "",
                                                 "FlexNvmPartitionHostTest.cpp"),
    "key space on a part without CSEc launched": (PARTITION_CPP, "&& !FlexNvmChip::deviceId().security", "&& false",
                                                  "FlexNvmPartitionHostTest.cpp"),
    "word codes not compared with the plan": (PARTITION_CPP, "|| (codes.partitionCode != plan.partitionCode)",
                                              "|| false", "FlexNvmPartitionHostTest.cpp"),
    "word parser takes lower-case digits": (PARTITION_CPP, "if ((c >= 'A') && (c <= 'F'))",
                                            "if (((c >= 'A') && (c <= 'F')) || ((c >= 'a') && (c <= 'f')))",
                                            "FlexNvmPartitionHostTest.cpp"),
    # NVM-12: data flash driver and the lock between data flash commands and EEPROM stores
    "program flash addressed (bit 23 missing)": (DATA_FLASH_H, "uint32_t const address = DATA_FLASH_SELECT | offset;",
                                                 "uint32_t const address = offset;", "FlexNvmDataFlashHostTest.cpp"),
    "launched while a command runs": (DATA_FLASH_H, "        if (!idle())\n        {\n            resumeAllInterrupts(old);",
                                      "        if (false)\n        {\n            resumeAllInterrupts(old);",
                                      "FlexNvmDataFlashHostTest.cpp"),
    "EEPROM store not checked": (LOCK_H, "return (fcnfg & (FTFC_FCNFG_RAMRDY_MASK | FTFC_FCNFG_EEERDY_MASK)) != 0U;",
                                 "return (static_cast<void>(fcnfg), true);", "FlexNvmDataFlashHostTest.cpp"),
    "launch with interrupts on": (DATA_FLASH_H, "uint32_t const old     = getMachineStateRegisterValueAndSuspendAllInterrupts();",
                                  "uint32_t const old     = 0U;", "FlexNvmDataFlashHostTest.cpp"),
    "old error flags not cleared": (DATA_FLASH_H, "FTFC->FSTAT = static_cast<uint8_t>(FTFC_FSTAT_ACCERR_MASK | FTFC_FSTAT_FPVIOL_MASK);",
                                    "", "FlexNvmDataFlashHostTest.cpp"),
    "MGSTAT0 ignored": (DATA_FLASH_H, "FTFC_FSTAT_FPVIOL_MASK | FTFC_FSTAT_MGSTAT0_MASK)) != 0U)",
                        "FTFC_FSTAT_FPVIOL_MASK)) != 0U)", "FlexNvmDataFlashHostTest.cpp"),
    "command never given up": (DATA_FLASH_H, "if (elapsed > _limitUs)", "if (false)", "FlexNvmDataFlashHostTest.cpp"),
    "erase limit of a phrase": (DATA_FLASH_H, "return launch(CMD_ERASE_SECTOR, offset, nullptr, ERASE_SECTOR_LIMIT_US);",
                                "return launch(CMD_ERASE_SECTOR, offset, nullptr, PROGRAM_PHRASE_LIMIT_US);",
                                "FlexNvmDataFlashHostTest.cpp"),
    "cache not invalidated": (DATA_FLASH_H, "            _invalidateCache(); //", "            //",
                              "FlexNvmDataFlashHostTest.cpp"),
    "read during a command": (DATA_FLASH_H, "        if (!flexNvmBlockLock.beginRead())", "        if (false)",
                              "FlexNvmDataFlashHostTest.cpp"),
    "other partition accepted": (DATA_FLASH_H, "(depart != NOT_PARTITIONED) && (depart != _region.partitionCode)",
                                 "(static_cast<void>(depart), false)", "FlexNvmDataFlashHostTest.cpp"),
    "phrase alignment not checked": (DATA_FLASH_H, "if ((offset % PHRASE_BYTES) != 0U)", "if (false)",
                                     "FlexNvmDataFlashHostTest.cpp"),
    "EEPROM store during a data flash command": (
        EEPROM_H, "if (((FTFC->FSTAT & FTFC_FSTAT_CCIF_MASK) != 0U) && flexNvmBlockLock.storeAllowed())",
        "if (flexNvmBlockLock.storeAllowed())", "FlexNvmDataFlashHostTest.cpp"),
    "EEPROM store waits forever for a command": (EEPROM_H, "else if ((now - waitStart) > COMMAND_WAIT_LIMIT_US)",
                                                 "else if ((now - waitStart) > (COMMAND_WAIT_LIMIT_US + 0xF0000000U))",
                                                 "FlexNvmDataFlashHostTest.cpp"),
    "EEPROM wait for a command not counted as time-out": (
        EEPROM_H, "COMMAND_WAIT_LIMIT_US)\n            {\n                ++_timeouts;",
        "COMMAND_WAIT_LIMIT_US)\n            {\n", "FlexNvmDataFlashHostTest.cpp"),
    # NVM-25: the block lock (safety review NVM12-S1 b)
    "read chunk without idle check": (
        DATA_FLASH_H, "            if (!idle())\n            {\n                resumeAllInterrupts(old);\n"
                      "                flexNvmBlockLock.endRead();",
        "            if (false)\n            {\n                resumeAllInterrupts(old);\n"
        "                flexNvmBlockLock.endRead();", "FlexNvmDataFlashHostTest.cpp"),
    "read chunk with interrupts on": (
        DATA_FLASH_H, "            uint32_t const old = getMachineStateRegisterValueAndSuspendAllInterrupts();\n"
                      "            if (!idle())",
        "            uint32_t const old = 0U;\n            if (!idle())", "FlexNvmDataFlashHostTest.cpp"),
    "read keeps no count (launch possible during a read)": (
        DATA_FLASH_H, "        if (!flexNvmBlockLock.beginRead())", "        if (!FlexNvmBlockLock::blockIdle())",
        "FlexNvmDataFlashHostTest.cpp"),
    "read count left after a busy chunk": (
        DATA_FLASH_H, "                flexNvmBlockLock.endRead();\n                return ::dataflash::Status::BUSY;",
        "                return ::dataflash::Status::BUSY;", "FlexNvmDataFlashHostTest.cpp"),
    "launch ignores the lock": (DATA_FLASH_H, "if (!flexNvmBlockLock.launchAllowed())", "if (false)",
                                "FlexNvmDataFlashHostTest.cpp"),
    "held-off launches not counted": (DATA_FLASH_H, "            ++_heldOff;", "", "FlexNvmDataFlashHostTest.cpp"),
    "hold without usable data flash": (DATA_FLASH_H, "        if (state() != State::READY)\n        {\n            return false;\n"
                                                     "        }\n        // NVM-29: the reader",
                                       "        // NVM-29: the reader", "FlexNvmDataFlashHostTest.cpp"),
    # NVM-29: FMC buffers of the FlexNVM after a command (board 2026-10-01: stale read-back)
    "FMC buffers never flushed": (DATA_FLASH_H, "        _buffersStale = true; //", "        //",
                                  "FlexNvmDataFlashHostTest.cpp"),
    "flush without the two reads": (DATA_FLASH_H, "        static_cast<void>(readFlexNvm(_region.start));\n"
                                                  "        static_cast<void>(readFlexNvm(_region.start + BUFFER_ENTRY_MAX_BYTES));",
                                    "", "FlexNvmDataFlashHostTest.cpp"),
    "flush reads the same word twice": (DATA_FLASH_H, "readFlexNvm(_region.start + BUFFER_ENTRY_MAX_BYTES)",
                                        "readFlexNvm(_region.start)", "FlexNvmDataFlashHostTest.cpp"),
    "speculation buffer not cleared": (DATA_FLASH_H, "= saved | MSCM_OCMDR_OCM1_MASK;", "= saved;",
                                       "FlexNvmDataFlashHostTest.cpp"),
    "speculation not restored": (DATA_FLASH_H,
                                 "        if (cleared)\n        {\n            IP_MSCM->OCMDR[MSCM_DATA_FLASH_DESCRIPTOR] = saved;",
                                 "        if (cleared)\n        {\n", "FlexNvmDataFlashHostTest.cpp"),
    "no flush at the end of a command": (DATA_FLASH_H, "        flushAfterCommand();", "",
                                         "FlexNvmDataFlashHostTest.cpp"),
    "flush while the block is busy": (DATA_FLASH_H, "if (_buffersStale && idle())", "if (_buffersStale)",
                                      "FlexNvmDataFlashHostTest.cpp"),
    "no flush before a read": (DATA_FLASH_H, "                flushReadBuffers(); // NVM-29: the end", "                // the end",
                               "FlexNvmDataFlashHostTest.cpp"),
    "no flush before a hold": (DATA_FLASH_H, "            flushReadBuffers(); // the hold was granted", "            // ",
                               "FlexNvmDataFlashHostTest.cpp"),
    "OCMDR of another memory written": (DATA_FLASH_H, "== OCMT_DATA_FLASH)", "!= 7U)", "FlexNvmDataFlashHostTest.cpp"),
    "launch ignores reads": (LOCK_H, "return (_holds == 0U) && (_reads == 0U);", "return _holds == 0U;",
                             "FlexNvmDataFlashHostTest.cpp"),
    "launch ignores holds": (LOCK_H, "return (_holds == 0U) && (_reads == 0U);", "return _reads == 0U;",
                             "FlexNvmDataFlashHostTest.cpp"),
    "store ignores holds": (LOCK_H, "bool storeAllowed() const { return _holds == 0U; }",
                            "bool storeAllowed() const { return true; }", "FlexNvmDataFlashHostTest.cpp"),
    "hold granted while the block is busy": (LOCK_H, "bool const ok      = blockIdle() && (_holds < MAX_COUNT);",
                                             "bool const ok      = (_holds < MAX_COUNT);",
                                             "FlexNvmDataFlashHostTest.cpp"),
    "read begun while the block is busy": (LOCK_H, "bool const ok      = blockIdle() && (_reads < MAX_COUNT);",
                                           "bool const ok      = (_reads < MAX_COUNT);",
                                           "FlexNvmDataFlashHostTest.cpp"),
    "holds without upper bound (wrap)": (LOCK_H, "(_holds < MAX_COUNT)", "true", "FlexNvmDataFlashHostTest.cpp"),
    "release does not release": (LOCK_H, "            --_holds;", "", "FlexNvmDataFlashHostTest.cpp"),
    "release below zero wraps": (LOCK_H, "        if (_holds != 0U)\n        {\n            --_holds;",
                                 "        if (true)\n        {\n            --_holds;", "FlexNvmDataFlashHostTest.cpp"),
    "refused holds not counted": (LOCK_H, "            ++_refusedHolds;", "", "FlexNvmDataFlashHostTest.cpp"),
    "EEPROM store ignores the hold": (
        EEPROM_H, "if (((FTFC->FSTAT & FTFC_FSTAT_CCIF_MASK) != 0U) && flexNvmBlockLock.storeAllowed())",
        "if ((FTFC->FSTAT & FTFC_FSTAT_CCIF_MASK) != 0U)", "FlexNvmDataFlashHostTest.cpp"),
    "hold waits not counted": (EEPROM_H, "_holdWaits += held ? 1U : 0U;", "static_cast<void>(held);",
                               "FlexNvmDataFlashHostTest.cpp"),
    "word parser takes a trailing field": (PARTITION_CPP, "if (*w != '\\0')\n    {\n        return false;",
                                           "if (false)\n    {\n        return false;", "FlexNvmPartitionHostTest.cpp"),
}


class MutationTest(unittest.TestCase):
    def test_mutations_are_detected(self):
        work = Path(tempfile.mkdtemp())
        try:
            survivors = []
            for i, (name, (rel, old, new, test)) in enumerate(MUTATIONS.items()):
                copy = work / f"m{i}"
                shutil.copytree(MODULE / "include", copy / "include")
                shutil.copytree(MODULE / "src", copy / "src")
                path = copy / rel
                text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
                self.assertEqual(text.count(old), 1, f"{name}: {old!r} found {text.count(old)} times")
                path.write_text(text.replace(old, new), encoding="utf-8")
                ok, out, exe = build(copy, test, copy)
                self.assertTrue(ok, f"mutant '{name}' does not compile (fault of this script):\n{out[-2000:]}")
                passed, _ = run(exe)
                if passed:
                    survivors.append(name)
            print(f"\n{len(MUTATIONS) - len(survivors)} of {len(MUTATIONS)} mutants detected", file=sys.stderr)
            self.assertEqual(survivors, [])
        finally:
            shutil.rmtree(work, ignore_errors=True)


def code_of(path: Path) -> str:
    """C++ without comments and string literals."""
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return "\n".join(re.sub(r'"(?:\\.|[^"\\])*"', '""', line.split("//")[0]) for line in text.splitlines())


class GuardTest(unittest.TestCase):
    def test_eeprom_driver_launches_no_flash_command(self):
        code = code_of(MODULE / EEPROM_H)
        for forbidden in ("FCCOB", "PGMPART", "SETRAM", "0x80U", "0x81U"):
            self.assertNotIn(forbidden, code)
        # NVM-12: CCIF only read (a store waits for the end of a data flash command), never written; NVM-25: in the
        # same check as the block lock
        self.assertEqual([line.strip() for line in code.splitlines() if "CCIF" in line],
                         ["if (((FTFC->FSTAT & FTFC_FSTAT_CCIF_MASK) != 0U) && flexNvmBlockLock.storeAllowed())"])
        self.assertIsNone(re.search(r"EepromDriver", code))
        writes = [line.strip() for line in code.splitlines() if re.search(r"FSTAT\s*=[^=]", line)]
        self.assertEqual(writes, ["FTFC->FSTAT = FTFC_FSTAT_RDCOLERR_MASK;"])
        self.assertIn("::bsp::BspReturnCode init() override { return ::bsp::BSP_ERROR; }", code)

    def test_data_flash_driver_only_erases_and_programs_the_data_flash(self):
        """NVM-12: Erase Flash Sector and Program Phrase only, always with address bit 23 (data flash) - never PGMPART,
        SETRAM, an erase of a block or of all blocks, and never an address of the program flash."""
        code = code_of(MODULE / DATA_FLASH_H)
        self.assertEqual(re.findall(r"CMD_\w+\s*=\s*(0x[0-9A-Fa-f]+U)", code), ["0x07U", "0x09U"])
        self.assertEqual([line.strip() for line in code.splitlines() if "FCCOB[3]" in line],
                         ["FTFC->FCCOB[3] = command;"])
        self.assertEqual(code.count("launch(CMD_"), 2)
        self.assertIn("uint32_t const address = DATA_FLASH_SELECT | offset;", code)
        self.assertIn("DATA_FLASH_SELECT = 0x800000U;", code)
        for forbidden in ("PGMPART", "SETRAM", "0x80U", "0x81U", "0x08U", "0x44U", "0x49U", "EepromDriver"):
            self.assertNotIn(forbidden, code)

    def test_block_lock_only_reads_registers(self):
        """NVM-25: the lock reads FSTAT/FCNFG and changes nothing in the hardware; every count change is made with
        interrupts suspended; one instance per image; the drivers decide with it inside their suspended sections."""
        code = code_of(MODULE / LOCK_H)
        self.assertIsNone(re.search(r"(?<![A-Za-z])\w*->\w+(\[[^]]*\])?\s*=[^=]", code), "the lock writes no register")
        for forbidden in ("FCCOB", "PGMPART", "SETRAM"):
            self.assertNotIn(forbidden, code)
        for name in ("beginRead", "endRead", "tryHold", "release"):
            body = re.search(r"\b" + name + r"\(\)\s*\{(.*?)\n    \}", code, re.S).group(1)
            self.assertIn("getMachineStateRegisterValueAndSuspendAllInterrupts()", body, name)
            self.assertIn("resumeAllInterrupts(old);", body, name)
        self.assertIn("inline FlexNvmBlockLock flexNvmBlockLock;", code)
        data_flash = code_of(MODULE / DATA_FLASH_H)
        launch = data_flash[data_flash.index("launch(uint8_t const command"):]
        self.assertLess(launch.index("getMachineStateRegisterValueAndSuspendAllInterrupts()"),
                        launch.index("flexNvmBlockLock.launchAllowed()"))
        self.assertLess(launch.index("flexNvmBlockLock.launchAllowed()"), launch.index("FTFC->FSTAT  ="))

    def test_chip_view_launches_no_flash_command(self):
        # NVM-10: the S32K1 has no Read Resource; the chip view reads registers only (SIM FCFG1/SDID, FTFC FCNFG)
        code = code_of(MODULE / CHIP_H)
        for forbidden in ("FCCOB", "FSTAT", "CCIF", "PGMPART", "SETRAM"):
            self.assertNotIn(forbidden, code)
        self.assertIsNone(re.search(r"(?<![A-Za-z])\w*->\w+\s*=[^=]", code), "the chip view writes no register")

    def test_start_up_never_partitions(self):
        for rel in ("src/lifecycle/StaticBsp.cpp", "src/main.cpp", "src/integration/Integration.cpp"):
            code = code_of(PORT / rel)
            for forbidden in ("FlexNvmPartition", "PGMPART", "FCCOB"):
                self.assertNotIn(forbidden, code, rel)
            self.assertIsNone(re.search(r"EepromDriver", code), rel)

    def test_partition_only_from_the_console_command_of_a_test_build(self):
        callers = []
        for base in ("platforms", "ecu", "libs"):
            for path in (ROOT / base).rglob("*.cpp"):
                if "test" in path.relative_to(ROOT).parts or path == MODULE / PARTITION_CPP:
                    continue
                if "FlexNvmPartition::partition(" in code_of(path):
                    callers.append(path.relative_to(ROOT).as_posix())
        self.assertEqual(callers, ["platforms/s32k1xx/port/main/src/systems/FlexNvmCommand.cpp"])
        command = (PORT / "src" / "systems" / "FlexNvmCommand.cpp").read_text(encoding="utf-8")
        call = command.index("FlexNvmPartition::partition(PLAN, word)")
        guard = command.rindex("#ifdef NX_FLEXNVM_PARTITION_COMMAND", 0, call)
        self.assertNotIn("#endif", command[guard:command.index("if (idx == ID_PARTITION)")])
        cmake = (PORT / "CMakeLists.txt").read_text(encoding="utf-8")
        self.assertRegex(cmake, r'option\(NX_FLEXNVM_PARTITION_COMMAND\s+"[^"]*"\s+OFF\)')
        link = cmake.index("target_link_libraries(main PRIVATE bspFlexNvmPartition)")
        block = cmake[cmake.rindex("if (NX_FLEXNVM_PARTITION_COMMAND)", 0, link):link]
        self.assertNotIn("endif", block)
        self.assertEqual(cmake.count("bspFlexNvmPartition"), 1)
        module_cmake = (MODULE / "CMakeLists.txt").read_text(encoding="utf-8")
        self.assertIn("add_library(bspFlexNvmPartition EXCLUDE_FROM_ALL", module_cmake)

    def test_confirmation_word_from_the_generator(self):
        command = (PORT / "src" / "systems" / "FlexNvmCommand.cpp").read_text(encoding="utf-8")
        self.assertIn("NX_NVM_PARTITION_CONFIRM};", command)
        self.assertNotRegex(code_of(PORT / "src" / "systems" / "FlexNvmCommand.cpp"), r"PGMPART-")


if __name__ == "__main__":
    unittest.main()
