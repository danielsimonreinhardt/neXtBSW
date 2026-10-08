"""NVM-07: non-volatile memory of a project - partition of the emulated EEPROM and fixed block places (nvm.py).

Own test profile: a synthetic derivative (copy of the test mcudb with a made-up FlexNVM of 128 KB, two EEPROM sizes and
codes other than any S32K1) under the synthetic board/project of test_nxconfig; plus the real S32K144 codes through a
copy of the demo project (the decision nvm01-eeesize: 4 KB EEPROM, 32 KB data flash, DEPART 1011b, load on reset).

Run: .venv/Scripts/python -m unittest tools/nxconfig/test_nvm.py
"""

import copy
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE.parent))

import nxconfig  # noqa: E402
from nxconfig import checks, nvm  # noqa: E402

TEST_MCUDB = HERE / "test" / "mcudb"
TEST_BOARD = HERE / "test" / "profiles" / "boards" / "testboard" / "board.yaml"
TEST_PROJECT = yaml.safe_load((HERE / "test/profiles/projects/testproject/project.yaml").read_text(encoding="utf-8"))
# PLAT-62: the own full-featured test profile (synthetic testboard_full/testproject_full) instead of the eval board
# project, which stays private (PLAT-46 F10) - a test against it would be red in the exported open tree. What
# proves something about the eval board itself uses EVAL_DEMO and is skipped where it is missing.
DEMO = REPO / "tools" / "nxconfig" / "test" / "profiles" / "projects" / "testproject_full"
EVAL_DEMO = REPO / "projects" / "evalboard_s32k144_demo"

# synthetic FlexNVM: 128 KB partitionable flash, EEPROM 2 KB (code 0x5) or 8 KB (code 0x1), two codes for 64/64
EMULATION = {
    "sizes": [{"code": 0x5, "eeprom_bytes": 2048}, {"code": 0x1, "eeprom_bytes": 8192},
              {"code": 0xF, "eeprom_bytes": 0}],
    "partitions": [{"code": 0x2, "data_flash_bytes": 0x20000, "backup_bytes": 0},
                   {"code": 0x6, "data_flash_bytes": 0x10000, "backup_bytes": 0x10000},
                   {"code": 0x7, "data_flash_bytes": 0x10000, "backup_bytes": 0x10000},
                   {"code": 0x9, "data_flash_bytes": 0x8000, "backup_bytes": 0x18000}],
    "timing": {"backups": [{"backup_bytes": 0x10000, "write_max_us": 3000, "partition_typ_ms": 90},
                           {"backup_bytes": 0x18000, "write_max_us": 3500}],
               "first_write_factor": 3, "source": "synthetic"},
    "source": "synthetic",
}


# SYS-95 (DFA-4 a): the PWM group BRIDGE of the synthetic testboard has no gate driver, so the pwmgate check would add
# its warning to every check of the NvM tests (test_nvm*.py); the warning is tested in test_pwmgate.py and
# test_nxconfig.py (PwmGateInSyntheticProfileTest), in the NvM modules it is switched off in-process like in
# test_nxconfig.py.
_pwmgate_off = mock.patch.object(checks.pwmgate, "check", lambda cfg, rep: [])


def setUpModule():
    _pwmgate_off.start()


def tearDownModule():
    _pwmgate_off.stop()


