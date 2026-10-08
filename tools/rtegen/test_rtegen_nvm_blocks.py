"""NVM-18: defined NvM blocks - generator (definitions against the connections, ASIL layout and ids, quality target
nvm.qlf.<Block>, migration warning) and the SIL test through the RTE with the NvM core of libs/bsw/nvm.

Own test profile test/nvm (synthetic NvmSwc plus the RTE-04 test model TypeSwc/TypeSink, project
buildtest_s32k148_min without SBC, BSW task profile test/multitask/tasks.yaml) with block definitions set in the
test, not the eval board.

  RteNvmDefinitionTest  definitions checked against the writing port (type, length, record), YAML path of the
                        messages, migration warning with the proposed definition
  RteNvmAsilLayoutTest  ASIL places (two copies with CRC-32 and counter) in the layout, table and ids; fault memory
                        blocks with integrity
  RteNvmQualityTest     nvm.qlf.<Block>: buffer, restore with the conversion nvm::Quality -> QLF_*, errors
  RteNvmContractTest    numbers shared with libs/bsw/nvm/include/nvm/Nvm.h (quality, overheads)
  RteNvmAsilHostTest    test/nvm/rte_nvm_asil_host_test.cpp: generated RTE + libs/bsw/nvm over five power cycles,
                        with a repaired copy, both copies lost and no memory

Run: .venv/Scripts/python -m unittest tools/rtegen/test_rtegen_nvm_blocks.py
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
NVM_LIB = REPO / "libs" / "bsw" / "nvm"
CORE = NVM_LIB / "include" / "nvm" / "Nvm.h"
BSP_INCLUDE = REPO / "external" / "openbsw" / "libs" / "bsw" / "bsp" / "include"
ZIG = [sys.executable, "-m", "ziglang"]
CXX_FLAGS = ["-std=c++17", "-O1", "-Wall", "-Wextra", "-Wconversion", "-Wno-sign-conversion", "-Werror"]

# the definitions of the profile: a scalar and a vector with ASIL, a scalar QM, the record with its integrity only
DEFINED = {"BootCount": {"type": "uint32", "integrity": "asil_b"},
           "Scaled": {"type": "uint16", "length": 4, "integrity": "asil_d"},
           "Trim": {"type": "uint16"},
           "Stat": {"integrity": "qm"}}


def with_blocks(config, blocks: dict):
    use = rtegen.nxmodel.NvmUse(blocks={k: rtegen.nxmodel.NvmBlockUse(**v) for k, v in blocks.items()})
    return dataclasses.replace(config, project=config.project.model_copy(update={"nvm": use}))


class Generator(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_with(self, blocks=None, ecu=None, **connections):
        ecu = copy.deepcopy(ecu or ECU)
        ecu["connections"].update(connections)
        path = self.tmp / "rte.yaml"
        path.write_text(yaml.safe_dump(ecu, sort_keys=False), encoding="utf-8")
        config = with_blocks(PROJECT, DEFINED if blocks is None else blocks)
        with contextlib.redirect_stderr(io.StringIO()) as err:
            result = rtegen.generate(path, config, self.tmp / "out", REPO, MULTI_TASKS)
        self.stderr = err.getvalue()
        return result

    def expect_error(self, fragment, blocks=None, **connections):
        with self.assertRaises(rtegen.RteError) as ctx:
            self.run_with(blocks, **connections)
        self.assertIn(fragment, str(ctx.exception))
        return str(ctx.exception)

    def generated(self, name="RteConfig.cpp"):
        return (self.tmp / "out" / name).read_text(encoding="utf-8")


class RteNvmDefinitionTest(Generator):
    def test_defined_profile_has_no_migration_warning(self):
        self.run_with()
        self.assertNotIn("NVM-18", self.stderr)
        self.assertNotIn("warning", self.stderr)

    def test_type_must_match(self):
        message = self.expect_error("connection NvmSwc.TrimOut.TrimOut -> nvm.Trim: type uint16 of the writing port "
                                    "does not match the definition project.yaml nvm.blocks.Trim (sint16)",
                                    dict(DEFINED, Trim={"type": "sint16"}))
        self.assertIn("change the definition", message)
        # the prefix gives the YAML path of the connection (TOOL-07)
        found = rtegen.rte_message(message, Path("rte.yaml"))
        self.assertEqual(found.path, ("connections", "NvmSwc.TrimOut.TrimOut"))

    def test_length_must_match(self):
        self.expect_error("does not match the definition project.yaml nvm.blocks.Scaled (uint16[3])",
                          dict(DEFINED, Scaled={"type": "uint16", "length": 3, "integrity": "asil_d"}))
        self.expect_error("does not match the definition project.yaml nvm.blocks.BootCount (uint32[2])",
                          dict(DEFINED, BootCount={"type": "uint32", "length": 2}))
        # an array defined as a scalar
        self.expect_error("nvm.blocks.Scaled (uint16)", dict(DEFINED, Scaled={"type": "uint16"}))

    def test_record_cannot_have_a_type(self):
        self.expect_error("cannot be described by type and length (project.yaml nvm.blocks.Stat names uint8); for a "
                          "record leave type and length out", dict(DEFINED, Stat={"type": "uint8", "length": 16}))

    def test_migration_warning_names_the_proposal(self):
        self.run_with({k: v for k, v in DEFINED.items() if k != "Scaled"})
        self.assertIn("connection TypeSwc.Scaled.Scaled -> nvm.Scaled: nvm.Scaled has no definition in project.yaml "
                      "nvm.blocks (NVM-18); it is stored as ", self.stderr)
        self.assertIn("Define the block: nvm.blocks.Scaled: {type: uint16, length: 4, integrity: qm}", self.stderr)
        self.assertEqual(self.stderr.count("NVM-18"), 1)
        # an entry that names only a place or a policy: the type is proposed, the given integrity kept
        self.run_with(dict(DEFINED, Trim={"integrity": "asil_a", "write": "change", "min_interval_ms": 100}))
        self.assertIn("nvm.Trim names no type in project.yaml nvm.blocks (NVM-18)", self.stderr)
        self.assertIn("nvm.blocks.Trim: {type: uint16, integrity: asil_a}", self.stderr)
        self.assertIn("NvmIntegrity::ASIL", self.generated())

    def test_validate_reports_the_warning_at_the_connection(self):
        blocks = {k: v for k, v in DEFINED.items() if k != "Trim"}
        self.run_with(blocks)
        warning = next(w for w in self.stderr.splitlines() if "nvm.Trim has no definition" in w)
        text = warning[warning.index("connection "):]
        found = rtegen.rte_message(text, Path("rte.yaml"), severity="warning")
        self.assertEqual(found.path, ("connections", "NvmSwc.TrimOut.TrimOut"))


class RteNvmAsilLayoutTest(Generator):
    def test_layout_table_and_ids(self):
        _, buffers, _ = self.run_with()
        header = self.generated("RteConfig.h")
        config = self.generated()
        # name order: BootCount ASIL 4 B (2 x 16), Scaled ASIL 8 B (2 x 20), Stat QM 16 B (2 x 24), Trim QM 2 B (2 x 12)
        self.assertIn(', 4U, 0U, &nvmImage_BootCount, &nvmDefault_BootCount, &nvmChanged[0], NvmWrite::SHUTDOWN, 0U, '
                      'NvmIntegrity::ASIL, &nvmQuality[0]}', config)
        self.assertIn(', 8U, 32U, &nvmImage_Scaled,', config)
        self.assertIn(', 16U, 72U, &nvmImage_Stat,', config)
        self.assertIn(', 2U, 120U, &nvmImage_Trim,', config)
        self.assertIn("NvmIntegrity::QM, &nvmQuality[3]}", config)
        self.assertIn("constexpr uint32_t NVM_LAYOUT_BYTES = 144U;", header)
        self.assertIn("constexpr uint32_t NVM_SLOT_MAX     = 24U;", header)
        self.assertIn("enum class NvmIntegrity : uint8_t", header)
        self.assertIn("uint8_t nvmQuality[4] = {};", config)
        # an ASIL block has another record id than the same block as QM (other format)
        count = buffers["NvmSwc.Count.Count"].dtype
        qm_id = rtegen.nvm_block_id("BootCount", count)
        asil_id = rtegen.nvm_block_id("BootCount", count, asil=True)
        self.assertNotEqual(qm_id, asil_id)
        self.assertIn(f'{{"BootCount", 0x{asil_id:04X}U,', config)
        self.assertIn(f'{{"Trim", 0x{rtegen.nvm_block_id("Trim", buffers["NvmSwc.TrimOut.TrimOut"].dtype):04X}U,',
                      config)
        # without a quality reader no conversion function (it would be unused)
        self.assertNotIn("nvmQlf", config)

    def test_fixed_places_with_asil_sizes(self):
        blocks = dict(DEFINED, Trim={"type": "uint16", "address": 0x100})
        self.run_with(blocks)
        self.assertIn(', 2U, 256U, &nvmImage_Trim,', self.generated())
        with self.assertRaises(rtegen.RteError) as ctx:
            self.run_with(dict(DEFINED, BootCount={"type": "uint32", "integrity": "asil_b", "address": 0},
                               Trim={"type": "uint16", "address": 0x1C}))
        self.assertIn("overlaps BootCount (0x0..0x1F)", str(ctx.exception))

    def test_window_too_small(self):
        project = with_blocks(copy.deepcopy(PROJECT), DEFINED)
        region = next(r for r in project.derivative.memory if r.kind == "eeprom_ram")
        project.derivative.memory = [r if r is not region else r.model_copy(update={"size": 100})
                                     for r in project.derivative.memory]
        path = self.tmp / "rte.yaml"
        path.write_text(yaml.safe_dump(ECU, sort_keys=False), encoding="utf-8")
        with self.assertRaises(rtegen.RteError) as ctx, contextlib.redirect_stderr(io.StringIO()):
            rtegen.generate(path, project, self.tmp / "out", REPO, MULTI_TASKS)
        # with definitions the layout of project.yaml nvm.blocks decides (NVM-07), with the ASIL sizes
        self.assertIn("no room for Stat (48 bytes) in the 100 bytes of emulated EEPROM", str(ctx.exception))

    def test_fault_memory_blocks_with_integrity(self):
        demo = rtegen.nxconfig.load(DEMO_FILE)
        plain = self.tmp / "plain"
        asil = self.tmp / "asil"
        with contextlib.redirect_stderr(io.StringIO()):
            rtegen.generate(demo.rte_file, demo, plain, REPO)
            rtegen.generate(demo.rte_file, with_blocks(demo, {"DemEvents": {"integrity": "asil_b"}}), asil, REPO)
        before = (plain / "RteConfig.cpp").read_text(encoding="utf-8")
        after = (asil / "RteConfig.cpp").read_text(encoding="utf-8")
        row = re.compile(r'\{"DemEvents", (0x[0-9A-F]{4})U, (\d+)U, (\d+)U, .*NvmIntegrity::(\w+), ')
        b, a = row.search(before), row.search(after)
        self.assertEqual((b.group(4), a.group(4)), ("QM", "ASIL"))
        self.assertNotEqual(b.group(1), a.group(1))            # other format, other id
        entries = re.compile(r'\{"DemEntries", 0x[0-9A-F]{4}U, (\d+)U, (\d+)U,')
        size = int(b.group(2))
        # DemEntries follows DemEvents: its address moved by the larger ASIL places
        moved = int(entries.search(after).group(2)) - int(entries.search(before).group(2))
        self.assertEqual(moved, 2 * (((size + 12 + 3) & ~3) - ((size + 8 + 3) & ~3)))


class RteNvmQualityTest(Generator):
    QLF = {"NvmSwc.WideTrim.WideTrim": "nvm.qlf.BootCount"}

    def test_quality_buffer_and_restore(self):
        _, buffers, _ = self.run_with(**self.QLF)
        buffer = buffers["nvm.qlf.BootCount"]
        self.assertEqual((buffer.name, buffer.ctype, buffer.init), ("Rte_Buf_NvmQlf_BootCount", "uint8", "64U"))   # RTE-28
        config = self.generated()
        restore = config.split("void nvmRestore()")[1].split("\n}\n")[0]
        self.assertIn("Rte_Buf_NvmQlf_BootCount = nvmQlf(nvmQuality[0]);", restore)
        function = config.split("uint8_t nvmQlf(uint8_t const quality)")[1].split("\n}\n")[0]
        # RTE-28: not read and never written are QLF_NOT_AVAILABLE (64), lost value and no memory stay errors
        for line in ("case 0U: return 64U; // NOT_READ: QLF_NOT_AVAILABLE", "case 1U: return 1U; // STORED: QLF_OK",
                     "case 2U: return 64U; // DEFAULT: QLF_NOT_AVAILABLE",
                     "case 3U: return 4U; // UNSECURED: QLF_ERROR_CRC",
                     "case 4U: return 16U; // NO_MEMORY: QLF_ERROR", "default: return 64U;"):
            self.assertIn(line, function)
        self.assertIn('{"nvm.BootCount.Qlf", "nvm.qlf.BootCount", SignalType::UINT8, &Rte_Buf_NvmQlf_BootCount}',
                      config)
        self.assertNotIn("warning", self.stderr)

    def test_quality_read_in_two_tasks_restores_the_copies(self):
        ecu = copy.deepcopy(ECU)
        ecu["tasks"][1]["runnables"] = ["TypeSink.Sink20ms", "NvmSwc.Run10ms"]
        ecu["tasks"][0]["runnables"] = ["TypeSwc.Run10ms", "TypeSink.Sink10ms"]
        ecu["tasks"][1]["period_ms"] = 10
        self.run_with(ecu=ecu, **self.QLF)
        restore = self.generated().split("void nvmRestore()")[1].split("\n}\n")[0]
        self.assertIn("Rte_Buf_NvmQlf_BootCount = nvmQlf(nvmQuality[0]);", restore)

    def test_errors(self):
        self.expect_error("connection NvmSwc.WideTrim.WideTrim -> nvm.qlf.Missing: no provided port writes "
                          "nvm.Missing", **{"NvmSwc.WideTrim.WideTrim": "nvm.qlf.Missing"})
        self.expect_error("expected nvm.qlf.<Block> with a block name",
                          **{"NvmSwc.WideTrim.WideTrim": "nvm.qlf.1bad"})
        self.expect_error("output port cannot use nvm.qlf",
                          **{"TypeSwc.TempOut.TempOut": "nvm.qlf.BootCount"})
        self.expect_error("does not fit nvm.qlf",
                          **{"TypeSwc.Cmd.Cmd": "nvm.qlf.BootCount"})


class RteNvmContractTest(unittest.TestCase):
    """Numbers the generator shares with the NvM core (libs/bsw/nvm/include/nvm/Nvm.h) and tools/nxconfig/nvm.py."""

    def test_quality_numbers(self):
        source = CORE.read_text(encoding="utf-8")
        enum = source.split("enum class Quality : uint8_t")[1].split("};")[0]
        numbers = {name: int(value) for name, value in re.findall(r"(\w+)\s*=\s*(\d+)U", enum)}
        self.assertEqual(numbers, {name: number for name, number, _ in rtegen.NVM_QUALITY})
        self.assertEqual({q for _, _, q in rtegen.NVM_QUALITY} - set(rtegen.QLF), set())

    def test_overheads(self):
        from nxconfig import nvm as nxnvm
        source = CORE.read_text(encoding="utf-8")
        self.assertIn("constexpr size_t RECORD_OVERHEAD = RECORD_HEADER + 2U; // + CRC-16", source)
        self.assertIn("constexpr size_t RECORD_HEADER   = 6U;", source)
        self.assertIn("constexpr size_t ASIL_HEADER     = 8U;", source)
        self.assertIn("constexpr size_t ASIL_OVERHEAD   = ASIL_HEADER + 4U;   // + CRC-32", source)
        self.assertEqual((rtegen.NVM_RECORD_OVERHEAD, rtegen.NVM_ASIL_OVERHEAD), (8, 12))
        self.assertEqual((nxnvm.QM_OVERHEAD, nxnvm.ASIL_OVERHEAD), (8, 12))
        self.assertEqual(nxnvm.MAX_BLOCK_BYTES, rtegen.NVM_MAX_BLOCK_BYTES)
        self.assertEqual({k: rtegen.PLATFORM_SIZES[k] for k in nxnvm.TYPE_BYTES}, nxnvm.TYPE_BYTES)
        self.assertEqual(set(nxnvm.TYPE_BYTES), set(rtegen.PLATFORM_TYPES))
        self.assertEqual([rtegen.nvm_slot_bytes(n, "asil_c") for n in (4, 5, 8)], [16, 20, 20])


def compile_cxx(sources, includes, exe: Path):
    return subprocess.run(ZIG + ["c++", *CXX_FLAGS, *[f"-I{i}" for i in includes], "-isystem", str(BSP_INCLUDE),
                                 *map(str, sources), "-o", str(exe)], capture_output=True, text=True)


@unittest.skipUnless(BSP_INCLUDE.is_dir(), "external/openbsw is not checked out (git submodule update --init)")
class RteNvmAsilHostTest(unittest.TestCase):
    """Generated RTE of test/nvm with block definitions and libs/bsw/nvm over five power cycles."""

    def test_power_cycles(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            ecu = copy.deepcopy(ECU)
            ecu["connections"]["NvmSwc.WideTrim.WideTrim"] = "nvm.qlf.BootCount"
            path = tmp / "rte.yaml"
            path.write_text(yaml.safe_dump(ecu, sort_keys=False), encoding="utf-8")
            out = tmp / "gen"
            with contextlib.redirect_stderr(io.StringIO()):
                rtegen.generate(path, with_blocks(PROJECT, DEFINED), out, REPO, MULTI_TASKS)
            swc = [NVM_DIR / "NvmSwc/generated/src", TYPES / "TypeSwc/generated/src", TYPES / "TypeSink/generated/src"]
            includes = [out, out / "tasks", REPO / "libs/bsw/autosarBase/include", HERE / "test" / "can_host" / "stubs",
                        NVM_DIR / "stubs", *swc, NVM_LIB / "include", NVM_LIB / "test" / "host"]
            objects = []
            for src in (out / "Rte.c", swc[0] / "NvmSwc.c", swc[1] / "TypeSwc.c", swc[2] / "TypeSink.c"):
                obj = tmp / (src.stem + ".o")
                r = subprocess.run(ZIG + ["cc", "-std=c99", "-O1", "-Wall", "-Wextra", "-Werror",
                                          *[f"-I{i}" for i in includes], "-c", str(src), "-o", str(obj)],
                                   capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr[-3000:])
                objects.append(obj)
            exe = tmp / "rte_nvm_asil_host_test.exe"
            r = compile_cxx([out / "RteConfig.cpp", NVM_DIR / "rte_nvm_asil_host_test.cpp", *objects], includes, exe)
            self.assertEqual(r.returncode, 0, r.stderr[-3000:])
            image = tmp / "eeprom.bin"
            outputs = []

            def run(which, arg):
                result = subprocess.run([str(exe), which, arg], capture_output=True, text=True)
                outputs.append(result.stdout.strip())
                self.assertEqual(result.returncode, 0, result.stdout[-3000:])

            run("1", str(image))
            run("2", str(image))
            # BootCount lies at 0 (ASIL, copies of 16 bytes): damage a data bit of copy A
            data = bytearray(image.read_bytes())
            self.assertEqual(data[0:4], data[16:20])            # both copies: same id and size
            self.assertEqual(data[4], 2)                         # write counter 2 (runs 1 and 2)
            data[8] ^= 0x01
            image.write_bytes(bytes(data))
            run("3", str(image))
            data = bytearray(image.read_bytes())
            self.assertEqual(data[0:16], data[16:32])           # repaired at the start, then both written again
            data[8] ^= 0x01
            data[16 + 8] ^= 0x02
            image.write_bytes(bytes(data))
            run("4", str(image))
            run("none", "-")
            print("\n" + "\n".join(outputs), file=sys.stderr)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
