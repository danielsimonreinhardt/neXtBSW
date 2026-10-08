"""Tests of the Cortex-M0+ building blocks without a board (PLAT-10).

- core facts (architecture, NVIC priority bits, RTOS syscall level) come from one table, tools/nxconfig config.CORES;
  the platform CMake code only passes NX_CORE_ARCHITECTURE on (PLAT-21)
- interrupt priority ranks (platforms/common/interruptPriority) on the host with several profiles, invalid profiles
  and ranks must not compile; ARCH-24: the profile comes from NX_CORE_ARCHITECTURE_* (not __ARM_ARCH), the VIM
  counterpart (Cortex-R) maps ranks to channels, and with the ARM compiler a Cortex-R4F translation no longer falls
  into the BASEPRI rules; the s32k1xx SystemTimer.cpp selects LPIT/DWT from the same macros
- build test with the M0+ test profile of PLAT-09 (tools/nxconfig/test, testproject_m0plus): FreeRTOS kernel, port
  selected from NX_CORE, ARMv6-M hard fault handler, ARMv6-M startup template, CMSIS core_cm0plus.h; object attributes
  (readelf -A), instruction set (objdump), vector table and handler symbols (nm) of the linked test ELF

Run: .venv/Scripts/python -m unittest platforms/common/test/test_cortex_m0plus.py
(needs cmake, ninja and arm-none-eabi-gcc from PATH or NX_ARM_GCC_BIN; ziglang in the .venv for the host part)
"""

import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
NXCONFIG = REPO / "tools" / "nxconfig"
sys.path.insert(0, str(NXCONFIG.parent))

from nxconfig import config as nxcfg  # noqa: E402

CMAKE = shutil.which("cmake")
NINJA = shutil.which("ninja")
GCC_PATH = os.pathsep.join(p for p in (os.environ.get("NX_ARM_GCC_BIN"), os.environ.get("PATH")) if p)
ARM_GCC = shutil.which("arm-none-eabi-gcc", path=GCC_PATH)
PRIORITY_INCLUDE = REPO / "platforms" / "common" / "interruptPriority" / "include"
PRIORITY_TEST = REPO / "platforms" / "common" / "interruptPriority" / "test" / "host" / "InterruptPriorityHostTest.c"
M0PLUS_PROJECT = NXCONFIG / "test" / "profiles" / "projects" / "testproject_m0plus" / "project.yaml"
TEST_MCUDB = NXCONFIG / "test" / "mcudb"
STARTUP = REPO / "platforms" / "s32k1xx" / "startup" / "armv6m" / "startUp.S"
PRIORITY_VIM_TEST = REPO / "platforms" / "common" / "interruptPriority" / "test" / "host" / "VimChannelHostTest.c"
SYSTEM_TIMER = REPO / "platforms" / "s32k1xx" / "port" / "bspConfiguration" / "src" / "bsp" / "SystemTimer"
OPENBSW_BSP_INCLUDE = REPO / "external" / "openbsw" / "libs" / "bsw" / "bsp" / "include"
ETL_INCLUDE = REPO / "external" / "openbsw" / "libs" / "3rdparty" / "etl" / "include"  # etl/platform.h (PLAT-58)

# ARCH-24: core macros as tools/bswgen writes them into mcu/derivativeFacts.h
NVIC_V6M = ("NX_CORE_ARCHITECTURE_ARMV6M=1", "NX_CORE_INTERRUPT_CONTROLLER_NVIC=1")
NVIC_V7M = ("NX_CORE_ARCHITECTURE_ARMV7M=1", "NX_CORE_INTERRUPT_CONTROLLER_NVIC=1")
NVIC_V8M = ("NX_CORE_ARCHITECTURE_ARMV8M=1", "NX_CORE_INTERRUPT_CONTROLLER_NVIC=1")
# synthetic VIM like tools/nxconfig/test/mcudb/testfam/TESTR4F.yaml, plus a fixed channel in the middle (16)
VIM_R4F = ("NX_CORE_ARCHITECTURE_ARMV7R=1", "NX_CORE_INTERRUPT_CONTROLLER_VIM=1", "NX_VIM_CHANNEL_COUNT=48",
           "NX_VIM_FIXED_CHANNEL_COUNT=3", "NX_VIM_FIXED_MASK0=0x00010003UL", "NX_VIM_FIXED_MASK1=0UL",
           "NX_VIM_FIXED_MASK2=0UL", "NX_VIM_FIXED_MASK3=0UL")