class Profile(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.mcudb = self.tmp / "mcudb"
        shutil.copytree(TEST_MCUDB, self.mcudb)
        board = yaml.safe_load(TEST_BOARD.read_text(encoding="utf-8"))
        self.derivative_file = next(self.mcudb.glob(f"*/{board['board']['mcu']}.yaml"))
        self.derivative = yaml.safe_load(self.derivative_file.read_text(encoding="utf-8"))
        board_dir = self.tmp / "boards" / "testboard"
        board_dir.mkdir(parents=True)
        shutil.copy(TEST_BOARD, board_dir / "board.yaml")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def check(self, nvm_use, emulation=EMULATION, window=0x2000, flash=0x20000):
        derivative = copy.deepcopy(self.derivative)
        memory = [r for r in derivative["memory"] if r["kind"] not in ("data_flash", "eeprom_ram")]
        if flash:
            memory.append({"name": "DFLASH", "kind": "data_flash", "start": 0x10000000, "size": flash,
                           "sector_size": 0x800})
        if window:
            memory.append({"name": "EERAM", "kind": "eeprom_ram", "start": 0x14000000, "size": window})
        derivative["memory"] = memory
        if emulation is not None:
            derivative["eeprom_emulation"] = copy.deepcopy(emulation)
        self.derivative_file.write_text(yaml.safe_dump(derivative, sort_keys=False), encoding="utf-8")
        project = copy.deepcopy(TEST_PROJECT)
        if nvm_use is not None:
            project["nvm"] = nvm_use
        project_dir = self.tmp / "projects" / "testproject"
        project_dir.mkdir(parents=True, exist_ok=True)
        path = project_dir / "project.yaml"
        path.write_text(yaml.safe_dump(project, sort_keys=False), encoding="utf-8")
        return nxconfig.check(path, self.mcudb)

    def messages(self, nvm_use, severity="error", **kw):
        _, messages = self.check(nvm_use, **kw)
        return [(m.yaml_path, m.text) for m in messages if m.severity == severity]


class PartitionTest(Profile):
    def test_without_nvm_section_nothing_changes(self):
        config, messages = self.check(None)
        self.assertEqual([m for m in messages if m.severity == "error"], [])
        self.assertIsNone(config.nvm)

    def test_split_with_one_code(self):
        config, messages = self.check({"partition": {"eeprom_bytes": 8192, "data_flash_bytes": 0x8000}})
        self.assertEqual([str(m) for m in messages if m.severity == "error"], [])
        p = config.nvm.partition
        self.assertEqual((p.eeprom_code, p.partition_code, p.backup_bytes), (0x1, 0x9, 0x18000))
        self.assertEqual(p.load_code, 0)                   # load on reset = command byte 0 (opposite of EEERST)
        self.assertEqual(p.write_limit_us, 3500 * 3)       # longest write of 96 KB backup x first-write factor
        self.assertIsNone(p.partition_typ_ms)
        self.assertEqual(config.nvm.usable_bytes, 8192)

    def test_split_with_two_codes_needs_the_code(self):
        errors = self.messages({"partition": {"eeprom_bytes": 2048, "data_flash_bytes": 0x10000}})
        self.assertEqual(errors[0][0], "nvm.partition.partition_code")
        self.assertIn("several codes", errors[0][1])
        self.assertIn("0x6, 0x7", errors[0][1])
        config, _ = self.check({"partition": {"eeprom_bytes": 2048, "data_flash_bytes": 0x10000,
                                              "partition_code": 7}})
        self.assertEqual(config.nvm.partition.partition_code, 7)
        self.assertEqual(config.nvm.partition.write_limit_us, 9000)
        self.assertEqual(config.nvm.partition.partition_typ_ms, 90)

    def test_code_of_another_split(self):
        errors = self.messages({"partition": {"eeprom_bytes": 2048, "data_flash_bytes": 0x10000,
                                              "partition_code": 9}})
        self.assertEqual(errors, [("nvm.partition.partition_code",
                                   "partition code 0x9 does not give 64 KB of data flash on TESTMCU64 (codes for it: "
                                   "0x6, 0x7)")])

    def test_size_and_split_must_exist(self):
        errors = self.messages({"partition": {"eeprom_bytes": 4096, "data_flash_bytes": 0x8000}})
        self.assertEqual(errors[0][0], "nvm.partition.eeprom_bytes")
        self.assertIn("possible: 2048, 8192, 0", errors[0][1])
        errors = self.messages({"partition": {"eeprom_bytes": 2048, "data_flash_bytes": 0x4000}})
        self.assertEqual(errors[0][0], "nvm.partition.data_flash_bytes")

    def test_eeprom_without_backup_and_backup_without_eeprom(self):
        errors = self.messages({"partition": {"eeprom_bytes": 2048, "data_flash_bytes": 0x20000}})
        self.assertIn("need an EEPROM backup", errors[0][1])
        errors = self.messages({"partition": {"eeprom_bytes": 0, "data_flash_bytes": 0x8000}})
        self.assertIn("needs emulated EEPROM", errors[0][1])

    def test_eeprom_larger_than_the_window(self):
        errors = self.messages({"partition": {"eeprom_bytes": 8192, "data_flash_bytes": 0x8000}}, window=0x1000)
        self.assertEqual(errors[0][0], "nvm.partition.eeprom_bytes")
        self.assertIn("do not fit the RAM window", errors[0][1])

    def test_codes_must_split_the_data_flash_region(self):
        errors = self.messages({"partition": {"eeprom_bytes": 8192, "data_flash_bytes": 0x8000}}, flash=0x10000)
        self.assertIn("inconsistent", errors[0][1])

    def test_derivative_without_emulation(self):
        errors = self.messages({"partition": {"eeprom_bytes": 2048, "data_flash_bytes": 0x8000}}, emulation=None)
        self.assertEqual(errors[0][0], "nvm.partition")
        self.assertIn("no partitionable emulated EEPROM", errors[0][1])

    def test_without_load_on_reset_a_warning(self):
        warnings = self.messages({"partition": {"eeprom_bytes": 8192, "data_flash_bytes": 0x8000,
                                                "load_on_reset": False}}, severity="warning")
        self.assertIn(("nvm.partition.load_on_reset", warnings[0][1]), warnings)
        self.assertIn("SETRAM", warnings[0][1])

    def test_unconfirmed_code_table_is_reported(self):
        emulation = dict(EMULATION, verify=True)
        warnings = self.messages({"partition": {"eeprom_bytes": 8192, "data_flash_bytes": 0x8000}},
                                 emulation=emulation, severity="warning")
        self.assertTrue(any(path == "eeprom_emulation.verify" and "NvM partition" in text
                            for path, text in warnings), warnings)

    def test_description_shown_before_writing(self):
        config, _ = self.check({"partition": {"eeprom_bytes": 8192, "data_flash_bytes": 0x8000}},
                               emulation=dict(EMULATION, verify=True))
        text = "\n".join(config.nvm.partition.describe())
        for fragment in ("8192 bytes (size code 0x1)", "partition code 0x9 = 1001b", "load on reset: yes (command "
                         "byte 0)", "IRREVERSIBLE", "verify: true"):
            self.assertIn(fragment, text)


KEYS = [{"code": 0x0, "slots": 0, "eeprom_bytes": 0},
        {"code": 0x6, "slots": 8, "system_slots": 2, "eeprom_bytes": 256},
        {"code": 0x9, "slots": 16, "system_slots": 2, "eeprom_bytes": 1024}]
ENDURANCE = [{"ratio": 32, "writes": 50000}, {"ratio": 8, "writes": 10000}]


class KeySpaceTest(Profile):
    """NVM-11: key space of a security module and write endurance, synthetic codes (not an S32K1's)."""

    EMU = dict(EMULATION, keys=KEYS, endurance=ENDURANCE)

    def plan(self, **partition):
        use = {"partition": dict({"eeprom_bytes": 8192, "data_flash_bytes": 0x8000}, **partition)}
        config, messages = self.check(use, emulation=self.EMU)
        return config, messages

    def test_key_space_comes_off_the_usable_eeprom(self):
        config, messages = self.plan(key_slots=16)
        self.assertEqual([str(m) for m in messages if m.severity == "error"], [])
        p = config.nvm.partition
        self.assertEqual((p.key_code, p.key_slots, p.key_system_slots, p.key_bytes), (0x9, 16, 2, 1024))
        self.assertEqual(p.usable_bytes, 7168)
        self.assertEqual(config.nvm.usable_bytes, 7168)
        self.assertEqual(p.confirm_word, "PGMPART-9-0-0-1-9")        # FCCOB1..5 in command order
        # 96 KB backup: ratio 8 (smallest in the table) holds up to 12288 bytes, capped at the usable 7168
        self.assertEqual(p.endurance_bytes, 7168)

    def test_no_key_space_named_zero(self):
        config, _ = self.plan(key_slots=0)
        p = config.nvm.partition
        self.assertEqual((p.key_code, p.key_bytes, p.usable_bytes), (0, 0, 8192))
        self.assertEqual(p.confirm_word, "PGMPART-0-0-0-1-9")
        self.assertIn("IRREVERSIBLE: only a mass erase", "\n".join(p.describe()))

    def test_key_space_not_named_is_not_writable(self):
        config, messages = self.plan()
        p = config.nvm.partition
        self.assertIsNone(p.key_code)
        self.assertFalse(p.writable)
        self.assertEqual(p.confirm_word, "PGMPART-1-9-0")             # old word: the console command refuses it
        self.assertFalse(any(m.yaml_path == "nvm.partition.key_slots" for m in messages))
        self.assertIn("NOT WRITABLE", "\n".join(p.describe()))

    def test_derivative_without_key_space(self):
        config, messages = self.check({"partition": {"eeprom_bytes": 8192, "data_flash_bytes": 0x8000}})
        p = config.nvm.partition
        self.assertEqual(p.key_code, 0)                               # nothing to name: key space 0, new word
        self.assertEqual(p.confirm_word, "PGMPART-0-0-0-1-9")
        self.assertFalse(any(m.yaml_path == "nvm.partition.key_slots" for m in messages))
        errors = self.messages({"partition": {"eeprom_bytes": 8192, "data_flash_bytes": 0x8000, "key_slots": 8}})
        self.assertEqual(errors[0][0], "nvm.partition.key_slots")
        self.assertIn("no key space", errors[0][1])

    def test_rejected_combinations(self):
        cases = [
            ({"key_slots": 5}, "nvm.partition.key_slots", "possible: 0, 8, 16"),
            ({"key_slots": 8, "load_on_reset": False}, "nvm.partition.key_slots", "load_on_reset"),
            ({"key_slots": 8, "eeprom_bytes": 2048, "data_flash_bytes": 0x10000, "partition_code": 6},
             "nvm.partition.key_slots", "full emulated EEPROM"),
            ({"key_slots": 0, "key_verify_only": True}, "nvm.partition.key_verify_only", "not offered"),
        ]
        for extra, path, fragment in cases:
            with self.subTest(extra=extra):
                partition = dict({"eeprom_bytes": 8192, "data_flash_bytes": 0x8000}, **extra)
                errors = self.messages({"partition": partition}, emulation=self.EMU)
                self.assertTrue(any(p == path and fragment in t for p, t in errors), errors)

    def test_key_space_without_backup_is_rejected(self):
        emulation = dict(self.EMU, sizes=[{"code": 0x5, "eeprom_bytes": 2048}, {"code": 0xF, "eeprom_bytes": 0}])
        errors = self.messages({"partition": {"eeprom_bytes": 0, "data_flash_bytes": 0x20000, "key_slots": 8}},
                               emulation=emulation)
        self.assertTrue(any(p == "nvm.partition.key_slots" and "needs an EEPROM backup" in t for p, t in errors),
                        errors)

    def test_key_space_larger_than_the_eeprom(self):
        emulation = dict(self.EMU, keys=KEYS + [{"code": 0xA, "slots": 99, "eeprom_bytes": 8192}])
        errors = self.messages({"partition": {"eeprom_bytes": 8192, "data_flash_bytes": 0x8000, "key_slots": 99}},
                               emulation=emulation)
        self.assertTrue(any("take 8192 bytes" in t for _, t in errors), errors)

    def test_display_names_every_split_with_cost_and_gain(self):
        config, _ = self.plan(key_slots=8)
        text = "\n".join(config.nvm.partition.describe())
        for fragment in ("key space: 8 slots (6 for user keys), 256 bytes off the end of the EEPROM",
                         "command bytes FCCOB0..5: 0x80 0x06 0x00 0x00 0x01 0x09",
                         "usable EEPROM: 7936 bytes (8192 less 256 of key space)",
                         "  0x2: data flash 128 KB, backup 0 KB, no emulated EEPROM",
                         "* 0x9: data flash 32 KB, backup 96 KB, backup:FlexRAM 12, 10 K writes with all 7936",
                         "  0x6: data flash 64 KB, backup 64 KB, backup:FlexRAM 8, 10 K writes with all 7936",
                         "IRREVERSIBLE: with key space", "WRITE_PROTECTION"):
            self.assertIn(fragment, text)

    def test_endurance_below_the_smallest_ratio(self):
        emulation = dict(self.EMU, endurance=[{"ratio": 16, "writes": 100000}])
        config, _ = self.check({"partition": {"eeprom_bytes": 8192, "data_flash_bytes": 0x8000, "key_slots": 8}},
                               emulation=emulation)
        p = config.nvm.partition
        self.assertEqual(p.endurance_bytes, 0x18000 // 16)            # 6144 of 7936 usable
        text = "\n".join(p.describe())
        self.assertIn("only while at most 6144 bytes are written (96 KB / 16)", text)
        self.assertIn("(no data sheet figure below 16), 100 K writes only if at most 4096 of 7936", text)  # 64 KB
        self.assertEqual(p.usage_findings(6144), [])
        self.assertIn("no write endurance", p.usage_findings(6148)[0])
        self.assertIn("exceed the 7936 usable bytes", p.usage_findings(8000)[0])

    def test_fixed_block_beyond_the_endurance_limit_warns(self):
        emulation = dict(self.EMU, endurance=[{"ratio": 16, "writes": 100000}])
        warnings = self.messages({"partition": {"eeprom_bytes": 8192, "data_flash_bytes": 0x8000, "key_slots": 8},
                                  "blocks": {"Late": {"address": 0x1800}, "Early": {"address": 0x100}}},
                                 emulation=emulation, severity="warning")
        paths = [p for p, _ in warnings]
        self.assertIn("nvm.blocks.Late.address", paths)
        self.assertNotIn("nvm.blocks.Early.address", paths)

    def test_fixed_block_in_the_key_space_is_an_error(self):
        errors = self.messages({"partition": {"eeprom_bytes": 8192, "data_flash_bytes": 0x8000, "key_slots": 16},
                                "blocks": {"Top": {"address": 0x1C00}}}, emulation=self.EMU)
        self.assertEqual(errors, [("nvm.blocks.Top.address",
                                   "address 0x1C00 lies outside the 7168 bytes of emulated EEPROM")])

    def test_endurance_writes(self):
        table = ((16, 100000), (256, 1600000))
        self.assertEqual(nvm.endurance_writes(table, 0x8000, 2048), 100000)
        self.assertIsNone(nvm.endurance_writes(table, 0x8000, 2049))
        self.assertEqual(nvm.endurance_writes(table, 0x10000, 256), 1600000)
        self.assertIsNone(nvm.endurance_writes(table, 0, 16))
        self.assertIsNone(nvm.endurance_writes(table, 0x8000, 0))


class BlockTest(Profile):
    def test_places_and_policies(self):
        config, messages = self.check({"partition": {"eeprom_bytes": 2048, "data_flash_bytes": 0x10000,
                                                     "partition_code": 6},
                                       "blocks": {"Trim": {"address": 0x100},
                                                  "Counter": {"write": "change", "min_interval_ms": 1000}}})
        self.assertEqual([str(m) for m in messages if m.severity == "error"], [])
        self.assertEqual(config.nvm.addresses, {"Trim": 0x100})
        self.assertEqual(config.nvm.writes, {"Counter": ("CHANGE", 1000)})

    def test_block_errors(self):
        errors = self.messages({"blocks": {"A": {"address": 0x102}, "B": {"address": 0x2000},
                                           "C": {"min_interval_ms": 5}}})
        self.assertEqual(sorted(errors), [
            ("nvm.blocks.A.address", "address 0x102 is not a multiple of 4"),
            ("nvm.blocks.B.address", "address 0x2000 lies outside the 8192 bytes of emulated EEPROM"),
            ("nvm.blocks.C.min_interval_ms", "min_interval_ms only applies with write: change")])

    def test_unknown_write_policy_is_a_schema_error(self):
        errors = self.messages({"blocks": {"A": {"write": "always"}}})
        self.assertTrue(errors and errors[0][0].startswith("nvm.blocks.A.write"), errors)


class DefinitionTest(Profile):
    """NVM-18: nvm.blocks.<Block> with type, length (scalar/vector) and integrity (qm, asil_a..asil_d)."""

    def test_sizes_follow_the_definition(self):
        # QM: one value with CRC-16 in two places; ASIL: two copies with CRC-32 and write counter
        self.assertEqual([nvm.copy_bytes(n) for n in (1, 4, 5, 8)], [12, 12, 16, 16])
        self.assertEqual([nvm.copy_bytes(n, "asil_b") for n in (1, 4, 5, 6)], [16, 16, 20, 20])
        self.assertEqual(nvm.block_bytes(4, "qm"), 24)
        self.assertEqual(nvm.block_bytes(4, "asil_d"), 32)
        self.assertTrue(nvm.is_asil("asil_a"))
        self.assertFalse(nvm.is_asil(None))
        config, messages = self.check({"blocks": {
            "Trim": {"type": "uint16"},
            "Curve": {"type": "sint16", "length": 8, "integrity": "asil_b"},
            "Flag": {"type": "boolean", "integrity": "qm", "address": 0x100},
            "Gain": {"type": "float64", "length": 2, "integrity": "asil_d"}}})
        self.assertEqual([m for m in messages if m.severity == "error"], [])
        d = config.nvm.definitions
        self.assertEqual((d["Trim"].size, d["Trim"].bytes, d["Trim"].integrity), (2, 24, "qm"))
        self.assertEqual((d["Curve"].size, d["Curve"].bytes, d["Curve"].asil), (16, 56, True))
        self.assertEqual((d["Gain"].size, d["Gain"].bytes), (16, 56))
        self.assertEqual(d["Curve"].describe(), "sint16[8], asil_b")
        self.assertEqual(config.nvm.integrity["Curve"], "asil_b")
        # layout like tools/rtegen: the fixed block stays, the others in name order into the gaps
        self.assertEqual(config.nvm.layout, {"Flag": 0x100, "Curve": 0, "Gain": 56, "Trim": 112})

    def test_definition_errors(self):
        errors = self.messages({"blocks": {
            "A": {"length": 4},
            "B": {"type": "uint32", "length": 0x2000},
            "DemEvents": {"type": "uint8", "length": 2, "integrity": "asil_b"}}})
        self.assertEqual(sorted(errors), [
            ("nvm.blocks.A.length", "length needs a type (the platform type of one element)"),
            ("nvm.blocks.B.length", "8192 x uint32 are 32768 bytes, a block holds at most 32767"),
            ("nvm.blocks.DemEvents.length", "DemEvents is a block of the fault memory: its layout comes from the "
                                            "diagnostic description (diag.yaml fault_memory); remove length"),
            ("nvm.blocks.DemEvents.type", "DemEvents is a block of the fault memory: its layout comes from the "
                                          "diagnostic description (diag.yaml fault_memory); remove type")])

    def test_schema_refuses_unknown_types_and_levels(self):
        for block, key in (({"type": "int16"}, "type"), ({"type": "uint8", "integrity": "sil2"}, "integrity"),
                           ({"type": "uint8", "length": 0}, "length")):
            with self.subTest(block=block):
                errors = self.messages({"blocks": {"A": block}})
                self.assertTrue(errors and errors[0][0].startswith(f"nvm.blocks.A.{key}"), errors)

    def test_fixed_places_checked_with_the_sizes(self):
        errors = self.messages({"blocks": {"A": {"type": "uint32", "integrity": "asil_a", "address": 0x10},
                                           "B": {"type": "uint8", "address": 0x20}}})
        self.assertEqual(len(errors), 1, errors)
        self.assertIn(errors[0][0], ("nvm.blocks.A.address", "nvm.blocks.B.address"))
        self.assertIn("overlaps", errors[0][1])
        # no room: every defined block together is larger than the emulated EEPROM
        errors = self.messages({"blocks": {"A": {"type": "uint8", "length": 3000},
                                           "B": {"type": "uint8", "length": 3000}}})
        self.assertEqual(errors, [("nvm.blocks", "no room for B (6016 bytes) in the 8192 bytes of emulated EEPROM "
                                                 "(sizes from the definitions in nvm.blocks)")])

    def test_defined_blocks_beyond_the_usable_eeprom(self):
        errors = self.messages({"partition": {"eeprom_bytes": 2048, "data_flash_bytes": 0x10000,
                                              "partition_code": 6},
                                "blocks": {"A": {"type": "uint8", "length": 1000},
                                           "B": {"type": "uint8", "length": 1000}}})
        self.assertEqual(errors[0][0], "nvm.blocks")
        self.assertIn("no room for B", errors[0][1])

    def test_suggestion(self):
        self.assertEqual(nvm.suggestion("Trim", "uint16"), "nvm.blocks.Trim: {type: uint16, integrity: qm}")
        self.assertEqual(nvm.suggestion("Curve", "sint16", 8), "nvm.blocks.Curve: {type: sint16, length: 8, "
                                                               "integrity: qm}")
        self.assertEqual(nvm.suggestion("Stat", None), "nvm.blocks.Stat: {integrity: qm}")


class LayoutTest(unittest.TestCase):
    SIZES = [("A", 24), ("B", 40), ("C", 16), ("D", 8)]

    def test_name_order_without_fixed_places(self):
        self.assertEqual(nvm.layout_blocks(self.SIZES, {}, 256), {"A": 0, "B": 24, "C": 64, "D": 80})

    def test_fixed_block_stays_and_the_others_fill_the_gaps(self):
        self.assertEqual(nvm.layout_blocks(self.SIZES, {"B": 32}, 256), {"B": 32, "A": 0, "C": 72, "D": 24})

    def test_adding_a_block_does_not_move_fixed_ones(self):
        before = nvm.layout_blocks(self.SIZES, {"C": 128, "B": 64}, 256)
        after = nvm.layout_blocks([("AA", 60)] + self.SIZES, {"C": 128, "B": 64}, 256)
        self.assertEqual((before["B"], before["C"]), (after["B"], after["C"]))

    def test_errors(self):
        with self.assertRaisesRegex(ValueError, "overlaps B"):
            nvm.layout_blocks(self.SIZES, {"B": 0, "C": 32}, 256)
        with self.assertRaisesRegex(ValueError, "outside"):
            nvm.layout_blocks(self.SIZES, {"B": 240}, 256)
        with self.assertRaisesRegex(ValueError, "no room for D"):
            nvm.layout_blocks(self.SIZES, {}, 84)
        with self.assertRaisesRegex(ValueError, "no such block"):
            nvm.layout_blocks(self.SIZES, {"X": 0}, 256)
        with self.assertRaisesRegex(ValueError, "multiple of 4"):
            nvm.layout_blocks(self.SIZES, {"A": 2}, 256)


class S32K144Test(unittest.TestCase):
    """The decision nvm01-eeesize against the real code table of the S32K144 (mcudb)."""

    DECISION = {"partition": {"eeprom_bytes": 4096, "data_flash_bytes": 0x8000, "partition_code": 0xB,
                              "load_on_reset": True}}

    def check(self, nvm_use):
        tmp = Path(tempfile.mkdtemp())
        try:
            project = yaml.safe_load((DEMO / "project.yaml").read_text(encoding="utf-8"))
            project["project"]["board"] = str((DEMO / project["project"]["board"]).resolve())
            for key in ("rte", "diag"):
                project["project"][key] = str((DEMO / project["project"][key]).resolve())
            project["nvm"] = nvm_use
            path = tmp / "project.yaml"
            path.write_text(yaml.safe_dump(project, sort_keys=False, allow_unicode=True), encoding="utf-8")
            return nxconfig.check(path)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_decision(self):
        config, messages = self.check(self.DECISION)
        self.assertEqual([str(m) for m in messages if m.severity == "error"], [])
        p = config.nvm.partition
        self.assertEqual((p.eeprom_code, p.partition_code, p.load_code), (0x2, 0xB, 0))
        self.assertEqual((p.data_flash_bytes, p.backup_bytes), (0x8000, 0x8000))
        self.assertEqual(p.write_limit_us, 4000)            # teewr32b max 2000 us (32 KB backup) x 2
        self.assertEqual(p.partition_typ_ms, 70)
        # confirmed by the user on 2026-09-22 (decision nvm07-mcudb-verify): no unconfirmed-value warning any more
        self.assertFalse(p.verify)
        warnings = [m for m in messages if m.severity == "warning"]
        self.assertFalse(any(m.yaml_path == "eeprom_emulation.verify" for m in warnings), [str(m) for m in warnings])

    def test_2_kb_is_not_programmable(self):
        """Finding of NVM-02: the S32K144 knows only 4 KB and 0 bytes of emulated EEPROM."""
        config, messages = self.check({"partition": {"eeprom_bytes": 2048, "data_flash_bytes": 0x8000,
                                                     "partition_code": 0xB}})
        self.assertIn("possible: 4096, 0", " ".join(m.text for m in messages if m.severity == "error"))

    def test_csec_decision(self):
        """NVM-11 decision 2026-09-23/28: FCCOB1 0x03 (20 slots), SFE off, FCCOB3 0, EEESIZE 0x2, DEPART 0xB."""
        config, messages = self.check({"partition": dict(self.DECISION["partition"], key_slots=20)})
        self.assertEqual([str(m) for m in messages if m.severity == "error"], [])
        p = config.nvm.partition
        self.assertEqual((p.key_code, p.sfe_code, p.load_code, p.eeprom_code, p.partition_code),
                         (0x3, 0x0, 0x0, 0x2, 0xB))
        self.assertEqual(p.confirm_word, "PGMPART-3-0-0-2-B")
        self.assertEqual(p.usable_bytes, 3584)                  # 4096 - 512 ([RM13] Table 36-62)
        self.assertEqual(config.nvm.usable_bytes, 3584)
        self.assertEqual(p.endurance_bytes, 2048)               # 32 KB backup / 16 ([DS13] Table 38)
        text = "\n".join(p.describe())
        for fragment in ("key space: 20 slots (17 for user keys), 512 bytes",
                         "command bytes FCCOB0..5: 0x80 0x03 0x00 0x00 0x02 0x0B",
                         "usable EEPROM: 3584 bytes",
                         "write endurance 100 K (data sheet, backup:EEPROM in use >= 16) only while at most 2048 "
                         "bytes are written (32 KB / 16)",
                         "  0x4: data flash 0 KB, backup 64 KB, backup:FlexRAM 16, 100 K writes with all 3584",
                         "  0xA: data flash 16 KB, backup 48 KB, backup:FlexRAM 12 (no data sheet figure below 16), "
                         "100 K writes only if at most 3072 of 3584",
                         "* 0xB: data flash 32 KB, backup 32 KB, backup:FlexRAM 8 (no data sheet figure below 16), "
                         "100 K writes only if at most 2048 of 3584"):
            self.assertIn(fragment, text)

    def test_csec_usable_sizes(self):
        """SYS-56: 4096/3968/3840/3584 bytes for 0/5/10/20 slots - nobody plans with 4 KB."""
        usable = {}
        for slots in (0, 5, 10, 20):
            config, _ = self.check({"partition": dict(self.DECISION["partition"], key_slots=slots)})
            usable[slots] = config.nvm.partition.usable_bytes
        self.assertEqual(usable, {0: 4096, 5: 3968, 10: 3840, 20: 3584})

    def test_demo_value_without_key_slots_is_not_writable(self):
        config, _ = self.check(self.DECISION)
        self.assertFalse(config.nvm.partition.writable)
        self.assertEqual(config.nvm.partition.confirm_word, "PGMPART-2-B-0")   # refused by the console command
        self.assertIn("NOT WRITABLE", "\n".join(config.nvm.partition.describe()))

    def test_32_kb_needs_the_code(self):
        _, messages = self.check({"partition": {"eeprom_bytes": 4096, "data_flash_bytes": 0x8000}})
        self.assertIn("(0x3, 0xB)", " ".join(m.text for m in messages if m.severity == "error"))


if __name__ == "__main__":
    unittest.main()
