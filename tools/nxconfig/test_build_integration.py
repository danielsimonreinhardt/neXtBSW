"""Tests of the build selection (ARCH-04): `nxconfig cmake`, build_settings() and the CMake presets.

Synthetic profiles from test/ (other family, no SBC, no debug section) and the repository's demo project.
Plus GeneratedIncludesTest (PLAT-25): every generate that the code includes unconditionally exists after the
generator run of a build (bswgen, blgen, rtegen) - for **every** project under projects/, not only the eval board.
Run: .venv/Scripts/python -m unittest tools/nxconfig/test_build_integration.py
"""

import json
import os
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
sys.path.insert(0, str(HERE.parent))

import nxconfig  # noqa: E402
from nxconfig import config as nxcfg  # noqa: E402

TEST_MCUDB = HERE / "test" / "mcudb"
TEST_BOARD = yaml.safe_load((HERE / "test/profiles/boards/testboard/board.yaml").read_text(encoding="utf-8"))
TEST_PROJECT = yaml.safe_load((HERE / "test/profiles/projects/testproject/project.yaml").read_text(encoding="utf-8"))
# PLAT-62: the own full-featured test profile (synthetic testboard_full/testproject_full) instead of the eval board
# project, which stays private (PLAT-46 F10) - a test against it would be red in the exported open tree. What
# proves something about the eval board itself uses EVAL_DEMO_PROJECT and is skipped where it is missing.
DEMO_PROJECT = REPO / "tools" / "nxconfig" / "test" / "profiles" / "projects" / "testproject_full" / "project.yaml"
EVAL_DEMO_PROJECT = REPO / "projects" / "evalboard_s32k144_demo" / "project.yaml"
eval_board = unittest.skipUnless(EVAL_DEMO_PROJECT.is_file(),
                                 "private eval board project not in this tree (exported open tree, PLAT-62)")
TEST_PROJECTS = HERE / "test" / "profiles" / "projects"
CMAKE = shutil.which("cmake")
NINJA = shutil.which("ninja")
# arm-none-eabi-gcc as for the presets: PATH with NX_ARM_GCC_BIN in front (workstation level)
GCC_PATH = os.pathsep.join(p for p in (os.environ.get("NX_ARM_GCC_BIN"), os.environ.get("PATH")) if p)
ARM_GCC = shutil.which("arm-none-eabi-gcc", path=GCC_PATH)
NX_TOOLCHAIN = REPO / "cmake" / "toolchains" / "NxArmNoneEabi-gcc.cmake"
OPENBSW_TOOLCHAIN = REPO / "external" / "openbsw" / "cmake" / "toolchains" / "ArmNoneEabi-gcc.cmake"

# ---------------------------------------------------------------------------------------------------------------
# PLAT-25: generates that the code includes unconditionally, per project (finding of ARCH-15/PLAT-24)
PROJECTS_DIR = REPO / "projects"
BSWGEN = REPO / "tools" / "bswgen" / "bswgen.py"
BLGEN = REPO / "tools" / "bswgen" / "blgen.py"
RTEGEN = REPO / "tools" / "rtegen" / "rtegen.py"
DIAGGEN = REPO / "tools" / "diaggen" / "diaggen.py"
EPK = REPO / "tools" / "a2lgen" / "epk.py"
BUILDID = REPO / "tools" / "diaggen" / "buildid.py"
SOURCE_SUFFIXES = (".c", ".cpp", ".h", ".hpp", ".inc")
# Roots that a project of this family always builds from; tests, mocks and vendor code are not part of it.
COMMON_ROOTS = ("ecu", "libs", "bootloader", "platforms/common")
SKIP_PARTS = frozenset({"test", "tests", "mock", "mocks", "3rdparty", "generated", "build", "__pycache__"})
# Directories that must not resolve an include of the build: a host test stub of a generate (for example
# platforms/s32k1xx/port/safety/safeSystem/test/host/stubs/integration/safeSystemConfiguration.h) would
# otherwise hide exactly the missing generate this check is about. Vendor code (3rdparty: etl, CMSIS, FreeRTOS)
# stays in the index, the build really includes it.
INDEX_SKIP_PARTS = frozenset({"test", "tests", "mock", "mocks", "stubs", "build", "__pycache__"})
# Both spellings: the port includes its generates as "integration/x.h" and as <integration/x.h> (both reach the
# generate directory, which is an include path of the target). An angle include without a directory is a
# toolchain header (<cstdint>, <stdint.h>) and is left out - it never resolves inside the repository.
INCLUDE_RE = re.compile(r'^\s*#\s*include\s*(?:"(?P<quoted>[^"]+)"|<(?P<angled>[^>]+/[^>]+)>)')
DIRECTIVE_RE = re.compile(r"^\s*#\s*(ifdef|ifndef|if|elif|else|endif)\b(.*)")
# Projects whose generators cannot run yet, with the reason they must report. The include check is skipped for
# them; when the gap closes, this test turns red and the entry has to go (it must not rot into a silent skip).
EXPECTED_GENERATOR_ERRORS = {
    # PLAT-14: the family stm32 has a platform, but no board generator yet (its ECU description has one task and no
    # component since TOOL-114)
    "nucleo_f446re_demo": "family stm32 is not supported by bswgen",
}


class Profiles(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, board=None, project=None) -> Path:
        board_dir = self.tmp / "boards" / "testboard"
        project_dir = self.tmp / "projects" / "testproject"
        board_dir.mkdir(parents=True, exist_ok=True)
        project_dir.mkdir(parents=True, exist_ok=True)
        (board_dir / "board.yaml").write_text(yaml.safe_dump(board or TEST_BOARD, sort_keys=False), encoding="utf-8")
        path = project_dir / "project.yaml"
        path.write_text(yaml.safe_dump(project or TEST_PROJECT, sort_keys=False), encoding="utf-8")
        return path

    def settings(self, board=None, project=None):
        config = nxconfig.load(self.write(board, project), TEST_MCUDB)
        return nxcfg.build_settings(config)

    def board_with_debug(self, **debug):
        board = yaml.safe_load(yaml.safe_dump(TEST_BOARD))
        board["board"]["debug"] = debug
        return board


