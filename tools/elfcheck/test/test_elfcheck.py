"""Tests of the image check tools/elfcheck (PLAT-80, tool-classification.md proposal D, finding TF6, SSR-083).

Profiles (each a class, selected with -k in tools/test/tests.yaml):

- ReaderUnitTest: readers, sector geometry, core table - no compiler, no build.
- DemoImagesTest: bootloader, application and updater of the demo build (S32K144) pass; then mutants of the images,
  the validity record and the derivative map must turn the check red: shifted section, overlap, wrong FP ABI,
  missing validity record, record in the application area, ROM check list gap, wrong derivative map, ...
  The ELF reader is compared with arm-none-eabi-readelf on the same images.
- Bt148ImagesTest: the three images of the S32K148 build test pass.
- S32K116ImagesTest: application, bootloader and updater of s32k116evb_minimal (built here with the project's
  preset, all three link since PLAT-12) plus an own small application linked with the real application
  linker template and the generated memoryLayout.h of the project. Finding K1 is fixed (PLAT-31): both images
  are v6S-M and contain only Thumb-1 instructions.
- Stm32ImagesTest: the STM32F446 bootloader (unequal sectors, record below the application; test layout of
  PLAT-16) against mcudb/stm32/STM32F446RE.yaml.

Run: .venv/Scripts/python -m unittest tools/elfcheck/test/test_elfcheck.py
"""

from __future__ import annotations

import importlib
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
TOOL = HERE.parent
REPO = TOOL.parents[1]
sys.path.insert(0, str(TOOL))

import check  # noqa: E402
import cores  # noqa: E402
import elffile  # noqa: E402
import ihex  # noqa: E402

GCC_PATH = os.pathsep.join(p for p in (os.environ.get("NX_ARM_GCC_BIN"), os.environ.get("PATH")) if p)
ARM_GCC = shutil.which("arm-none-eabi-gcc", path=GCC_PATH)
READELF = shutil.which("arm-none-eabi-readelf", path=GCC_PATH)

# PLAT-62: the own full-featured test profile (synthetic testboard_full/testproject_full) instead of the eval board
# project, which stays private (PLAT-46 F10) - a test against it would be red in the exported open tree. What
# proves something about the eval board itself uses EVAL_DEMO_PROJECT and is skipped where it is missing.
DEMO_PROJECT = REPO / "tools" / "nxconfig" / "test" / "profiles" / "projects" / "testproject_full" / "project.yaml"
EVAL_DEMO_PROJECT = REPO / "projects" / "evalboard_s32k144_demo" / "project.yaml"
BT148_PROJECT = REPO / "projects" / "buildtest_s32k148_min" / "project.yaml"
K116_PROJECT = REPO / "projects" / "s32k116evb_minimal" / "project.yaml"