try:
    import ziglang  # noqa: F401
    ZIG = True
except ImportError:
    ZIG = False

# 32-bit instructions of ARMv6-M (all others are 16-bit Thumb-1)
THUMB1_WIDE = {"bl", "mrs", "msr", "dsb", "dmb", "isb", "udf.w"}
OBJDUMP_LINE = re.compile(r"^\s*[0-9a-f]+:\s+([0-9a-f]{4})(?: ([0-9a-f]{4}))?\s+(\S+)")


def arm_tool(name: str) -> str:
    return str(Path(ARM_GCC).with_name(Path(ARM_GCC).name.replace("gcc", name)))


def non_thumb1_instructions(obj: Path) -> list[str]:
    """Instructions of an object that are not in the ARMv6-M (Thumb-1) instruction set."""
    out = subprocess.run([arm_tool("objdump"), "-d", str(obj)], capture_output=True, text=True, check=True).stdout
    bad = []
    for line in out.splitlines():
        m = OBJDUMP_LINE.match(line)
        if m and m.group(2) and m.group(3) not in THUMB1_WIDE:
            bad.append(line.strip())
    return bad


class CoreArchitectureTableTest(unittest.TestCase):
    """PLAT-21: the cores are described once, in tools/nxconfig/config.py CORES."""

    def test_every_core_has_an_architecture(self):
        table = {name: core.architecture for name, core in nxcfg.CORES.items()}
        self.assertEqual(table["cortex-m0plus"], "armv6m")
        self.assertEqual(table["cortex-m4f"], "armv7m")
        self.assertEqual(table["cortex-m7f"], "armv7m")
        self.assertEqual(table["cortex-m33"], "armv8m")
        self.assertEqual(table["cortex-r4f"], "armv7r")                 # ARCH-23
        self.assertEqual({a for a in table.values()}, {"armv6m", "armv7m", "armv8m", "armv7r"})

    def test_cmake_takes_the_architecture_from_nxconfig(self):
        """No second core table in the platform CMake code; it uses NX_CORE_ARCHITECTURE of "nxconfig cmake"."""
        text = (REPO / "platforms" / "common" / "cmake" / "NxCoreArchitecture.cmake").read_text(encoding="utf-8")
        self.assertEqual(re.findall(r"set\(_(cortex-[a-z0-9]+) (armv[0-9]m)\)", text), [])
        self.assertIn("NX_CORE_ARCHITECTURE", text)

    def test_nvic_priority_bits_of_every_core(self):
        """The NVIC priority bits of the checks come from the same table (ARMv6-M: exactly 2); ARCH-23: only cores
        with an NVIC have them (Cortex-R4F: VIM)."""
        from nxconfig import checks
        nvic = {n: c for n, c in nxcfg.CORES.items() if c.interrupt_controller == "nvic"}
        self.assertEqual(checks.NVIC_PRIORITY_BITS, {n: c.nvic_priority_bits for n, c in nvic.items()})
        for name, core in nvic.items():
            with self.subTest(core=name):
                low, high = core.nvic_priority_bits
                self.assertTrue(2 <= low <= high <= 8)
                # ARMv6-M has no BASEPRI: the RTOS masks everything, so there is no syscall level above 0
                self.assertEqual(core.max_syscall_default == 0, core.architecture == "armv6m")