class BuildSettingsTest(Profiles):
    def test_synthetic_project_selects_board_derivative_family_core(self):
        settings, messages = self.settings()
        self.assertEqual(messages, [])
        self.assertEqual(settings["NX_PROJECT_NAME"], "testproject")
        self.assertEqual(settings["NX_BOARD"], "testboard")
        self.assertEqual(settings["NX_DERIVATIVE"], "TESTMCU64")
        self.assertEqual(settings["NX_FAMILY"], "testfam")
        self.assertEqual(settings["NX_CORE"], "cortex-m33")
        self.assertEqual(settings["NX_PACKAGE"], "QFP64")
        self.assertEqual(settings["NX_PROJECT_DIR"], (self.tmp / "projects" / "testproject").as_posix())
        self.assertEqual(settings["NX_BOARD_DIR"], (self.tmp / "boards" / "testboard").as_posix())
        self.assertEqual(settings["NX_DERIVATIVE_FILE"], (TEST_MCUDB / "testfam" / "TESTMCU64.yaml").as_posix())
        # no project.rte, no board.debug: no RTE file, no J-Link targets
        self.assertEqual(settings["NX_RTE_FILE"], "")
        self.assertEqual((settings["NX_DEBUG_INTERFACE"], settings["NX_JLINK_DEVICE"]), ("", ""))
        # PLAT-26: project, board, derivative and the two task manifests (taskStacks.yaml, tasks.yaml)
        self.assertEqual(len(settings["NX_CONFIG_FILES"]), 5)

    def test_rte_file_is_a_configure_input(self):
        project = yaml.safe_load(yaml.safe_dump(TEST_PROJECT))
        project["project"]["rte"] = "ecu.yaml"
        (self.tmp / "projects" / "testproject").mkdir(parents=True)
        (self.tmp / "projects" / "testproject" / "ecu.yaml").write_text(   # ARCH-34: schema needs one component
            "schema_version: 2\ncomponents:\n  - {name: Swc, path: swc/Swc}\n", encoding="utf-8")   # RTE-22
        settings, _ = self.settings(project=project)
        rte = (self.tmp / "projects" / "testproject" / "ecu.yaml").as_posix()
        self.assertEqual(settings["NX_RTE_FILE"], rte)
        self.assertEqual(settings["NX_CONFIG_FILES"][3], rte)        # after project, board, derivative (PLAT-26)

    def test_jlink_device_follows_the_derivative_interface_the_board(self):
        settings, messages = self.settings(board=self.board_with_debug(interface="swd"))
        self.assertEqual(messages, [])
        self.assertEqual(settings["NX_JLINK_DEVICE"], "TESTMCU64")
        self.assertEqual(settings["NX_DEBUG_INTERFACE"], "SWD")
        self.assertEqual(settings["NX_DEBUG_SPEED_KHZ"], "1000")
        settings, _ = self.settings(board=self.board_with_debug(interface="JTAG", probe="J-Link EDU Mini"))
        self.assertEqual((settings["NX_JLINK_DEVICE"], settings["NX_DEBUG_INTERFACE"]), ("TESTMCU64", "JTAG"))

    def test_debug_speed_and_jlink_device_name_from_the_configuration(self):
        """ARCH-08 (from ARCH-04): board.debug.speed_khz, derivative.jlink_device if J-Link names the chip otherwise."""
        settings, messages = self.settings(board=self.board_with_debug(interface="SWD", speed_khz=4000))
        self.assertEqual(messages, [])
        self.assertEqual((settings["NX_DEBUG_SPEED_KHZ"], settings["NX_JLINK_DEVICE"]), ("4000", "TESTMCU64"))
        mcudb = self.tmp / "mcudb"
        shutil.copytree(TEST_MCUDB, mcudb)
        file = mcudb / "testfam" / "TESTMCU64.yaml"
        doc = yaml.safe_load(file.read_text(encoding="utf-8"))
        doc["derivative"]["jlink_device"] = "TESTMCU64xxx_M33"
        file.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
        config = nxconfig.load(self.write(board=self.board_with_debug(interface="SWD")), mcudb)
        settings, _ = nxcfg.build_settings(config)
        self.assertEqual((settings["NX_JLINK_DEVICE"], settings["NX_DEBUG_SPEED_KHZ"]), ("TESTMCU64xxx_M33", "1000"))
        errors = [m for m in nxconfig.check(self.write(board=self.board_with_debug(interface="SWD", speed_khz=0)),
                                            TEST_MCUDB)[1] if m.severity == "error"]
        self.assertEqual([m.yaml_path for m in errors], ["board.debug.speed_khz"])

    def test_other_probe_gives_no_jlink_targets(self):
        settings, messages = self.settings(board=self.board_with_debug(interface="SWD", probe="ST-Link"))
        self.assertEqual(messages, [])
        self.assertEqual(settings["NX_JLINK_DEVICE"], "")
        self.assertEqual(settings["NX_DEBUG_PROBE"], "ST-Link")

    def test_unsupported_interface_is_reported_with_yaml_path(self):
        settings, messages = self.settings(board=self.board_with_debug(interface="SPI-BDM"))
        self.assertEqual(settings["NX_JLINK_DEVICE"], "")
        self.assertEqual(len(messages), 1)
        msg = messages[0]
        self.assertEqual((msg.severity, msg.yaml_path), ("warning", "board.debug.interface"))
        self.assertIn("SPI-BDM", msg.text)
        self.assertEqual(msg.file, self.tmp / "boards" / "testboard" / "board.yaml")
        self.assertIsInstance(msg.line, int)

    def test_full_test_profile(self):
        """PLAT-62: the same settings for the own full test profile, which has no board port of its own."""
        config = nxconfig.load(DEMO_PROJECT)
        settings, messages = nxcfg.build_settings(config)
        self.assertEqual(messages, [])
        self.assertEqual((settings["NX_BOARD"], settings["NX_DERIVATIVE"], settings["NX_FAMILY"]),
                         ("testboard_full", "S32K144", "s32k1xx"))
        self.assertEqual((settings["NX_JLINK_DEVICE"], settings["NX_DEBUG_INTERFACE"]), ("S32K144", "SWD"))
        self.assertEqual(settings["NX_RTE_FILE"], (DEMO_PROJECT.parent / "rte.yaml").as_posix())
        self.assertFalse((REPO / "ecu" / "platforms" / settings["NX_BOARD"]).exists())
        mcudb = (REPO / "mcudb" / "s32k1xx").as_posix()
        self.assertEqual(settings["NX_CONFIG_FILES"][-2:], [f"{mcudb}/S32K144_LQFP100.pinmux.yaml", f"{mcudb}/S32K148.yaml"])
        self.assertTrue(all(Path(p).is_file() for p in settings["NX_CONFIG_FILES"]))
        self.assertEqual(settings["NX_DEBUG_SPEED_KHZ"], "1000")

    @eval_board
    def test_demo_project(self):
        config = nxconfig.load(EVAL_DEMO_PROJECT)
        settings, messages = nxcfg.build_settings(config)
        self.assertEqual(messages, [])
        self.assertEqual((settings["NX_BOARD"], settings["NX_DERIVATIVE"], settings["NX_FAMILY"]),
                         ("evalboard_s32k144", "S32K144", "s32k1xx"))
        self.assertEqual((settings["NX_JLINK_DEVICE"], settings["NX_DEBUG_INTERFACE"]), ("S32K144", "JTAG"))
        # ARCH-04: the ECU description lives in the project, the board port is chosen by the board name
        self.assertEqual(settings["NX_RTE_FILE"], (EVAL_DEMO_PROJECT.parent / "rte.yaml").as_posix())
        self.assertTrue((REPO / "ecu" / "platforms" / settings["NX_BOARD"] / "Options.cmake").is_file())
        # ARCH-08 (from ARCH-03): the pin multiplexing table and the header derivative are configure inputs too
        mcudb = (REPO / "mcudb" / "s32k1xx").as_posix()
        self.assertEqual(settings["NX_CONFIG_FILES"][-2:], [f"{mcudb}/S32K144_LQFP100.pinmux.yaml", f"{mcudb}/S32K148.yaml"])
        self.assertTrue(all(Path(p).is_file() for p in settings["NX_CONFIG_FILES"]))
        self.assertEqual(settings["NX_DEBUG_SPEED_KHZ"], "1000")