def build_dir_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location("build_dirs", REPO / "tools" / "test" / "build_dirs.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def codes(findings, level: str = "error") -> set[str]:
    return {f.code for f in findings if f.level == level}


# ---------------------------------------------------------------------- mutation helpers

def copy_images(images: dict[str, Path], dst: Path) -> dict[str, Path]:
    """Copies ELF, hex and record files of every image into dst (same file names)."""
    dst.mkdir(parents=True, exist_ok=True)
    result = {}
    for key, elf in images.items():
        for extra in (elf, elf.with_suffix(".hex"), check.record_of(elf)):
            if extra.is_file():
                shutil.copy2(extra, dst / extra.name)
        result[key] = dst / elf.name
    return result


def patch(path: Path, offset: int, fmt: str, value: int) -> None:
    data = bytearray(path.read_bytes())
    struct.pack_into(fmt, data, offset, value)
    path.write_bytes(bytes(data))


def shift_segment_of(path: Path, section: str, delta: int) -> None:
    """Moves the load segment holding `section` (run and load address, all its sections) by delta."""
    img = elffile.load(path)
    seg = img.segment_of(img.section(section))
    for sec in img.sections:
        if sec.alloc and img.segment_of(sec) == seg:
            patch(path, sec.header_offset + 12, "<I", sec.addr + delta)
    patch(path, seg.header_offset + 8, "<I", seg.vaddr + delta)
    patch(path, seg.header_offset + 12, "<I", seg.paddr + delta)


def patch_attribute(path: Path, tag: int, value: int) -> None:
    img = elffile.load(path)
    patch(path, img.attributes[tag].offset, "<B", value)


def symbol_file_offset(path: Path, symbol: str, byte: int = 0) -> int:
    img = elffile.load(path)
    sym = img.symbol(symbol)
    address = sym.value + byte
    sec = next(s for s in img.sections if s.has_content and s.addr <= address < s.addr + s.size)
    return sec.offset + address - sec.addr


def rename_symbol(path: Path, old: str, new: str) -> None:
    """DOC-28: gives the symbol `old` the (not longer) name `new` in the symbol string table .strtab."""
    img = elffile.load(path)
    strtab = img.section(".strtab")
    data = bytearray(path.read_bytes())
    at = data.find(b"\0" + old.encode() + b"\0", strtab.offset - 1, strtab.offset + strtab.size)
    assert at >= 0 and len(new) <= len(old), (old, new)
    data[at + 1:at + 2 + len(new)] = new.encode() + b"\0"
    path.write_bytes(bytes(data))


def patch_layout(path: Path, index: int, value: int) -> None:
    patch(path, symbol_file_offset(path, check.LAYOUT_SYMBOL, 4 * index), "<I", value)


def write_hex(path: Path, image: dict[int, int]) -> None:
    lines, upper = [], None
    for start, data in ihex.blocks(image):
        for pos in range(0, len(data), 16):
            address = start + pos
            if address >> 16 != upper:
                upper = address >> 16
                rec = bytes([2, 0, 0, 4, upper >> 8, upper & 0xFF])
                lines.append(":" + (rec + bytes([-sum(rec) & 0xFF])).hex().upper())
            chunk = data[pos:pos + 16]
            rec = bytes([len(chunk), (address >> 8) & 0xFF, address & 0xFF, 0]) + chunk
            lines.append(":" + (rec + bytes([-sum(rec) & 0xFF])).hex().upper())
    lines.append(":00000001FF")
    path.write_text("\n".join(lines) + "\n", encoding="ascii")


def rewrite_record(path: Path, change) -> None:
    """Reads the record, lets `change(address, bytearray)` modify it and returns address and bytes."""
    (address, raw), = ihex.blocks(ihex.load(path))
    data = bytearray(raw)
    address = change(address, data) or address
    write_hex(path, {address + i: b for i, b in enumerate(data)})


def patch_config_field(elf: Path, index: int, value: int, elf_too: bool = True) -> None:
    """NVM-15: one byte of the flash configuration field in the hex next to the ELF and (elf_too) in the ELF."""
    img = elffile.load(elf)
    sec = img.section(".flashconfig")
    if elf_too:
        patch(elf, sec.offset + index, "<B", value)
    hex_path = elf.with_suffix(".hex")
    data = ihex.load(hex_path)
    data[img.lma(sec) + index] = value
    write_hex(hex_path, data)


def derivative_variant(src: Path, dst: Path, old: str, new: str) -> Path:
    text = src.read_text(encoding="utf-8")
    assert old in text, f"{old!r} not in {src}"
    dst.write_text(text.replace(old, new, 1), encoding="utf-8")
    return dst


# ---------------------------------------------------------------------- 1. readers without a build

class ReaderUnitTest(unittest.TestCase):
    def test_attributes_of_a_cortex_m4f_image(self):
        # Tag_CPU_name "7E-M", Tag_CPU_arch 13, profile 'M', Tag_FP_arch 6, HardFP_use 1, VFP_args 1
        body = b"\x05" + b"7E-M\0" + bytes([6, 13, 7, ord("M"), 9, 2, 10, 6, 27, 1, 28, 1, 34, 1])
        sub = b"\x01" + struct.pack("<I", 5 + len(body)) + body
        vendor = b"aeabi\0" + sub
        data = b"A" + struct.pack("<I", 4 + len(vendor)) + vendor
        attrs = elffile.parse_attributes(data, base=100)
        self.assertEqual(attrs[elffile.TAG_CPU_NAME].value, "7E-M")
        self.assertEqual(attrs[elffile.TAG_CPU_ARCH].value, 13)
        self.assertEqual(attrs[elffile.TAG_CPU_ARCH_PROFILE].value, ord("M"))
        self.assertEqual(attrs[elffile.TAG_ABI_VFP_ARGS].value, 1)
        self.assertEqual(data[attrs[elffile.TAG_FP_ARCH].offset - 100], 6, "offset points at the value")

    def test_broken_attributes_are_refused(self):
        with self.assertRaises(elffile.ElfError):
            elffile.parse_attributes(b"B\0\0\0\0")
        with self.assertRaises(elffile.ElfError):
            elffile.parse_attributes(b"A" + struct.pack("<I", 200) + b"aeabi\0")

    def test_hex_reader(self):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "x.hex"
            write_hex(path, {0x7F000 + i: i for i in range(20)})
            image = ihex.load(path)
            self.assertEqual(ihex.blocks(image), [(0x7F000, bytes(range(20)))])
            text = path.read_text()
            path.write_text(text.replace(":10F00000", ":10F00001", 1))
            with self.assertRaisesRegex(ihex.HexError, "checksum"):
                ihex.load(path)

    def test_sector_boundaries(self):
        f446_file = REPO / "mcudb" / "stm32" / "STM32F446RE.yaml"
        if f446_file.is_file():                     # TOOL-244: private family stm32, missing in the exported tree
            f446 = check.load_derivative(f446_file)[1][0]
            self.assertEqual(f446.boundaries()[:6], [0x08000000, 0x08004000, 0x08008000, 0x0800C000, 0x08010000,
                                                     0x08020000])
            self.assertEqual(f446.boundaries()[-1], 0x08080000)
            self.assertEqual(f446.uniform_sector_size(), 0, "unequal sectors: the layout value is 0")
        k144 = check.load_derivative(REPO / "mcudb" / "s32k1xx" / "S32K144.yaml")[1][0]
        self.assertEqual(len(k144.boundaries()), 129)

    def test_core_table_covers_every_derivative(self):
        """Every derivative of mcudb has an expectation, so a new core cannot slip past the check."""
        for path in sorted((REPO / "mcudb").glob("*/*.yaml")):
            if path.name.endswith(".pinmux.yaml"):
                continue
            derivative, _ = check.load_derivative(path)
            with self.subTest(derivative=path.name):
                want, reason = cores.expected(derivative["core"], derivative.get("fpu"))
                self.assertIsNotNone(want, reason)

    def test_core_table_refuses_unknown_and_ambiguous(self):
        self.assertIsNone(cores.expected("cortex-m85", None)[0])
        self.assertIsNone(cores.expected("cortex-m7f", None)[0], "two FPU variants and none named")
        self.assertIsNone(cores.expected("cortex-m4f", "dp")[0])

    def test_expectation_of_the_projects(self):
        exp = check.load_expectation(DEMO_PROJECT)
        self.assertEqual((exp.derivative, exp.flash.name, exp.board), ("S32K144", "PFLASH", "testboard_full"))
        self.assertEqual(exp.bootloader_size, 0x8000)
        self.assertEqual(exp.ram_spans(), [(0x1FFF8000, 0x20007000)], "SRAM_L and SRAM_U are one block")
        if K116_PROJECT.is_file():                  # TOOL-243c: S32K116EVB profile private until PLAT-13
            exp = check.load_expectation(K116_PROJECT)
            self.assertEqual(exp.bootloader_ram, ("SRAM_U", 0x20000000, 0x20003000))
            self.assertEqual(exp.app_ram, [("SRAM_U", 0x20000000, 0x20003800)])

    def test_no_generator_is_imported(self):
        """The expectation must not come from the chain it checks (TF6)."""
        for path in TOOL.glob("*.py"):
            text = path.read_text(encoding="utf-8")
            with self.subTest(file=path.name):
                self.assertIsNone(re.search(r"^\s*(from|import)\s+(nxconfig|blgen|bswgen|canflash|hostbuild)\b",
                                            text, re.M))
                self.assertNotIn("PySide", text)

    def test_build_warnings_are_named(self):
        """TOOL-50: the warning check of S32K116ImagesTest names the lines (compiler, linker)."""
        out = ("[1/20] Building C object bootloader/.../main.c.obj\n"
               "C:/x/main.c:12:5: warning: unused variable 'a' [-Wunused-variable]\n"
               "[9/20] Linking C executable bootloader.elf\n"
               "arm-none-eabi-ld.exe: warning: bootloader.elf has a LOAD segment with RWX permissions\n"
               "   text\t   data\t    bss\n")
        self.assertEqual(build_warnings(out), [out.splitlines()[1], out.splitlines()[3]])
        self.assertEqual(build_warnings("[0/4] Re-checking globbed directories...\n[1/2] Building ...\n"), [])

    def test_heap_symbols(self):
        """DOC-28 (P5 (b), NXCPP-05, DE5): the symbols of the allocator are found - the ones of DE5 by name, every
        operator new overload, 32 and 64 bit - and nothing else: placement new, our own operator delete, undefined
        references and names that only start alike stay free. The real images: DemoImagesTest."""
        def image(*names: str, shndx: int = 1) -> elffile.ElfImage:
            img = elffile.ElfImage(Path("synthetic.elf"), b"", 0)
            img.symbols = [elffile.Symbol(n, 0x1001, 16, check.STT_FUNC, 1, shndx) for n in names]
            return img

        forbidden = ["malloc", "_malloc_r", "free", "_free_r", "_sbrk", "_sbrk_r", "calloc", "realloc",
                     "_Znwj", "_Znaj", "_ZnwjRKSt9nothrow_t", "_ZnwjSt11align_val_t", "_Znwm", "_ZnamRKSt9nothrow_t"]
        for name in forbidden:
            with self.subTest(symbol=name):
                out = check.Findings()
                check.check_heap(image(name), out, "application")
                self.assertEqual([(f.code, f.where) for f in out.errors], [("heap-symbol", f"symbol {name}")])
                self.assertIn("0x00001000", out.errors[0].message, "address without the Thumb bit")
        allowed = ["_ZnwjPv", "_ZnajPv", "_ZnwmPv", "_ZdlPv", "_ZdlPvj", "mallocInfo", "freeRunning", "_sbrk_limit",
                   "__malloc_lock", "_ZN3etl4pool4freeEPv"]
        out = check.Findings()
        check.check_heap(image(*allowed), out, "application")
        self.assertEqual(out, [])
        out = check.Findings()
        check.check_heap(image("malloc", "_Znwj", shndx=0), out, "application")
        self.assertEqual(out, [], "undefined references are not in the image")
        out = check.Findings()
        check.check_heap(image("malloc", "malloc"), out, "bootloader")
        self.assertEqual(len(out), 1, "one finding per name (local and global symbol of the same name)")


class IsrWrapperUnitTest(unittest.TestCase):
    """SYS-170 (SYS69-S1, SFP-F01): the interrupt frame check on synthetic images - no build, no compiler."""

    BASE = 0x8000

    @staticmethod
    def bl(at: int, target: int, kind: str = "bl") -> bytes:
        """Thumb-2 BL / B.W (T4) at address `at` to `target`."""
        imm = target - (at + 4)
        s = (imm >> 24) & 1
        i1, i2 = (imm >> 23) & 1, (imm >> 22) & 1
        j1, j2 = 1 ^ i1 ^ s, 1 ^ i2 ^ s
        h1 = 0xF000 | (s << 10) | ((imm >> 12) & 0x3FF)
        h2 = (0xD000 if kind == "bl" else 0x9000) | (j1 << 13) | (j2 << 11) | ((imm >> 1) & 0x7FF)
        return struct.pack("<HH", h1, h2)

    def image(self, vectors: list[str | int], bodies: dict[str, list[tuple[str, str]]]) -> elffile.ElfImage:
        """Vector table at BASE (entries by function name or raw word), functions behind it: push; calls; pop."""
        functions = ["SafePreIsrHook", "SafePostIsrHook", "DefaultISR", *bodies]
        address = {}
        at = self.BASE + 0x400
        for name in functions:
            address[name] = at
            at += 0x40
        code = bytearray(at - self.BASE)
        for name, calls in bodies.items():
            pos = address[name]
            code[pos - self.BASE:pos - self.BASE + 2] = struct.pack("<H", 0xB508)          # push {r3, lr}
            pos += 2
            for target, kind in calls:
                if kind == "vfp":                                   # vpush {d8-d9}
                    code[pos - self.BASE:pos - self.BASE + 4] = struct.pack("<HH", 0xED2D, 0x8B04)
                else:
                    code[pos - self.BASE:pos - self.BASE + 4] = self.bl(pos, address[target], kind)
                pos += 4
        words = [w if isinstance(w, int) else address[w] | 1 for w in vectors]
        code[0:4 * len(words)] = struct.pack(f"<{len(words)}I", *words)
        img = elffile.ElfImage(Path("synthetic.elf"), bytes(code), 0)
        img.sections = [elffile.Section(1, ".text", 1, 6, self.BASE, 0, len(code), 0)]   # PROGBITS, ALLOC|EXEC
        img.symbols = [elffile.Symbol("__isr_vector", self.BASE, 4 * len(words), 1, 1, 1)]
        img.symbols += [elffile.Symbol(n, a | 1, 0x40, check.STT_FUNC, 1, 1) for n, a in address.items()]
        return img

    def run_check(self, vectors, bodies) -> tuple[int, list]:
        out = check.Findings()
        n = check.check_isr_wrappers(self.image(vectors, bodies), out, "application")
        return n, [(f.code, f.where) for f in out.errors]

    WRAPPER = [("SafePreIsrHook", "bl"), ("Handler", "bl"), ("SafePostIsrHook", "b.w")]

    def table(self, *external) -> list:
        return [0x20001000, "Reset", *([0] * 13), *external]

    def test_wrapped_vectors_pass(self):
        bodies = {"Reset": [], "Handler": [], "Safe_Tick": self.WRAPPER, "Safe_Uart": self.WRAPPER,
                  "Safe_WDOG_EWM_IRQHandler": [], "LPIT0_Ch1_IRQHandler": []}
        vectors = self.table("Safe_Tick", "Safe_Uart", "DefaultISR", 0, "Safe_WDOG_EWM_IRQHandler",
                             "LPIT0_Ch1_IRQHandler", 0xFFFFFFFF)
        img = self.image(vectors, bodies)
        deadman = img.symbol("LPIT0_Ch1_IRQHandler")       # the deadman's vector name is an alias of its handler
        img.symbols.append(elffile.Symbol("Safe_LPIT0_Ch1_IRQHandler", deadman.value, deadman.size, check.STT_FUNC,
                                          1, 1))
        out = check.Findings()
        n = check.check_isr_wrappers(img, out, "application")
        self.assertEqual(out, [])
        self.assertEqual(n, 5)                  # SysTick, IRQ 0, 1, 3, 4; reserved 0 and TRIM word skipped
        _, errors = self.run_check(vectors, bodies)
        self.assertEqual(errors, [("isr-unwrapped", "vector 20 (IRQ 4)")], "without the alias name: red")

    def test_unwrapped_vectors_turn_it_red(self):
        bodies = {"Reset": [], "Handler": [], "Safe_Tick": self.WRAPPER,
                  "Safe_PostFirst": [("SafePostIsrHook", "bl"), ("SafePreIsrHook", "bl")],
                  "Safe_NoPost": [("SafePreIsrHook", "bl"), ("Handler", "bl")]}
        cases = {"plain handler on SysTick": (self.table("Handler"), "vector 15"),
                 "plain handler on an IRQ": (self.table("Safe_Tick", "Handler"), "vector 16 (IRQ 0)"),
                 "hook order": (self.table("Safe_Tick", "Safe_PostFirst"), "vector 16 (IRQ 0)"),
                 "no post hook": (self.table("Safe_Tick", "Safe_NoPost"), "vector 16 (IRQ 0)"),
                 "no function": (self.table("Safe_Tick", 0x00012345), "vector 16 (IRQ 0)")}
        for name, (vectors, where) in cases.items():
            with self.subTest(case=name):
                _, errors = self.run_check(vectors, bodies)
                self.assertEqual(errors, [("isr-unwrapped", where)])

    def test_image_without_safety_shell_is_not_checked(self):
        img = self.image(self.table("Reset"), {"Reset": []})
        img.symbols = [s for s in img.symbols if s.name != "SafePreIsrHook"]
        out = check.Findings()
        self.assertEqual(check.check_isr_wrappers(img, out, "application"), 0)
        self.assertEqual(out, [])

    def test_floating_point_reached_from_a_wrapper_turns_it_red(self):
        """SYS170-S4 a: a VFP instruction in anything a wrapped handler reaches through BL/B.W is reported (lazy FP
        stacking would write into the closed safety stack); in data of the code ($d), behind DefaultISR or in a
        function nobody reaches it is not."""
        wrapper = [("SafePreIsrHook", "bl"), ("Handler", "bl"), ("SafePostIsrHook", "b.w")]
        base = {"Reset": [], "Safe_Tick": wrapper, "Leaf": [("", "vfp")], "Unreached": [("", "vfp")]}
        cases = {
            "handler itself": ({"Handler": [("", "vfp")]}, [("isr-fpu", "function Handler")]),
            "two calls deep": ({"Handler": [("Middle", "bl")], "Middle": [("Leaf", "b.w")]},
                               [("isr-fpu", "function Leaf")]),
            "behind DefaultISR": ({"Handler": [("DefaultISR", "bl")]}, []),
            "not reached": ({"Handler": []}, []),
        }
        for name, (extra, expected) in cases.items():
            with self.subTest(case=name):
                bodies = {**base, **extra}
                if name == "behind DefaultISR":
                    bodies["DefaultISR"] = [("Leaf", "bl")]
                _, errors = self.run_check(self.table("Safe_Tick"), bodies)
                self.assertEqual(errors, expected)
        # the same bytes marked as data inside the code ($d ... $t) are no instruction
        img = self.image(self.table("Safe_Tick"), {**base, "Handler": [("", "vfp")]})
        handler = img.symbol("Handler").value & ~1
        img.symbols += [elffile.Symbol("$d", handler + 2, 0, 0, 0, 1), elffile.Symbol("$t", handler + 6, 0, 0, 0, 1)]
        out = check.Findings()
        check.check_isr_wrappers(img, out, "application")
        self.assertEqual(out, [])

    def test_fpu_decoder(self):
        vfp = {"vpush {d8-d9}": (0xED2D, 0x8B04), "vmov s19, r3": (0xEE09, 0x3A90), "vldr s0, [r0]": (0xED90, 0x0A00),
               "vmrs r0, fpscr": (0xEEF1, 0x0A10)}
        other = {"stmdb sp!": (0xE92D, 0x4FF0), "bl": (0xF000, 0xF800), "ldrd": (0xE9D0, 0x0100),
                 "mcr p15 (no FP)": (0xEE01, 0x0F10)}
        for name, (h1, h2) in {**vfp, **other}.items():
            with self.subTest(instruction=name):
                found = check.fpu_instructions(struct.pack("<HHH", 0xB508, h1, h2), 0x1000, [])
                self.assertEqual(found, [0x1002] if name in vfp else [])

    def test_branch_decoder(self):
        for at, target in ((0x1F000, 0x1F720), (0x1F000, 0x36324), (0x36000, 0x1F000), (0x8000, 0x8004)):
            for kind in ("bl", "b.w"):
                with self.subTest(at=hex(at), target=hex(target), kind=kind):
                    code = struct.pack("<H", 0xB508) + self.bl(at + 2, target, kind) + struct.pack("<H", 0xBF00)
                    self.assertEqual(check.thumb_calls(code, at), [(target, kind)])


class ConfigFieldUnitTest(unittest.TestCase):
    """NVM-15: flash configuration field of an image that writes sector 0 - without compiler and build."""

    REFERENCE = bytes([0xFF] * 12 + [0xFE, 0x7F, 0xFF, 0xFF])

    @classmethod
    def setUpClass(cls):
        cls.exp = check.load_expectation(DEMO_PROJECT)

    def image(self, field: bytes | None = None, start: int = 0) -> dict[int, int]:
        """Vector table words at `start` plus the field at 0x400 (left out with None)."""
        image = {start + i: 0x11 for i in range(0x40)}
        if field is not None:
            image.update({0x400 + i: b for i, b in enumerate(field)})
        return image

    def run_field(self, image: dict[int, int], exp=None) -> check.Findings:
        out = check.Findings()
        check.check_config_field(image, exp or self.exp, out, "img.hex", "bootloader")
        return out

    def test_family_and_reference(self):
        self.assertEqual(self.exp.family, "s32k1xx")
        self.assertEqual(check.CONFIG_FIELDS["s32k1xx"].reference, self.REFERENCE)
        startup = (REPO / "bootloader" / "platforms" / "s32k1xx" / "startup.S").read_text(encoding="utf-8")
        words = re.search(r"\.section \.FlashConfig.*?((?:\s*\.long\s+0x[0-9A-F]+){4})", startup, re.S).group(1)
        values = [int(w, 16) for w in re.findall(r"0x[0-9A-F]+", words)]
        self.assertEqual(struct.pack("<4I", *values), self.REFERENCE, "startup.S of the bootloader")

    def test_reference_field_passes(self):
        self.assertEqual(self.run_field(self.image(self.REFERENCE)), [])

    def test_image_outside_sector_0_is_not_checked(self):
        self.assertEqual(self.run_field(self.image(None, start=0x8000)), [])

    def test_family_without_field_is_not_checked(self):
        exp = check.load_expectation(DEMO_PROJECT)
        exp.family = "stm32"
        self.assertEqual(self.run_field(self.image(None), exp), [])

    def test_mutants_turn_it_red(self):
        def fsec(value):
            return self.REFERENCE[:12] + bytes([value]) + self.REFERENCE[13:]
        mutants = {
            "missing": (None, "fcf-missing"),
            "erased (all 0xFF)": (b"\xff" * 16, "fcf-secured"),
            "secured SEC 11": (fsec(0xFF), "fcf-secured"),
            "secured SEC 00": (fsec(0xFC), "fcf-secured"),
            "mass erase disabled": (fsec(0xEE), "fcf-mass-erase"),
            "backdoor key enabled": (fsec(0xBE), "fcf-backdoor"),
            "FPROT sector 0": (self.REFERENCE[:8] + b"\xfe\xff\xff\xff" + self.REFERENCE[12:], "fcf-protection"),
            "FDPROT": (self.REFERENCE[:15] + b"\x00", "fcf-protection"),
            "FOPT": (self.REFERENCE[:13] + b"\x7b" + self.REFERENCE[14:], "fcf-value"),
            "backdoor key bytes": (b"\x00" + self.REFERENCE[1:], "fcf-value"),
        }
        for name, (field, code) in mutants.items():
            with self.subTest(mutant=name):
                self.assertIn(code, codes(self.run_field(self.image(field))))
        partial = self.image(None)
        partial.update({0x400 + i: 0xFF for i in range(12)})           # FSEC..FDPROT left out
        self.assertIn("fcf-missing", codes(self.run_field(partial)))


# ---------------------------------------------------------------------- 2. demo build with mutants

class ImagesTestBase(unittest.TestCase):
    project: Path
    images: dict[str, Path]

    def run_check(self, images: dict[str, Path] | None = None, derivative: Path | None = None,
                  board: str | None = None, **kwargs):
        exp = check.load_expectation(self.project, derivative, None, board)
        images = images or self.images
        return check.check_images(exp, images.get("bootloader"), images.get("app"), images.get("updater"), **kwargs)

    def assert_clean(self, findings):
        self.assertEqual(findings.errors, [], "\n".join(map(str, findings)))
        self.assertLessEqual(codes(findings, "warning"), {"segment-extra-bytes"}, "\n".join(map(str, findings)))

    def compare_with_readelf(self, elf: Path):
        img = elffile.load(elf)
        out = subprocess.run([READELF, "-SlW", str(elf)], capture_output=True, text=True, check=True).stdout
        for m in re.finditer(r"\]\s+(\S+)\s+\S+\s+([0-9a-f]{8})\s+([0-9a-f]{6})\s+([0-9a-f]{6})", out):
            sec = img.section(m.group(1))
            self.assertIsNotNone(sec, m.group(1))
            self.assertEqual((sec.addr, sec.offset, sec.size), tuple(int(m.group(i), 16) for i in (2, 3, 4)),
                             m.group(1))
        loads = re.findall(r"LOAD\s+0x([0-9a-f]+) 0x([0-9a-f]+) 0x([0-9a-f]+) 0x([0-9a-f]+) 0x([0-9a-f]+)", out)
        mine = [(s.offset, s.vaddr, s.paddr, s.filesz, s.memsz) for s in img.segments if s.type == elffile.PT_LOAD]
        self.assertEqual(mine, [tuple(int(v, 16) for v in row) for row in loads])
        attrs = subprocess.run([READELF, "-A", str(elf)], capture_output=True, text=True, check=True).stdout
        arch = re.search(r"Tag_CPU_arch: (\S+)", attrs).group(1)
        self.assertEqual(cores.ARCH_NAMES[img.attribute(elffile.TAG_CPU_ARCH)], arch)
        self.assertEqual("VFP registers" in attrs, img.attribute(elffile.TAG_ABI_VFP_ARGS) == 1)


@unittest.skipUnless(ARM_GCC and READELF, "arm-none-eabi-gcc/readelf not found (PATH, NX_ARM_GCC_BIN)")
def configured_project(build: Path) -> Path | None:
    """The project a target build was configured with (NX_PROJECT in its CMakeCache.txt), or None."""
    cache = Path(build) / "CMakeCache.txt"
    if not cache.is_file():
        return None
    for line in cache.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("NX_PROJECT:"):
            value = Path(line.split("=", 1)[1].strip())
            if value.is_dir():
                value = value / "project.yaml"
            return value if value.is_file() else None
    return None


class DemoImagesTest(ImagesTestBase):
    # The expectation is the one of the project the demo build was built from (target-demo: the eval board project,
    # CMakeCache NX_PROJECT) - the record of the build carries that board's target identity, so checking it against
    # the synthetic testproject_full of PLAT-62 reports record-target for a correct build. Without that project (the
    # exported open tree) there is no demo build either, and the class is skipped.
    project: Path

    @classmethod
    def setUpClass(cls):
        build = build_dir_module().demo_build_dir(REPO)
        cls.images = check.find_build_images(build)
        if set(cls.images) != {"bootloader", "app", "updater"}:
            raise unittest.SkipTest(f"demo build incomplete in {build} (tools/test -k target-demo)")
        project = configured_project(build)
        if project is None:
            raise unittest.SkipTest(f"no project (NX_PROJECT) in the CMake cache of {build}")
        cls.project = project
        cls.tmp = Path(tempfile.mkdtemp(prefix="elfcheck_demo_"))
        cls.count = 0

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def fresh(self) -> dict[str, Path]:
        type(self).count += 1
        return copy_images(self.images, self.tmp / f"m{self.count}")

    def test_demo_images_pass(self):
        findings = self.run_check()
        self.assert_clean(findings)
        # the updater carries the bootloader it was built with, both records were checked (no warning left)
        self.assertEqual({f.code for f in findings}, {"segment-extra-bytes"})

    def test_reader_agrees_with_readelf(self):
        for key, elf in self.images.items():
            with self.subTest(image=key):
                self.compare_with_readelf(elf)

    def test_image_mutants_turn_it_red(self):
        def text_out_of_flash(m):
            shift_segment_of(m["app"], ".text", 0x80000)

        def list_across_application_end(m):
            # still inside the code flash, but reaching into the key sector behind the application area
            sec = elffile.load(m["app"]).section(".checksum")
            shift_segment_of(m["app"], ".checksum", 0x7E000 - 0x10 - sec.addr)

        def vector_table_into_bootloader(m):
            # the segment of .interrupts starts at 0 (it carries the ELF header): move the section inside it
            sec = elffile.load(m["app"]).section(".interrupts")
            patch(m["app"], sec.header_offset + 12, "<I", 0x2000)

        def data_into_flexram(m):
            # run address only (the load address stays in flash)
            img = elffile.load(m["app"])
            sec = img.section(".data_general")
            seg = img.segment_of(sec)
            patch(m["app"], sec.header_offset + 12, "<I", 0x14000000)
            patch(m["app"], seg.header_offset + 8, "<I", 0x14000000)

        def bss_over_boot_request(m):
            patch_layout(m["bootloader"], 9, elffile.load(m["app"]).section(".bss").addr + 0x10)

        def app_start_inside_bootloader(m):
            patch_layout(m["bootloader"], 4, 0x4000)

        def app_start_not_on_a_sector(m):
            patch_layout(m["bootloader"], 4, 0x8100)

        def record_in_application_area(m):
            patch_layout(m["bootloader"], 6, 0x40000)

        def fp_abi_soft(m):
            patch_attribute(m["app"], elffile.TAG_ABI_VFP_ARGS, 0)

        def fp_double_precision(m):
            patch_attribute(m["bootloader"], elffile.TAG_ABI_HARDFP_USE, 3)

        def fp_arch_of_another_core(m):
            patch_attribute(m["updater"], elffile.TAG_FP_ARCH, 8)

        def cpu_arch_v7m(m):
            patch_attribute(m["app"], elffile.TAG_CPU_ARCH, 10)

        def record_missing(m):
            check.record_of(m["app"]).unlink()

        def updater_record_missing(m):
            check.record_of(m["updater"]).unlink()

        def record_at_the_key_sector(m):
            rewrite_record(check.record_of(m["app"]), lambda address, data: address - 0x1000)

        def record_size_too_short(m):
            def change(address, data):
                struct.pack_into("<I", data, 8, struct.unpack_from("<I", data, 8)[0] - 4)
                struct.pack_into("<I", data, 16, zlib.crc32(bytes(data[:16])))
            rewrite_record(check.record_of(m["app"]), change)

        def record_of_another_layout(m):
            def change(address, data):
                struct.pack_into("<I", data, 40, 0x12345678)
                struct.pack_into("<I", data, 52, zlib.crc32(bytes(data[24:52])))
            rewrite_record(check.record_of(m["app"]), change)

        def image_changed_after_the_record(m):
            hex_path = m["app"].with_suffix(".hex")
            image = ihex.load(hex_path)
            image[0x9000] ^= 0xFF
            write_hex(hex_path, image)

        def rom_list_gap(m):
            path = m["app"]
            last_end = symbol_file_offset(path, check.ROM_LIST_END) - 4
            data = path.read_bytes()
            patch(path, last_end, "<I", struct.unpack_from("<I", data, last_end)[0] - 8)

        def rom_list_symbol_missing(m):
            img = elffile.load(m["app"])
            sym = img.symbol(check.ROM_LIST_START)
            data = m["app"].read_bytes()
            m["app"].write_bytes(data.replace(b"__checksum_list_start\0", b"__checksum_list_stXrt\0"))
            self.assertIsNotNone(sym)

        def updater_with_another_bootloader(m):
            path = m["bootloader"]
            img = elffile.load(path)
            text = img.section(".text")
            patch(path, text.offset + 0x40, "<B", img.data[text.offset + 0x40] ^ 0xFF)

        # NVM-15: the bootloader writes sector 0; its configuration field secured, mass erase off, or missing
        def fcf_secured(m):
            patch_config_field(m["bootloader"], 12, 0xFF)

        def fcf_mass_erase_disabled(m):
            patch_config_field(m["bootloader"], 12, 0xEE)

        def fcf_secured_in_the_hex_only(m):
            patch_config_field(m["bootloader"], 12, 0xFC, elf_too=False)

        def fcf_missing(m):
            img = elffile.load(m["bootloader"])
            patch(m["bootloader"], img.section(".flashconfig").header_offset + 20, "<I", 0)   # sh_size
            m["bootloader"].with_suffix(".hex").unlink()

        # DOC-28 (P5 (b), DE5): the allocator in an image - a symbol renamed in the symbol string table
        def app_with_malloc(m):
            rename_symbol(m["app"], "_ZdlPvj", "malloc")         # the state before DOC-28: delete -> free -> malloc

        def app_with_operator_new(m):
            rename_symbol(m["app"], "_ZdlPvj", "_Znwj")

        def bootloader_with_sbrk(m):
            img = elffile.load(m["bootloader"])
            name = next(s.name for s in img.symbols if s.type == check.STT_FUNC and s.bind == 1 and s.shndx
                        and len(s.name) >= len("_sbrk_r"))
            rename_symbol(m["bootloader"], name, "_sbrk_r")

        # SYS-170 (SYS69-S1): a vector straight on its handler, around the SAFE_ISR wrapper (gate stays open)
        def vector_around_its_wrapper(m):
            img = elffile.load(m["app"])
            table = img.symbol("__isr_vector")
            raw = img.read(table.value, table.size)
            words = struct.unpack(f"<{len(raw) // 4}I", raw)
            wrapper = img.symbol("Safe_SysTick_Handler")
            index = words.index(wrapper.value)
            patch(m["app"], symbol_file_offset(m["app"], "__isr_vector", 4 * index), "<I",
                  img.symbol("SysTick_Handler").value)

        mutants = {
            vector_around_its_wrapper: "isr-unwrapped",
            app_with_malloc: "heap-symbol",
            app_with_operator_new: "heap-symbol",
            bootloader_with_sbrk: "heap-symbol",
            text_out_of_flash: "section-outside-flash",
            list_across_application_end: "section-outside-area",
            vector_table_into_bootloader: "overlap",
            data_into_flexram: "section-region-kind",
            bss_over_boot_request: "boot-request",
            app_start_inside_bootloader: "overlap",
            app_start_not_on_a_sector: "sector-alignment",
            record_in_application_area: "record-position",
            fp_abi_soft: "fp-abi",
            fp_double_precision: "fp-precision",
            fp_arch_of_another_core: "fp-arch",
            cpu_arch_v7m: "core-arch",
            record_missing: "record-missing",
            updater_record_missing: "record-missing",
            record_at_the_key_sector: "record-position",
            record_size_too_short: "record-image",
            record_of_another_layout: "record-layout",
            image_changed_after_the_record: "record-image",
            rom_list_gap: "rom-list-coverage",
            rom_list_symbol_missing: "rom-list-missing",
            updater_with_another_bootloader: "blob-differs",
            fcf_secured: "fcf-secured",
            fcf_mass_erase_disabled: "fcf-mass-erase",
            fcf_secured_in_the_hex_only: "fcf-secured",
            fcf_missing: "fcf-missing",
        }
        for mutate, code in mutants.items():
            with self.subTest(mutant=mutate.__name__):
                images = self.fresh()
                mutate(images)
                findings = self.run_check(images)
                self.assertIn(code, codes(findings), "\n".join(map(str, findings)) or "no finding")

    def test_derivative_map_mutants_turn_it_red(self):
        """TF6: the layout is consistent with itself; only the derivative says it is wrong."""
        src = REPO / "mcudb" / "s32k1xx" / "S32K144.yaml"
        flash = "name: PFLASH, kind: code_flash, start: 0x00000000, size: 0x00080000, sector_size: 0x1000, " \
                "program_size: 8"
        mutants = {
            "flash_half_as_large": (flash, flash.replace("0x00080000", "0x00040000"), "layout-flash"),
            "sector_size_2k": (flash, flash.replace("0x1000", "0x800"), "layout-sector-size"),
            "sector_size_8k": (flash, flash.replace("0x1000", "0x2000"), "sector-alignment"),
            "program_size_16": (flash, flash.replace("program_size: 8", "program_size: 16"), "layout-phrase"),
            "sram_u_smaller": ("{ name: SRAM_U, kind: ram, start: 0x20000000, size: 0x00007000",
                               "{ name: SRAM_U, kind: ram, start: 0x20000000, size: 0x00004000", "layout-ram"),
            "core_m0plus": ("core: cortex-m4f", "core: cortex-m0plus", "core-arch"),
            "fpu_double": ("core: cortex-m4f", "core: cortex-m7f\n  fpu: dp", "fp-arch"),
        }
        for name, (old, new, code) in mutants.items():
            with self.subTest(mutant=name):
                derivative = derivative_variant(src, self.tmp / f"{name}.yaml", old, new)
                findings = self.run_check(derivative=derivative)
                self.assertIn(code, codes(findings), "\n".join(map(str, findings)) or "no finding")

    def test_record_of_another_board_is_red(self):
        findings = self.run_check(board="evalboard_s32k148")
        self.assertIn("record-target", codes(findings))

    def test_command_line(self):
        env = dict(os.environ, PYTHONUTF8="1")
        build = self.images["app"].parents[3]
        ok = subprocess.run([sys.executable, str(TOOL), "--project", str(self.project), "--build", str(build)],
                            capture_output=True, text=True, env=env)
        self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)
        self.assertIn("0 errors", ok.stdout)
        images = self.fresh()
        patch_attribute(images["app"], elffile.TAG_ABI_VFP_ARGS, 0)
        bad = subprocess.run([sys.executable, str(TOOL), "--project", str(self.project), "--bootloader",
                              str(images["bootloader"]), "--app", str(images["app"])],
                             capture_output=True, text=True, env=env)
        self.assertEqual(bad.returncode, 1, bad.stdout)
        self.assertRegex(bad.stdout, r"error: .*app\.ecu\.elf: section \.ARM\.attributes: Tag_ABI_VFP_args")

    def test_fcf_command_line(self):
        """NVM-15: `elfcheck fcf` - the post-build step of the bootloader (bootloader/CMakeLists.txt)."""
        env = dict(os.environ, PYTHONUTF8="1")
        bl = self.images["bootloader"]

        def fcf(*args):
            return subprocess.run([sys.executable, str(TOOL), "fcf", *args], capture_output=True, text=True, env=env)

        ok = fcf("--family", "s32k1xx", "--project", str(self.project), str(bl), str(bl.with_suffix(".hex")))
        self.assertEqual(ok.returncode, 0, ok.stdout + ok.stderr)
        images = self.fresh()
        patch_config_field(images["bootloader"], 12, 0xFF, elf_too=False)
        bad = fcf("--family", "s32k1xx", "--project", str(self.project), str(images["bootloader"]),
                  str(images["bootloader"].with_suffix(".hex")))
        self.assertEqual(bad.returncode, 1, bad.stdout + bad.stderr)
        self.assertRegex(bad.stderr, r"error: .*bootloader\.hex: flash configuration field .*FSEC 0xFF")
        self.assertNotRegex(bad.stderr, r"bootloader\.elf:", "the ELF itself is unchanged")
        # a family without a configuration field: no project needed, nothing checked
        self.assertEqual(fcf("--family", "stm32", str(images["bootloader"])).returncode, 0)


