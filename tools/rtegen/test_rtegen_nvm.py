"""NVM-02: non-volatile blocks - generator (targets nvm.<Block>), NvM core and the SIL test through the RTE.

Own test profile test/nvm (synthetic NvmSwc plus the RTE-04 test model TypeSwc/TypeSink, project
buildtest_s32k148_min without SBC, BSW task profile test/multitask/tasks.yaml), not the eval board.

  RteNvmGenTest       generator: block table, layout, ids, hand-over task, restore of task copies, messages
  (NvM core: libs/bsw/nvm/test/test_nvm_core.py since NVM-19; the application uses libs/bsw/nvm)
  RteNvmHostTest      test/nvm/rte_nvm_host_test.cpp: generated RTE + NvM core over four power cycles
  RteNvmPlaceTest     NVM-07: fixed block places and write policies of project.yaml nvm.blocks
  (FlexNvmEeprom.h moved with NVM-07 to platforms/s32k1xx/bsp/bspFlexNvm, tested there: test/test_flexnvm.py)

Run: .venv/Scripts/python -m unittest tools/rtegen/test_rtegen_nvm.py
"""

import contextlib
import copy
import dataclasses
import io
import re
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

import rtegen  # noqa: E402

NVM_DIR = HERE / "test" / "nvm"
TYPES = HERE / "test" / "types"
MULTI_TASKS = HERE / "test" / "multitask" / "tasks.yaml"
PROJECT = rtegen.nxconfig.load(REPO / "projects" / "buildtest_s32k148_min" / "project.yaml")
# PLAT-62: the own full-featured test profile (synthetic testboard_full/testproject_full) instead of the eval board
# project, which stays private (PLAT-46 F10) - a test against it would be red in the exported open tree. What
# proves something about the eval board itself uses EVAL_DEMO_FILE and is skipped where it is missing.
DEMO_FILE = REPO / "tools" / "nxconfig" / "test" / "profiles" / "projects" / "testproject_full" / "project.yaml"
EVAL_DEMO_FILE = REPO / "projects" / "evalboard_s32k144_demo" / "project.yaml"
ECU = rtegen.with_resolved_paths(
    yaml.safe_load((NVM_DIR / "rte.yaml").read_text(encoding="utf-8")), NVM_DIR / "rte.yaml")
SYSTEMS = REPO / "ecu" / "application" / "src" / "systems"
NVM_INCLUDE = REPO / "libs" / "bsw" / "nvm" / "include"
NVM_CORE = NVM_INCLUDE / "nvm" / "Nvm.h"
BSP_INCLUDE = REPO / "external" / "openbsw" / "libs" / "bsw" / "bsp" / "include"
ZIG = [sys.executable, "-m", "ziglang"]
CXX_FLAGS = ["-std=c++17", "-O1", "-Wall", "-Wextra", "-Wconversion", "-Wno-sign-conversion", "-Werror"]


def compile_cxx(sources, includes, exe: Path, defines=()):
    return subprocess.run(ZIG + ["c++", *CXX_FLAGS, *defines, *[f"-I{i}" for i in includes],
                                 "-isystem", str(BSP_INCLUDE), *map(str, sources), "-o", str(exe)],
                          capture_output=True, text=True)


class RteNvmGenTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_with(self, ecu=None, project=PROJECT, out="out"):
        path = self.tmp / "rte.yaml"
        path.write_text(yaml.safe_dump(ecu or ECU, sort_keys=False), encoding="utf-8")
        with contextlib.redirect_stderr(io.StringIO()) as err:
            result = rtegen.generate(path, project, self.tmp / out, REPO, MULTI_TASKS)
        self.stderr = err.getvalue()
        return result

    def expect_error(self, fragment, ecu):
        with self.assertRaises(rtegen.RteError) as ctx:
            self.run_with(ecu)
        self.assertIn(fragment, str(ctx.exception))
        return str(ctx.exception)

    def ecu(self, **connections):
        ecu = copy.deepcopy(ECU)
        ecu["connections"].update(connections)
        return ecu

    def generated(self, name="RteConfig.cpp", out="out"):
        return (self.tmp / out / name).read_text(encoding="utf-8")

    def test_crc_is_the_one_of_the_core(self):
        self.assertEqual(rtegen.crc16_ccitt(b"123456789"), 0x29B1)   # nvm::crc16, NvmHostTest testCrc
        self.assertEqual([rtegen.nvm_slot_bytes(n) for n in (3, 4, 8, 9)], [12, 12, 16, 20])
        self.assertIn("constexpr size_t RECORD_OVERHEAD = RECORD_HEADER + 2U;", NVM_CORE.read_text(encoding="utf-8"))
        self.assertIn("constexpr size_t RECORD_HEADER   = 6U;", NVM_CORE.read_text(encoding="utf-8"))
        self.assertEqual(rtegen.NVM_RECORD_OVERHEAD, 8)

    def test_blocks_of_the_profile(self):
        _, buffers, tasks = self.run_with()
        self.assertEqual({b.nvm for b in buffers.values() if b.nvm}, {"BootCount", "Trim", "Stat", "Scaled"})
        # the reader of a block reads the buffer of the writer
        self.assertEqual(buffers["NvmSwc.Count.Count"].links, ["nvm.BootCount"])
        header = self.generated("RteConfig.h")
        self.assertIn("#define NX_RTE_HAS_NVM 1", header)
        self.assertIn("constexpr size_t NVM_BLOCK_COUNT    = 4U;", header)
        self.assertIn("constexpr uint32_t NVM_WINDOW_START = 0x14000000U;", header)   # FLEXRAM of the S32K148
        self.assertIn("constexpr uint32_t NVM_WINDOW_SIZE  = 4096U;", header)
        config = self.generated()
        # name order, two slots each: BootCount 4 B (2 x 12), Scaled 8 B (2 x 16), Stat 16 B (2 x 24), Trim 2 B
        for row in ('{"BootCount", 0x', ', 4U, 0U, &nvmImage_BootCount, &nvmDefault_BootCount, &nvmChanged[0], '
                    'NvmWrite::SHUTDOWN, 0U, NvmIntegrity::QM, &nvmQuality[0]}',
                    ', 8U, 24U, &nvmImage_Scaled,', ', 16U, 56U, &nvmImage_Stat,', ', 2U, 104U, &nvmImage_Trim,'):
            self.assertIn(row, config)
        self.assertIn("constexpr uint32_t NVM_LAYOUT_BYTES = 128U;", header)
        # default = init value of the writing port (ARXML), also for records and arrays
        self.assertIn("uint16 const nvmDefault_Trim = 500;", config)
        self.assertIn("TypeSwc_StatBus const nvmDefault_Stat = {0, 0, 0, {0, 0, {0, 0}, 0}};", config)
        self.assertIn('static_assert(sizeof(nvmImage_Stat) == 16U, "nvm.Stat: size of the generated layout");',
                      config)
        # restore reaches the published value and every task copy (the record is read in the slow task)
        restore = config.split("void nvmRestore()")[1].split("\n}\n")[0]
        for name in ("Rte_Buf_TypeSwc_Stat_Stat", "Rte_Cpy_fast_TypeSwc_Stat_Stat", "Rte_Cpy_slow_TypeSwc_Stat_Stat",
                     "Rte_Buf_NvmSwc_Count_Count", "Rte_Buf_NvmSwc_TrimOut_TrimOut"):
            self.assertIn(f"std::memcpy(&{name}, &nvmImage_", restore)
        # hand-over at the end of the writing task (fast), after the task published its copies, and after init
        fast = config.split("void writeOutputs_fast()")[1].split("void readInputs_slow()")[0]
        self.assertLess(fast.index("publish the values of this cycle"), fast.index("nvmHandOver(&Rte_Buf_TypeSwc_Stat"))
        slow = config.split("void writeOutputs_slow()")[1].split("} // namespace")[0]
        self.assertNotIn("nvmHandOver", slow)
        init = config.split("\nvoid init()")[1]
        self.assertIn("nvmHandOver(&Rte_Buf_NvmSwc_Count_Count, &nvmImage_BootCount", init)
        self.assertEqual(init.count("::async::LockType const lock;"), 1)   # under the lock of the task copies
        # signal table shows the connection
        self.assertIn('{"NvmSwc.Count.Count", "nvm.BootCount", SignalType::UINT32', config)
        # NVM-18: the profile defines no block in project.yaml - each one gets the proposed definition (migration)
        self.assertEqual(self.stderr.count("has no definition in project.yaml nvm.blocks (NVM-18)"), 4)
        self.assertIn("Define the block: nvm.blocks.BootCount: {type: uint32, integrity: qm}", self.stderr)
        self.assertIn("Define the block: nvm.blocks.Scaled: {type: uint16, length: 4, integrity: qm}", self.stderr)
        self.assertIn("Define the block: nvm.blocks.Stat: {integrity: qm}", self.stderr)

    def test_ids_depend_on_name_and_type(self):
        _, buffers, _ = self.run_with()
        stat = buffers["TypeSwc.Stat.Stat"].dtype
        count = buffers["NvmSwc.Count.Count"].dtype
        self.assertNotEqual(rtegen.nvm_block_id("Stat", stat), rtegen.nvm_block_id("Stat2", stat))
        self.assertNotEqual(rtegen.nvm_block_id("X", stat), rtegen.nvm_block_id("X", count))
        self.assertEqual(rtegen.nvm_block_id("X", count), rtegen.nvm_block_id("X", count))
        ids = [rtegen.nvm_block_id(f"B{i}", count) for i in range(3000)]
        self.assertFalse({0x0000, 0xFFFF} & set(ids))

    def test_hand_over_task_is_the_writer_task(self):
        ecu = self.ecu()
        ecu["tasks"][0]["runnables"] = ["TypeSwc.Run10ms", "TypeSink.Sink10ms"]
        ecu["tasks"][1]["runnables"] = ["TypeSink.Sink20ms", "NvmSwc.Run10ms"]
        ecu["tasks"][1]["period_ms"] = 10
        self.run_with(ecu)
        config = self.generated()
        slow = config.split("void writeOutputs_slow()")[1].split("} // namespace")[0]
        self.assertIn("nvmHandOver(&Rte_Buf_NvmSwc_TrimOut_TrimOut", slow)
        # BootCount is written by the init runnable only; its port is accessed in the slow task by Run10ms
        self.assertIn("nvmHandOver(&Rte_Buf_NvmSwc_Count_Count", slow)

    def test_without_nvm_targets_nothing_changes(self):
        """A project without nvm.* targets and without DTCs (the demo project without its diagnostic description)
        gets no NvM code at all. With its DTCs (NVM-03) the NvM carries only the blocks of the fault memory."""
        demo = rtegen.nxconfig.load(DEMO_FILE)
        # NVM-05: the demo stores its ECU statistics (nvm.blocks.EcuStatistics) - without that entry here
        stats = demo.project.nvm.blocks.pop("EcuStatistics", None)
        with contextlib.redirect_stderr(io.StringIO()):
            rtegen.generate(demo.rte_file, demo, self.tmp / "demo", REPO, diag=None)
        self.assertNotIn("NX_RTE_HAS_NVM", self.generated("RteConfig.h", "demo"))
        config = self.generated("RteConfig.cpp", "demo")
        for text in ("NVM-02", "nvmImage", "nvmHandOver", "nvmRestore"):
            self.assertNotIn(text, config)
        with contextlib.redirect_stderr(io.StringIO()):
            rtegen.generate(demo.rte_file, demo, self.tmp / "dem", REPO)
        self.assertIn("constexpr size_t NVM_BLOCK_COUNT    = 2U;", self.generated("RteConfig.h", "dem"))
        config = self.generated("RteConfig.cpp", "dem")
        self.assertIn('{"DemEvents", 0x', config)
        self.assertNotIn("nvmHandOver", config)          # no block of a component
        # NVM-05: with its entry the statistics block follows the blocks of the fault memory
        self.assertIsNotNone(stats)
        demo.project.nvm.blocks["EcuStatistics"] = stats
        with contextlib.redirect_stderr(io.StringIO()):
            rtegen.generate(demo.rte_file, demo, self.tmp / "stats", REPO)
        self.assertIn("constexpr size_t NVM_BLOCK_COUNT    = 3U;", self.generated("RteConfig.h", "stats"))
        self.assertIn('{"EcuStatistics", 0x', self.generated("RteConfig.cpp", "stats"))

    def test_block_without_writer(self):
        ecu = self.ecu(**{"NvmSwc.Count.Count": "rte"})
        self.expect_error("nvm.BootCount is read by NvmSwc.StoredCount.StoredCount, but no provided port writes it",
                          ecu)

    def test_two_writers(self):
        ecu = self.ecu(**{"NvmSwc.TrimOut.TrimOut": "nvm.BootCount"})
        # TrimOut is uint16: the second writer is refused before any type check
        self.expect_error("nvm.BootCount is already written by NvmSwc.Count.Count", ecu)

    def test_one_port_two_blocks(self):
        ecu = self.ecu(**{"NvmSwc.TrimOut.TrimOut": ["nvm.Trim", "nvm.Trim2"]})
        self.expect_error("already stored as nvm.Trim; a port writes one block", ecu)

    def test_reader_of_other_type(self):
        ecu = self.ecu(**{"NvmSwc.WideTrim.WideTrim": "nvm.Trim"})
        message = self.expect_error("connection NvmSwc.WideTrim.WideTrim -> nvm.Trim: type uint32", ecu)
        self.assertIn("does not match uint16", message)
        self.assertIn("NvmSwc.TrimOut.TrimOut", message)

    def test_record_reader_must_match(self):
        ecu = self.ecu(**{"TypeSink.Stat.Stat": "nvm.Scaled"})
        self.expect_error("connection TypeSink.Stat.Stat -> nvm.Scaled: type TypeSwc_StatBus", ecu)

    def test_block_names(self):
        for bad in ("nvm.", "nvm.1Count", "nvm.Boot.Count", "nvm.Boot-Count", "nvm." + "X" * 33):
            with self.subTest(bad=bad):
                ecu = self.ecu(**{"NvmSwc.Count.Count": bad, "NvmSwc.StoredCount.StoredCount": "rte.init"})
                self.expect_error("expected nvm.<Block> with a block name", ecu)

    def test_block_nobody_reads_is_a_warning(self):
        ecu = self.ecu(**{"NvmSwc.StoredCount.StoredCount": "rte.init"})
        self.run_with(ecu)
        self.assertIn("nvm.BootCount is written by NvmSwc.Count.Count but no required port reads it", self.stderr)

    def test_layout_larger_than_the_window(self):
        project = copy.deepcopy(PROJECT)
        region = next(r for r in project.derivative.memory if r.kind == "eeprom_ram")
        project.derivative.memory = [r if r is not region else r.model_copy(update={"size": 100})
                                     for r in project.derivative.memory]
        with self.assertRaises(rtegen.RteError) as ctx:
            self.run_with(project=project)
        self.assertIn("the NvM blocks need 128 bytes", str(ctx.exception))
        self.assertIn("(FLEXRAM) has 100", str(ctx.exception))

    def test_derivative_without_eeprom_window(self):
        project = copy.deepcopy(PROJECT)
        project.derivative.memory = [r for r in project.derivative.memory if r.kind != "eeprom_ram"]
        self.run_with(project=project)
        header = self.generated("RteConfig.h")
        self.assertIn("constexpr uint32_t NVM_WINDOW_START = 0x00000000U;", header)
        self.assertIn("constexpr uint32_t NVM_WINDOW_SIZE  = 0U;", header)

    def test_id_collision(self):
        original = rtegen.nvm_block_id
        try:
            rtegen.nvm_block_id = lambda name, dtype: 0x1234
            self.expect_error("get the same record id 0x1234", self.ecu())
        finally:
            rtegen.nvm_block_id = original