class CoreSettingsTest(unittest.TestCase):
    """PLAT-09: compiler options of the core follow from derivative.core and derivative.fpu (config.CORES)."""

    def settings(self, project: Path, mcudb=TEST_MCUDB):
        settings, _ = nxcfg.build_settings(nxconfig.load(project, mcudb))
        return tuple(settings[n] for n in ("NX_CORE", "NX_FPU", "NX_MCPU", "NX_MFLOAT_ABI", "NX_MFPU"))

    def test_core_options_per_profile(self):
        cases = [
            (TEST_PROJECTS / "testproject_m0plus" / "project.yaml", TEST_MCUDB,
             ("cortex-m0plus", "none", "cortex-m0plus", "soft", "")),
            (TEST_PROJECTS / "testproject_m7f" / "project.yaml", TEST_MCUDB,
             ("cortex-m7f", "dp", "cortex-m7", "hard", "fpv5-d16")),
            (TEST_PROJECTS / "testproject" / "project.yaml", TEST_MCUDB,
             ("cortex-m33", "sp", "cortex-m33", "hard", "fpv5-sp-d16")),
            (TEST_PROJECTS / "testproject_r4f" / "project.yaml", TEST_MCUDB,
             ("cortex-r4f", "dp", "cortex-r4f", "hard", "vfpv3-d16")),
            (DEMO_PROJECT, nxcfg.MCUDB, ("cortex-m4f", "sp", "cortex-m4", "hard", "fpv4-sp-d16")),
        ]
        for project, mcudb, expected in cases:
            with self.subTest(project=project.parent.name):
                self.assertEqual(self.settings(project, mcudb), expected)

    def test_core_architecture_setting(self):
        """PLAT-21: the platform code (FreeRTOS port, fault handler, startup) gets NX_CORE_ARCHITECTURE from here."""
        cases = [(TEST_PROJECTS / "testproject_m0plus" / "project.yaml", TEST_MCUDB, "armv6m"),
                 (TEST_PROJECTS / "testproject_m7f" / "project.yaml", TEST_MCUDB, "armv7m"),
                 (TEST_PROJECTS / "testproject" / "project.yaml", TEST_MCUDB, "armv8m"),
                 (TEST_PROJECTS / "testproject_r4f" / "project.yaml", TEST_MCUDB, "armv7r"),     # ARCH-23
                 (DEMO_PROJECT, nxcfg.MCUDB, "armv7m")]
        for project, mcudb, expected in cases:
            with self.subTest(project=project.parent.name):
                settings, _ = nxcfg.build_settings(nxconfig.load(project, mcudb))
                self.assertEqual(settings["NX_CORE_ARCHITECTURE"], expected)
                self.assertIn(f'set(NX_CORE_ARCHITECTURE "{expected}")', nxcfg.render_cmake(settings, "test"))

    def test_core_table(self):
        self.assertEqual(list(nxcfg.CORES), ["cortex-m0plus", "cortex-m4f", "cortex-m7f", "cortex-m33", "cortex-r4f"])
        for name, core in nxcfg.CORES.items():
            for fpu, mfpu in core.fpus.items():
                with self.subTest(core=name, fpu=fpu):
                    info = nxconfig.model.DerivativeInfo(name="X", family="f", core=name, fpu=fpu, packages=["P"],
                                                         pin_pattern="P")
                    flags = nxcfg.core_flags(info)
                    self.assertEqual((flags["NX_FPU"], flags["NX_MCPU"]), (fpu, core.mcpu))
                    self.assertEqual(flags["NX_MFLOAT_ABI"], "soft" if fpu == "none" else "hard")
                    self.assertEqual(flags["NX_MFPU"] == "", fpu == "none")


