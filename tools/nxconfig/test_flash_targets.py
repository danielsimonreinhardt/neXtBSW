"""Tests of the flash targets per debug probe (PLAT-18).

- FlashSettingsTest: board.debug.probe -> probe kind, flash tool candidates and target names (config.build_settings),
  with test profiles per probe (synthetic board and derivative, no S32K1 values) and the real boards.
- FlashTargetConfigureTest: the block "Flash-Ziele je Debug-Probe" of the root CMakeLists.txt configured in a
  project without compiler (project NONE) with the settings of each test profile. Workstation tools are stand-in
  files; the tests read the generated command files and the command lines (ninja -t commands) and never run a
  flash tool. The only target that is really built is the one without a tool: it has to fail with its reason.

Run: .venv/Scripts/python -m unittest tools/nxconfig/test_flash_targets.py
"""

import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE.parent))

import nxconfig  # noqa: E402
from nxconfig import config as nxcfg  # noqa: E402

TEST_MCUDB = HERE / "test" / "mcudb"
TEST_BOARD = yaml.safe_load((HERE / "test/profiles/boards/testboard/board.yaml").read_text(encoding="utf-8"))
TEST_PROJECT = yaml.safe_load((HERE / "test/profiles/projects/testproject/project.yaml").read_text(encoding="utf-8"))
CMAKE = shutil.which("cmake")
NINJA = shutil.which("ninja")
ROOT_CMAKE = REPO / "CMakeLists.txt"
BLOCK_RE = re.compile(r"^# >>> Flash-Ziele je Debug-Probe \(PLAT-18\).*?^# <<< Flash-Ziele je Debug-Probe \(PLAT-18\)[^\n]*\n",
                      re.S | re.M)
# workstation variables of the block: removed from the environment so the tests do not see this PC's tools
WORKSTATION_ENV = ("NX_JLINK_BIN", "NX_STM32_PROGRAMMER_BIN", "NX_OPENOCD_BIN", "NX_PYOCD_BIN", "NX_UNIFLASH_DIR")

# Test profiles per probe: board.debug of the synthetic test board (derivative TESTMCU64, family testfam)
PROBE_PROFILES = {
    "jlink": {"interface": "JTAG", "probe": "J-Link EDU Mini"},
    "stlink": {"interface": "SWD", "probe": "ST-LINK/V2-1"},
    "cmsis-dap": {"interface": "SWD", "probe": "OpenSDA"},
    "daplink": {"interface": "SWD", "probe": "CMSIS-DAP"},
    "xds110": {"interface": "JTAG", "probe": "XDS110"},
}


class ProfileMixin:
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.mcudb = TEST_MCUDB

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def derivative(self, **entries):
        """Copy of the test mcudb with additional derivative entries for TESTMCU64."""
        self.mcudb = self.tmp / "mcudb"
        shutil.copytree(TEST_MCUDB, self.mcudb)
        file = self.mcudb / "testfam" / "TESTMCU64.yaml"
        doc = yaml.safe_load(file.read_text(encoding="utf-8"))
        doc["derivative"].update(entries)
        file.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")

    def config(self, debug=None):
        board = yaml.safe_load(yaml.safe_dump(TEST_BOARD))
        if debug is not None:
            board["board"]["debug"] = debug
        board_dir, project_dir = self.tmp / "boards" / "testboard", self.tmp / "projects" / "testproject"
        board_dir.mkdir(parents=True, exist_ok=True)
        project_dir.mkdir(parents=True, exist_ok=True)
        (board_dir / "board.yaml").write_text(yaml.safe_dump(board, sort_keys=False), encoding="utf-8")
        project = project_dir / "project.yaml"
        project.write_text(yaml.safe_dump(TEST_PROJECT, sort_keys=False), encoding="utf-8")
        return nxconfig.load(project, self.mcudb)

    def settings(self, debug=None):
        return nxcfg.build_settings(self.config(debug))


