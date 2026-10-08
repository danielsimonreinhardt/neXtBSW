# SPDX-License-Identifier: Apache-2.0
"""NVM-14: keys of the security module (project.yaml security), the key store of the workstation and the scan for
key material in the repository.

Own test profile: the synthetic derivative and key space of test_nvm (TESTMCU64 with invented codes, 16 slots of which
2 are system slots), plus a copy of the demo project (S32K144, 20 slots) for the real numbers. Every refusal has its
counter-check: WRITE_PROTECTION in every spelling and place is refused with its YAML path, the same project without
it passes; a key of the key store in a file is found, the same file without it is clean.

Run: .venv/Scripts/python -m unittest tools/nxconfig/test_security.py
"""

import os
import shutil
import subprocess
import sys
import tempfile
import typing
import unittest
from pathlib import Path
from unittest import mock

import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import nxconfig  # noqa: E402
from nxconfig import keystore, model, security  # noqa: E402
from nxconfig.__main__ import main as nxconfig_main  # noqa: E402
from test_nvm import EMULATION, KEYS, Profile  # noqa: E402

PARTITION = {"partition": {"eeprom_bytes": 8192, "data_flash_bytes": 0x8000, "key_slots": 16}}
EMU = dict(EMULATION, keys=KEYS)
# PLAT-62: the own full-featured test profile (synthetic testboard_full/testproject_full) instead of the eval board
# project, which stays private (PLAT-46 F10) - a test against it would be red in the exported open tree. What
# proves something about the eval board itself uses EVAL_DEMO and is skipped where it is missing.
DEMO = REPO / "tools" / "nxconfig" / "test" / "profiles" / "projects" / "testproject_full" / "project.yaml"
EVAL_DEMO = REPO / "projects" / "evalboard_s32k144_demo" / "project.yaml"
SECRET = bytes.fromhex("5f3c9a0e71d24b86a9e0c3f1172b4d6e")      # invented for the tests, in no key store


class SecurityProfile(Profile):
    def run_check(self, security_use, nvm_use=PARTITION):
        return self.check_with(security_use, nvm_use)

    def check_with(self, security_use, nvm_use):
        # Profile.check writes the project; add the security section afterwards and check again
        self.check(nvm_use, emulation=EMU)
        path = self.tmp / "projects" / "testproject" / "project.yaml"
        project = yaml.safe_load(path.read_text(encoding="utf-8"))
        if security_use is not None:
            project["security"] = security_use
        path.write_text(yaml.safe_dump(project, sort_keys=False), encoding="utf-8")
        return nxconfig.check(path, self.mcudb)

    def errors(self, security_use, nvm_use=PARTITION):
        _, messages = self.check_with(security_use, nvm_use)
        return [(m.yaml_path, m.text) for m in messages if m.severity == "error"]


