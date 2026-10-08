"""BL-34: the bootloader switches the low-voltage detect reset on (SYS-125, safety review SYS125-R5 a, SM-59).

SYS-125 set PMC_LVDSC1[LVDRE] in the application's boardInit(); LVDRE is 0 after power-on, so the bootloader ran
without LVD reset from power-on to the jump - and for as long as a tester holds it for an update. The bootloader now
calls the family port's own function (platforms/s32k1xx/port/main/include/pmc/LowVoltageDetectReset.h) from
bl_system_init() in the reset handler, through the one C++ unit bootloader/platforms/s32k1xx/bl_lvd_s32k1xx.cpp.

Covered without hardware:
- bl_lvd_s32k1xx.cpp as the bootloader builds it, on a register model of PMC_LVDSC1 that counts reads and writes
  (device header S32K148_PMC.h before the port header, so its static_asserts against the device header are compiled
  in): from every start value - power-on 0, LVDIE left by older code, LVDF set, all ones - exactly one write of 0x10
  (LVDRE on, LVDIE off, LVDACK not written), no read, and the port's own check lowVoltageDetectResetOn() holds
- the value is the port's LVDSC1_START_VALUE and the reference manual's LVDRE bit
- wiring, readable without a compiler: bl_system_init() calls it right after the watchdog write; the family
  cmake adds the unit and gives only it the port's include directory; the target links as C

The target image itself (bl_system_init -> one strb 0x10 to 0x4007D000 before the RAM init) is checked by
bootloader/test/test_bl_reset_path_elf.py.

Run: .venv/Scripts/python -m unittest bootloader/test/host/test_bl_lvd_host.py
(needs ziglang in the .venv)
"""

import ctypes
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
BOOTLOADER = REPO / "bootloader"
FAMILY = BOOTLOADER / "platforms" / "s32k1xx"
LVD_CPP = FAMILY / "bl_lvd_s32k1xx.cpp"
PORT_INCLUDE = REPO / "platforms" / "s32k1xx" / "port" / "main" / "include"
VENDOR = REPO / "external" / "openbsw" / "platforms" / "s32k1xx" / "bsp" / "bspMcu" / "include" / "3rdparty" / "nxp"

try:
    import ziglang  # noqa: F401

    HAVE_ZIG = True
except ImportError:  # pragma: no cover - the test skips without the host compiler
    HAVE_ZIG = False

LVDRE, LVDIE, LVDACK, LVDF = 0x10, 0x20, 0x40, 0x80  # [RM13] 41.7.1.2

MODEL_MCU_H = r"""
#pragma once
/* Register model of PMC_LVDSC1 for the host test of bl_lvd_s32k1xx.cpp: the vendor header gives the field masks
 * (the port header checks itself against them), IP_PMC points at a model whose register counts its accesses. */
#include "S32K148_COMMON.h"
#include "S32K148_PMC.h"
#include <cstdint>
struct ModelReg8
{
    uint8_t value;
    uint32_t writes;
    uint32_t reads;
    ModelReg8& operator=(uint8_t v)
    {
        value = v;
        writes++;
        return *this;
    }
    operator uint8_t()
    {
        reads++;
        return value;
    }
};
struct ModelPmc
{
    ModelReg8 LVDSC1;
};
extern ModelPmc bl_model_pmc;
#undef IP_PMC
#define IP_PMC (&bl_model_pmc)
"""

HARNESS = r"""
#include "mcu/mcu.h"
#include "bl_lvd_s32k1xx.h"
#include "pmc/LowVoltageDetectReset.h"
#define EXPORT extern "C" __declspec(dllexport)
ModelPmc bl_model_pmc;
EXPORT uint32_t start_from(uint32_t before)
{
    bl_model_pmc.LVDSC1.value  = static_cast<uint8_t>(before);
    bl_model_pmc.LVDSC1.writes = 0U;
    bl_model_pmc.LVDSC1.reads  = 0U;
    bl_lvd_reset_enable();
    return bl_model_pmc.LVDSC1.value;
}
EXPORT uint32_t writes(void) { return bl_model_pmc.LVDSC1.writes; }
EXPORT uint32_t reads(void) { return bl_model_pmc.LVDSC1.reads; }
EXPORT uint32_t port_start_value(void) { return ::platform::pmc::LVDSC1_START_VALUE; }
EXPORT int port_check(uint32_t value) { return ::platform::pmc::lowVoltageDetectResetOn(static_cast<uint8_t>(value)); }
EXPORT uint32_t vendor_lvdre(void) { return PMC_LVDSC1_LVDRE_MASK; }
"""


def code(path: Path) -> str:
    """Source without C/C++ comments."""
    return re.sub(r"/\*.*?\*/|//[^\n]*", "", path.read_text(encoding="utf-8"), flags=re.S)