@unittest.skipUnless(ZIG, "ziglang not installed")
class InterruptPriorityHostTest(unittest.TestCase):
    """Ranks -> NVIC levels (nxInterruptPriority.h), compiled for the host with zig cc."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def compile(self, *defines: str, source: Path = PRIORITY_TEST, profile=NVIC_V7M):
        """profile: the core macros of the generated mcu/derivativeFacts.h (ARCH-24), prepended to the defines."""
        exe = self.tmp / "prio.exe"
        r = subprocess.run([sys.executable, "-m", "ziglang", "cc", "-std=c99", "-Wall", "-Wextra", "-Werror",
                            f"-I{PRIORITY_INCLUDE}", *(f"-D{d}" for d in (*profile, *defines)), str(source), "-o",
                            str(exe)], capture_output=True, text=True)
        return r, exe

    def values(self, *defines: str, profile=NVIC_V7M) -> dict[str, str]:
        source = PRIORITY_VIM_TEST if "NX_CORE_INTERRUPT_CONTROLLER_VIM=1" in profile else PRIORITY_TEST
        r, exe = self.compile(*defines, source=source, profile=profile)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = subprocess.run([str(exe)], capture_output=True, text=True, check=True).stdout
        return dict(item.split("=") for item in out.split())

    def test_profiles(self):
        cases = [
            # bits, max syscall level -> levels, lowest, ranks, kernel, max syscall, level of rank 0, of the last rank
            ((4, 5), ("16", "15", "11", "0xf0", "0x50", "5", "15")),      # S32K144 today (FreeRtosPlatformConfig.h)
            ((2, 1), ("4", "3", "3", "0xc0", "0x40", "1", "3")),
            ((3, 2), ("8", "7", "6", "0xe0", "0x40", "2", "7")),
            ((8, 16), ("256", "255", "240", "0xff", "0x10", "16", "255")),
        ]
        for (bits, level), expected in cases:
            with self.subTest(bits=bits, level=level):
                v = self.values(f"NX_NVIC_PRIORITY_BITS={bits}", f"NX_IRQ_MAX_SYSCALL_LEVEL={level}")
                self.assertEqual(tuple(v[k] for k in ("levels", "lowest", "os_ranks", "kernel", "max_syscall",
                                                      "rank0", "last")), expected)
        # the CAN priority 8 of the eval board is rank 3
        v = self.values("NX_NVIC_PRIORITY_BITS=4", "NX_IRQ_MAX_SYSCALL_LEVEL=5", "NX_TEST_RANK=3")
        self.assertEqual(v["test_rank"], "8")

    def test_invalid_rank_and_profiles_do_not_compile(self):
        ok, _ = self.compile("NX_NVIC_PRIORITY_BITS=2", "NX_IRQ_MAX_SYSCALL_LEVEL=1", "NX_TEST_RANK=2")
        self.assertEqual(ok.returncode, 0, ok.stderr)                  # last available rank
        cases = [
            (("NX_NVIC_PRIORITY_BITS=2", "NX_IRQ_MAX_SYSCALL_LEVEL=1", "NX_TEST_RANK=3"), "array size is negative"),
            (("NX_NVIC_PRIORITY_BITS=4", "NX_IRQ_MAX_SYSCALL_LEVEL=5", "NX_TEST_RANK=11"), "array size is negative"),
            (("NX_NVIC_PRIORITY_BITS=1", "NX_IRQ_MAX_SYSCALL_LEVEL=1"), "must be 2..8"),
            (("NX_NVIC_PRIORITY_BITS=4", "NX_IRQ_MAX_SYSCALL_LEVEL=0"), "must be 1..2^NX_NVIC_PRIORITY_BITS-1"),
            (("NX_NVIC_PRIORITY_BITS=4", "NX_IRQ_MAX_SYSCALL_LEVEL=16"), "must be 1..2^NX_NVIC_PRIORITY_BITS-1"),
            (("NX_NVIC_PRIORITY_BITS=2", "NX_IRQ_MAX_SYSCALL_LEVEL=1", "__NVIC_PRIO_BITS=4"),
             "__NVIC_PRIO_BITS of the device header differs"),
            (("NX_IRQ_MAX_SYSCALL_LEVEL=1",), "needs NX_NVIC_PRIORITY_BITS"),
        ]
        for defines, message in cases:
            with self.subTest(defines=defines):
                r, _ = self.compile(*defines)
                self.assertNotEqual(r.returncode, 0)
                self.assertIn(message, r.stderr)

    def test_architecture_decides_not_the_compiler(self):
        """ARCH-24: the syscall level rules follow NX_CORE_ARCHITECTURE_* (the host compiler has no __ARM_ARCH)."""
        v = self.values("NX_NVIC_PRIORITY_BITS=2", "NX_IRQ_MAX_SYSCALL_LEVEL=0", profile=NVIC_V6M)
        self.assertEqual((v["os_ranks"], v["rank0"], v["last"], v["max_syscall"]), ("4", "0", "3", "0x00"))
        v = self.values("NX_NVIC_PRIORITY_BITS=3", "NX_IRQ_MAX_SYSCALL_LEVEL=2", profile=NVIC_V8M)
        self.assertEqual((v["os_ranks"], v["rank0"]), ("6", "2"))
        limits = ("NX_NVIC_PRIORITY_BITS=4", "NX_IRQ_MAX_SYSCALL_LEVEL=5")
        cases = [
            (NVIC_V6M, ("NX_NVIC_PRIORITY_BITS=2", "NX_IRQ_MAX_SYSCALL_LEVEL=1"), "ARMv6-M has no BASEPRI"),
            (NVIC_V8M, ("NX_NVIC_PRIORITY_BITS=4", "NX_IRQ_MAX_SYSCALL_LEVEL=0"),
             "must be 1..2^NX_NVIC_PRIORITY_BITS-1"),
            ((), limits, "exactly one NX_CORE_ARCHITECTURE_*"),
            (("NX_CORE_ARCHITECTURE_ARMV7M=1",), limits, "exactly one NX_CORE_INTERRUPT_CONTROLLER_*"),
            ((*NVIC_V7M, "NX_CORE_ARCHITECTURE_ARMV6M=1"), limits, "exactly one NX_CORE_ARCHITECTURE_*"),
            ((*NVIC_V7M, "NX_CORE_INTERRUPT_CONTROLLER_VIM=1"), limits, "exactly one NX_CORE_INTERRUPT_CONTROLLER_*"),
            # NVIC rules on a Cortex-R architecture: the M-profile header refuses instead of taking the BASEPRI branch
            (("NX_CORE_ARCHITECTURE_ARMV7R=1", "NX_CORE_INTERRUPT_CONTROLLER_NVIC=1"),
             ("NX_NVIC_PRIORITY_BITS=4", "NX_IRQ_MAX_SYSCALL_LEVEL=0"), "NVIC interrupt priorities are for the Cortex-M"),
        ]
        for profile, defines, message in cases:
            with self.subTest(profile=profile, defines=defines):
                r, _ = self.compile(*defines, profile=profile)
                self.assertNotEqual(r.returncode, 0)
                self.assertIn(message, r.stderr)

    def test_vim_channels(self):
        """ARCH-24: on a VIM the ranks take channels; fixed channels (0, 1, 16 here) are no rank channel."""
        v = self.values(profile=VIM_R4F)
        self.assertEqual(v, {"channels": "48", "os_channels": "45", "fixed": "3", "first_free": "2", "last": "47"})
        self.assertEqual(self.values("NX_TEST_CHANNEL=17", profile=VIM_R4F)["test_channel"], "17")
        # the audit case of ARCH-19 on the VIM profile: syscall level 0 is no BASEPRI error
        self.assertEqual(self.values("NX_IRQ_MAX_SYSCALL_LEVEL=0", profile=VIM_R4F)["os_channels"], "45")
        # a synthetic VIM with 128 channels and a fixed channel in every word
        wide = ("NX_CORE_ARCHITECTURE_ARMV7R=1", "NX_CORE_INTERRUPT_CONTROLLER_VIM=1", "NX_VIM_CHANNEL_COUNT=128",
                "NX_VIM_FIXED_CHANNEL_COUNT=4", "NX_VIM_FIXED_MASK0=0x1UL", "NX_VIM_FIXED_MASK1=0x80000000UL",
                "NX_VIM_FIXED_MASK2=0x10000UL", "NX_VIM_FIXED_MASK3=0x2UL")
        v = self.values("NX_TEST_CHANNEL=64", profile=wide)
        self.assertEqual((v["os_channels"], v["fixed"], v["first_free"], v["last"], v["test_channel"]),
                         ("124", "4", "1", "127", "64"))
        for channel in (63, 80, 97):
            with self.subTest(fixed_channel=channel):
                r, _ = self.compile(f"NX_TEST_CHANNEL={channel}", source=PRIORITY_VIM_TEST, profile=wide)
                self.assertNotEqual(r.returncode, 0)
                self.assertIn("array size is negative", r.stderr)

    def test_invalid_vim_profiles_and_channels_do_not_compile(self):
        cases = [
            (VIM_R4F, ("NX_TEST_CHANNEL=48",), "array size is negative"),             # beyond the derivative
            (VIM_R4F, ("NX_TEST_CHANNEL=16",), "array size is negative"),             # fixed channel in the middle
            (VIM_R4F, ("NX_TEST_CHANNEL=0",), "array size is negative"),              # fixed FIQ channel
            (VIM_R4F, ("NX_NVIC_PRIORITY_BITS=4",), "a VIM core has no NVIC priority bits"),
            (VIM_R4F, ("NX_IRQ_MAX_SYSCALL_LEVEL=1",), "a VIM core has no syscall level"),
            (VIM_R4F[:2], (), "needs NX_VIM_CHANNEL_COUNT"),
            (("NX_CORE_ARCHITECTURE_ARMV7M=1", *VIM_R4F[1:]), (), "VIM interrupt channels are for the Cortex-R"),
            ((*VIM_R4F[:2], "NX_VIM_CHANNEL_COUNT=129", *VIM_R4F[3:]), (), "NX_VIM_CHANNEL_COUNT must be 2..128"),
            ((*VIM_R4F[:3], "NX_VIM_FIXED_CHANNEL_COUNT=48", *VIM_R4F[4:]), (), "leaves no channel"),
        ]
        for profile, defines, message in cases:
            with self.subTest(defines=defines, profile=profile):
                r, _ = self.compile(*defines, source=PRIORITY_VIM_TEST, profile=profile)
                self.assertNotEqual(r.returncode, 0)
                self.assertIn(message, r.stderr)

    def test_every_core_of_the_table_is_known_to_the_header(self):
        """One source: every architecture and controller of nxconfig CORES has its macro in nxInterruptPriority.h."""
        text = (PRIORITY_INCLUDE / "interrupts" / "nxInterruptPriority.h").read_text(encoding="utf-8")
        for name, core in nxcfg.CORES.items():
            with self.subTest(core=name):
                self.assertIn(f"NX_CORE_ARCHITECTURE_{core.architecture.upper()}", text)
                self.assertIn(f"NX_CORE_INTERRUPT_CONTROLLER_{core.interrupt_controller.upper()}", text)
        self.assertNotIn("__ARM_ARCH)", text)


@unittest.skipIf(ARM_GCC is None, "arm-none-eabi-gcc (NX_ARM_GCC_BIN) not found")
class InterruptPriorityCrossTest(unittest.TestCase):
    """ARCH-24, finding ARCH-19: GCC defines __ARM_ARCH 7 for Cortex-R4F and Cortex-M4F. The audit compiled
    nxInterruptPriority.h with syscall level 0 for both: the R4F ran into the BASEPRI #error, the M0+ compiled. Now
    the generated architecture decides, and the compiler profile (__ARM_ARCH_PROFILE) is only cross-checked."""

    CORES = {"r4f": ("-mcpu=cortex-r4f", "-marm"), "m4f": ("-mcpu=cortex-m4", "-mthumb"),
             "m0plus": ("-mcpu=cortex-m0plus", "-mthumb")}

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.env = {**os.environ, "PATH": GCC_PATH}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def compile(self, core: str, *defines: str):
        source = self.tmp / "probe.c"
        source.write_text('#include "interrupts/nxInterruptPriority.h"\nint nxProbe;\n', encoding="utf-8")
        return subprocess.run([ARM_GCC, *self.CORES[core], "-std=c99", "-Wall", "-Wextra", "-Werror", "-fsyntax-only",
                               f"-I{PRIORITY_INCLUDE}", *(f"-D{d}" for d in defines), str(source)],
                              capture_output=True, text=True, env=self.env)

    def test_compiler_defines_the_same_arch_for_r4f_and_m4f(self):
        """The reason for the generated macro, measured: __ARM_ARCH is equal, only the profile differs."""
        found = {}
        for core in ("r4f", "m4f"):
            out = subprocess.run([ARM_GCC, *self.CORES[core], "-dM", "-E", "-x", "c", os.devnull],
                                 capture_output=True, text=True, env=self.env, check=True).stdout
            macros = {}
            for line in out.splitlines():
                parts = line.split(" ", 2)
                if len(parts) == 3:
                    macros[parts[1]] = parts[2]
            found[core] = (macros["__ARM_ARCH"], macros["__ARM_ARCH_PROFILE"])
        self.assertEqual(found, {"r4f": ("7", "82"), "m4f": ("7", "77")})

    def test_r4f_with_syscall_level_0_is_no_basepri_error(self):
        r = self.compile("r4f", *VIM_R4F, "NX_IRQ_MAX_SYSCALL_LEVEL=0")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("BASEPRI", r.stderr)

    def test_m_profile_cases_unchanged(self):
        ok = self.compile("m0plus", *NVIC_V6M, "NX_NVIC_PRIORITY_BITS=2", "NX_IRQ_MAX_SYSCALL_LEVEL=0")
        self.assertEqual(ok.returncode, 0, ok.stderr)
        ok = self.compile("m4f", *NVIC_V7M, "NX_NVIC_PRIORITY_BITS=4", "NX_IRQ_MAX_SYSCALL_LEVEL=5")
        self.assertEqual(ok.returncode, 0, ok.stderr)
        bad = self.compile("m4f", *NVIC_V7M, "NX_NVIC_PRIORITY_BITS=4", "NX_IRQ_MAX_SYSCALL_LEVEL=0")
        self.assertNotEqual(bad.returncode, 0)
        self.assertIn("must be 1..2^NX_NVIC_PRIORITY_BITS-1 (0 disables the BASEPRI masking)", bad.stderr)

    def test_settings_of_another_profile_stop_the_build(self):
        """Wrong settings for the compiler, or no architecture at all (the audit case), are errors, not a branch."""
        cases = [
            ("r4f", (*NVIC_V7M, "NX_NVIC_PRIORITY_BITS=4", "NX_IRQ_MAX_SYSCALL_LEVEL=5"), "Cortex-M compiler profile"),
            ("m4f", VIM_R4F, "Cortex-R compiler profile"),
            ("r4f", ("NX_NVIC_PRIORITY_BITS=4", "NX_IRQ_MAX_SYSCALL_LEVEL=0"), "exactly one NX_CORE_ARCHITECTURE_*"),
        ]
        for core, defines, message in cases:
            with self.subTest(core=core, message=message):
                r = self.compile(core, *defines)
                self.assertNotEqual(r.returncode, 0)
                self.assertIn(message, r.stderr)
                self.assertNotIn("BASEPRI", r.stderr)


@unittest.skipUnless(ZIG and (OPENBSW_BSP_INCLUDE / "bsp").is_dir(), "ziglang or the OpenBSW submodule missing")
class SystemTimerArchitectureTest(unittest.TestCase):
    """ARCH-24: the s32k1xx SystemTimer.cpp selects its counter from NX_CORE_ARCHITECTURE_* (ARMv6-M: LPIT, ARMv7-M:
    DWT), not from __ARM_ARCH; any other architecture stops. Compiled for the host against the register stubs of its
    own host test (the timing itself is tested there, tools/test system-timer-*)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def compile(self, *defines: str):
        return subprocess.run([sys.executable, "-m", "ziglang", "c++", "-std=c++17", "-c", "-Wall", "-Werror",
                               f"-I{SYSTEM_TIMER / 'test' / 'host' / 'stubs'}", "-isystem", str(OPENBSW_BSP_INCLUDE),
                               "-isystem", str(ETL_INCLUDE), *(f"-D{d}" for d in defines), str(SYSTEM_TIMER / "SystemTimer.cpp"), "-o",
                               str(self.tmp / "SystemTimer.o")],
                              capture_output=True, text=True)

    def test_counter_per_architecture(self):
        ok = self.compile("NX_CORE_ARCHITECTURE_ARMV7M=1")
        self.assertEqual(ok.returncode, 0, ok.stderr)
        # ARMv6-M counts the LPIT: without its clock the file stops with the LPIT message, with it it compiles
        lpit = self.compile("NX_CORE_ARCHITECTURE_ARMV6M=1")
        self.assertNotEqual(lpit.returncode, 0)
        self.assertIn("ARMv6-M system timer counts LPIT ticks", lpit.stderr)
        ok = self.compile("NX_CORE_ARCHITECTURE_ARMV6M=1", "NX_CLOCK_LPIT_HZ=48000000U")
        self.assertEqual(ok.returncode, 0, ok.stderr)
        r4f = self.compile("NX_CORE_ARCHITECTURE_ARMV7R=1")
        self.assertNotEqual(r4f.returncode, 0)
        self.assertIn("SystemTimer.cpp is a Cortex-M template", r4f.stderr)
        text = (SYSTEM_TIMER / "SystemTimer.cpp").read_text(encoding="utf-8")
        self.assertNotIn("__ARM_ARCH)", text)

