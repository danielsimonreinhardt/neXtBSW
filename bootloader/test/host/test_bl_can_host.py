"""BL-39: CAN path of the bootloader HAL s32k1xx on a FlexCAN instance with 16 message buffers.

Board 2026-10-02 (VER-04): the updater built with -DBOOTLOADER_CAN_BUS=CAN1 (Diag-CAN, FlexCAN2 of the S32K144)
was started by the bootloader and then never sent a status frame and wrote nothing; the bootloader of the same
build would have done the same. Cause: can_init() of bootloader/platforms/s32k1xx/hal_s32k1xx.c cleared
FLEXCAN_RAMn_COUNT (128) words of message buffer RAM - the RAM of a 32-buffer instance. FlexCAN1/2 of the S32K144
(FlexCAN1 of the S32K142, FlexCAN2 of the S32K146) have 16 buffers ([RM] Rev. 13, Table 55-1); the word at offset
0x180 is unimplemented, the access an access error ([RM] p. 1942) -> HardFault -> Fault_Handler resets -> the
bootloader starts the updater again: reset loop, nothing written, nothing sent. Bus 0 (FlexCAN0, 32 buffers) never
hit it.

Covered without hardware: the unchanged HAL (hal_init, hal_can_send, hal_can_receive) with the generated board part
of three builds against a FlexCAN register model with the buffer count of each instance of the derivative
(bootloader/test/host/bl_can_host.c, guard pages at the real base addresses):
- test profile testproject_s32k142_bl (not the eval board, architecture section 11 question 7): bootloader on
  DIAG = FlexCAN1 of the S32K142 (16 buffers), 16 MHz crystal, 250 kbit/s, other ids, no SBC;
- eval board, build option BOOTLOADER_CAN_BUS=CAN1 (FlexCAN2, 16 buffers, SBC only read) - the VER-04 build;
- eval board, project bus CAN0 (FlexCAN0, 32 buffers, SBC configured).
Each: no access error, no access to a foreign instance, no buffer above 8, MAXMB 8, bit timing of the board part,
module out of freeze, receive filters of buffers 0..7, buffers above 8 untouched, one frame sent in buffer 8, one
received from buffer 2, no reset. Mutant: the former loop over FLEXCAN_RAMn_COUNT must give the access error at
0x180 on every 16-buffer build and none on FlexCAN0.

Run: .venv/Scripts/python -m unittest bootloader/test/host/test_bl_can_host.py
(needs ziglang in the .venv, like tools/canflash/hostbuild.py; Windows x86-64)
"""

import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
BOOTLOADER = REPO / "bootloader"
FAMILY = BOOTLOADER / "platforms" / "s32k1xx"
HAL_C = FAMILY / "hal_s32k1xx.c"
HARNESS_C = HERE / "bl_can_host.c"
MODEL_CAN = HERE / "model_can"
VENDOR = REPO / "external" / "openbsw" / "platforms" / "s32k1xx" / "bsp" / "bspMcu" / "include" / "3rdparty" / "nxp"
PROFILES = REPO / "tools" / "nxconfig" / "test" / "profiles" / "projects"
EVAL_PROJECT = REPO / "projects" / "evalboard_s32k144_demo" / "project.yaml"

sys.path.insert(0, str(REPO / "tools" / "canflash"))
import hostbuild  # noqa: E402

try:
    import ziglang  # noqa: F401

    HAVE_ZIG = True
except ImportError:  # pragma: no cover - the test skips without the host compiler
    HAVE_ZIG = False

# message buffers per instance FlexCAN0/1/2 ([RM] Rev. 13, Table 55-1; 0: the derivative has no such instance)
BUFFERS = {"S32K142": (32, 16, 0), "S32K144": (32, 16, 16)}

# the HAL's buffer use: receive 0..5 physical, 6..7 functional, transmit 8 (MCR[MAXMB] = 8)
TX_MB, RX_FUNC_FIRST, MAXMB = 8, 6, 8
MCR_MDIS, MCR_FRZ, MCR_HALT, MCR_SRXDIS, MCR_IRMQ = 1 << 31, 1 << 30, 1 << 28, 1 << 17, 1 << 16
CTRL2_MRP = 1 << 18
PCC_CGC = 1 << 30
UNTOUCHED = 0xA5A5A5A5  # what the model's RAM holds after power-up

# (name, project, CAN bus override, derivative, board instance, SBC init: None = no SBC, else configure flag, pins)
BUILDS = [
    ("s32k142_diag", PROFILES / "testproject_s32k142_bl" / "project.yaml", None, "S32K142", 1, None,
     {"A12": 0x300, "C7": 0x300}),
    ("eval_can1", EVAL_PROJECT, "CAN1", "S32K144", 2, 0, {"C16": 0x300, "C17": 0x300}),
    ("eval_can0", EVAL_PROJECT, None, "S32K144", 0, 1, {"C2": 0x300, "C3": 0x300}),
]

