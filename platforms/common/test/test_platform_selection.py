# SPDX-License-Identifier: Apache-2.0
"""Platform selection of the root CMakeLists.txt for families without an OpenBSW platform (ARCH-25).

- FreeRTOS port selection platforms/common/3rdparty/freertosPort by NX_CORE_ARCHITECTURE: armv6m, armv7m (OpenBSW
  CM4F targets of the family platform), armv7r (rm4x ARM_CRx_No_GIC, PLAT-35), armv8m stops with "port missing",
  armv7m without FPU, armv7r without double precision FPU and a family
  platform without the CM4F targets stop as well; the old place platforms/s32k1xx/3rdparty/freertosPort forwards.
- Root CMakeLists.txt, marked blocks run in CMake script mode with synthetic directories: an own
  platforms/<family>/CMakeLists.txt is enough, external/openbsw/platforms/<family> is only needed without it; the
  CMSIS include directories (cmsis, cmsis/m-profile) only for M-profile architectures.
- The profile headers of the interrupt ranks live under platforms/common/cortex-m|cortex-r (architecture section 8).

Needs cmake on PATH, no compiler. Run: .venv/Scripts/python -m unittest platforms/common/test/test_platform_selection.py
"""

import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
CMAKE = shutil.which("cmake")
COMMON_PORT = REPO / "platforms" / "common" / "3rdparty" / "freertosPort"
OLD_PORT = REPO / "platforms" / "s32k1xx" / "3rdparty" / "freertosPort"
ROOT_CMAKE = REPO / "CMakeLists.txt"
MFPU = {("armv7r", "dp"): "vfpv3-d16", ("armv7m", "sp"): "fpv4-sp-d16"}


def flat(text: str) -> str:
    """CMake wraps long messages; compare with single spaces."""
    return re.sub(r"\s+", " ", text)


def root_block(title: str) -> str:
    text = ROOT_CMAKE.read_text(encoding="utf-8")
    name = re.escape(title)
    match = re.search(rf"^[ \t]*# >>> {name} - Anfang\n(.*?)^[ \t]*# <<< {name} - Ende", text, re.MULTILINE | re.DOTALL)
    assert match, f"block '{title}' not found in CMakeLists.txt"
    return match.group(1)