@unittest.skipIf(None in (CMAKE, NINJA, ARM_GCC), "cmake, ninja or arm-none-eabi-gcc (NX_ARM_GCC_BIN) not found")
class ToolchainConfigureTest(unittest.TestCase):
    """PLAT-09: cmake/toolchains/NxArmNoneEabi-gcc.cmake configured with test profiles per core, no target build.

    test/toolchain includes the nxconfig settings like the root CMakeLists.txt and builds a probe library; the object
    attributes show which core options reached the compiler.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def env(self, cc: bool) -> dict:
        env = {**os.environ, "PATH": GCC_PATH, "PYTHONUTF8": "1"}
        env.pop("CC", None)
        env.pop("CXX", None)
        if cc:
            env["CC"], env["CXX"] = "arm-none-eabi-gcc", "arm-none-eabi-g++"
        return env

    def configure(self, project: Path, mcudb: Path, name: str, toolchain: Path | None = None, cc=False,
                  build=True) -> tuple[dict[str, str], Path]:
        settings = self.tmp / name / "settings.cmake"
        r = subprocess.run([sys.executable, str(HERE), "cmake", str(project), str(settings), "--mcudb", str(mcudb)],
                           capture_output=True, text=True, encoding="utf-8", env=self.env(cc))
        self.assertEqual(r.returncode, 0, r.stderr)
        build_dir = self.tmp / name / "build"
        run = [CMAKE, "-S", str(HERE / "test" / "toolchain"), "-B", str(build_dir), "-G", "Ninja",
               f"-DNX_SETTINGS_FILE={settings.as_posix()}", "-DCMAKE_C_FLAGS=-Wall -Wextra", "-DCMAKE_CXX_FLAGS=-Wall"]
        if toolchain is not None:
            run.append(f"-DCMAKE_TOOLCHAIN_FILE={toolchain.as_posix()}")
        r = subprocess.run(run, capture_output=True, text=True, env=self.env(cc))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        if build:
            r = subprocess.run([CMAKE, "--build", str(build_dir)], capture_output=True, text=True, env=self.env(cc))
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        flags = dict(line.split("=", 1) for line in (build_dir / "flags.txt").read_text().splitlines() if "=" in line)
        return flags, build_dir

    def attributes(self, build_dir: Path, source: str) -> dict[str, str]:
        obj = next(build_dir.rglob(f"{source}.obj"))
        readelf = Path(ARM_GCC).with_name(Path(ARM_GCC).name.replace("gcc", "readelf"))
        r = subprocess.run([str(readelf), "-A", str(obj)], capture_output=True, text=True, check=True)
        return dict(line.strip().split(": ", 1) for line in r.stdout.splitlines() if line.strip().startswith("Tag_"))

    def multilib(self, flags: str) -> str:
        options = [f for f in flags.split() if f.startswith(("-mcpu=", "-mfloat-abi=", "-mfpu=", "-mthumb"))]
        r = subprocess.run([ARM_GCC, *options, "-print-multi-directory"], capture_output=True, text=True, check=True,
                           env=self.env(False))
        return r.stdout.strip()

    def assert_options(self, flags: dict[str, str], mcpu: str, float_abi: str, mfpu: str | None):
        for kind in ("C", "CXX", "LINK"):
            tokens = flags[kind].split()
            self.assertEqual([t for t in tokens if t.startswith("-mcpu=")], [f"-mcpu={mcpu}"], kind)
            self.assertEqual([t for t in tokens if t.startswith("-mfloat-abi=")], [f"-mfloat-abi={float_abi}"], kind)
            self.assertEqual([t for t in tokens if t.startswith("-mfpu=")], [f"-mfpu={mfpu}"] if mfpu else [], kind)
            self.assertIn("-mthumb" if kind != "LINK" else "-specs=nano.specs", tokens, kind)
        # PLAT-58: the assembler gets the FPU as well (VFP register save in assembler sources), still no float ABI
        self.assertEqual(flags["ASM"].split(), ["-g", f"-mcpu={mcpu}"] + ([f"-mfpu={mfpu}"] if mfpu else []))
        self.assertIn("-Wall -Wextra", flags["C"])                     # cache flags kept behind the toolchain flags

    def test_cortex_m0plus(self):
        flags, build_dir = self.configure(TEST_PROJECTS / "testproject_m0plus", TEST_MCUDB, "m0plus")
        self.assert_options(flags, "cortex-m0plus", "soft", None)
        self.assertTrue(Path(flags["COMPILER"]).stem.startswith("arm-none-eabi-gcc"))   # default without CC
        for source in ("probe.c", "probe.cpp", "probe.S"):
            attrs = self.attributes(build_dir, source)
            self.assertEqual(attrs["Tag_CPU_arch"], "v6S-M", source)
            self.assertNotIn("Tag_FP_arch", attrs, source)
            self.assertNotIn("Tag_ABI_VFP_args", attrs, source)
        self.assertEqual(self.multilib(flags["C"]), "thumb/v6-m/nofp")

    def test_cortex_m7f_double_precision(self):
        flags, build_dir = self.configure(TEST_PROJECTS / "testproject_m7f", TEST_MCUDB, "m7f")
        self.assert_options(flags, "cortex-m7", "hard", "fpv5-d16")
        attrs = self.attributes(build_dir, "probe.c")
        self.assertEqual(attrs["Tag_CPU_arch"], "v7E-M")
        self.assertEqual(attrs["Tag_ABI_VFP_args"], "VFP registers")
        self.assertIn("FPv5", attrs["Tag_FP_arch"])
        self.assertNotIn("Tag_ABI_HardFP_use", attrs)                  # double precision: no "SP only"
        self.assertEqual(self.attributes(build_dir, "probe.S")["Tag_CPU_arch"], "v7E-M")
        self.assertEqual(self.multilib(flags["C"]), "thumb/v7e-m+dp/hard")

    def test_cortex_m33_single_precision(self):
        flags, build_dir = self.configure(TEST_PROJECTS / "testproject", TEST_MCUDB, "m33")
        self.assert_options(flags, "cortex-m33", "hard", "fpv5-sp-d16")
        attrs = self.attributes(build_dir, "probe.c")
        self.assertEqual((attrs["Tag_CPU_arch"], attrs["Tag_ABI_HardFP_use"]), ("v8-M.mainline", "SP only"))

    def test_cortex_r4f_double_precision(self):
        """ARCH-23: ARMv7-R with VFPv3-D16 reaches C, C++ and assembler; the GCC multilib exists (audit ARCH-19)."""
        flags, build_dir = self.configure(TEST_PROJECTS / "testproject_r4f", TEST_MCUDB, "r4f")
        self.assert_options(flags, "cortex-r4f", "hard", "vfpv3-d16")
        for source in ("probe.c", "probe.cpp"):
            attrs = self.attributes(build_dir, source)
            self.assertEqual((attrs["Tag_CPU_arch"], attrs["Tag_CPU_arch_profile"]), ("v7", "Realtime"), source)
            self.assertEqual(attrs["Tag_ABI_VFP_args"], "VFP registers", source)
            self.assertIn("VFPv3-D16", attrs["Tag_FP_arch"], source)
            self.assertNotIn("Tag_ABI_HardFP_use", attrs, source)      # double precision: no "SP only"
        self.assertEqual(self.attributes(build_dir, "probe.S")["Tag_CPU_arch_profile"], "Realtime")
        self.assertEqual(self.multilib(flags["C"]), "thumb/v7+fp/hard")

    @unittest.skipUnless(OPENBSW_TOOLCHAIN.is_file(), "external/openbsw not checked out")
    def test_cortex_m4f_flags_equal_openbsw_toolchain(self):
        """The eval board derivative gets exactly the flags of OpenBSW's ArmNoneEabi-gcc.cmake (byte-identical build)."""
        ours, _ = self.configure(DEMO_PROJECT, nxcfg.MCUDB, "m4f", cc=True, build=False)
        openbsw, _ = self.configure(DEMO_PROJECT, nxcfg.MCUDB, "openbsw", toolchain=OPENBSW_TOOLCHAIN, cc=True,
                                    build=False)
        self.assertEqual({k: v for k, v in ours.items() if k not in ("COMPILER", "ASM")},
                         {k: v for k, v in openbsw.items() if k not in ("COMPILER", "ASM")})
        # the one intended difference (PLAT-58): the assembler also gets the FPU, OpenBSW gives it only the core
        self.assertEqual(ours["ASM"].split(), openbsw["ASM"].split() + ["-mfpu=fpv4-sp-d16"])
        self.assert_options(ours, "cortex-m4", "hard", "fpv4-sp-d16")

    def test_toolchain_file_needs_nxconfig_settings(self):
        (self.tmp / "empty.cmake").write_text("", encoding="utf-8")
        r = subprocess.run([CMAKE, "-S", str(HERE / "test" / "toolchain"), "-B", str(self.tmp / "build"), "-G", "Ninja",
                            f"-DNX_SETTINGS_FILE={(self.tmp / 'empty.cmake').as_posix()}"],
                           capture_output=True, text=True, env=self.env(False))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("needs NX_MCPU", r.stderr)