class WriteProtectionTest(SecurityProfile):
    def test_counter_check_flags_without_write_protection_pass(self):
        config, messages = self.run_check({"master_ecu_key": "testboard-dev",
                                           "keys": {"MASTER_ECU_KEY": {"flags": []},
                                                    "KEY_1": {"flags": ["key_usage", "debugger_protection"]}}})
        self.assertEqual([str(m) for m in messages if m.severity == "error"], [])
        self.assertEqual(security.master_ecu_key_name(config), "testboard-dev")

    def test_flag_refused_in_every_spelling_with_yaml_path(self):
        for spelling in ("write_protection", "WRITE_PROTECTION", "Write-Protection", "writeprotect", "wp"):
            with self.subTest(spelling=spelling):
                errors = self.errors({"keys": {"KEY_1": {"flags": ["key_usage", spelling]}}})
                self.assertTrue(errors, spelling)
                path, text = errors[0]
                self.assertTrue(path.startswith("security.keys.KEY_1.flags"), path)
                self.assertIn("WRITE_PROTECTION is never allowed", text)

    def test_refused_on_the_master_ecu_key_too(self):
        errors = self.errors({"keys": {"MASTER_ECU_KEY": {"flags": ["WRITE_PROTECTION"]}}})
        self.assertTrue(errors[0][0].startswith("security.keys.MASTER_ECU_KEY.flags"), errors)

    def test_refused_as_a_field(self):
        errors = self.errors({"keys": {"KEY_2": {"write_protection": True}}})
        self.assertEqual(errors[0][0], "security.keys.KEY_2")
        self.assertIn("WRITE_PROTECTION is never allowed", errors[0][1])
        errors = self.errors({"write_protection": False})
        self.assertEqual(errors[0][0], "security")
        self.assertIn("WRITE_PROTECTION is never allowed", errors[0][1])

    def test_schema_never_offers_it(self):
        offered = typing.get_args(model.KeyFlag)
        self.assertNotIn(model.FORBIDDEN_KEY_FLAG, offered)
        self.assertEqual(offered, model.KEY_FLAGS)
        self.assertFalse(any(model.is_write_protection(f) for f in offered))
        self.assertEqual(keystore.SHE_FLAGS, model.KEY_FLAGS)

    def test_guard_behind_the_schema(self):
        """A later schema that offers the flag (simulated: the validators are passed by) is still refused by
        security.py - the second guard, with the YAML path."""
        config, _ = self.run_check({"keys": {"KEY_1": {"flags": ["key_usage"]}}})
        path = self.tmp / "projects" / "testproject" / "project.yaml"
        rep = nxconfig.config.Reporter()
        rep.sources["project"] = nxconfig.config.Source(path, {"security": {"keys": {"KEY_1": {
            "flags": ["key_usage", "write_protection"]}}}})
        key = config.project.security.keys["KEY_1"]
        object.__setattr__(key, "flags", ["key_usage", "write_protection"])
        security.check(config, rep)
        errors = [(m.yaml_path, m.text) for m in rep.messages if m.severity == "error"]
        self.assertEqual(errors[0][0], "security.keys.KEY_1.flags[1]")
        self.assertEqual(len([e for e in errors if "WRITE_PROTECTION" in e[1]]), 1)   # once, not twice
        # without the raw tree (e.g. a caller that built the model itself) the loaded flags still catch it
        rep = nxconfig.config.Reporter()
        security.check(config, rep)
        self.assertEqual([m.yaml_path for m in rep.messages], ["security.keys.KEY_1.flags"])

    def test_checked_flags_for_the_load_key_tool(self):
        self.assertEqual(keystore.checked_flags(["key_usage", "boot_protection"]), ("boot_protection", "key_usage"))
        for spelling in ("write_protection", "WRITE_PROTECTION", "wp"):
            with self.assertRaises(keystore.WriteProtectionRefused):
                keystore.checked_flags(["key_usage", spelling])
        with self.assertRaises(keystore.KeyStoreError):
            keystore.checked_flags(["secret_flag"])

    def test_production_refused(self):
        errors = self.errors({"stage": "production"})
        self.assertEqual(errors[0][0], "security.stage")
        config, _ = self.run_check({"stage": "production"})
        self.assertIsNone(config)


class KeyNamesTest(SecurityProfile):
    def test_names_and_key_space(self):
        errors = self.errors({"keys": {"KEY_15": {}, "SECRET_KEY": {}}})
        self.assertIn(("security.keys.KEY_15",
                       "KEY_15 lies beyond the key space: 16 slots leave KEY_1 ... KEY_14 (nvm.partition.key_slots)"),
                      errors)
        self.assertTrue(any(p == "security.keys.SECRET_KEY" and "unknown key" in t for p, t in errors), errors)
        self.assertEqual(self.errors({"keys": {"KEY_14": {}, "BOOT_MAC_KEY": {}}}), [])

    def test_keys_need_a_key_space(self):
        errors = self.errors({"keys": {"KEY_1": {}}},
                             nvm_use={"partition": {"eeprom_bytes": 8192, "data_flash_bytes": 0x8000}})
        self.assertEqual(errors[0][0], "security.keys")

    def test_verify_only_needs_the_flag_extension(self):
        errors = self.errors({"keys": {"KEY_1": {"flags": ["verify_only"]}}})
        self.assertEqual(errors[0][0], "security.keys.KEY_1.flags")

    def test_name_that_looks_like_a_key_is_refused(self):
        errors = self.errors({"master_ecu_key": SECRET.hex()})
        self.assertEqual(errors[0][0], "security.master_ecu_key")
        self.assertIn("key material", errors[0][1])

    def test_default_name_is_the_board(self):
        config, _ = self.run_check({})
        self.assertEqual(security.master_ecu_key_name(config), config.board.board.name)
        config, _ = self.run_check(None)
        self.assertEqual(security.master_ecu_key_name(config), config.board.board.name)


