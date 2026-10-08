"""Host tests of tools/canflash/flashguard.py and its use in canflash blupdate and provision.py (NVM-15).

No hardware, no compiler: images are dictionaries or small hex files, debugger runs are command texts. The flash
targets of the build (CMakeLists.txt nx_add_flash_target) and provision.py are checked with the command texts they
really write; if the demo build is there, its generated flash-bootloader.jlink with bootloader.hex as well.

Run: .venv/Scripts/python -m unittest tools/canflash/test_flashguard.py
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import re
import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))

import canflash  # noqa: E402
import flashguard  # noqa: E402
import provision  # noqa: E402

# PLAT-62: the own full test profile (memory layout of the eval board demo) instead of the eval board project, which
# stays private (PLAT-46 F10) and is missing in the exported open tree
DEMO = ROOT / "tools" / "nxconfig" / "test" / "profiles" / "projects" / "testproject_full" / "project.yaml"
REFERENCE = bytes([0xFF] * 12 + [0xFE, 0x7F, 0xFF, 0xFF])


def image(field: bytes | None = REFERENCE, start: int = 0) -> dict[int, int]:
    data = {start + i: 0x11 for i in range(0x40)}
    if field is not None:
        data.update({0x400 + i: b for i, b in enumerate(field)})
    return data


def fsec(value: int) -> bytes:
    return REFERENCE[:12] + bytes([value]) + REFERENCE[13:]


def target(**changes) -> flashguard.Target:
    values = dict(project=DEMO, derivative="S32K144", family="s32k1xx", flash_start=0, first_sector_size=0x1000,
                  key_choices=(0, 5, 10, 20), key_slots=None)
    values.update(changes)
    return flashguard.Target(**values)


def build_dirs():
    spec = importlib.util.spec_from_file_location("build_dirs", ROOT / "tools" / "test" / "build_dirs.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ConfigFieldTest(unittest.TestCase):
    def problems(self, data, family="s32k1xx"):
        return flashguard.sector0_problems(data, family, 0, 0x1000)

    def test_reference_is_the_one_of_bootloader_and_updater(self):
        self.assertEqual(flashguard.CONFIG_FIELDS["s32k1xx"].reference, REFERENCE)
        self.assertEqual(canflash.FCF_REFERENCE, REFERENCE)
        self.assertEqual(canflash.FCF_ADDR, 0x400)
        upd = (ROOT / "bootloader" / "platforms" / "s32k1xx" / "upd_family.h").read_text(encoding="utf-8")
        table = re.search(r"fcfReference\[UPD_FCF_SIZE\] = \{(.*?)\};", upd, re.S).group(1)
        table = re.sub(r"/\*.*?\*/", "", table, flags=re.S)
        self.assertEqual(bytes(int(v, 16) for v in re.findall(r"0x([0-9A-F]{2})U", table)), REFERENCE)

    def test_reference_passes(self):
        self.assertEqual(self.problems(image()), [])

    def test_counter_checks_turn_it_red(self):
        cases = {
            "missing": (None, "no flash configuration field"),
            "erased": (b"\xff" * 16, "secures the chip"),
            "secured": (fsec(0xFF), "secures the chip"),
            "mass erase disabled": (fsec(0xEE), "disables mass erase"),
            "backdoor key enabled": (fsec(0xBE), "enables the backdoor key"),
            "FPROT": (REFERENCE[:8] + b"\x00" * 4 + REFERENCE[12:], "protect flash"),
            "FOPT": (REFERENCE[:13] + b"\x7b" + REFERENCE[14:], "FOPT 0x7B"),
        }
        for name, (field, text) in cases.items():
            with self.subTest(case=name):
                problems = self.problems(image(field))
                self.assertEqual(len(problems), 1, problems)
                self.assertIn(text, problems[0])
        partial = image(None)
        partial.update({0x400 + i: 0xFF for i in range(12)})
        self.assertIn("only 12 of 16 bytes", self.problems(partial)[0])

    def test_image_outside_sector_0_and_other_families_are_free(self):
        self.assertEqual(self.problems(image(None, start=0x8000)), [])
        self.assertEqual(self.problems(image(None), family="stm32"), [])
        self.assertEqual(self.problems(image(None), family=None), [])


class CsecRiskTest(unittest.TestCase):
    def test_demo_project(self):
        t = flashguard.target_of_project(DEMO)
        self.assertEqual((t.derivative, t.family, t.flash_start, t.first_sector_size, t.key_slots),
                         ("S32K144", "s32k1xx", 0, 0x1000, 20))
        self.assertTrue(t.csec_capable)
        risk = flashguard.csec_risk(t)
        self.assertEqual(risk.level, "keys")
        self.assertTrue(risk.refuse_mass_erase)
        self.assertIn("20 CSEc key slots", risk.reason)

    def test_levels(self):
        self.assertEqual(flashguard.csec_risk(target()).level, "capable")
        self.assertEqual(flashguard.csec_risk(target(key_slots=0)).level, "capable", "key slots 0 now, but the "
                         "part may carry an earlier partition")
        self.assertEqual(flashguard.csec_risk(target(), depart=0xF).level, "none")
        partitioned = flashguard.csec_risk(target(key_slots=0), depart=0xB)
        self.assertEqual(partitioned.level, "partitioned")
        self.assertTrue(partitioned.refuse_mass_erase)
        self.assertEqual(flashguard.csec_risk(target(key_choices=(0,))).level, "none")
        self.assertEqual(flashguard.csec_risk(target(key_choices=())).level, "none")
        self.assertEqual(flashguard.csec_risk(None).level, "capable")
        # the project's key slots win over a read partition code (the part is about to be partitioned)
        self.assertEqual(flashguard.csec_risk(target(key_slots=20), depart=0xF).level, "keys")

    def test_other_projects_load(self):
        for project in sorted((ROOT / "projects").glob("*/project.yaml")):
            with self.subTest(project=project.parent.name):
                try:
                    t = flashguard.target_of_project(project)
                except ValueError as e:
                    self.assertIn("mcudb", str(e))          # a board whose derivative is not in mcudb yet
                    continue
                self.assertGreater(t.first_sector_size, 0)


class MassEraseTest(unittest.TestCase):
    POSITIVE = [
        "erase",
        "  Erase  ",
        "unlock kinetis",
        "exec EnableEraseAllFlashBanks",
        "kinetis mdm mass_erase",
        "stm32f4x mass_erase 0",
        "flash erase_sector 0 0 last",
        "pyocd erase --chip -t s32k144",
        "pyocd flash --erase chip -t x a.hex",
        "pyocd flash -e chip a.hex",
        "STM32_Programmer_CLI -c port=SWD -e all",
    ]
    NEGATIVE = [
        'loadfile "C:/b/bootloader.hex"',
        'flash write_image erase "C:/b/app.hex"',
        "verify_image a.hex",
        'verifybin "k.bin", 0x0007D000',
        "exec SetSkipDebugDeInit = 1",
        "r", "g", "connect", "exit",
        "# erase",
        "mem32 0x40048054, 4",
        "-w app.hex -v",
        "pyocd flash -t x a.hex",
        "erase 0x7D000,0x7DFFF",            # a range: sector erase, reported as sector 0 erase if it covers it
    ]

    def test_mass_erase_is_found(self):
        for line in self.POSITIVE:
            with self.subTest(line=line):
                self.assertEqual(len(flashguard.mass_erase_commands(f"connect\n{line}\n")), 1)

    def test_normal_commands_are_not(self):
        for line in self.NEGATIVE:
            with self.subTest(line=line):
                self.assertEqual(flashguard.mass_erase_commands(line), [])

    def test_range_erase_of_sector_0(self):
        text = "erase 0x0, 0xFFF\nerase 0x7D000,0x7DFFF\nerase 0x800 0x1800\n"
        self.assertEqual([n for n, _ in flashguard.sector0_erase_commands(text, 0, 0x1000)], [1, 3])


class DebugFlashTest(unittest.TestCase):
    def test_mass_erase_refused_with_way_back(self):
        demo = flashguard.target_of_project(DEMO)
        with self.assertRaises(flashguard.GuardError) as caught:
            flashguard.check_debug_flash("connect\nunlock kinetis\n", [], demo)
        self.assertIn("NVM-13", str(caught.exception))
        self.assertIn("MASTER_ECU_KEY", str(caught.exception))
        with self.assertRaises(flashguard.GuardError):
            flashguard.check_debug_flash("erase\n", [], target(key_slots=0), depart=0x3)

    def test_mass_erase_only_warned_while_the_state_is_unknown(self):
        lines = []
        risk = flashguard.check_debug_flash("erase\n", [], target(), log=lines.append)
        self.assertEqual(risk.level, "capable")
        self.assertTrue(lines and "WARNING" in lines[0] and "NVM-13" in lines[0])
        lines.clear()
        flashguard.check_debug_flash("erase\n", [], target(), depart=0xF, log=lines.append)
        self.assertEqual(lines, [], "read unpartitioned: mass erase works")

    def test_sector_0_image_without_field_refused(self):
        demo = flashguard.target_of_project(DEMO)
        flashguard.check_debug_flash('loadfile "bl.hex"\n', [("bl.hex", image())], demo)
        for field in (None, fsec(0xFF)):
            with self.subTest(field=field):
                with self.assertRaisesRegex(flashguard.GuardError, "bl.hex: "):
                    flashguard.check_debug_flash('loadfile "bl.hex"\n', [("bl.hex", image(field))], demo)
        with self.assertRaisesRegex(flashguard.GuardError, "erases sector 0 on its own"):
            flashguard.check_debug_flash("erase 0x0,0xFFF\n", [], demo)

    def test_flash_targets_of_the_build_do_not_mass_erase(self):
        """Every command the flash targets (CMakeLists.txt nx_add_flash_target) write, for every tool."""
        text = (ROOT / "CMakeLists.txt").read_text(encoding="utf-8")
        body = re.search(r"function \(nx_add_flash_target name\)(.*?)endfunction", text, re.S).group(1)
        commands = "\n".join(line.replace("\\n", "\n") for line in re.findall(r'"([^"\n]*)"', body))
        self.assertIn("loadfile", commands)
        self.assertIn("write_image erase", commands)
        self.assertEqual(flashguard.mass_erase_commands(commands), [])
        self.assertEqual(flashguard.mass_erase_commands(commands + "\nerase\n")[0][1], "erase", "counter-check")

    def test_generated_flash_bootloader_script(self):
        build = build_dirs().demo_build_dir(ROOT)
        script = build / "bootloader" / "RelWithDebInfo" / "flash-bootloader.jlink"
        hexfile = build / "bootloader" / "RelWithDebInfo" / "bootloader.hex"
        if not (script.is_file() and hexfile.is_file()):
            self.skipTest(f"no demo build in {build} (tools/test -k target-demo)")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = flashguard.main(["--project", str(DEMO), "--script", str(script), "--image", str(hexfile)])
        self.assertEqual(code, 0)
        self.assertIn("flashguard: ok", out.getvalue())

    def test_command_line(self):
        with tempfile.TemporaryDirectory() as name:
            tmp = Path(name)
            script = tmp / "x.jlink"
            script.write_text("connect\nerase\nexit\n", encoding="ascii")
            err = io.StringIO()
            with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(flashguard.main(["--project", str(DEMO), "--script", str(script)]), 1)
            self.assertIn("mass erase refused", err.getvalue())
            script.write_text("connect\nexit\n", encoding="ascii")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(flashguard.main(["--project", str(DEMO), "--script", str(script)]), 0)
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(flashguard.main(["--project", str(tmp / "missing.yaml")]), 2)


class ProvisionTest(unittest.TestCase):
    def plan(self) -> provision.Plan:
        return provision.Plan(DEMO, "test", "test", bytes(range(16)), 0x7D000, 0x1000, b"\x5a" * 64, "S32K144",
                              "JTAG", "1000", 0x40048054, 16)

    def test_script_of_provision_passes(self):
        with tempfile.TemporaryDirectory() as name:
            files = provision.write_files(self.plan(), Path(name))
            provision.guard_script(files["script"], DEMO, (files["hex"],))

    def test_guard_refuses_a_mass_erase_in_the_script(self):
        with tempfile.TemporaryDirectory() as name:
            files = provision.write_files(self.plan(), Path(name))
            files["script"].write_text(files["script"].read_text() + "unlock kinetis\n", encoding="ascii")
            with self.assertRaisesRegex(provision.ProvisionError, "NVM-13"):
                provision.guard_script(files["script"], DEMO, (files["hex"],))

    def test_run_is_guarded(self):
        """--run: the guard runs before J-Link; a refused script never reaches run_jlink."""
        with tempfile.TemporaryDirectory() as name, \
                mock.patch.object(provision, "plan_for_project", return_value=self.plan()), \
                mock.patch.object(provision, "run_jlink") as run, \
                mock.patch.object(provision.flashguard, "mass_erase_commands", return_value=[(1, "erase", "x")]), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = provision.main(["--project", str(DEMO), "--uid", bytes(range(16)).hex(), "--out", name, "--run"])
        self.assertEqual(code, 1)
        run.assert_not_called()


def blob(data: bytes) -> bytes:
    head = struct.pack("<III", 0x50554C42, len(data), zlib.crc32(data))
    return head + struct.pack("<I", zlib.crc32(head)) + data


class BlupdateTest(unittest.TestCase):
    BOOTLOADER = bytes(0x400) + REFERENCE + b"\x22" * 0x40

    def test_updater_with_stub_field_passes(self):
        updater = b"\x10" * 0x100 + REFERENCE + b"\x20" * 8 + blob(self.BOOTLOADER)
        canflash.check_updater_stub_field(updater, "s32k1xx")

    def test_updater_without_stub_field_refused(self):
        """The field only inside the embedded bootloader does not count: the stub has to carry its own."""
        updater = b"\x10" * 0x100 + blob(self.BOOTLOADER)
        with self.assertRaisesRegex(ValueError, "NVM-15"):
            canflash.check_updater_stub_field(updater, "s32k1xx")
        canflash.check_updater_stub_field(updater, "stm32")        # family without a field: nothing to check

    def test_built_updater_carries_it(self):
        build = build_dirs().demo_build_dir(ROOT)
        hexfile = build / "bootloader" / "updater" / "RelWithDebInfo" / "blupdater.hex"
        if not hexfile.is_file():
            self.skipTest(f"no demo build in {build} (tools/test -k target-demo)")
        from intelhex import IntelHex
        ih = IntelHex(str(hexfile))
        data = ih.tobinstr(start=min(ih.addresses()), end=max(ih.addresses()))
        canflash.check_updater_stub_field(data, "s32k1xx")

    def test_csec_notice_for_the_demo(self):
        text = canflash.blupdate_csec_notice(DEMO)
        self.assertIn("SECURED", text)
        self.assertIn("NVM-13", text)
        self.assertIn("20 CSEc key slots", text)
        self.assertIsNone(flashguard.csec_notice(flashguard.csec_risk(target(key_choices=()))))

    def test_confirmation_shows_the_notice(self):
        lines = []
        self.assertTrue(canflash.confirm_update("nextbsw-bl 0.3.4", ask=lambda prompt: "UPDATE", out=lines.append,
                                                csec="CSEc: notice"))
        self.assertIn("CSEc: notice", lines)

    def test_cmd_blupdate_refuses_updater_without_stub_field_before_the_bus(self):
        updater = b"\x10" * 0x100 + blob(self.BOOTLOADER)
        err = io.StringIO()
        with mock.patch.object(canflash, "prepare"), \
                mock.patch.object(canflash, "load_app_image", return_value=updater), \
                mock.patch.object(canflash, "check_bootloader_image", return_value="nextbsw-bl 0.3.4"), \
                mock.patch.object(canflash, "selected_family", return_value="s32k1xx"), \
                mock.patch.object(canflash, "open_bus") as open_bus, \
                contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = canflash.main(["blupdate", "blupdater.hex", "--yes"])
        self.assertEqual(code, 1)
        self.assertIn("carries no flash configuration field", err.getvalue())
        open_bus.assert_not_called()

    def test_cmd_blupdate_with_yes_prints_the_notice(self):
        updater = b"\x10" * 0x100 + REFERENCE + blob(self.BOOTLOADER)
        out = io.StringIO()

        def prepare(args):
            args.timing_project = str(DEMO)

        with mock.patch.object(canflash, "prepare", side_effect=prepare), \
                mock.patch.object(canflash, "load_app_image", return_value=updater), \
                mock.patch.object(canflash, "check_bootloader_image", return_value="nextbsw-bl 0.3.4"), \
                mock.patch.object(canflash, "selected_family", return_value="s32k1xx"), \
                mock.patch.object(canflash, "open_bus"), \
                mock.patch.object(canflash, "bootloader_update", return_value=0), \
                contextlib.redirect_stdout(out):
            code = canflash.main(["blupdate", "blupdater.hex", "--yes"])
        self.assertEqual(code, 0)
        self.assertIn("SECURED", out.getvalue())


if __name__ == "__main__":
    unittest.main()