class RteNvmHostTest(unittest.TestCase):
    """Generated RTE of test/nvm with the NvM core over four power cycles (virtual EEPROM in a file)."""

    def test_power_cycles(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            out = tmp / "gen"
            with contextlib.redirect_stderr(io.StringIO()):
                rtegen.generate(NVM_DIR / "rte.yaml", PROJECT, out, REPO, MULTI_TASKS)
            swc = [NVM_DIR / "NvmSwc/generated/src", TYPES / "TypeSwc/generated/src", TYPES / "TypeSink/generated/src"]
            includes = [out, out / "tasks", REPO / "libs/bsw/autosarBase/include", HERE / "test" / "can_host" / "stubs",
                        NVM_DIR / "stubs", *swc, SYSTEMS, SYSTEMS / "test" / "host", NVM_INCLUDE]
            objects = []
            for src in (out / "Rte.c", swc[0] / "NvmSwc.c", swc[1] / "TypeSwc.c", swc[2] / "TypeSink.c"):
                obj = tmp / (src.stem + ".o")
                r = subprocess.run(ZIG + ["cc", "-std=c99", "-O1", "-Wall", "-Wextra", "-Werror",
                                          *[f"-I{i}" for i in includes], "-c", str(src), "-o", str(obj)],
                                   capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr[-3000:])
                objects.append(obj)
            exe = tmp / "rte_nvm_host_test.exe"
            r = compile_cxx([out / "RteConfig.cpp", NVM_DIR / "rte_nvm_host_test.cpp", *objects], includes, exe)
            self.assertEqual(r.returncode, 0, r.stderr[-3000:])
            image = tmp / "eeprom.bin"
            outputs = []
            for run in ("1", "2"):
                result = subprocess.run([str(exe), run, str(image)], capture_output=True, text=True)
                outputs.append(result.stdout.strip())
                self.assertEqual(result.returncode, 0, result.stdout[-3000:])
            # damage the newest record of the boot count (slot 1, written by run 2): the next start takes slot 0
            data = bytearray(image.read_bytes())
            self.assertEqual(data[12 + 4], 2)            # sequence 2 in slot 1 of BootCount (address 0, slot 12 B)
            data[12 + 6] ^= 0x40                          # a data bit
            image.write_bytes(bytes(data))
            for run, arg in (("3", str(image)), ("none", "-")):
                result = subprocess.run([str(exe), run, arg], capture_output=True, text=True)
                outputs.append(result.stdout.strip())
                self.assertEqual(result.returncode, 0, result.stdout[-3000:])
            print("\n" + "\n".join(outputs), file=sys.stderr)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class RteNvmPlaceTest(unittest.TestCase):
    """NVM-07: project.yaml nvm.blocks - a fixed place stays when blocks are added, write policy per block."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def config_file(self, name):
        return (self.tmp / "out" / name).read_text(encoding="utf-8")

    def run_with(self, blocks, ecu=ECU):
        config = dataclasses.replace(PROJECT, project=PROJECT.project.model_copy(
            update={"nvm": rtegen.nxmodel.NvmUse(blocks={k: rtegen.nxmodel.NvmBlockUse(**v)
                                                         for k, v in blocks.items()})}))
        path = self.tmp / "rte.yaml"
        path.write_text(yaml.safe_dump(ecu, sort_keys=False), encoding="utf-8")
        with contextlib.redirect_stderr(io.StringIO()):
            return rtegen.generate(path, config, self.tmp / "out", REPO, MULTI_TASKS)

    def test_fixed_place_and_policy(self):
        self.run_with({"Trim": {"address": 0x200}, "BootCount": {"write": "change", "min_interval_ms": 500}})
        config = self.config_file("RteConfig.cpp")
        self.assertIn(", 2U, 512U, &nvmImage_Trim,", config)                    # fixed place
        self.assertIn(", 8U, 24U, &nvmImage_Scaled,", config)                    # name order around it
        self.assertIn("NvmWrite::CHANGE, 500U, NvmIntegrity::QM,", config)       # BootCount
        self.assertIn("constexpr uint32_t NVM_LAYOUT_BYTES = 536U;", self.config_file("RteConfig.h"))

    def test_added_block_does_not_move_a_fixed_one(self):
        self.run_with({"Trim": {"address": 0x200}})
        before = self.config_file("RteConfig.cpp")
        ecu = copy.deepcopy(ECU)
        ecu["connections"]["TypeSwc.TempOut.TempOut"] = "nvm.AAA"
        self.run_with({"Trim": {"address": 0x200}}, ecu)
        after = self.config_file("RteConfig.cpp")
        self.assertIn(", 2U, 512U, &nvmImage_Trim,", before)
        self.assertIn(", 2U, 512U, &nvmImage_Trim,", after)

    def test_overlap_is_an_error(self):
        with self.assertRaises(rtegen.RteError) as ctx:
            self.run_with({"Trim": {"address": 0x10}, "Stat": {"address": 0x18}})
        self.assertIn("overlaps", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