# ---------------------------------------------------------------------- 3. S32K148 build test

@unittest.skipUnless(ARM_GCC, "arm-none-eabi-gcc not found (PATH, NX_ARM_GCC_BIN)")
class Bt148ImagesTest(ImagesTestBase):
    project = BT148_PROJECT

    def test_bt148_images_pass(self):
        build = Path(os.environ.get("NX_BT148_BUILD_DIR", ""))
        images = check.find_build_images(build) if build.name else {}
        if "app" not in images:
            self.skipTest(f"no S32K148 build test in '{build}' (tools/test -k target-buildtest-s32k148-min)")
        self.assertIn("bootloader", images, "the build test builds the bootloader as well")
        self.images = images
        self.assert_clean(self.run_check())


# ---------------------------------------------------------------------- 4. S32K116: bootloader + own image

# K1 (PLAT-80), fixed in PLAT-31: bootloader/platforms/s32k1xx/startup.S said `.arch armv7-m` and copied .data/.bss
# with post-indexed ldr.w/str.w (Thumb-2) - the S32K116 bootloader and updater were marked v7 and would have faulted
# on the Cortex-M0+. The startup is Thumb-1 now; test_bootloader_and_updater and test_only_thumb1_instructions guard it.


def non_thumb1_instructions(elf: Path) -> list[str]:
    """The instruction check of platforms/common/test/test_cortex_m0plus.py (PLAT-10), loaded by path.

    In a linked image objdump also prints constant data inside code sections (layoutValues, tables) as hex rows
    `22a0:  0000 0000 0000 0002 ...  ................`; their "mnemonic" is a hex word - such rows are data, not
    instructions, and are left out."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "test_cortex_m0plus", REPO / "platforms" / "common" / "test" / "test_cortex_m0plus.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # an instruction has at most two halfwords; a shorter data row is still reported (red rather than overlooked)
    data_row = re.compile(r"^[0-9a-f]+:\s+(?:[0-9a-f]{4} ){3,}")
    return [line for line in module.non_thumb1_instructions(elf) if not data_row.match(line)]

def build_warnings(output: str) -> list[str]:
    """The lines of a build output that carry a warning - named in the failure message (TOOL-50: the tail of the
    output did not show the line that turned the check red)."""
    return [line for line in output.splitlines() if re.search(r"\bwarning\b:", line, re.I)]


SMALL_APP = r"""
#include <stdint.h>
extern uint32_t __StackTop;
void Reset_Handler(void);
static void Default_Handler(void) { for (;;) {} }
__attribute__((section(".isr_vector"), used)) void (*const vectors[48])(void) = {
    (void (*)(void))&__StackTop, Reset_Handler, Default_Handler, Default_Handler,
};
volatile uint32_t counter = 7U;           /* .data */
volatile uint32_t samples[16];            /* .bss */
void Reset_Handler(void) { for (;;) { samples[counter & 15U] += counter; counter++; } }
"""


@unittest.skipUnless(ARM_GCC, "arm-none-eabi-gcc not found (PATH, NX_ARM_GCC_BIN)")
class S32K116ImagesTest(ImagesTestBase):
    project = K116_PROJECT

    @classmethod
    def setUpClass(cls):
        build = os.environ.get("NX_S32K116_BUILD_DIR")
        if not build:
            raise unittest.SkipTest("NX_S32K116_BUILD_DIR not set (tools/test -k elfcheck-s32k116 sets it)")
        cls.build = Path(build)
        # TOOL-50: configure on every run, not only without CMakeCache.txt. After a change of the checked-out state
        # (new generators, project files) ninja re-runs CMake inside `cmake --build`; the configuration warnings of
        # nxconfig/bswgen (e.g. "board.yaml:85 (can[0].phy): warning: no module for part ...") then landed in the
        # build output and the warning check below turned the first run red (setUpClass, 0 instead of 5 tests),
        # the repetition green. Configure output and build output are kept apart now; the build output holds only
        # compiler and linker messages.
        # The interpreter running this test: in a worktree the preset's ${sourceDir}/.venv does not exist.
        # A cache whose compiler moved or vanished is configured fresh (build_dirs.stale_compiler, VERIFY-0928).
        fresh, _ = build_dir_module().fresh_args(cls.build, GCC_PATH)
        run = subprocess.run(["cmake", "--preset", "s32k116evb-minimal", *fresh, "-B", str(cls.build),
                              f"-DPython3_EXECUTABLE={Path(sys.executable).as_posix()}"], cwd=REPO,
                             capture_output=True, text=True)
        if run.returncode:
            raise AssertionError("configure s32k116evb-minimal failed:\n" + run.stdout[-3000:] + run.stderr[-3000:])
        run = subprocess.run(["cmake", "--build", str(cls.build), "--parallel", "2"], cwd=REPO,
                             capture_output=True, text=True)
        if run.returncode:
            raise AssertionError("build of the s32k116evb-minimal images failed:\n" + run.stdout[-3000:] + run.stderr[-3000:])
        warnings = build_warnings(run.stdout + run.stderr)
        if warnings:
            raise AssertionError("bootloader build not warning free:\n" + "\n".join(warnings[:40]))
        cls.images = check.find_build_images(cls.build)
        cls.tmp = Path(tempfile.mkdtemp(prefix="elfcheck_k116_"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def small_app(self, name: str, cpu_flags: list[str]) -> Path:
        """Own application image: the real linker template with the project's generated memoryLayout.h."""
        out = self.tmp / name
        out.mkdir()
        template = REPO / "platforms" / "s32k1xx" / "port" / "main" / "linkerscript"
        includes = ["-I", str(template), "-I", str(REPO / "platforms" / "s32k1xx" / "hardFaultHandler" / "include"),
                    "-I", str(self.build / "generated" / "board")]
        self.assertTrue((self.build / "generated" / "board" / "memory" / "memoryLayout.h").is_file())
        pre = subprocess.run([ARM_GCC, "-E", "-P", "-x", "c", *includes, str(template / "application.dld.in"),
                              "-o", str(out / "application.dld")], capture_output=True, text=True)
        self.assertEqual(pre.returncode, 0, pre.stderr)
        (out / "app.c").write_text(SMALL_APP, encoding="utf-8")
        elf = out / "app.elf"
        link = subprocess.run([ARM_GCC, *cpu_flags, "-mthumb", "-Os", "-Wall", "-Werror", "-nostdlib",
                               "-nostartfiles", "-ffunction-sections", "-fdata-sections", "-Wl,--gc-sections",
                               "-T", str(out / "application.dld"), str(out / "app.c"), "-o", str(elf)],
                              capture_output=True, text=True)
        self.assertEqual(link.returncode, 0, link.stderr)
        return elf

    def known(self, findings) -> set[tuple[str, str]]:
        return {(Path(f.file).name, f.code) for f in findings.errors}

    def test_bootloader_and_updater(self):
        self.assertEqual(set(self.images), {"bootloader", "app", "updater"})
        findings = self.run_check()
        self.assertEqual(self.known(findings), set(), "\n".join(map(str, findings)))
        self.assertLessEqual(codes(findings, "warning"), {"segment-extra-bytes"})

    def test_only_thumb1_instructions(self):
        """PLAT-31: no 32-bit Thumb-2 instruction in the linked images except bl/msr/mrs/dmb/dsb/isb (ARMv6-M)."""
        for key in ("bootloader", "updater"):   # the application (flash padding rows): test_s32k116_images.py
            with self.subTest(image=key):
                self.assertEqual(non_thumb1_instructions(self.images[key]), [])

    def test_own_application_image(self):
        app = self.small_app("m0plus", ["-mcpu=cortex-m0plus", "-mfloat-abi=soft"])
        findings = self.run_check({"bootloader": self.images["bootloader"], "app": app}, records=False)
        self.assertEqual(self.known(findings), set(), "\n".join(map(str, findings)))
        self.assertIsNotNone(elffile.load(app).symbol(check.ROM_LIST_START), "the template's ROM check list")

    def test_own_application_image_for_the_wrong_core(self):
        app = self.small_app("m4f", ["-mcpu=cortex-m4", "-mfpu=fpv4-sp-d16", "-mfloat-abi=hard"])
        findings = self.run_check({"bootloader": self.images["bootloader"], "app": app}, records=False)
        self.assertTrue({("app.elf", "core-arch"), ("app.elf", "fp-arch"), ("app.elf", "fp-abi")}
                        <= self.known(findings), "\n".join(map(str, findings)))

    def test_s32k144_images_are_not_s32k116_images(self):
        """The demo images of the other derivative against this map (if the demo build is there)."""
        demo = check.find_build_images(build_dir_module().demo_build_dir(REPO))
        if "app" not in demo:
            self.skipTest("no demo build")
        exp = check.load_expectation(derivative_file=REPO / "mcudb" / "s32k1xx" / "S32K116.yaml")
        findings = check.check_images(exp, demo.get("bootloader"), demo.get("app"), demo.get("updater"))
        self.assertTrue({"layout-flash", "section-outside-flash", "core-arch"} <= codes(findings))