# the loop before BL-39: all of FLEXCAN_RAMn_COUNT (the RAM of a 32-buffer instance)
FIXED_LOOP = "for (uint32_t i = 0U; i < (CAN_MB_USED * CAN_MB_WORDS); ++i)"
FORMER_LOOP = "for (uint32_t i = 0U; i < FLEXCAN_RAMn_COUNT; ++i)"


def generate(project: Path, can: str | None, out: Path) -> Path:
    """Generated board part and memory layout of the build, as bootloader/CMakeLists.txt writes them."""
    blgen = hostbuild._blgen()
    config = blgen.generate(project, out, can=can, board=True)
    hostbuild.append_sector_table(out / blgen.MEMORY_HEADER, config)
    return out


def build(out: Path, generated: Path, hal: Path = HAL_C) -> subprocess.CompletedProcess:
    """The harness with the HAL (unchanged, or a mutant) as executable, zig cc like the other host tests."""
    includes = (MODEL_CAN, generated, BOOTLOADER / "include", BOOTLOADER / "src" / "core", FAMILY, VENDOR)
    cmd = [sys.executable, "-m", "ziglang", "cc", "-target", "x86_64-windows-gnu", "-O1", "-std=c99", "-Wall",
           "-Wextra", "-Werror",
           # target-only parts of the HAL: long_call (ARM), addresses as uint32_t (32-bit target)
           "-Wno-unknown-attributes", "-Wno-int-to-pointer-cast", "-Wno-pointer-to-int-cast",
           *(f"-I{d}" for d in includes), str(hal), str(HARNESS_C), "-o", str(out)]
    return subprocess.run(cmd, capture_output=True, text=True)


def run(exe: Path, buffers: tuple[int, int, int]) -> dict[str, str]:
    result = subprocess.run([str(exe), *map(str, buffers)], capture_output=True, text=True, timeout=60)
    if result.returncode != 0 or "done 1" not in result.stdout:
        raise AssertionError(f"{exe.name} {buffers}: exit {result.returncode:#x}\n{result.stdout}\n{result.stderr}")
    values: dict[str, str] = {}
    for line in result.stdout.splitlines():
        key, _, value = line.partition(" ")
        if key in ("mb", "pcr"):
            sub, _, rest = value.partition(" ")
            values[f"{key} {sub}"] = rest
        else:
            values[key] = value
    return values