class FlashSettingsTest(ProfileMixin, unittest.TestCase):
    def test_probe_selects_the_implementations(self):
        expected = {
            "jlink": ("jlink", ["jlink"]),
            "stlink": ("stlink", ["stm32cubeprogrammer", "openocd"]),
            "cmsis-dap": ("cmsis-dap", ["pyocd", "openocd"]),
            "daplink": ("cmsis-dap", ["pyocd", "openocd"]),
            "xds110": ("xds110", ["dslite"]),
        }
        for profile, debug in PROBE_PROFILES.items():
            with self.subTest(profile=profile):
                settings, messages = self.settings(debug)
                self.assertEqual(messages, [])
                self.assertEqual((settings["NX_DEBUG_PROBE_KIND"], settings["NX_FLASH_TOOLS"]), expected[profile])
                self.assertEqual(settings["NX_DEBUG_PROBE"], debug["probe"])
                # the J-Link device name only for a J-Link, as before PLAT-18
                self.assertEqual(settings["NX_JLINK_DEVICE"], "TESTMCU64" if profile == "jlink" else "")

    def test_probe_spellings(self):
        for text, kind in (("J-Link", "jlink"), ("jlink plus", "jlink"), ("ST-Link", "stlink"), ("STLINK-V3", "stlink"),
                           ("OpenSDA", "cmsis-dap"), ("DAPLink", "cmsis-dap"), ("cmsis_dap", "cmsis-dap"),
                           ("XDS110", "xds110"), (None, "jlink"), ("PEmicro Multilink", None), ("", None)):
            with self.subTest(probe=text):
                self.assertEqual(nxcfg.probe_kind(text), kind)

    def test_target_names_follow_the_derivative(self):
        settings, _ = self.settings(PROBE_PROFILES["cmsis-dap"])
        self.assertEqual(settings["NX_PYOCD_TARGET"], "testmcu64")
        self.assertEqual((settings["NX_OPENOCD_TARGET"], settings["NX_OPENOCD_INTERFACE"], settings["NX_OPENOCD_TRANSPORT"]),
                         ("", "interface/cmsis-dap.cfg", "swd"))
        self.derivative(pyocd_target="testmcu64xx", openocd_target="target/testfam.cfg")
        settings, messages = self.settings({"interface": "JTAG", "probe": "ST-LINK"})
        self.assertEqual(messages, [])
        self.assertEqual((settings["NX_OPENOCD_TARGET"], settings["NX_OPENOCD_INTERFACE"], settings["NX_OPENOCD_TRANSPORT"]),
                         ("target/testfam.cfg", "interface/stlink.cfg", "hla_jtag"))
        self.assertEqual(settings["NX_PYOCD_TARGET"], "")                   # ST-LINK: no pyOCD candidate
        settings, _ = self.settings(PROBE_PROFILES["cmsis-dap"])
        self.assertEqual(settings["NX_PYOCD_TARGET"], "testmcu64xx")

    def test_unknown_probe_is_reported_with_yaml_path(self):
        settings, messages = self.settings({"interface": "SWD", "probe": "PEmicro Multilink"})
        self.assertEqual(settings["NX_FLASH_TOOLS"], [])
        self.assertEqual([(m.severity, m.yaml_path) for m in messages], [("warning", "board.debug.probe")])
        self.assertIn("PEmicro Multilink", messages[0].text)
        self.assertIn("stlink (stm32cubeprogrammer/openocd)", messages[0].text)
        self.assertIsInstance(messages[0].line, int)

    def test_interface_the_probe_does_not_drive(self):
        settings, messages = self.settings({"interface": "cJTAG", "probe": "ST-LINK"})
        self.assertEqual(settings["NX_FLASH_TOOLS"], [])
        self.assertEqual([(m.severity, m.yaml_path) for m in messages], [("warning", "board.debug.interface")])
        self.assertIn("does not drive cJTAG", messages[0].text)
        settings, messages = self.settings({"interface": "cJTAG", "probe": "XDS110"})
        self.assertEqual((messages, settings["NX_FLASH_TOOLS"]), ([], ["dslite"]))

    def test_without_debug_section_no_flash_tools(self):
        settings, messages = self.settings()
        self.assertEqual((messages, settings["NX_FLASH_TOOLS"], settings["NX_DEBUG_PROBE_KIND"]), ([], [], ""))

    def test_boards_of_the_repository(self):
        """The eval board stays J-Link; the boards whose probe is no J-Link now get their implementations.

        PLAT-62: plus the own test profile (J-Link over SWD); the eval board project only where it exists (private,
        missing in the exported open tree). TOOL-244: the same for the Nucleo project (family stm32 is private)."""
        projects = REPO / "projects"
        cases = {"buildtest_s32k148_min": ("jlink", ["jlink"], "S32K148", "JTAG"),
                 "nucleo_f446re_demo": ("stlink", ["stm32cubeprogrammer", "openocd"], "", "SWD"),
                 "s32k116evb_minimal": ("cmsis-dap", ["pyocd", "openocd"], "", "SWD")}
        if not (projects / "nucleo_f446re_demo" / "project.yaml").is_file():
            del cases["nucleo_f446re_demo"]
        if not (projects / "s32k116evb_minimal" / "project.yaml").is_file():     # TOOL-243c: private until PLAT-13
            del cases["s32k116evb_minimal"]
        files = {name: projects / name / "project.yaml" for name in cases}
        cases["testproject_full"] = ("jlink", ["jlink"], "S32K144", "SWD")
        files["testproject_full"] = REPO / "tools/nxconfig/test/profiles/projects/testproject_full/project.yaml"
        if (projects / "evalboard_s32k144_demo" / "project.yaml").is_file():
            cases["evalboard_s32k144_demo"] = ("jlink", ["jlink"], "S32K144", "JTAG")
            files["evalboard_s32k144_demo"] = projects / "evalboard_s32k144_demo" / "project.yaml"
        for project, (kind, tools, jlink, interface) in cases.items():
            with self.subTest(project=project):
                settings, messages = nxcfg.build_settings(nxconfig.load(files[project]))
                self.assertEqual(messages, [])
                self.assertEqual((settings["NX_DEBUG_PROBE_KIND"], settings["NX_FLASH_TOOLS"], settings["NX_JLINK_DEVICE"],
                                  settings["NX_DEBUG_INTERFACE"], settings["NX_DEBUG_SPEED_KHZ"]),
                                 (kind, tools, jlink, interface, "1000"))


