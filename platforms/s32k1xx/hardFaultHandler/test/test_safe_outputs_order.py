"""IO-32: the fault handlers switch the power outputs off before anything else (disassembly check).

The watchdog vector and the hard fault vector enter customHardFaultHandler directly (LR = EXC_RETURN), and its first
action is the safe outputs loop over nxSafeOutputs (hardFaultHandler/safeOutputs.h): before the `dsb` that ends the
loop there is no call, no stack access, no register above R3 (besides the XPSR copy in R12), no store except the
table store `str r2, [r1]` and no literal other than the table address. Only after that come the dump and
HardFault_Handler_Final with its UART output.

- Both handler variants (armv7m, armv6m) are assembled and linked into a small test ELF with a strong table and
  checked; built-in faults (loop removed, loop after the dump clear, stack use in the loop, address and value
  swapped, call before the loop) must be detected.
- An application ELF (argument --elf, environment NX_APP_ELF, otherwise app.ecu.elf under build/) is checked the same
  way, plus: vectors 3 (HardFault) and 38 (WDOG_EWM) branch straight into customHardFaultHandler, the table lies in
  a read-only section, it is the strong table of the project (as many entries as NX_SAFE_OUTPUT_COUNT of the
  generat next to the ELF), and it ends with an entry with address 0.
- IO-32 deadman: vector 65 (LPIT0 channel 1, IRQ 49) of the application ELF carries the halt deadman handler of the
  debug halt guard, and that handler works the safe outputs table through before it calls anything and before it
  writes any other register - only the flag check of the channel may come first. A project without a power stage
  has no such handler (the vector stays DefaultISR); the check is then skipped.

Run: .venv/Scripts/python platforms/s32k1xx/hardFaultHandler/test/test_safe_outputs_order.py [--elf <app.ecu.elf>]
(needs arm-none-eabi-gcc from PATH or NX_ARM_GCC_BIN)
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
MODULE = HERE.parent
REPO = HERE.parents[3]
GCC_PATH = os.pathsep.join(p for p in (os.environ.get("NX_ARM_GCC_BIN"), os.environ.get("PATH")) if p)
ARM_GCC = shutil.which("arm-none-eabi-gcc", path=GCC_PATH)
ELF_ARG: list[str] = []

INSN = re.compile(r"^\s*([0-9a-f]+):\s+(?:[0-9a-f]{4}(?: [0-9a-f]{4})?|[0-9a-f]{8})\s+(\S+)\s*(.*)$")
LITERAL = re.compile(r"\[pc, #\d+\]\s*[;@]\s*\(([0-9a-f]+)")
HIGH_REGISTERS = re.compile(r"\b(r[4-9]|r1[0-1]|sl|fp|sp|lr|pc)\b")
FORBIDDEN = {"bl", "blx", "bx", "push", "pop", "stmdb", "stmia", "stm", "ldm", "ldmia", "svc", "strd", "ldrd"}

VECTOR_HARD_FAULT, VECTOR_WDOG = 3, 38      # exception numbers (IRQ 22 + 16)
VECTOR_LPIT0_CH1 = 65                       # IO-32 halt deadman (IRQ 49 + 16, S32K14x)
# The deadman handler returns, so it may use the stack and any register; what must not happen before the outputs
# are off is a call or a store to anything but the table entry's register.
DEADMAN_FORBIDDEN = {"bl", "blx", "svc"}


def arm_tool(name: str) -> str:
    return str(Path(ARM_GCC).with_name(Path(ARM_GCC).name.replace("gcc", name)))


class Elf:
    """Minimal reader of a 32-bit little-endian ELF: sections and symbols by name (no pyelftools needed)."""

    def __init__(self, path: Path):
        self.path = path
        self.data = path.read_bytes()
        assert self.data[:4] == b"\x7fELF" and self.data[4] == 1 and self.data[5] == 1, f"{path}: no ELF32 LE"
        shoff, = struct.unpack_from("<I", self.data, 0x20)
        shentsize, shnum, shstrndx = struct.unpack_from("<HHH", self.data, 0x2E)
        raw = [struct.unpack_from("<IIIIIIIIII", self.data, shoff + i * shentsize) for i in range(shnum)]
        names = raw[shstrndx]
        self.sections = []
        for s in raw:
            name = self.data[names[4] + s[0]:].split(b"\0", 1)[0].decode()
            self.sections.append({"name": name, "type": s[1], "flags": s[2], "addr": s[3], "offset": s[4],
                                  "size": s[5]})

    def read(self, address: int, size: int) -> bytes:
        for s in self.sections:
            if s["type"] != 8 and s["flags"] & 2 and s["addr"] <= address and address + size <= s["addr"] + s["size"]:
                start = s["offset"] + address - s["addr"]
                return self.data[start:start + size]
        raise KeyError(f"0x{address:08x} is in no loaded section")

    def word(self, address: int) -> int:
        return struct.unpack("<I", self.read(address, 4))[0]

    def section_of(self, address: int) -> dict:
        return next(s for s in self.sections if s["flags"] & 2 and s["addr"] <= address < s["addr"] + s["size"])


def symbols(elf: Path) -> dict[str, int]:
    out = subprocess.run([arm_tool("nm"), str(elf)], capture_output=True, text=True, check=True).stdout
    table = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 3:
            table[parts[2]] = int(parts[0], 16)
    return table


def disassemble(elf: Path, function: str) -> list[tuple[int, str, str]]:
    out = subprocess.run([arm_tool("objdump"), "-d", f"--disassemble={function}", str(elf)],
                         capture_output=True, text=True, check=True).stdout
    return [(int(m.group(1), 16), m.group(2), m.group(3)) for m in map(INSN.match, out.splitlines()) if m]


def order_findings(elf_path: Path) -> list[str]:
    """Problems of the safe outputs prologue of customHardFaultHandler, empty if it is right."""
    elf, syms = Elf(elf_path), symbols(elf_path)
    table = syms.get("nxSafeOutputs")
    if table is None:
        return ["no symbol nxSafeOutputs"]
    code = disassemble(elf_path, "customHardFaultHandler")
    if not code:
        return ["no customHardFaultHandler"]
    end = next((i for i, (_, op, _) in enumerate(code) if op == "dsb"), None)
    if end is None:
        return ["no dsb that ends the safe outputs loop"]
    problems = []
    prologue = code[:end]
    stores = [(a, op, args) for a, op, args in prologue if op.startswith("str")]
    literals = [elf.word(int(m.group(1), 16)) for _, op, args in prologue if op.startswith("ldr")
                for m in [LITERAL.search(args)] if m]
    if literals != [table]:
        problems.append(f"literals before the loop end {[hex(v) for v in literals]} are not only nxSafeOutputs "
                        f"(0x{table:08x})")
    if [(op, args.replace(" ", "").replace(",#0]", "]")) for _, op, args in stores] != [("str", "r2,[r1]")]:
        problems.append(f"stores before the loop end: {stores} (expected exactly the table store str r2, [r1])")
    for address, op, args in prologue:
        if op.split(".")[0] in FORBIDDEN:
            problems.append(f"0x{address:x}: {op} {args} before the outputs are off")
        if op != "mrs" and HIGH_REGISTERS.search(args.split(";")[0].split("@")[0].replace("[pc", "[")):
            problems.append(f"0x{address:x}: {op} {args} uses a register above R3, SP or LR")
    first = prologue[0][1] if prologue else ""
    if first not in ("mrs", "ldr"):
        problems.append(f"first instruction {first} is neither the XPSR copy nor the table load")
    value_loads = [args.replace(" ", "") for _, op, args in prologue if op == "ldr"]
    if "r2,[r0,#4]" not in value_loads:
        problems.append("the value is not loaded from the entry (ldr r2, [r0, #4])")
    return problems


def vector_findings(elf_path: Path) -> list[str]:
    elf, syms = Elf(elf_path), symbols(elf_path)
    vectors = syms.get("__isr_vector", syms.get("__VECTOR_TABLE"))
    target = syms["customHardFaultHandler"]
    problems = []
    for number in (VECTOR_HARD_FAULT, VECTOR_WDOG):
        handler = elf.word(vectors + 4 * number) & ~1
        name = next((n for n, a in syms.items() if a == handler and not n.startswith("$")), hex(handler))
        code = disassemble(elf_path, name)
        if not code:
            problems.append(f"vector {number}: no code at {name}")
            continue
        _, op, args = code[0]
        direct = op in ("b", "b.w", "b.n") and f"<customHardFaultHandler>" in args
        via_register = (op == "ldr" and args.startswith("r0,") and len(code) > 1 and code[1][1] == "bx"
                        and elf.word(int(LITERAL.search(args).group(1), 16)) & ~1 == target)
        if not (direct or via_register):
            problems.append(f"vector {number} ({name}) starts with {op} {args}, not a branch to "
                            "customHardFaultHandler")
    return problems


def deadman_findings(elf_path: Path) -> list[str]:
    """IO-32: the halt deadman handler on vector 65 switches the power outputs off before anything else.

    Empty when the handler is right, and also when the project has no power stage (no handler on the vector).
    """
    elf, syms = Elf(elf_path), symbols(elf_path)
    vectors = syms.get("__isr_vector", syms.get("__VECTOR_TABLE"))
    table = syms.get("nxSafeOutputs")
    if vectors is None or table is None:
        return ["no vector table or no nxSafeOutputs"]
    handler = elf.word(vectors + 4 * VECTOR_LPIT0_CH1) & ~1
    name = next((n for n, a in syms.items() if a == handler and not n.startswith("$")), hex(handler))
    if name == "DefaultISR" or syms.get("LPIT0_Ch1_IRQHandler") is None:
        return []                                   # project without power stage: no deadman
    if handler != (syms["LPIT0_Ch1_IRQHandler"] & ~1):
        return [f"vector {VECTOR_LPIT0_CH1} points at {name}, not at LPIT0_Ch1_IRQHandler"]
    code = disassemble(elf_path, name)
    if not code:
        return [f"no code at {name}"]
    end = next((i for i, (_, op, _) in enumerate(code) if op.startswith("dsb")), None)
    if end is None:
        return ["the deadman handler has no dsb: it does not walk the safe outputs table"]
    problems = []
    prologue = code[:end]
    literals = [elf.word(int(m.group(1), 16)) for _, op, args in prologue if op.startswith("ldr")
                for m in [LITERAL.search(args)] if m]
    if table not in literals:
        problems.append(f"the deadman handler does not load nxSafeOutputs (0x{table:08x}) before the outputs are off")
    # Exactly the table store; which registers the compiler picks is its business, but a second store before the
    # barrier would be another register written before the power outputs are safe.
    stores = [(a, op, args) for a, op, args in prologue if op.startswith("str")]
    if len(stores) != 1:
        problems.append(f"{len(stores)} stores before the outputs are off: {stores} (expected only the table store)")
    for address, op, args in prologue:
        if op.split(".")[0] in DEADMAN_FORBIDDEN:
            problems.append(f"0x{address:x}: {op} {args} before the outputs are off")
    return problems


def table_findings(elf_path: Path, expected_count: int | None) -> list[str]:
    elf, syms = Elf(elf_path), symbols(elf_path)
    table = syms["nxSafeOutputs"]
    section = elf.section_of(table)
    problems = []
    if section["flags"] & 1:
        problems.append(f"nxSafeOutputs lies in the writable section {section['name']}")
    count = 0
    while elf.word(table + 16 * count) != 0:
        count += 1
        if count > 64:
            return problems + ["nxSafeOutputs has no end entry within 64 entries"]
    if expected_count is not None and count != expected_count:
        problems.append(f"nxSafeOutputs has {count} entries, the generat NX_SAFE_OUTPUT_COUNT {expected_count}")
    return problems


def generated_count(elf_path: Path) -> int | None:
    """NX_SAFE_OUTPUT_COUNT of the generat of the build the ELF belongs to."""
    for parent in elf_path.parents:
        header = parent / "generated" / "board" / "bsp" / "safeOutputs" / "safeOutputsConfiguration.h"
        if header.exists():
            m = re.search(r"#define NX_SAFE_OUTPUT_COUNT (\d+)", header.read_text(encoding="utf-8"))
            return int(m.group(1)) if m else None
    return None


STUB = """
#include <stdint.h>
typedef struct { uint32_t a, v, g, m; } E;
const E nxSafeOutputs[] = {{0x40038094u, 0x3Fu, 0x400650E0u, 0x40000000u}, {0u, 0u, 0u, 0u}};
void HardFault_Handler_Final(void) { for (;;) {} }
"""

ARCHITECTURES = {"armv7m": "-mcpu=cortex-m4", "armv6m": "-mcpu=cortex-m0plus"}


@unittest.skipUnless(ARM_GCC, "arm-none-eabi-gcc not found (PATH or NX_ARM_GCC_BIN)")
class HandlerOrderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def link(self, arch: str, source: str, name: str) -> Path:
        asm = self.tmp / f"{name}.S"
        asm.write_text(source, encoding="utf-8")
        stub = self.tmp / "stub.c"
        stub.write_text(STUB, encoding="utf-8")
        elf = self.tmp / f"{name}.elf"
        cmd = [ARM_GCC, ARCHITECTURES[arch], "-mthumb", "-O1", "-nostdlib", "-nostartfiles",
               f"-I{MODULE / 'include'}", str(asm), str(stub), "-o", str(elf), "-Wl,-e,customHardFaultHandler",
               "-Wl,--defsym=__hard_fault_dump_start=0x20000000", "-Wl,--defsym=__hard_fault_ram_start=0x1FFF8000",
               "-Wl,--defsym=__hard_fault_ram_end=0x20007000", "-Wl,-Ttext=0x1000"]
        r = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr[-3000:])
        return elf

    def source(self, arch: str) -> str:
        return (MODULE / "src" / arch / "hardFaultHandler.S").read_text(encoding="utf-8").replace("\r\n", "\n")

    def test_handlers_switch_the_outputs_off_first(self):
        for arch in ARCHITECTURES:
            with self.subTest(arch=arch):
                self.assertEqual(order_findings(self.link(arch, self.source(arch), arch)), [])

    def test_mutants_are_detected(self):
        loop = re.compile(r"    ldr     R0, =nxSafeOutputs\n.*?    dsb\n", re.S)
        for arch in ARCHITECTURES:
            src = self.source(arch)
            block = loop.search(src).group(0)
            clear = "    // Clear the whole dump"
            final = "    // Complete HardFault handling"
            mutants = {
                "loop removed": src.replace(block, ""),
                "loop after the dump clear": src.replace(block, "").replace(final, block + final),
                "stack in the loop": src.replace(block, block.replace("    5:\n", "    5:\n    push    {R4}\n")),
                "address and value swapped": src.replace(block, block.replace("str     R2, [R1]",
                                                                              "str     R1, [R2]")),
                "call before the loop": src.replace(block, "    bl      HardFault_Handler_Final\n" + block),
                "dump before the outputs": src.replace(block, "").replace(clear, block + clear).replace(
                    "    // Save flags for dump", "    ldr     R2, =__hard_fault_dump_start\n    str     R2, [R2]\n"
                    "    // Save flags for dump"),
            }
            for name, mutant in mutants.items():
                with self.subTest(arch=arch, mutant=name):
                    self.assertNotEqual(mutant, src)
                    self.assertNotEqual(order_findings(self.link(arch, mutant, f"{arch}_{len(name)}")), [])


def application_elf() -> Path | None:
    for candidate in [*ELF_ARG, os.environ.get("NX_APP_ELF")]:
        if candidate:
            return Path(candidate)
    found = sorted((REPO / "build").glob("**/app.ecu.elf"), key=lambda p: p.stat().st_mtime, reverse=True)
    return found[0] if found else None


@unittest.skipUnless(ARM_GCC, "arm-none-eabi-gcc not found (PATH or NX_ARM_GCC_BIN)")
class ApplicationElfTest(unittest.TestCase):
    def setUp(self):
        self.elf = application_elf()
        if self.elf is None or not self.elf.exists():
            self.skipTest("no application ELF (--elf, NX_APP_ELF or build/**/app.ecu.elf)")

    def test_order_vectors_and_table(self):
        print(f"\nELF {self.elf}", file=sys.stderr)
        self.assertEqual(order_findings(self.elf), [])
        self.assertEqual(vector_findings(self.elf), [])
        self.assertEqual(table_findings(self.elf, generated_count(self.elf)), [])

    def test_halt_deadman_switches_the_outputs_off_first(self):
        self.assertEqual(deadman_findings(self.elf), [])


if __name__ == "__main__":
    if "--elf" in sys.argv:
        i = sys.argv.index("--elf")
        ELF_ARG.append(sys.argv[i + 1])
        del sys.argv[i:i + 2]
    unittest.main()