# ---------------------------------------------------------------------- 5. STM32F446 bootloader

@unittest.skipUnless(ARM_GCC, "arm-none-eabi-gcc not found (PATH, NX_ARM_GCC_BIN)")
class Stm32ImagesTest(unittest.TestCase):
    """Bootloader of the family stm32 linked as bootloader/test/test_bl_stm32_build.py does it (test layout with
    unequal sectors and the record below the application), checked against mcudb/stm32/STM32F446RE.yaml."""

    LAYOUT = "stm32f446_unequal"
    DERIVATIVE = REPO / "mcudb" / "stm32" / "STM32F446RE.yaml"

    @classmethod
    def setUpClass(cls):
        if not (REPO / "external" / "openbsw" / "libs" / "3rdparty" / "cmsis").is_dir():
            raise unittest.SkipTest("external/openbsw missing (CMSIS headers)")
        sys.path.insert(0, str(REPO / "bootloader" / "test"))
        # the bootloader build of PLAT-16, not a generator of the check; imported by name because the module belongs
        # to the private family stm32 (TOOL-244: tools/test/tests.yaml drops this class in the export)
        stm32 = importlib.import_module("test_bl_stm32_build")

        cls.stm32 = stm32
        cls.tmp = Path(tempfile.mkdtemp(prefix="elfcheck_stm32_"))
        layout = stm32.hostbuild.load_test_layout(cls.LAYOUT)
        cls.layout = layout
        builder = stm32.Stm32BuildTest("test_bootloader_links_for_the_stm32f446_profile")
        builder.tmp = cls.tmp
        builder.gen = stm32.hostbuild.write_test_layout(cls.LAYOUT, cls.tmp / "gen")
        cls.elf = builder.link_bootloader()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def expectation(self, derivative: Path | None = None):
        exp = check.load_expectation(derivative_file=derivative or self.DERIVATIVE, flash="FLASH",
                                     board=self.layout["target"]["board"])
        ram = self.layout["bootloader_ram"]
        exp.bootloader_size = self.layout["bootloader_size"]             # project values of the test layout
        exp.bootloader_ram = ("SRAM1", ram["start"], ram["start"] + ram["size"])
        exp.app_ram = [("SRAM1", 0x20000000, 0x2001C000), ("SRAM2", 0x2001C000, 0x20020000)]
        return exp

    def test_stm32_bootloader_passes(self):
        findings = check.check_images(self.expectation(), bootloader=self.elf)
        self.assertEqual(list(findings), [], "\n".join(map(str, findings)))
        layout = check.read_layout(elffile.load(self.elf), check.Findings())
        self.assertEqual((layout.sector_size, layout.record_addr, layout.app_start), (0, 0x08004000, 0x0800C000),
                         "unequal sectors, record in sector 1, application behind the key sector 2")

    def test_stm32_derivative_mutants(self):
        flash = "      - { size: 0x00004000, count: 4 }"
        mutants = {
            "uniform_16k": ("    sectors:", "    sector_size: 0x4000\n    sectors_old:", "layout-sector-size"),
            "first_sectors_32k": (flash, "      - { size: 0x00008000, count: 2 }", "sector-alignment"),
            "flash_256k": ("    size: 0x00080000", "    size: 0x00040000", "layout-flash"),
        }
        for name, (old, new, code) in mutants.items():
            with self.subTest(mutant=name):
                derivative = derivative_variant(self.DERIVATIVE, self.tmp / f"{name}.yaml", old, new)
                findings = check.check_images(self.expectation(derivative), bootloader=self.elf)
                self.assertIn(code, codes(findings), "\n".join(map(str, findings)) or "no finding")


