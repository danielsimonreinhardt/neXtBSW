"""Target build of bootloader and updater on another CAN bus of the board (BL-39, build option BOOTLOADER_CAN_BUS).

Board 2026-10-02 (VER-04): the updater built with -DBOOTLOADER_CAN_BUS=CAN1 for the eval board (Diag-CAN,
FlexCAN2 with 16 message buffers) hung in a reset loop - the HAL cleared the message buffer RAM of a 32-buffer
instance (bootloader/test/host/test_bl_can_host.py runs that HAL against a register model). No build covered the
option: every target build used the project bus CAN0. This test configures the demo preset with
-DBOOTLOADER_CAN_BUS=CAN1 in a build directory of its own and builds the targets bootloader and blupdater:

- configure and build succeed without a warning (the flags of the target build, -Werror);
- the build really is the Diag-CAN build: CMake reports bus CAN1, the generated board part names FlexCAN2 with
  pins PTC16/PTC17 and only reads the SBC (sbc_init(false)), and the updater embeds exactly the bootloader built
  here (record hex written by tools/canflash record);
- the HAL source of the build has no loop over FLEXCAN_RAMn_COUNT (the cause of VER-04).

NX_BL_CAN_BUS_BUILD=<directory> keeps the build (e.g. for the board test VER-04: blupdater.hex of the Diag-CAN);
otherwise a temporary directory is used and removed.

Needs cmake, ninja, arm-none-eabi-gcc (PATH or NX_ARM_GCC_BIN), external/openbsw and the eval board project.
Run: .venv/Scripts/python -m unittest bootloader/test/test_bl_can_bus_build.py
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
PRESET = "evalboard-s32k144-demo"
PROJECT = REPO / "projects" / "evalboard_s32k144_demo" / "project.yaml"
BUS = "CAN1"

CMAKE = shutil.which("cmake")
NINJA = shutil.which("ninja")
GCC_PATH = os.pathsep.join(p for p in (os.environ.get("NX_ARM_GCC_BIN"), os.environ.get("PATH")) if p)
ARM_GCC = shutil.which("arm-none-eabi-gcc", path=GCC_PATH)
OPENBSW = REPO / "external" / "openbsw" / "platforms" / "s32k1xx" / "CMakeLists.txt"


def read_hex(path: Path) -> bytes:
    """Flash content of an Intel hex file from address 0 (gaps 0xFF), records 00 and 04."""
    memory: dict[int, int] = {}
    upper = 0
    for line in path.read_text(encoding="ascii").split():
        raw = bytes.fromhex(line[1:])
        count, address, kind = raw[0], int.from_bytes(raw[1:3], "big"), raw[3]
        if kind == 0x00:
            for i, value in enumerate(raw[4:4 + count]):
                memory[upper + address + i] = value
        elif kind == 0x04:
            upper = int.from_bytes(raw[4:6], "big") << 16
    end = max(memory) + 1 if memory else 0
    return bytes(memory.get(a, 0xFF) for a in range(end))


@unittest.skipIf(None in (CMAKE, NINJA, ARM_GCC) or not OPENBSW.is_file(),
                 "cmake, ninja, arm-none-eabi-gcc (NX_ARM_GCC_BIN) or external/openbsw missing")
@unittest.skipUnless(PROJECT.is_file(), "eval board project not in this tree (exported open tree, PLAT-62)")
class DiagCanBuildTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        keep = os.environ.get("NX_BL_CAN_BUS_BUILD")
        cls.tmp = None if keep else Path(tempfile.mkdtemp(prefix="bl_can1_"))
        cls.build = Path(keep) if keep else cls.tmp / "build"
        env = {**os.environ, "PATH": GCC_PATH, "PYTHONUTF8": "1"}
        cls.configure = subprocess.run(
            [CMAKE, "--preset", PRESET, "-B", str(cls.build), f"-DBOOTLOADER_CAN_BUS={BUS}",
             f"-DPython3_EXECUTABLE={sys.executable}"], cwd=REPO, capture_output=True, text=True, env=env)
        cls.make = None
        if cls.configure.returncode == 0:
            cls.make = subprocess.run([CMAKE, "--build", str(cls.build), "--config", "RelWithDebInfo", "--target",
                                       "bootloader", "blupdater"], capture_output=True, text=True, env=env)

    @classmethod
    def tearDownClass(cls):
        if cls.tmp is not None:
            shutil.rmtree(cls.tmp, ignore_errors=True)

    def output(self, run) -> str:
        return (run.stdout + run.stderr) if run is not None else ""

    def test_configure_and_build_without_warnings(self):
        self.assertEqual(self.configure.returncode, 0, self.output(self.configure)[-4000:])
        self.assertIsNotNone(self.make)
        self.assertEqual(self.make.returncode, 0, self.output(self.make)[-4000:])
        self.assertNotIn("warning", self.output(self.make).lower())

    def test_it_is_the_diag_can_build(self):
        self.assertIn(f"bootloader: CAN bus {BUS}", self.output(self.configure))
        board = (self.build / "generated" / "board" / "bootloader" / "bl_board.h").read_text(encoding="utf-8")
        self.assertIn("#define BOARD_CAN           IP_FLEXCAN2", board)
        self.assertIn("#define BOARD_CAN_PCC_INDEX PCC_FlexCAN2_INDEX", board)
        self.assertIn("IP_PORTC->PCR[16U] = PORT_PCR_MUX(3U);", board)
        self.assertIn("IP_PORTC->PCR[17U] = PORT_PCR_MUX(3U);", board)
        self.assertIn("sbc_init(false);", board)
        cmake = (self.build / "generated" / "board" / "bootloader" / "bl_board.cmake").read_text(encoding="utf-8")
        self.assertIn(f'set(NX_BL_CAN_BUS "{BUS}")', cmake)

    def test_images_written(self):
        for image in ("bootloader/RelWithDebInfo/bootloader.elf", "bootloader/RelWithDebInfo/bootloader.hex",
                      "bootloader/updater/RelWithDebInfo/blupdater.elf",
                      "bootloader/updater/RelWithDebInfo/blupdater.hex",
                      "bootloader/updater/RelWithDebInfo/blupdater.record.hex"):
            with self.subTest(image=image):
                self.assertTrue((self.build / image).is_file(), image)

    def test_updater_embeds_this_bootloader(self):
        """upd_blob.c (canflash blimage): 16-byte header, then the flash content of the bootloader.hex built here."""
        blob = (self.build / "bootloader" / "updater" / "RelWithDebInfo" / "upd_blob.c").read_text(encoding="utf-8")
        self.assertIn("blimage from bootloader.hex", blob)
        size = int(re.search(r"(\d+) bytes, CRC-32", blob).group(1))
        body = blob[blob.index("] = {"):]
        data = bytes(int(b, 16) for b in re.findall(r"0x([0-9A-Fa-f]{2})", body))
        image = read_hex(self.build / "bootloader" / "RelWithDebInfo" / "bootloader.hex")
        self.assertLessEqual(len(image), size)
        self.assertEqual(data[16:16 + size], image.ljust(size, b"\xff"))  # blimage pads to the program phrase

    def test_hal_clears_only_its_buffers(self):
        hal = (REPO / "bootloader" / "platforms" / "s32k1xx" / "hal_s32k1xx.c").read_text(encoding="utf-8")
        code = re.sub(r"/\*.*?\*/|//[^\n]*", "", hal, flags=re.S)
        self.assertNotIn("FLEXCAN_RAMn_COUNT", code)


if __name__ == "__main__":
    unittest.main()