class DemoTest(unittest.TestCase):
    """The eval board demo (S32K144, 20 slots: KEY_1 ... KEY_17) with a copy that adds the section."""

    def test_demo_s32k144(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            project = yaml.safe_load(DEMO.read_text(encoding="utf-8"))
            for key in ("board", "rte", "diag"):
                if key in project["project"]:
                    project["project"][key] = str((DEMO.parent / project["project"][key]).resolve())
            project["security"] = {"keys": {"MASTER_ECU_KEY": {}, "KEY_17": {"flags": ["key_usage"]}}}
            path = tmp / "project.yaml"
            path.write_text(yaml.safe_dump(project, sort_keys=False, allow_unicode=True), encoding="utf-8")
            config, messages = nxconfig.check(path)
            self.assertEqual([str(m) for m in messages if m.severity == "error"], [])
            project["security"]["keys"]["KEY_18"] = {}
            project["security"]["keys"]["KEY_17"]["flags"].append("WRITE_PROTECTION")
            path.write_text(yaml.safe_dump(project, sort_keys=False, allow_unicode=True), encoding="utf-8")
            _, messages = nxconfig.check(path)
            self.assertTrue(any(m.yaml_path.startswith("security.keys.KEY_17.flags") and m.line
                                for m in messages if m.severity == "error"), [str(m) for m in messages])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class KeyStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.store = self.tmp / "store"
        self.env = mock.patch.dict(os.environ, {keystore.KEY_DIR_ENV: str(self.store)})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_create_load_never_overwrite(self):
        self.assertEqual(keystore.key_dir(), self.store)
        path = keystore.create_master_ecu_key("board-a")
        self.assertEqual(path, self.store / "board-a.key")
        key = keystore.load_master_ecu_key("board-a")
        self.assertEqual(len(key.key), 16)
        self.assertNotIn(key.key.hex(), repr(key))
        with self.assertRaises(keystore.KeyStoreError):
            keystore.create_master_ecu_key("board-a")
        self.assertEqual(keystore.load_master_ecu_key("board-a").key, key.key)
        self.assertEqual(keystore.stored_keys(), {"board-a": key.key})

    def test_file_format(self):
        self.store.mkdir()
        (self.store / "b.key").write_text("# comment\n5f3c9a0e 71d24b86\na9e0c3f1172b4d6e  # trailing\n",
                                          encoding="utf-8")
        self.assertEqual(keystore.load_master_ecu_key("b").key, SECRET)
        for bad in ("00" * 16, "ff" * 16, "000102030405060708090a0b0c0d0e0f", "12" * 15, "zz" * 16):
            (self.store / "c.key").write_text(bad, encoding="utf-8")
            with self.assertRaises(keystore.KeyStoreError, msg=bad):
                keystore.load_master_ecu_key("c")

    def test_missing_key_says_how_to_create_it(self):
        with self.assertRaises(keystore.KeyStoreError) as e:
            keystore.load_master_ecu_key("nowhere")
        self.assertIn("csec-key new", str(e.exception))
        with self.assertRaises(keystore.KeyStoreError):
            keystore.key_path("../escape")

    def test_command_line(self):
        project = DEMO.parent                                  # PLAT-62: the full test profile
        self.assertEqual(nxconfig_main(["csec-key", "new", str(project)]), 0)
        self.assertEqual(len(keystore.stored_keys()), 1)
        self.assertEqual(nxconfig_main(["csec-key", "new", str(project)]), 1)      # never a second one
        self.assertEqual(nxconfig_main(["csec-key", "where", str(project)]), 0)


class ScanTest(unittest.TestCase):
    """Counter-checks of the leak scan on a throw-away git repository."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        (self.repo / ".gitignore").write_text("ignored/\n", encoding="utf-8")
        (self.repo / "clean.txt").write_text("nothing here\n", encoding="utf-8")
        self.keys = {"dev": SECRET}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def scan(self):
        return keystore.scan(self.repo, self.keys)

    def test_clean_tree(self):
        self.assertEqual(self.scan(), [])

    def test_key_in_hex_is_found_in_every_form(self):
        forms = [SECRET.hex(), SECRET.hex().upper(), ", ".join(f"0x{b:02X}" for b in SECRET),
                 " ".join(f"{b:02x}" for b in SECRET)]
        for form in forms:
            with self.subTest(form=form):
                (self.repo / "leak.c").write_text(f"static const unsigned char k[] = {{ {form} }};\n", "utf-8")
                found = self.scan()
                self.assertEqual([(f.path, f.line) for f in found], [("leak.c", 1)], form)
                self.assertIn("'dev'", found[0].text)
                self.assertNotIn(SECRET.hex(), str(found[0]))           # the report never repeats the key
        # spread over lines (C array), raw bytes
        (self.repo / "leak.c").write_text("{" + ",\n".join(f"0x{b:02x}" for b in SECRET) + "}\n", "utf-8")
        self.assertEqual([(f.path, f.line) for f in self.scan()], [("leak.c", None)])
        (self.repo / "leak.c").unlink()
        (self.repo / "leak.bin").write_bytes(b"\x00" * 7 + SECRET + b"\x00")
        self.assertEqual([f.path for f in self.scan()], ["leak.bin"])

    def test_ignored_files_are_not_committed_and_not_reported(self):
        (self.repo / "ignored").mkdir()
        (self.repo / "ignored" / "x.key").write_text(SECRET.hex(), "utf-8")
        self.assertEqual(self.scan(), [])

    def test_key_file_and_written_out_master_ecu_key(self):
        (self.repo / "board.key").write_text("# nothing\n", "utf-8")
        (self.repo / "project.yaml").write_text(f"master_ecu_key: {os.urandom(16).hex()}\n", "utf-8")
        found = {(f.path, f.line) for f in keystore.scan(self.repo, {})}
        self.assertEqual(found, {("board.key", None), ("project.yaml", 1)})
        # counter-check: the public SHE example value next to the name is no secret
        (self.repo / "board.key").unlink()
        (self.repo / "project.yaml").write_text("MASTER_ECU_KEY = 000102030405060708090a0b0c0d0e0f\n", "utf-8")
        self.assertEqual(keystore.scan(self.repo, {}), [])


class RepositoryScanTest(unittest.TestCase):
    """This tree against every key of the key store of this workstation (none there: only key files and written out
    MASTER_ECU_KEYs are looked for)."""

    def test_no_key_material_in_this_tree(self):
        # PLAT-62: an exported tree has no git repository (distcheck export without --git-init) - there every file of
        # the tree is a candidate (no .gitignore to apply, the export holds only what is published)
        files = None
        if subprocess.run(["git", "-C", str(REPO), "rev-parse", "--git-dir"], capture_output=True).returncode != 0:
            files = [p.relative_to(REPO).as_posix() for p in REPO.rglob("*")
                     if p.is_file() and not ({"build", "__pycache__", ".venv", "external"} & set(p.relative_to(REPO).parts))]
        found = keystore.scan(REPO, files=files)
        self.assertEqual([str(f) for f in found], [])


if __name__ == "__main__":
    unittest.main()