@unittest.skipIf(None in (CMAKE, NINJA, ARM_GCC), "cmake, ninja or arm-none-eabi-gcc (NX_ARM_GCC_BIN) not found")
class CortexM0plusBuildTest(unittest.TestCase):
    """Compile and link the building blocks with the Cortex-M0+ toolchain profile (no board, no derivative header)."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.env = {**os.environ, "PATH": GCC_PATH, "PYTHONUTF8": "1"}
        cls.env.pop("CC", None)
        cls.env.pop("CXX", None)
        settings = cls.tmp / "settings.cmake"
        r = subprocess.run([sys.executable, str(NXCONFIG), "cmake", str(M0PLUS_PROJECT), str(settings), "--mcudb",
                            str(TEST_MCUDB)], capture_output=True, text=True, encoding="utf-8", env=cls.env)
        assert r.returncode == 0, r.stderr
        cls.build = cls.tmp / "build"
        r = subprocess.run([CMAKE, "-S", str(HERE / "cortexm0plus"), "-B", str(cls.build), "-G", "Ninja",
                            f"-DNX_SETTINGS_FILE={settings.as_posix()}"], capture_output=True, text=True, env=cls.env)
        assert r.returncode == 0, r.stdout + r.stderr
        cls.result = subprocess.run([CMAKE, "--build", str(cls.build)], capture_output=True, text=True, env=cls.env)
        cls.elf = cls.build / "m0plus.elf"

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def obj(self, pattern: str) -> Path:
        found = sorted(self.build.rglob(pattern))
        self.assertEqual(len(found), 1, pattern)
        return found[0]

    def attributes(self, obj: Path) -> dict[str, str]:
        out = subprocess.run([arm_tool("readelf"), "-A", str(obj)], capture_output=True, text=True, check=True).stdout
        return dict(line.strip().split(": ", 1) for line in out.splitlines() if line.strip().startswith("Tag_"))

    def symbols(self) -> dict[str, tuple[int, str]]:
        out = subprocess.run([arm_tool("nm"), str(self.elf)], capture_output=True, text=True, check=True).stdout
        return {name: (int(addr, 16), kind) for addr, kind, name in (l.split() for l in out.splitlines()
                                                                   if len(l.split()) == 3)}

    def test_build_links(self):
        self.assertEqual(self.result.returncode, 0, self.result.stdout + self.result.stderr)
        self.assertNotIn("warning", self.result.stdout.lower())

    def test_objects_are_armv6m_without_fpu(self):
        objects = {
            "FreeRTOS port": "freertos_cm0_sysTick/**/port.c.obj",
            "FreeRTOS kernel": "freertos/**/tasks.c.obj",
            "hard fault handler": "armv6m/hardFaultHandler.S.obj",
            "dump reader": "HardFaultDump.cpp.obj",
            "startup": "startUp.S.obj",
            "CMSIS core_cm0plus.h": "cmsis_probe.c.obj",
        }
        for what, pattern in objects.items():
            with self.subTest(what=what):
                attrs = self.attributes(self.obj(pattern))
                self.assertEqual(attrs["Tag_CPU_arch"], "v6S-M")
                self.assertEqual(attrs.get("Tag_THUMB_ISA_use"), "Thumb-1")
                self.assertNotIn("Tag_FP_arch", attrs)
                self.assertNotIn("Tag_ABI_VFP_args", attrs)
        self.assertEqual(self.attributes(self.elf)["Tag_CPU_arch"], "v6S-M")

    def test_only_thumb1_instructions(self):
        for pattern in ("armv6m/hardFaultHandler.S.obj", "startUp.S.obj", "freertos_cm0_sysTick/**/port.c.obj"):
            with self.subTest(object=pattern):
                self.assertEqual(non_thumb1_instructions(self.obj(pattern)), [])

    def test_thumb1_check_detects_thumb2(self):
        """The instruction check itself: an ARMv7-M object with IT block and 32-bit data processing is reported."""
        source = self.tmp / "thumb2.S"
        source.write_text(".syntax unified\n.arch armv7-m\n.thumb\n.text\nf:\n cmp r0, #1\n it eq\n addeq r1, r1, #300\n"
                          " ldr r2, [r1], #4\n bx lr\n", encoding="utf-8")
        obj = self.tmp / "thumb2.o"
        subprocess.run([ARM_GCC, "-c", str(source), "-o", str(obj)], check=True, env=self.env)
        bad = non_thumb1_instructions(obj)
        self.assertEqual(len(bad), 2, bad)                          # addeq.w, ldr.w post-index (it is 16-bit)

    def test_handler_has_no_fault_status_registers(self):
        out = subprocess.run([arm_tool("objdump"), "-d", str(self.obj("armv6m/hardFaultHandler.S.obj"))],
                             capture_output=True, text=True, check=True).stdout
        self.assertNotIn("e000ed28", out.lower())                    # SCB->CFSR of the ARMv7-M handler
        self.assertIn("movs\tr6, #2", out)                           # DUMP_FLAG_NO_FAULT_STATUS
        strings = subprocess.run([arm_tool("strings"), str(self.obj("HardFaultDump.cpp.obj"))], capture_output=True,
                                 text=True, check=True).stdout
        self.assertIn("CFSR/HFSR/MMFAR/BFAR n/a", strings)           # reader compiled with the ARMv6-M report

    def test_port_calls_setup_applications_isr(self):
        out = subprocess.run([arm_tool("objdump"), "-dr", str(self.obj("freertos_cm0_sysTick/**/port.c.obj"))],
                             capture_output=True, text=True, check=True).stdout
        body = out.split("<vPortSetupTimerInterrupt>:", 1)[1].split("\n\n", 1)[0]
        self.assertIn("R_ARM_THM_CALL\tsetupApplicationsIsr", body)

    def test_vector_table(self):
        sym = self.symbols()
        out = subprocess.run([arm_tool("objcopy"), "-O", "binary", "--only-section=.isr_vector", str(self.elf),
                              str(self.tmp / "vectors.bin")], capture_output=True, text=True, env=self.env)
        self.assertEqual(out.returncode, 0, out.stderr)
        data = (self.tmp / "vectors.bin").read_bytes()
        self.assertEqual(len(data), (16 + 32) * 4)
        vectors = struct.unpack(f"<{len(data) // 4}I", data)
        self.assertEqual(vectors[0], sym["__StackTop"][0])
        expected = {1: "Reset_Handler", 3: "HardFault_Handler", 11: "SVC_Handler", 14: "PendSV_Handler",
                    15: "SysTick_Handler", 16: "TEST_IRQ0_Handler", 47: "TEST_IRQ31_Handler"}
        for index, name in expected.items():
            with self.subTest(vector=index):
                self.assertEqual(vectors[index], sym[name][0] | 1, name)
        self.assertEqual([vectors[i] for i in (4, 5, 6, 7, 8, 9, 10, 12, 13, 18, 46)], [0] * 11)   # reserved
        # port handlers under the vector names, IRQ handlers weak and defaulting to DefaultISR
        self.assertNotEqual(sym["SVC_Handler"][0], sym["DefaultISR"][0])
        self.assertEqual(sym["SysTick_Handler"][1], "T")
        self.assertEqual(sym["TEST_IRQ0_Handler"], (sym["DefaultISR"][0], "W"))
        self.assertEqual(sym["HardFault_Handler"][1], "T")

    def test_startup_rejects_more_than_32_interrupts(self):
        include = self.tmp / "irq33" / "startup"
        include.mkdir(parents=True)
        (include / "nxIrqVectors.inc").write_text("".join(f"NX_IRQ_VECTOR(IRQ{i}_Handler)\n" for i in range(33)),
                                                  encoding="utf-8")
        r = subprocess.run([ARM_GCC, "-mcpu=cortex-m0plus", "-c", f"-I{include.parent}", str(STARTUP), "-o",
                            str(self.tmp / "irq33.o")], capture_output=True, text=True, env=self.env)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("at most 32 external interrupts", r.stderr)


if __name__ == "__main__":
    unittest.main()