@unittest.skipIf(CMAKE is None, "cmake not on PATH")
class FreeRtosPortSelectionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="nxport_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def configure(self, core: str, architecture: str, fpu: str = "sp", cm4_targets: bool = True,
                  directory: Path = COMMON_PORT) -> subprocess.CompletedProcess:
        source = self.tmp / "src"
        source.mkdir(exist_ok=True)
        targets = ("add_library(freeRtosCm4SysTick INTERFACE)\nadd_library(freeRtosCm4SysTickPort INTERFACE)\n"
                   if cm4_targets else "")
        (source / "CMakeLists.txt").write_text(
            "cmake_minimum_required(VERSION 3.28)\n"
            f'set(NX_CORE "{core}")\nset(NX_CORE_ARCHITECTURE "{architecture}")\nset(NX_FPU "{fpu}")\n'
            # NX_MFPU as nxconfig writes it (only the Cortex-R port reads it)
            f'set(NX_MFPU "{MFPU.get((architecture, fpu), "")}")\n'
            'set(NX_FAMILY "testfamily")\n'
            "project(portselection NONE)\n"
            # the ARMv6-M port is a real library with port.c; its targets must exist, it is never compiled here
            "add_library(freeRtos INTERFACE)\nadd_library(bspInterrupts INTERFACE)\n"
            + targets +
            f'add_subdirectory("{directory.as_posix()}" sub)\n'
            "get_target_property(port nxFreeRtosPort INTERFACE_LINK_LIBRARIES)\n"
            "get_target_property(impl nxFreeRtosPortImpl INTERFACE_LINK_LIBRARIES)\n"
            'message(STATUS "PORT=${port} IMPL=${impl}")\n', encoding="utf-8")
        return subprocess.run([CMAKE, "-S", str(source), "-B", str(self.tmp / "build"), "-G", "Ninja"],
                              capture_output=True, text=True)

    def selected(self, r: subprocess.CompletedProcess) -> str:
        match = re.search(r"PORT=(\S+) IMPL=(\S+)", r.stdout)
        self.assertIsNotNone(match, r.stdout + r.stderr)
        return f"{match.group(1)} {match.group(2)}"

    def test_armv7m_uses_the_openbsw_cm4f_port(self):
        for core in ("cortex-m4f", "cortex-m7f"):
            with self.subTest(core=core):
                r = self.configure(core, "armv7m")
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual(self.selected(r), "freeRtosCm4SysTickPort freeRtosCm4SysTick")
                shutil.rmtree(self.tmp / "build", ignore_errors=True)

    def test_armv6m_uses_the_cm0_port(self):
        r = self.configure("cortex-m0plus", "armv6m", fpu="none", cm4_targets=False)
        # port.c has no enabled language in this compiler-less project; generation may complain, the selection
        # itself is printed during configuration
        self.assertEqual(self.selected(r), "freeRtosCm0SysTickPort freeRtosCm0SysTick")
        self.assertNotIn("no FreeRTOS port", r.stderr)

    def test_armv7r_uses_the_crx_port(self):
        """PLAT-35: ARM_CRx_No_GIC of platforms/rm4x/3rdparty/freertos_crx; never a Cortex-M port, even when the CM4F
        targets exist."""
        for cm4_targets in (False, True):
            with self.subTest(cm4_targets=cm4_targets):
                r = self.configure("cortex-r4f", "armv7r", fpu="dp", cm4_targets=cm4_targets)
                self.assertEqual(self.selected(r), "freeRtosCrxPort freeRtosCrx")
                self.assertNotIn("no FreeRTOS port", r.stderr)
                shutil.rmtree(self.tmp / "build", ignore_errors=True)

    def test_armv7r_without_double_precision_fpu_stops(self):
        r = self.configure("cortex-r4", "armv7r", fpu="none", cm4_targets=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("needs a VFPv3 double precision FPU", flat(r.stderr))
        self.assertNotIn("PORT=", r.stdout)

    def test_armv8m_stops_with_port_missing(self):
        r = self.configure("cortex-m33", "armv8m")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("no FreeRTOS port for core 'cortex-m33' (armv8m): port missing", flat(r.stderr))

    def test_armv7m_without_fpu_stops(self):
        r = self.configure("cortex-m7", "armv7m", fpu="none")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("needs a core with FPU", flat(r.stderr))

    def test_family_platform_without_cm4f_targets_stops(self):
        r = self.configure("cortex-m4f", "armv7m", cm4_targets=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("platform of family 'testfamily' defines no targets freeRtosCm4SysTick", flat(r.stderr))

    def test_missing_architecture_stops(self):
        r = self.configure("cortex-m4f", "")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("NX_CORE_ARCHITECTURE is not set", flat(r.stderr))

    def test_old_place_forwards(self):
        r = self.configure("cortex-m4f", "armv7m", directory=OLD_PORT)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.selected(r), "freeRtosCm4SysTickPort freeRtosCm4SysTick")
        if not (REPO / "platforms" / "rm4x" / "3rdparty" / "freertos_crx").is_dir():
            return                                  # TOOL-244: the ARMv7-R port of the private family rm4x is not exported
        shutil.rmtree(self.tmp / "build", ignore_errors=True)
        r = self.configure("cortex-r4f", "armv7r", fpu="dp", directory=OLD_PORT)
        self.assertEqual(self.selected(r), "freeRtosCrxPort freeRtosCrx")

    def test_selection_by_architecture_not_core_name(self):
        text = re.sub(r"#.*", "", (COMMON_PORT / "CMakeLists.txt").read_text(encoding="utf-8"))  # code only
        self.assertNotRegex(text, r"NX_CORE\s+STREQUAL")
        self.assertNotIn("__ARM_ARCH", text)
        root = ROOT_CMAKE.read_text(encoding="utf-8")
        # ARCH-57: the path of the tree is NX_ROOT, not the relative path - the root CMakeLists.txt also runs as
        # the build of a workspace whose source directory is the customer repository
        self.assertIn('add_subdirectory("${NX_ROOT}/platforms/common/3rdparty/freertosPort"', root)
        self.assertNotIn("platforms/s32k1xx/3rdparty/freertosPort", root)


@unittest.skipIf(CMAKE is None, "cmake not on PATH")
class RootPlatformTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="nxroot_"))
        self.repo = self.tmp / "repo"
        self.openbsw = self.repo / "external" / "openbsw"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def platform(self, family: str, own: bool, openbsw: bool):
        if own:
            (self.repo / "platforms" / family).mkdir(parents=True, exist_ok=True)
            (self.repo / "platforms" / family / "CMakeLists.txt").write_text("", encoding="utf-8")
        if openbsw:
            (self.openbsw / "platforms" / family).mkdir(parents=True, exist_ok=True)
            (self.openbsw / "platforms" / family / "CMakeLists.txt").write_text("", encoding="utf-8")

    def run_block(self, title: str, variables: dict[str, str], prints) -> subprocess.CompletedProcess:
        script = self.tmp / "block.cmake"
        header = (f'cmake_minimum_required(VERSION 3.28)\nset(CMAKE_CURRENT_SOURCE_DIR "{self.repo.as_posix()}")\n'
                  f'set(OPENBSW_DIR "{self.openbsw.as_posix()}")\n')
        header += "".join(f'set({k} "{v}")\n' for k, v in variables.items())
        footer = "".join(f'message(STATUS "{n}=[${{{n}}}]")\n' for n in prints)
        script.write_text(header + root_block(title) + footer, encoding="utf-8")
        return subprocess.run([CMAKE, "-P", str(script)], capture_output=True, text=True)

    def family(self, family: str) -> subprocess.CompletedProcess:
        return self.run_block("Plattform der Familie (ARCH-25)",
                              {"NX_FAMILY": family, "NX_DERIVATIVE": "TESTDERIVATIVE"},
                              ["NX_FAMILY_PLATFORM_DIR", "NX_FAMILY_OWN_PLATFORM"])

    def test_own_family_platform_is_enough(self):
        self.platform("rm4x", own=True, openbsw=False)
        r = self.family("rm4x")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("NX_FAMILY_OWN_PLATFORM=[ON]", r.stdout)
        self.assertIn(f"NX_FAMILY_PLATFORM_DIR=[{(self.repo / 'platforms' / 'rm4x').as_posix()}]", r.stdout)

    def test_openbsw_platform_without_own_one(self):
        self.platform("s32k1xx", own=False, openbsw=True)
        r = self.family("s32k1xx")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("NX_FAMILY_OWN_PLATFORM=[OFF]", r.stdout)

    def test_no_platform_at_all_stops(self):
        r = self.family("nofamily")
        self.assertNotEqual(r.returncode, 0)
        text = flat(r.stderr)
        self.assertIn("family nofamily of TESTDERIVATIVE has no platform", text)
        self.assertIn("platforms/nofamily/CMakeLists.txt", text)

    def includes(self, architecture: str) -> list[str]:
        r = self.run_block("CMSIS-Pfade nach Kernprofil (ARCH-25)",
                           {"NX_FAMILY": "fam", "NX_CORE_ARCHITECTURE": architecture,
                            "BOARD_GENERATED_DIR": "GEN"}, ["_BSP_MCU_INCLUDES"])
        self.assertEqual(r.returncode, 0, r.stderr)
        match = re.search(r"_BSP_MCU_INCLUDES=\[(.*)\]", r.stdout)
        self.assertIsNotNone(match, r.stdout)
        repo, openbsw = self.repo.as_posix(), self.openbsw.as_posix()
        return [p.replace(openbsw, "OPENBSW").replace(repo, "REPO") for p in match.group(1).split(";")]

    def test_cmsis_only_for_m_profile(self):
        family = ["REPO/platforms/fam/bsp/bspMcu/include"]
        openbsw_family = ["OPENBSW/platforms/fam/bsp/bspMcu/include"]
        cmsis = ["OPENBSW/libs/3rdparty/cmsis", "OPENBSW/libs/3rdparty/cmsis/m-profile"]
        # order as before ARCH-25 for the M profile (byte-identical images)
        self.assertEqual(self.includes("armv6m"),
                         family + ["REPO/platforms/common/3rdparty/cmsis"] + openbsw_family + cmsis + ["GEN"])
        for architecture in ("armv7m", "armv8m"):
            with self.subTest(architecture=architecture):
                self.assertEqual(self.includes(architecture), family + openbsw_family + cmsis + ["GEN"])
        self.assertEqual(self.includes("armv7r"), family + openbsw_family + ["GEN"])


class ProfileLayoutTest(unittest.TestCase):
    COMMON = REPO / "platforms" / "common"

    def test_profile_headers_under_cortex_m_and_cortex_r(self):
        nvic = self.COMMON / "cortex-m" / "interruptPriority" / "include" / "interrupts" / "nxNvicPriority.h"
        vim = self.COMMON / "cortex-r" / "interruptPriority" / "include" / "interrupts" / "nxVimChannel.h"
        self.assertTrue(nvic.is_file())
        self.assertTrue(vim.is_file())
        self.assertFalse((self.COMMON / "interruptPriority" / "cortex-m").exists())
        self.assertFalse((self.COMMON / "interruptPriority" / "cortex-r").exists())
        entry = self.COMMON / "interruptPriority" / "include" / "interrupts" / "nxInterruptPriority.h"
        includes = re.findall(r'^#include "([^"]+)"', entry.read_text(encoding="utf-8"), re.MULTILINE)
        self.assertEqual(sorted((entry.parent / i).resolve() for i in includes), sorted([nvic, vim]))


if __name__ == "__main__":
    unittest.main()
