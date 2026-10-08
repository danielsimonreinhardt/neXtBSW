"""Translation probe of the AUTOSAR base types for the target core profiles (ARCH-45).

The host test test/host/AutosarBaseHostTest.c runs on the 64-bit PC (tools/test -k autosar-base). Its static
assertions only say something about the targets if the target compiler evaluates them too: this test compiles the
same file with arm-none-eabi-gcc -fsyntax-only and AUTOSARBASE_TARGET_PROBE=1 (adds 32-bit pointer and int checks)
for the core profiles of decision D11 (ARM Cortex-M and Cortex-R, 32 bit, little endian):

- Cortex-M4F (-mthumb, FPv4-SP hard float), Cortex-M0+ (-mthumb, soft float)
- Cortex-R4 with VFPv3-D16 hard float, as Thumb and as ARM code

Counter-checks show that the probe is not green by construction: a big-endian compile (not a D11 profile) and
copies of the headers with one deliberate error each (wrong width, wrong signedness, wrong value, and since ARCH-46
a type of the right size that is not the <stdint.h> type, e.g. uint32 = unsigned int) must fail.

Run: .venv/Scripts/python -m unittest libs/bsw/autosarBase/test/test_autosar_base_cross.py
(needs arm-none-eabi-gcc from PATH or NX_ARM_GCC_BIN; no board, no linking)
"""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
MODULE = HERE.parent
INCLUDE = MODULE / "include"
SOURCE = HERE / "host" / "AutosarBaseHostTest.c"
GCC_PATH = os.pathsep.join(p for p in (os.environ.get("NX_ARM_GCC_BIN"), os.environ.get("PATH")) if p)
ARM_GCC = shutil.which("arm-none-eabi-gcc", path=GCC_PATH)

PROFILES = {
    "cortex-m4f": ("-mcpu=cortex-m4", "-mthumb", "-mfloat-abi=hard", "-mfpu=fpv4-sp-d16"),
    "cortex-m0plus": ("-mcpu=cortex-m0plus", "-mthumb"),
    "cortex-r4-thumb": ("-mcpu=cortex-r4", "-mfloat-abi=hard", "-mfpu=vfpv3-d16", "-mthumb"),
    "cortex-r4-arm": ("-mcpu=cortex-r4", "-mfloat-abi=hard", "-mfpu=vfpv3-d16", "-marm"),
}
FLAGS = ("-std=c11", "-Wall", "-Wextra", "-Wconversion", "-Werror", "-pedantic", "-fsyntax-only",
         "-DAUTOSARBASE_TARGET_PROBE=1")

# (header, original text, deliberate error, part of the expected compiler message)
MUTATIONS = [
    ("Platform_Types.h", "typedef uint32_t uint32;", "typedef unsigned short uint32;", '"uint32"'),
    ("Platform_Types.h", "typedef int8_t sint8;", "typedef uint8_t sint8;", '"sint8"'),
    ("Platform_Types.h", "typedef uint8_t boolean;", "typedef int8_t boolean;", "boolean"),
    ("Platform_Types.h", "typedef double float64;", "typedef float float64;", '"float64"'),
    ("Platform_Types.h", "typedef uint64_t uint64;", "typedef uint32_t uint64;", '"uint64"'),
    # ARCH-46: same size and signedness, but not the newlib type (unsigned long/long) - only the type identity check
    # catches these, they are the state before ARCH-46
    ("Platform_Types.h", "typedef uint32_t uint32;", "typedef unsigned int uint32;", "uint32 is uint32_t"),
    ("Platform_Types.h", "typedef int32_t sint32;", "typedef signed int sint32;", "sint32 is int32_t"),
    # boolean as C99 bool: one byte and unsigned, but not the AUTOSAR boolean
    ("Platform_Types.h", "typedef uint8_t boolean;", "typedef _Bool boolean;", "boolean is uint8_t, not _Bool"),
    ("Platform_Types.h", "#define CPU_BYTE_ORDER      LOW_BYTE_FIRST", "#define CPU_BYTE_ORDER      HIGH_BYTE_FIRST",
     "LOW_BYTE_FIRST"),
    ("Platform_Types.h", "#define CPU_TYPE            CPU_TYPE_32", "#define CPU_TYPE            CPU_TYPE_64",
     "CPU_TYPE_32"),
    # only caught with AUTOSARBASE_TARGET_PROBE (32-bit pointers): shows that the target-only checks are active
    ("Platform_Types.h", "typedef void* VoidPtr;", "typedef unsigned long long VoidPtr;", "VoidPtr must be CPU_TYPE"),
    ("Std_Types.h", "typedef uint8 Std_ReturnType;", "typedef sint16 Std_ReturnType;", "Std_ReturnType"),
    ("Std_Types.h", "#define E_NOT_OK 0x01u", "#define E_NOT_OK 0x00u", "E_OK/E_NOT_OK"),
    ("Std_Types.h", "#define STD_HIGH 0x01u", "#define STD_HIGH 0x00u", "STD_HIGH/STD_LOW"),
]


@unittest.skipIf(ARM_GCC is None, "arm-none-eabi-gcc (NX_ARM_GCC_BIN) not found")
class AutosarBaseCrossTest(unittest.TestCase):

    def setUp(self):
        self.env = {**os.environ, "PATH": GCC_PATH}

    def compile(self, cpu, include=INCLUDE, *extra):
        return subprocess.run([ARM_GCC, *cpu, *FLAGS, *extra, f"-I{include}", str(SOURCE)],
                              capture_output=True, text=True, env=self.env)

    def test_core_profiles_compile(self):
        for name, cpu in PROFILES.items():
            with self.subTest(profile=name):
                r = self.compile(cpu)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertEqual(r.stderr.strip(), "", "warning-free expected")

    def test_big_endian_is_rejected(self):
        """The compiler's byte order is checked against CPU_BYTE_ORDER: a big-endian core is no D11 profile."""
        r = self.compile(PROFILES["cortex-m4f"], INCLUDE, "-mbig-endian")
        self.assertNotEqual(r.returncode, 0, "big endian must be rejected (D11)")
        self.assertIn("little endian", r.stderr)

    def test_deliberate_errors_in_the_headers_are_found(self):
        cpu = PROFILES["cortex-m0plus"]
        for header, original, broken, message in MUTATIONS:
            with self.subTest(header=header, broken=broken), tempfile.TemporaryDirectory() as tmp:
                for h in INCLUDE.glob("*.h"):
                    shutil.copy(h, tmp)
                path = Path(tmp) / header
                text = path.read_text(encoding="utf-8")
                self.assertIn(original, text, "mutation no longer matches the header")
                path.write_text(text.replace(original, broken, 1), encoding="utf-8")
                r = self.compile(cpu, tmp)
                self.assertNotEqual(r.returncode, 0, f"not found: {broken}")
                self.assertIn(message, r.stderr)


if __name__ == "__main__":
    unittest.main()