@unittest.skipUnless(HAVE_ZIG and sys.platform == "win32", "needs ziglang in the .venv and Windows (guard pages)")
@unittest.skipUnless(VENDOR.is_dir(), "submodule external/openbsw missing (vendor headers)")
class CanPathTest(unittest.TestCase):
    """The unchanged HAL on every build: no access error, the buffers it owns and nothing else."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        cls.root = Path(cls.tmp.name)
        cls.results = {}
        for name, project, can, derivative, instance, sbc, pins in BUILDS:
            if not project.is_file():
                continue  # exported open tree without the eval board project (PLAT-62)
            generated = generate(project, can, cls.root / name / "gen")
            exe = cls.root / name / "bl_can_host.exe"
            result = build(exe, generated)
            assert result.returncode == 0, result.stderr
            cls.results[name] = run(exe, BUFFERS[derivative])

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def builds(self):
        for name, project, can, derivative, instance, sbc, pins in BUILDS:
            if name in self.results:
                yield name, self.results[name], derivative, instance, sbc, pins

    def test_all_builds_ran(self):
        expected = {name for name, project, *_ in BUILDS if project.is_file()}
        self.assertEqual(set(self.results), expected)
        self.assertIn("s32k142_diag", self.results, "the test profile must always run")

    def test_board_instance_of_the_build(self):
        for name, r, derivative, instance, *_ in self.builds():
            with self.subTest(build=name):
                self.assertEqual(int(r["board_instance"]), instance)

    def test_no_access_error_and_no_foreign_instance(self):
        for name, r, derivative, instance, *_ in self.builds():
            for stage in ("init", "end"):
                for i in range(3):
                    with self.subTest(build=name, stage=stage, instance=i):
                        self.assertEqual(int(r[f"{stage}.can{i}.errors"]), 0, r.get(f"{stage}.can{i}.first_error"))
                        if i != instance:
                            self.assertEqual(int(r[f"{stage}.can{i}.accesses"]), 0, "access to a foreign FlexCAN")

    def test_only_the_buffers_of_the_hal(self):
        for name, r, derivative, instance, *_ in self.builds():
            with self.subTest(build=name):
                self.assertEqual(int(r[f"init.can{instance}.highest_buffer"]), TX_MB)
                buffers = BUFFERS[derivative][instance]
                for mb in range(TX_MB + 1, min(buffers, 16)):
                    cs, ident, mask = (int(v, 0) for v in r[f"mb {mb}"].split())
                    self.assertEqual((cs, ident), (UNTOUCHED, UNTOUCHED), f"buffer {mb} written")

    def test_controller_running_with_the_board_timing(self):
        for name, r, *_ in self.builds():
            with self.subTest(build=name):
                mcr = int(r["mcr"], 0)
                self.assertEqual(mcr & 0x7F, MAXMB)
                self.assertEqual(mcr & (MCR_MDIS | MCR_FRZ | MCR_HALT), 0, "module not out of freeze")
                self.assertEqual(mcr & (MCR_SRXDIS | MCR_IRMQ), MCR_SRXDIS | MCR_IRMQ)
                self.assertEqual(int(r["ctrl1"], 0), int(r["board_ctrl1"], 0))
                self.assertTrue(int(r["ctrl2"], 0) & CTRL2_MRP)
                self.assertTrue(int(r["pcc_can"], 0) & PCC_CGC)
                self.assertEqual(int(r["resets"]), 0)
                self.assertEqual(int(r["resets_end"]), 0)

    def test_pins_and_sbc_of_the_board_part(self):
        for name, r, derivative, instance, sbc, pins in self.builds():
            with self.subTest(build=name):
                for pin, value in pins.items():
                    self.assertEqual(int(r[f"pcr {pin}"], 0), value, pin)
                if sbc is None:
                    self.assertEqual(int(r["sbc_inits"]), 0)
                else:
                    self.assertEqual(int(r["sbc_inits"]), 1)
                    self.assertEqual(int(r["sbc_configure"]), sbc)

    def test_receive_filters(self):
        for name, r, *_ in self.builds():
            phys, func = int(r["id_phys"], 0), int(r["id_func"], 0)
            for mb in range(TX_MB + 1):
                with self.subTest(build=name, mb=mb):
                    cs, ident, mask = (int(v, 0) for v in r[f"mb {mb}"].split())
                    if mb == TX_MB:
                        self.assertEqual((cs >> 24) & 0xF, 0x8, "transmit buffer inactive")
                        continue
                    self.assertEqual((cs >> 24) & 0xF, 0x4, "receive buffer empty")
                    self.assertEqual(ident, (phys if mb < RX_FUNC_FIRST else func) << 18)
                    self.assertEqual(mask, 0x7FF << 18)

    def test_send_and_receive(self):
        for name, r, *_ in self.builds():
            with self.subTest(build=name):
                self.assertEqual(int(r["send"]), 1)
                self.assertEqual(int(r["send.transmits"]), 1)
                self.assertEqual(int(r["send.buffer"]), TX_MB)
                cs, ident, w2, w3 = (int(v, 0) for v in r["send.mb"].split())
                self.assertEqual((cs >> 24) & 0xF, 0xC)
                self.assertEqual((cs >> 16) & 0xF, 8)
                self.assertEqual(ident, 0x7E8 << 18)
                self.assertEqual((w2, w3), (0x06500200, 0x3201F4AA))
                self.assertEqual(int(r["send.tx_idle"]), 0, "frame still pending in the model")
                self.assertEqual(int(r["receive"]), 1)
                self.assertEqual(int(r["receive.id"], 0), int(r["id_phys"], 0))
                self.assertEqual(int(r["receive.len"]), 8)
                self.assertEqual(r["receive.data"], "0210112233445566")
                self.assertEqual(int(r["receive.iflag1"], 0) & (1 << 2), 0, "flag of buffer 2 not cleared")


@unittest.skipUnless(HAVE_ZIG and sys.platform == "win32", "needs ziglang in the .venv and Windows (guard pages)")
@unittest.skipUnless(VENDOR.is_dir(), "submodule external/openbsw missing (vendor headers)")
class FormerLoopMutantTest(unittest.TestCase):
    """The loop before BL-39 must be caught on every 16-buffer build (the VER-04 finding) and not on FlexCAN0."""

    def test_former_ram_clear(self):
        self.assertEqual(HAL_C.read_text(encoding="utf-8").count(FIXED_LOOP), 1, "fixed loop not found in the HAL")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            mutant = root / "hal_s32k1xx.c"
            mutant.write_text(HAL_C.read_text(encoding="utf-8").replace(FIXED_LOOP, FORMER_LOOP), encoding="utf-8")
            for name, project, can, derivative, instance, sbc, pins in BUILDS:
                if not project.is_file():
                    continue
                with self.subTest(build=name):
                    generated = generate(project, can, root / name / "gen")
                    exe = root / name / "mutant.exe"
                    result = build(exe, generated, hal=mutant)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    r = run(exe, BUFFERS[derivative])
                    errors = int(r[f"init.can{instance}.errors"])
                    if BUFFERS[derivative][instance] == 16:
                        self.assertGreater(errors, 0, "the former loop must hit unimplemented RAM")
                        self.assertEqual(r[f"init.can{instance}.first_error"], "0x180 write")
                    else:
                        self.assertEqual(errors, 0)


class SourceRuleTest(unittest.TestCase):
    """No loop of the HAL may be sized by the RAM of the largest instance."""

    def test_no_loop_over_flexcan_ramn_count(self):
        code = re.sub(r"/\*.*?\*/|//[^\n]*", "", HAL_C.read_text(encoding="utf-8"), flags=re.S)
        self.assertNotIn("FLEXCAN_RAMn_COUNT", code)
        self.assertNotIn("CAN_RAMn_COUNT", code)


if __name__ == "__main__":
    unittest.main()