def fake_tool(directory: Path, name: str) -> Path:
    """Stand-in file for a workstation tool: found by find_program, never executed."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (name + (".exe" if os.name == "nt" else ""))
    path.write_text("stand-in, must not run\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


@unittest.skipIf(None in (CMAKE, NINJA), "cmake or ninja not on PATH")
class FlashTargetConfigureTest(ProfileMixin, unittest.TestCase):
    """Configure of the flash block with test profiles per probe (no compiler, no flash tool is run)."""

    def configure(self, debug=None, tools=None, cache=None, env=None, name="build"):
        settings, _ = nxcfg.build_settings(self.config(debug))
        source = self.tmp / f"src-{name}"
        source.mkdir()
        (source / "settings.cmake").write_text(nxcfg.render_cmake(settings, "test"), encoding="utf-8")
        block = BLOCK_RE.search(ROOT_CMAKE.read_text(encoding="utf-8"))
        self.assertIsNotNone(block, "flash block markers missing in the root CMakeLists.txt")
        (source / "CMakeLists.txt").write_text(
            "cmake_minimum_required(VERSION 3.28)\n"
            'include("${CMAKE_CURRENT_SOURCE_DIR}/settings.cmake")\n'
            "project(flashtargets NONE)\n" + block.group(0) +
            "add_custom_target(app.ecu)\nadd_custom_target(bootloader)\n"
            'nx_add_flash_target(flash DEPENDS app.ecu WHAT "app.ecu.hex and its validity record" '
            'IMAGES "${CMAKE_BINARY_DIR}/app.ecu.hex" "${CMAKE_BINARY_DIR}/app.ecu.record.hex")\n'
            'nx_add_flash_target(flash-bootloader DEPENDS bootloader WHAT "bootloader.hex" '
            'IMAGES "${CMAKE_BINARY_DIR}/bootloader.hex")\n', encoding="utf-8")
        self.build = self.tmp / name
        run_env = {k: v for k, v in os.environ.items() if k not in WORKSTATION_ENV}
        run_env["PATH"] = str(Path(CMAKE).parent)                            # no flash tool of this PC on PATH
        run_env.update(env or {})
        self.env = run_env
        args = [CMAKE, "-S", str(source), "-B", str(self.build), "-G", "Ninja", f"-DCMAKE_MAKE_PROGRAM={NINJA}",
                "-DCMAKE_BUILD_TYPE=Debug", "-DCMAKE_FIND_USE_CMAKE_SYSTEM_PATH=OFF",
                "-DCMAKE_FIND_USE_SYSTEM_ENVIRONMENT_PATH=OFF", "-DCMAKE_IGNORE_PATH=C:/Program Files/SEGGER/JLink"]
        args += [f"-D{k}={v}" for k, v in {**(tools or {}), **(cache or {})}.items()]
        r = subprocess.run(args, capture_output=True, text=True, env=run_env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r

    def command(self, target="flash") -> str:
        r = subprocess.run([NINJA, "-C", str(self.build), "-t", "commands", target], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        line = r.stdout.strip().splitlines()[-1]
        # Windows: ninja wraps the command as cmd.exe /C "cd /D <dir> && <command>"
        return line[:-1] if re.match(r'(?i)\S*cmd\.exe /C "', line) and line.endswith('"') else line

    def script(self, name: str) -> str:
        return (self.build / "Debug" / name).read_text(encoding="utf-8")

    def assert_never_nogui(self, *texts):
        for text in texts:
            self.assertNotIn("nogui", text.lower())

    # ----------------------------------------------------------------------------------------------- J-Link
    def test_jlink_script_as_before(self):
        """Same script as before PLAT-18 (eval board: device, si, speed, jtagconf, connect, r, loadfile, r, g, exit);
        PLAT-86: exec SetSkipDebugDeInit = 1 after connect keeps the DWT of the application running after exit."""
        jlink = fake_tool(self.tmp / "segger", "JLink")
        r = self.configure(PROBE_PROFILES["jlink"], tools={"JLINK_EXE": jlink.as_posix()})
        self.assertIn(f"flash targets: jlink ({jlink.as_posix()}) for probe J-Link EDU Mini", r.stdout)
        b = self.build.as_posix()
        self.assertEqual(self.script("flash.jlink"),
                         "device TESTMCU64\nsi JTAG\nspeed 1000\njtagconf -1,-1\nconnect\n"
                         "exec SetSkipDebugDeInit = 1\nr\n"
                         f'loadfile "{b}/app.ecu.hex"\nloadfile "{b}/app.ecu.record.hex"\nr\ng\nexit\n')
        self.assertEqual(self.script("flash-bootloader.jlink"),
                         "device TESTMCU64\nsi JTAG\nspeed 1000\njtagconf -1,-1\nconnect\n"
                         "exec SetSkipDebugDeInit = 1\nr\n"
                         f'loadfile "{b}/bootloader.hex"\nr\ng\nexit\n')
        command = self.command()
        self.assertTrue(command.endswith(f"-ExitOnError 1 -CommandFile {b}/Debug/flash.jlink"), command)
        self.assertNotIn("-USB", command)
        self.assert_never_nogui(command, self.script("flash.jlink"))

    def test_jlink_swd_speed_and_serial(self):
        jlink = fake_tool(self.tmp / "segger", "JLink")
        self.derivative(jlink_device="TESTMCU64_M33")
        self.configure({"interface": "SWD", "probe": "J-Link", "speed_khz": 4000},
                       tools={"JLINK_EXE": jlink.as_posix()}, cache={"NX_DEBUG_PROBE_SERIAL": "801234567"})
        script = self.script("flash.jlink")
        self.assertTrue(script.startswith("device TESTMCU64_M33\nsi SWD\nspeed 4000\nconnect\n"
                                          "exec SetSkipDebugDeInit = 1\nr\n"), script)
        self.assertNotIn("jtagconf", script)
        self.assertIn("-USB 801234567 -ExitOnError 1 -CommandFile", self.command())

    def test_jlink_from_the_environment_directory(self):
        jlink = fake_tool(self.tmp / "segger_env", "JLink")
        r = self.configure(PROBE_PROFILES["jlink"], env={"NX_JLINK_BIN": str(jlink.parent)})
        self.assertIn(f"flash targets: jlink ({jlink.as_posix()})", r.stdout)

    def test_missing_jlink_gives_a_target_that_says_why(self):
        r = self.configure(PROBE_PROFILES["jlink"])
        self.assertIn("flash targets report when called: no flash tool for probe J-Link EDU Mini (jlink)", r.stdout)
        self.assertIn("JLINK_EXE", r.stdout)
        self.assertFalse((self.build / "Debug" / "flash.jlink").exists())
        for target in ("flash", "flash-bootloader"):
            with self.subTest(target=target):
                build = subprocess.run([CMAKE, "--build", str(self.build), "--target", target], capture_output=True,
                                       text=True, env=self.env)
                self.assertNotEqual(build.returncode, 0)
                output = re.sub(r"\s+", " ", build.stdout + build.stderr)
                self.assertIn(f"{target}: no flash tool for probe J-Link EDU Mini (jlink) on this workstation: "
                              "jlink not found (cache variable JLINK_EXE, environment variable NX_JLINK_BIN", output)

    # ------------------------------------------------------------------------------------------------ ST-LINK
    def test_stlink_stm32cubeprogrammer_first(self):
        cli = fake_tool(self.tmp / "st", "STM32_Programmer_CLI")
        openocd = fake_tool(self.tmp / "openocd", "openocd")
        self.derivative(openocd_target="target/testfam.cfg")
        self.configure(PROBE_PROFILES["stlink"], tools={"NX_STM32_PROGRAMMER_CLI": cli.as_posix(),
                                                         "NX_OPENOCD_EXE": openocd.as_posix()})
        b = self.build.as_posix()
        command = self.command()
        self.assertTrue(command.endswith(f"-c port=SWD freq=1000 -w {b}/app.ecu.hex -v -w {b}/app.ecu.record.hex -v "
                                         "-rst"), command)
        self.assertTrue(self.command("flash-bootloader").endswith(f"-w {b}/bootloader.hex -v -rst"))
        self.assert_never_nogui(command)

    def test_stlink_openocd_by_workstation_choice(self):
        cli = fake_tool(self.tmp / "st", "STM32_Programmer_CLI")
        openocd = fake_tool(self.tmp / "openocd", "openocd")
        self.derivative(openocd_target="target/testfam.cfg")
        r = self.configure(PROBE_PROFILES["stlink"], tools={"NX_STM32_PROGRAMMER_CLI": cli.as_posix(),
                                                             "NX_OPENOCD_EXE": openocd.as_posix()},
                           cache={"NX_FLASH_TOOL": "openocd", "NX_DEBUG_PROBE_SERIAL": "066DFF"})
        self.assertIn("flash targets: openocd", r.stdout)
        b = self.build.as_posix()
        self.assertEqual(self.script("flash.openocd.cfg"),
                         "source [find interface/stlink.cfg]\nadapter serial 066DFF\ntransport select hla_swd\n"
                         "source [find target/testfam.cfg]\nadapter speed 1000\ninit\nreset init\n"
                         f'flash write_image erase "{b}/app.ecu.hex"\nverify_image "{b}/app.ecu.hex"\n'
                         f'flash write_image erase "{b}/app.ecu.record.hex"\nverify_image "{b}/app.ecu.record.hex"\n'
                         "reset run\nshutdown\n")
        self.assertTrue(self.command().endswith(f"-f {b}/Debug/flash.openocd.cfg"))

    def test_stlink_openocd_without_target_script_says_so(self):
        openocd = fake_tool(self.tmp / "openocd", "openocd")
        r = self.configure(PROBE_PROFILES["stlink"], tools={"NX_OPENOCD_EXE": openocd.as_posix()})
        output = re.sub(r"\s+", " ", r.stdout)
        self.assertIn("flash targets report when called: no flash tool for probe ST-LINK/V2-1 (stlink) on this "
                      "workstation: stm32cubeprogrammer not found (cache variable NX_STM32_PROGRAMMER_CLI", output)
        self.assertIn("openocd derivative TESTMCU64 names no OpenOCD target script (derivative.openocd_target in mcudb)",
                      output)

    def test_tool_of_another_probe_is_ignored_with_a_warning(self):
        cli = fake_tool(self.tmp / "st", "STM32_Programmer_CLI")
        r = self.configure(PROBE_PROFILES["stlink"], tools={"NX_STM32_PROGRAMMER_CLI": cli.as_posix()},
                           cache={"NX_FLASH_TOOL": "pyocd"})
        self.assertIn("NX_FLASH_TOOL pyocd does not drive probe ST-LINK/V2-1", re.sub(r"\s+", " ", r.stderr))
        self.assertIn("flash targets: stm32cubeprogrammer", r.stdout)

    # ----------------------------------------------------------------------------------------- OpenSDA/CMSIS-DAP
    def test_cmsis_dap_pyocd(self):
        pyocd = fake_tool(self.tmp / "pyocd_bin", "pyocd")
        r = self.configure(PROBE_PROFILES["cmsis-dap"], env={"NX_PYOCD_BIN": str(pyocd.parent)})
        self.assertIn(f"flash targets: pyocd ({pyocd.as_posix()}) for probe OpenSDA", r.stdout)
        b = self.build.as_posix()
        command = self.command()
        self.assertTrue(command.endswith(f"flash -t testmcu64 -f 1000000 -O dap_protocol=swd {b}/app.ecu.hex "
                                         f"{b}/app.ecu.record.hex"), command)

    def test_cmsis_dap_openocd(self):
        openocd = fake_tool(self.tmp / "openocd", "openocd")
        self.derivative(openocd_target="target/testfam.cfg")
        self.configure({"interface": "JTAG", "probe": "CMSIS-DAP", "speed_khz": 400},
                       tools={"NX_OPENOCD_EXE": openocd.as_posix()})
        script = self.script("flash-bootloader.openocd.cfg")
        self.assertTrue(script.startswith("source [find interface/cmsis-dap.cfg]\ntransport select jtag\n"
                                          "source [find target/testfam.cfg]\nadapter speed 400\n"), script)
        self.assertTrue(script.endswith("reset run\nshutdown\n"))

    # ------------------------------------------------------------------------------------------------- XDS110
    def test_xds110_dslite(self):
        dslite = fake_tool(self.tmp / "uniflash", "dslite")
        ccxml = self.tmp / "RM46.ccxml"
        r = self.configure(PROBE_PROFILES["xds110"], tools={"NX_DSLITE_EXE": dslite.as_posix()})
        self.assertIn("dslite NX_DSLITE_CCXML names no UniFlash target configuration", re.sub(r"\s+", " ", r.stdout))
        ccxml.write_text("<configurations/>\n", encoding="utf-8")
        self.configure(PROBE_PROFILES["xds110"], tools={"NX_DSLITE_EXE": dslite.as_posix()},
                       cache={"NX_DSLITE_CCXML": ccxml.as_posix()}, name="build2")
        b = self.build.as_posix()
        self.assertTrue(self.command().endswith(f"--mode flash --config={ccxml.as_posix()} --flash --verify "
                                                f"{b}/app.ecu.hex {b}/app.ecu.record.hex"), self.command())

    # ---------------------------------------------------------------------------------------- no usable probe
    def test_board_without_probe_description(self):
        for name, debug in (("nodebug", None), ("unknown", {"interface": "SWD", "probe": "PEmicro Multilink"})):
            with self.subTest(board=name):
                r = self.configure(debug, name=name)
                self.assertIn("flash targets report when called: board testboard describes no debug probe the flash "
                              "targets know", re.sub(r"\s+", " ", r.stdout))
                self.assertIn("board testboard describes no debug probe", self.command())


class FlashTargetUseTest(unittest.TestCase):
    """The application and the bootloader use the one implementation of the root CMakeLists.txt."""

    def test_flash_targets_come_from_the_block(self):
        root = ROOT_CMAKE.read_text(encoding="utf-8")
        self.assertIsNotNone(BLOCK_RE.search(root))
        for file, target in (("ecu/application/CMakeLists.txt", "flash"),
                             ("bootloader/CMakeLists.txt", "flash-bootloader")):
            with self.subTest(file=file):
                text = (REPO / file).read_text(encoding="utf-8")
                self.assertRegex(text, rf"nx_add_flash_target\(\s*{target}\s")
                self.assertNotRegex(text, r"(?i)jlink|openocd|pyocd|dslite|STM32_Programmer")
        self.assertNotIn("NoGui", root)


if __name__ == "__main__":
    unittest.main()