class WorkspaceDerivativeTest(unittest.TestCase):
    """A derivative of a customer workspace (search.mcudb of nextbsw.yaml, ARCH-56) is found before the tree's
    mcudb - the bootloader's fcf check of target-new-board (NBMCU in the workspace) failed without it."""

    def test_workspace_mcudb_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "nextbsw.yaml").write_text("schema_version: 1\nsearch:\n  mcudb: [mcudb]\n", encoding="utf-8")
            (root / "mcudb" / "fam").mkdir(parents=True)
            own = root / "mcudb" / "fam" / "WSMCU.yaml"
            own.write_text("derivative: {name: WSMCU, core: cortex-m4}\n", encoding="utf-8")
            (root / "projects" / "p").mkdir(parents=True)
            project = root / "projects" / "p" / "project.yaml"
            project.write_text("project: {}\n", encoding="utf-8")
            dirs = check.workspace_mcudb(project)
            self.assertEqual(dirs, ((root / "mcudb").resolve(),))
            self.assertEqual(check.find_derivative_file("WSMCU", check.MCUDB, dirs), own)
            with self.assertRaises(ValueError):          # counter-check: not in the tree alone
                check.find_derivative_file("WSMCU", check.MCUDB)

    def test_no_pin_file_keeps_the_tree(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ".git").mkdir()
            project = Path(tmp) / "project.yaml"
            project.write_text("project: {}\n", encoding="utf-8")
            self.assertEqual(check.workspace_mcudb(project), ())


if __name__ == "__main__":
    unittest.main()