@unittest.skipUnless(HAVE_ZIG, "ziglang not installed (host compiler)")
class LvdUnitTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.mkdtemp(prefix="bl34_")
        tmp = Path(cls._tmp)
        (tmp / "mcu").mkdir()
        (tmp / "mcu" / "mcu.h").write_text(MODEL_MCU_H, encoding="utf-8")
        harness = tmp / "harness.cpp"
        harness.write_text(HARNESS, encoding="utf-8")
        cmd = [sys.executable, "-m", "ziglang", "c++", "-target", "x86_64-windows-gnu", "-shared", "-O1",
               "-std=c++17", "-fno-exceptions", "-fno-rtti", "-Wall", "-Wextra", "-Wconversion", "-Werror",
               f"-I{tmp}", f"-I{FAMILY}", f"-I{PORT_INCLUDE}", f"-I{VENDOR}", str(LVD_CPP), str(harness),
               "-o", str(tmp / "lvd.dll")]
        result = subprocess.run(cmd, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr[-4000:]
        cls.lib = ctypes.CDLL(str(tmp / "lvd.dll"))
        for name in ("start_from", "writes", "reads", "port_start_value", "vendor_lvdre"):
            getattr(cls.lib, name).restype = ctypes.c_uint32
        cls.lib.start_from.argtypes = [ctypes.c_uint32]
        cls.lib.port_check.argtypes = [ctypes.c_uint32]

    @classmethod
    def tearDownClass(cls):
        if sys.platform == "win32":
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.FreeLibrary.argtypes = [ctypes.c_void_p]
            kernel32.FreeLibrary(ctypes.c_void_p(cls.lib._handle))
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def test_one_write_of_lvdre_from_every_start_value(self):
        for before in (0x00, LVDIE, LVDIE | LVDRE, LVDRE, LVDF, LVDF | LVDIE, 0xFF):
            with self.subTest(before=hex(before)):
                after = self.lib.start_from(before)
                self.assertEqual(after, LVDRE, "LVDRE on, LVDIE off, LVDACK and reserved bits not written")
                self.assertEqual(self.lib.writes(), 1, "exactly one write")
                self.assertEqual(self.lib.reads(), 0, "no read-modify-write")
                self.assertTrue(self.lib.port_check(after), "the port's check accepts the bootloader's value")

    def test_value_is_the_ports_and_the_reference_manuals(self):
        self.assertEqual(self.lib.port_start_value(), LVDRE)
        self.assertEqual(self.lib.vendor_lvdre(), LVDRE)
        self.assertFalse(self.lib.port_check(LVDRE | LVDIE), "an interrupt instead of the reset is not accepted")
        self.assertFalse(self.lib.port_check(0x00))


class WiringTest(unittest.TestCase):
    def test_reset_handler_path(self):
        hal = code(FAMILY / "hal_s32k1xx.c")
        init = re.search(r"void bl_system_init\(void\)\s*\{(.*?)\n\}", hal, re.S).group(1)
        statements = [s.strip() for s in init.split(";") if s.strip()]
        self.assertEqual(statements[-1], "bl_lvd_reset_enable()", "the LVD reset is the last step of the system init")
        self.assertTrue(statements[-2].startswith("IP_WDOG->CS"), "right after the watchdog write")
        self.assertEqual(len(re.findall(r"\bbl_lvd_reset_enable\s*\(", hal)), 1)
        startup = (FAMILY / "startup.S").read_text(encoding="utf-8")
        handler = startup[startup.index("Reset_Handler:"):]
        self.assertLess(handler.index("bl      bl_system_init"), handler.index("__data_load"),
                        "bl_system_init runs before the RAM is initialized")

    def test_unit_uses_the_port_header_after_the_device_header(self):
        unit = code(LVD_CPP)
        includes = re.findall(r'#include\s+"([^"]+)"', unit)
        self.assertIn("pmc/LowVoltageDetectReset.h", includes)
        self.assertLess(includes.index("mcu/mcu.h"), includes.index("pmc/LowVoltageDetectReset.h"),
                        "device header first: the port header's static_asserts check against it")
        self.assertRegex(unit, r"enableLowVoltageDetectReset\(IP_PMC->LVDSC1\)")
        self.assertNotRegex(unit, r"LVDSC1\s*=|0x10", "no value of its own in the bootloader")

    def test_build_wiring(self):
        family_cmake = (FAMILY / "family.cmake").read_text(encoding="utf-8")
        self.assertIn("bl_lvd_s32k1xx.cpp", family_cmake)
        self.assertRegex(family_cmake, r"INCLUDE_DIRECTORIES\s+\"\$\{NX_ROOT\}/platforms/s32k1xx/port/main/include\"")
        cmake = (BOOTLOADER / "CMakeLists.txt").read_text(encoding="utf-8")
        self.assertIn("${BL_FAMILY_SOURCES}", cmake)
        self.assertRegex(cmake, r"LINKER_LANGUAGE C\b")
        self.assertFalse((BOOTLOADER / "platforms" / "stm32" / "family.cmake").exists(),
                         "the LVD of the s32k1xx PMC is no part of another family")


if __name__ == "__main__":
    unittest.main()