@unittest.skipIf(None in (CMAKE, NINJA), "cmake or ninja not on PATH")
class ArmV7rStopsLoudlyTest(unittest.TestCase):
    """ARCH-23: the synthetic Cortex-R4F profile (TESTR4F/testboard_r4f/testproject_r4f) configures and hands on
    NX_CORE_ARCHITECTURE armv7r. Platform code without an armv7r variant (FreeRTOS port, hard fault handler, updater
    cycle counter) stops with a message naming core and architecture, instead of building Cortex-M code.

    Only configure steps, no compiler: project(... NONE) resp. cmake -P.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.env = {**os.environ, "PYTHONUTF8": "1"}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def settings(self, project: Path) -> Path:
        out = self.tmp / f"{project.name}.cmake"
        r = subprocess.run([sys.executable, str(HERE), "cmake", str(project), str(out), "--mcudb", str(TEST_MCUDB)],
                           capture_output=True, text=True, encoding="utf-8", env=self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        return out

    def add_subdirectory(self, directory: Path) -> subprocess.CompletedProcess:
        source = self.tmp / "src"
        source.mkdir(exist_ok=True)
        (source / "CMakeLists.txt").write_text(
            "cmake_minimum_required(VERSION 3.28)\n"
            f'include("{self.settings(TEST_PROJECTS / "testproject_r4f").as_posix()}")\n'
            "project(armv7rstop NONE)\n"
            f'add_subdirectory("{directory.as_posix()}" sub)\n', encoding="utf-8")
        return subprocess.run([CMAKE, "-S", str(source), "-B", str(self.tmp / "build"), "-G", "Ninja"],
                              capture_output=True, text=True, env=self.env)

    def updater(self, project: Path) -> subprocess.CompletedProcess:
        """bootloader/updater/CMakeLists.txt in script mode up to its first build command, family s32k1xx (the
        family whose updater has cycle counters), so only the architecture decides."""
        script = self.tmp / "updater.cmake"
        script.write_text(
            f'include("{self.settings(project).as_posix()}")\n'
            "set(NX_FAMILY s32k1xx)\n"
            f'set(CMAKE_SOURCE_DIR "{REPO.as_posix()}")\n'
            f'set(CMAKE_CURRENT_SOURCE_DIR "{(REPO / "bootloader" / "updater").as_posix()}")\n'
            f'include("{(REPO / "bootloader" / "updater" / "CMakeLists.txt").as_posix()}")\n', encoding="utf-8")
        return subprocess.run([CMAKE, "-P", str(script)], capture_output=True, text=True, env=self.env, cwd=self.tmp)

    def test_settings_of_the_profile(self):
        text = self.settings(TEST_PROJECTS / "testproject_r4f").read_text(encoding="utf-8")
        for line in ('set(NX_CORE "cortex-r4f")', 'set(NX_CORE_ARCHITECTURE "armv7r")', 'set(NX_MCPU "cortex-r4f")',
                     'set(NX_MFPU "vfpv3-d16")', 'set(NX_MFLOAT_ABI "hard")', 'set(NX_JLINK_DEVICE "")'):
            self.assertIn(line, text)

    def test_freertos_port_no_longer_stops(self):
        # Since PLAT-35 armv7r selects the FreeRTOS CRx port (platforms/rm4x/3rdparty/freertos_crx); the selection
        # itself is tested in platforms/common/test/test_platform_selection.py. Here only: no "port missing" stop.
        r = self.add_subdirectory(REPO / "platforms" / "s32k1xx" / "3rdparty" / "freertosPort")
        self.assertNotIn("no FreeRTOS port for core 'cortex-r4f'", r.stderr)

    def test_hard_fault_handler_stops(self):
        r = self.add_subdirectory(REPO / "platforms" / "s32k1xx" / "hardFaultHandler")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("nxHardFaultHandler: no hard fault handler variant for core 'cortex-r4f'", r.stderr)
        self.assertIn("(armv7r,", re.sub(r"\s+", " ", r.stderr))

    def test_updater_cycle_counter_stops(self):
        r = self.updater(TEST_PROJECTS / "testproject_r4f")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("blupdater: no cycle counter for core 'cortex-r4f' (armv7r)", re.sub(r"\s+", " ", r.stderr))
        # control: the same script with an ARMv7-M profile passes the cycle counter selection
        r = self.updater(TEST_PROJECTS / "testproject_m7f")
        self.assertNotIn("no cycle counter", r.stderr)


@unittest.skipIf(CMAKE is None, "cmake not on PATH")
class CMakeIncludeTest(Profiles):
    def cmake_values(self, include: Path, names) -> dict[str, str]:
        script = self.tmp / "print.cmake"
        script.write_text(f'include("{include.as_posix()}")\n' +
                          "".join(f'message(STATUS "{n}=[${{{n}}}]")\n' for n in names), encoding="utf-8")
        r = subprocess.run([CMAKE, "-P", str(script)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = {}
        for line in r.stdout.splitlines():
            key, _, value = line.removeprefix("-- ").partition("=")
            out[key] = value[1:-1]
        return out

    def test_values_survive_cmake_quoting(self):
        tricky = {"NX_A": 'C:/dir with space/a"b', "NX_B": "${HOME}\\x", "NX_C": "a;b", "NX_L": ["one", "two words"]}
        include = self.tmp / "tricky.cmake"
        include.write_text(nxcfg.render_cmake(tricky, "test"), encoding="utf-8")
        values = self.cmake_values(include, tricky)
        self.assertEqual(values["NX_A"], tricky["NX_A"])
        self.assertEqual(values["NX_B"], tricky["NX_B"])
        self.assertEqual(values["NX_C"], "a\\;b")          # escaped: stays one list element
        self.assertEqual(values["NX_L"], "one;two words")

    def test_command_line_writes_include_file(self):
        project = self.write()
        out = self.tmp / "build" / "generated" / "nxconfig" / "project.cmake"
        run = [sys.executable, str(HERE), "cmake", str(project.parent), str(out), "--mcudb", str(TEST_MCUDB)]
        env = {**os.environ, "PYTHONUTF8": "1"}
        r = subprocess.run(run, capture_output=True, text=True, encoding="utf-8", env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("testproject: board testboard, TESTMCU64 (testfam, cortex-m33)", r.stdout)
        values = self.cmake_values(out, ["NX_BOARD", "NX_FAMILY", "NX_CORE", "NX_JLINK_DEVICE", "NX_CONFIG_FILES"])
        self.assertEqual((values["NX_BOARD"], values["NX_FAMILY"], values["NX_CORE"]),
                         ("testboard", "testfam", "cortex-m33"))
        self.assertEqual(values["NX_JLINK_DEVICE"], "")
        self.assertEqual(len(values["NX_CONFIG_FILES"].split(";")), 5)   # PLAT-26: + two task manifests
        # unchanged settings do not touch the file (no needless rebuilds)
        stamp = out.stat().st_mtime_ns
        os.utime(out, ns=(stamp - 10**9, stamp - 10**9))
        r = subprocess.run(run, capture_output=True, text=True, encoding="utf-8", env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(out.stat().st_mtime_ns, stamp - 10**9)

    def test_command_line_errors_write_nothing(self):
        project = yaml.safe_load(yaml.safe_dump(TEST_PROJECT))
        project["use"]["can"] = ["BODY"]
        path = self.write(project=project)
        out = self.tmp / "project.cmake"
        r = subprocess.run([sys.executable, str(HERE), "cmake", str(path), str(out), "--mcudb", str(TEST_MCUDB)],
                           capture_output=True, text=True, encoding="utf-8", env={**os.environ, "PYTHONUTF8": "1"})
        self.assertEqual(r.returncode, 1)
        self.assertIn("BODY", r.stderr)
        self.assertFalse(out.exists())


def _configure_presets(presets_file: Path, seen: set | None = None) -> list:
    """configurePresets of a preset file and of the files it includes (ARCH-57: the base presets live in
    cmake/NxPresets.json so a workspace outside the tree can include the same file)."""
    presets_file = Path(presets_file).resolve()
    seen = set() if seen is None else seen
    if presets_file in seen or not presets_file.is_file():
        return []
    seen.add(presets_file)
    data = json.loads(presets_file.read_text(encoding="utf-8"))
    found = [p for p in data.get("configurePresets", []) if isinstance(p, dict) and "name" in p]
    for include in data.get("include", []):
        found += _configure_presets(presets_file.parent / include, seen)
    return found


class PresetsTest(unittest.TestCase):
    """The project is the only selection in the presets; the toolchain location stays on the workstation."""

    @classmethod
    def setUpClass(cls):
        # seit ARCH-57 stehen die Basis-Presets in cmake/NxPresets.json und werden per "include" eingebunden
        cls.presets = {p["name"]: p for p in _configure_presets(REPO / "CMakePresets.json")}

    def resolved(self, name, key):
        preset = self.presets[name]
        if key in preset.get("cacheVariables", {}):
            return preset["cacheVariables"][key]
        parents = preset.get("inherits", [])
        for parent in [parents] if isinstance(parents, str) else parents:
            value = self.resolved(parent, key)
            if value is not None:
                return value
        return None

    def test_every_visible_preset_selects_an_existing_project(self):
        visible = [n for n, p in self.presets.items() if not p.get("hidden")]
        if EVAL_DEMO_PROJECT.is_file():              # PLAT-62: the export drops the presets of private projects
            self.assertIn("evalboard-s32k144-demo-worktree", visible)
        for name in visible:
            project = self.resolved(name, "NX_PROJECT")
            self.assertIsNotNone(project, name)
            self.assertTrue(Path(project.replace("${sourceDir}", str(REPO))).is_file(), (name, project))
            self.assertIsNone(self.resolved(name, "BUILD_TARGET_PLATFORM"), name)

    def test_root_cmake_uses_own_toolchain_file(self):
        root = (REPO / "CMakeLists.txt").read_text(encoding="utf-8")
        self.assertIn("cmake/toolchains/NxArmNoneEabi-gcc.cmake", root)
        self.assertIsNone(re.search(r"(?<!Nx)ArmNoneEabi", root))      # OpenBSW's M4F-only files (PLAT-09)
        self.assertTrue(NX_TOOLCHAIN.is_file())

    def test_own_modules_are_added_without_the_target_all(self):
        """PLAT-25: a module directory of ours is only made known, never built by `all` (only what is linked).

        The check is on the text of the root CMakeLists.txt, because the effect (the target `all` of a board
        without SBC) needs a configured build directory; the target builds show it.
        """
        root = (REPO / "CMakeLists.txt").read_text(encoding="utf-8")
        plain = [args.split()[0] for args in re.findall(r"add_subdirectory\(([^()]*)\)", root)
                 if re.search(r"(?:^|[\s\"])(?:libs|platforms/\$\{NX_FAMILY\}/bsp)/", args)
                 and "EXCLUDE_FROM_ALL" not in args]
        self.assertEqual(plain, [], "module directories must be added with EXCLUDE_FROM_ALL")
        self.assertIn('add_subdirectory("${NX_ROOT}/${_NX_MODULE_DIR}" "${_NX_MODULE_DIR}" EXCLUDE_FROM_ALL)', root)
        for module in ("libs/bsw/sbcUja113x", "platforms/${NX_FAMILY}/bsp/bspLpspi"):
            self.assertIn(module, root)

    def test_no_fixed_toolchain_in_presets(self):
        for name, preset in self.presets.items():
            self.assertNotIn("toolchainFile", preset, name)            # chosen from the derivative's core
            for key, value in preset.get("environment", {}).items():
                self.assertNotIn(":/", value.replace("${sourceDir}", ""), (name, key))   # no absolute paths


def unconditional_includes(file: Path):
    """Yields (include, line) of every #include "..." of `file` that no #if/#ifdef guards.

    The include guard of the file itself (a leading `#ifndef <IDENTIFIER>`) is not a condition; everything else
    is, so a module bound source may include its own generate behind `#if NX_MODULE_..._COUNT`.
    """
    depth, guard = 0, False
    for number, line in enumerate(file.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        directive = DIRECTIVE_RE.match(line)
        if directive:
            kind, rest = directive.group(1), directive.group(2).strip()
            if kind in ("if", "ifdef", "ifndef"):
                if depth == 0 and not guard and kind == "ifndef" and rest.isidentifier():
                    guard = True
                else:
                    depth += 1
            elif kind == "endif":
                depth = max(0, depth - 1)
            continue
        include = INCLUDE_RE.match(line)
        if include and depth == 0:
            yield include.group("quoted") or include.group("angled"), number


def families(repo: Path) -> set[str]:
    """Family names of the repository (platforms/<family>, also below bootloader/platforms)."""
    return {d.name for d in (repo / "platforms").iterdir() if d.is_dir()} - {"common"}


def source_files(repo: Path, family: str):
    """Sources and headers a project of `family` builds: the common roots plus the platform of its family.

    Code of another family is left out wherever it stands (platforms/<family>, bootloader/platforms/<family>,
    bootloader/updater/platforms/<family>): it includes the generates of that family, which a project of this
    family does not have.
    """
    others = families(repo) - {family}
    for root in (*COMMON_ROOTS, f"platforms/{family}"):
        base = repo / root
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            parts = set(path.relative_to(repo).parts)
            if path.suffix in SOURCE_SUFFIXES and path.is_file() and not SKIP_PARTS & parts \
                    and not others & parts:
                yield path


def real_headers(repo: Path) -> dict[str, list[str]]:
    """Every header the build can really include, by file name (external/openbsw too, test stubs left out)."""
    index: dict[str, list[str]] = {}
    for path in repo.rglob("*"):
        if path.suffix in SOURCE_SUFFIXES and not INDEX_SKIP_PARTS & set(path.relative_to(repo).parts) \
                and path.is_file():
            index.setdefault(path.name, []).append(path.as_posix())
    return index


def missing_generates(generated, sources, headers) -> list[tuple[str, Path, int]]:
    """(include, source, line) of every unconditional include that neither exists as a file nor was generated."""
    missing = []
    for path in sources:
        for include, line in unconditional_includes(path):
            if include in generated:
                continue
            if (path.parent / include).is_file():   # quoted include next to the including file (preprocessor rule)
                continue
            if any(p.endswith("/" + include) for p in headers.get(Path(include).name, [])):
                continue
            missing.append((include, path, line))
    return missing


def culprit(include: str) -> str:
    """The generator (and the input) that should have produced this file - the cause, not only the symptom."""
    if include.startswith("integration/") and include.endswith("Configuration.h"):
        module = include[len("integration/"):-len("Configuration.h")]
        return (f"tools/bswgen (modules.py) from the manifest module.yaml of the module {module}: a manifest that "
                "nxconfig does not find (libs/bsw, libs/bsp, platforms/<family>/port, ecu/platforms/<board>) "
                "generates no header")
    if include.startswith("integration/"):
        return "tools/bswgen (modules.py), integration of the board devices"
    if include.startswith(("bootloader/", "memory/")):
        return ("tools/bswgen (blgen.py), bootloader part and memory partitioning of derivative and project "
                "(root CMakeLists.txt and bootloader/CMakeLists.txt each run it)")
    if include.startswith(("Rte", "rte")) or include.endswith(".arxml"):
        return "tools/rtegen, ECU description project.rte of the project"
    return "tools/bswgen, board configuration of the project"


class GeneratedIncludesTest(unittest.TestCase):
    """PLAT-25: per project under projects/, every unconditionally included generate must exist after the run.

    Why per project: after IO-19 the profile buildtest_s32k148_min was not buildable for days because
    integration/safeSystemConfiguration.h was no longer generated (the manifest was only looked for under
    ecu/platforms/<board>). The target build of that profile was not part of the test run, so nothing said a
    word - a silent break (ARCH-15, PLAT-24). This check needs no compiler: it runs the generators of the
    project and compares what the code includes without a condition with what came out.

    Consequence for reusable code: a module may include its own generate unconditionally only if the generator
    writes it for every project (that is the rule for integration/<module>Configuration.h, which exists with
    zero instances as well); everything else belongs behind `#if NX_MODULE_<MODULE>_COUNT`.
    """

    @classmethod
    def setUpClass(cls):
        cls.headers = real_headers(REPO)
        cls.projects = sorted(PROJECTS_DIR.glob("*/project.yaml"))
        cls.sources: dict[str, list[Path]] = {}                     # per family, several projects share one

    def family_sources(self, family: str) -> list[Path]:
        return self.sources.setdefault(family, list(source_files(REPO, family)))

    def generate(self, project: Path, out: Path) -> tuple[dict[str, str], dict[str, str]]:
        """Runs the generators of a build (root CMakeLists.txt and bootloader/CMakeLists.txt).

        bswgen writes the board configuration and blgen the bootloader part into the same directory
        (BOARD_GENERATED_DIR), rtegen the RTE into its own (RTE_GENERATED_DIR), diaggen the UDS jobs into
        DIAG_GENERATED_DIR (ecu/uds/CMakeLists.txt, COM-28); all are include paths of the
        targets, so a file name is measured against its own generate directory.
        """
        files, errors = {}, {}
        for tool, script, directory, args in (("bswgen", BSWGEN, out / "board", []),
                                              ("blgen", BLGEN, out / "board", []),
                                              ("rtegen", RTEGEN, out / "rte", ["--repo", str(REPO)]),
                                              ("diaggen", DIAGGEN, out / "diag", [])):
            run = subprocess.run([sys.executable, str(script), str(project), str(directory), *args],
                                 capture_output=True, text=True, encoding="utf-8",
                                 env={**os.environ, "PYTHONUTF8": "1"})
            if run.returncode != 0:
                errors[tool] = (run.stderr or run.stdout).strip()
            for file in sorted(directory.rglob("*")) if directory.is_dir() else []:
                if file.is_file():
                    files.setdefault(file.relative_to(directory).as_posix(), tool)
        # RTE-26: xcp/xcpEpk.h is written at build time by tools/a2lgen/epk.py (ecu/xcp/CMakeLists.txt, target
        # xcpEpk) into its own include directory - run it the same way
        directory = out / "epk"
        run = subprocess.run([sys.executable, str(EPK), "-o", str(directory / "xcp" / "xcpEpk.h"),
                              "--name", project.parent.name, "--tree", str(REPO), "--project", str(project)],
                             capture_output=True, text=True, encoding="utf-8", env={**os.environ, "PYTHONUTF8": "1"})
        if run.returncode != 0:
            errors["epk"] = (run.stderr or run.stdout).strip()
        for file in sorted(directory.rglob("*")) if directory.is_dir() else []:
            if file.is_file():
                files.setdefault(file.relative_to(directory).as_posix(), "epk")
        # COM-44: diag/DiagBuildId.h is written at build time by tools/diaggen/buildid.py (ecu/uds/CMakeLists.txt)
        directory = out / "buildid"
        run = subprocess.run([sys.executable, str(BUILDID), "-o", str(directory / "diag" / "DiagBuildId.h"),
                              "--tree", str(REPO)],
                             capture_output=True, text=True, encoding="utf-8", cwd=str(REPO),
                             env={**os.environ, "PYTHONUTF8": "1"})
        if run.returncode != 0:
            errors["buildid"] = (run.stderr or run.stdout).strip()
        for file in sorted(directory.rglob("*")) if directory.is_dir() else []:
            if file.is_file():
                files.setdefault(file.relative_to(directory).as_posix(), "buildid")
        return files, errors

    def test_every_project_has_the_generates_its_code_includes(self):
        self.assertTrue(self.projects, "no project under projects/")
        for project in self.projects:
            name = project.parent.name
            with self.subTest(project=name):
                config = nxconfig.load(project)
                header = config.derivative.device_header
                if header is not None and header.generated_by and not os.environ.get("NX_SVD_DIR"):
                    continue    # PLAT-12: device header generated from an SVD on workstation level
                family = nxcfg.build_settings(config)[0]["NX_FAMILY"]
                tmp = Path(tempfile.mkdtemp())
                try:
                    generated, errors = self.generate(project, tmp)
                finally:
                    shutil.rmtree(tmp, ignore_errors=True)
                expected = EXPECTED_GENERATOR_ERRORS.get(name)
                if expected is not None:
                    self.assertTrue(errors, f"{name}: the generators run now - remove the entry in "
                                            f"EXPECTED_GENERATOR_ERRORS and let the check run")
                    self.assertIn(expected, " ".join(errors.values()), f"{name}: other reason than expected")
                    continue
                self.assertEqual(errors, {}, f"{name}: generator failed")
                missing = missing_generates(generated, self.family_sources(family), self.headers)
                report = "\n".join(f"  {name}: {include} missing after {family} generate run\n"
                                   f"    included by {source.relative_to(REPO).as_posix()}:{line}\n"
                                   f"    cause: {culprit(include)}" for include, source, line in missing)
                self.assertEqual(missing, [], f"generates missing for project {name}:\n{report}")

    def test_the_check_names_project_file_and_culprit(self):
        """Test of the check itself with a built in error: a port file includes a generate nobody writes."""
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        source = tmp / "SafeSystem.cpp"
        # angle form as in platforms/s32k1xx/port/safety/safeSystem/include/safeSystem/SafeSystem.h - the file of
        # the break after IO-19; <cstdint> is a toolchain header and no generate
        source.write_text("#include <cstdint>\n"
                          '#include "bsp/Bsp.h"\n'
                          "#include <integration/safeSystemConfiguration.h>\n"
                          '#if NX_MODULE_SBCUJA113X_COUNT\n#include "integration/sbcUja113xConfiguration.h"\n'
                          "#endif\n", encoding="utf-8")
        missing = missing_generates({"integration/moduleConfiguration.h": "bswgen"}, [source],
                                    {"Bsp.h": ["/repo/external/openbsw/libs/bsp/include/bsp/Bsp.h"]})
        self.assertEqual([(i, p.name, line) for i, p, line in missing],
                         [("integration/safeSystemConfiguration.h", "SafeSystem.cpp", 3)])
        self.assertIn("module safeSystem", culprit("integration/safeSystemConfiguration.h"))

    def test_include_guard_is_not_a_condition(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        file = tmp / "guarded.h"
        file.write_text("#ifndef GUARD_H\n#define GUARD_H\n"
                        '#include "always.h"\n'
                        "#ifdef NX_USE_SPI_LPSPI1\n"
                        '#include "conditional.h"\n'
                        "#else\n"
                        '#include "other.h"\n'
                        "#endif\n"
                        '#include "also_always.h"\n'
                        "#endif\n", encoding="utf-8")
        self.assertEqual([i for i, _ in unconditional_includes(file)], ["always.h", "also_always.h"])


if __name__ == "__main__":
    unittest.main()
