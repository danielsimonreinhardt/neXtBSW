"""SYS-116: the selected safe state of a PWM group - freewheel or active short - in every walk (host and disassembly).

doc/safety/partner-and-degraded.md part C, decisions C1, C2, C4, C5, C6 of 2026-09-29. Three layers, each with
built-in faults that must be detected:

- **HostTest** (zig c++): test/host/SafeStateSelectHostTest.cpp - the C walk of hardFaultHandler/safeOutputs.h, S1 and
  SD of safeIo, the selection service and the start test of src/SafeOutputSelection.cpp on a register model with the
  two-group table of test/host/selectStubs. 22 mutants of the walk, the service, the start test, the degraded walk,
  the read-back rule of SYS-30, the interrupt lock of SYS-172 (race tests with a lock model and a pending interrupt
  between check and store)
  and of the stub table (a static_assert that stops the build counts as detected).
- **AsmWalkTest** (arm-none-eabi-gcc): both assembler handlers (armv7m, armv6m) assembled **with**
  NX_SAFE_OUTPUT_SELECT, linked with src/SafeOutputSelection.cpp compiled for the target, and their walk **executed**
  by a small interpreter of the Thumb instructions it uses (ldr/str/cmp/tst/adds/subs/lsls/b/beq/bne) on a model of
  the peripheral registers and the RAM - the same scenarios as the host test, so the two walks (assembler and C)
  are held to one expectation. Plus the disassembly rules of the fault handler prologue (test_safe_outputs_order):
  nothing before the barrier but R0-R3, one store `str r2, [r1]`, no call, no stack, and literals only the extended
  table, the selection words and the two patterns. 9 mutants per architecture.
- Without NX_SAFE_OUTPUT_SELECT the handlers assemble to exactly the bytes of the handler before SYS-116 (checked by
  comparing with the handler sources of the base commit is the job of the target build; here: the preprocessed
  source without the definition contains no trace of the selection).

Run: .venv/Scripts/python platforms/s32k1xx/hardFaultHandler/test/test_safe_state_select.py
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
MODULE = HERE.parent                                    # platforms/s32k1xx/hardFaultHandler
PLATFORM = MODULE.parent                                # platforms/s32k1xx
REPO = HERE.parents[3]
sys.path.insert(0, str(HERE))
import test_safe_outputs_order as order  # noqa: E402

HOST_TEST = Path("hardFaultHandler/test/host/SafeStateSelectHostTest.cpp")
COPY = ("hardFaultHandler/include", "hardFaultHandler/src", "hardFaultHandler/test/host",
        "port/safety/safeIo/include", "port/safety/safeIo/src", "port/safety/safeIo/test/host/stubs")

SHORT_LOW, SHORT_HIGH = 0xC35A3CA5, 0x3CA5C35A

try:
    import ziglang  # noqa: F401
    HAVE_ZIG = True
except ImportError:
    HAVE_ZIG = False


def copy_tree(dst: Path) -> Path:
    for rel in COPY:
        shutil.copytree(PLATFORM / rel, dst / rel)
    return dst


def build_host(tree: Path, exe: Path) -> subprocess.CompletedProcess:
    cmd = [sys.executable, "-m", "ziglang", "c++", "-std=c++17", "-O1", "-Wall", "-Wextra", "-Wconversion",
           "-Wno-sign-conversion", "-Werror", "-DNX_SAFE_OUTPUT_SELECT=1",
           f"-I{tree / 'hardFaultHandler/test/host/selectStubs'}", f"-I{tree / 'hardFaultHandler/include'}",
           f"-I{tree / 'port/safety/safeIo/include'}", f"-I{tree / 'port/safety/safeIo/test/host/stubs'}",
           str(tree / HOST_TEST), "-o", str(exe)]
    return subprocess.run(cmd, capture_output=True, text=True)


HOST_MUTANTS = {
    # file (relative to platforms/s32k1xx), original, mutation
    "walk: short values swapped": ("hardFaultHandler/include/hardFaultHandler/safeOutputs.h",
                                   "                return entry->shortLow;", "                return entry->shortHigh;"),
    "walk: timer clock not checked": ("hardFaultHandler/include/hardFaultHandler/safeOutputs.h",
                                      "if ((*gate & entry->groupGateMask) != 0U)", "if (gate != nullptr)"),
    "walk: word of group 1 for every group": ("hardFaultHandler/include/hardFaultHandler/safeOutputs.h",
                                              "nxSafeStateSelection[entry->group - 1U]", "nxSafeStateSelection[0]"),
    "walk: any other word is short high": ("hardFaultHandler/include/hardFaultHandler/safeOutputs.h",
                                           "if (word == NX_SAFE_STATE_WORD_SHORT_HIGH)",
                                           "if (word != NX_SAFE_STATE_WORD_SHORT_LOW)"),
    "walk: bit test instead of the pattern": ("hardFaultHandler/include/hardFaultHandler/safeOutputs.h",
                                              "if (word == NX_SAFE_STATE_WORD_SHORT_LOW)",
                                              "if ((word & NX_SAFE_STATE_WORD_SHORT_LOW) != 0U)"),
    "service: allowed list ignored": ("hardFaultHandler/src/SafeOutputSelection.cpp",
                                      "    if ((state >= NX_SAFE_STATE_COUNT) || ((GROUPS[group].allowed & "
                                      "NX_SAFE_STATE_BIT(state)) == 0U))",
                                      "    if (state >= NX_SAFE_STATE_COUNT)"),
    "service: block ignored": ("hardFaultHandler/src/SafeOutputSelection.cpp",
                               "if ((state != NX_SAFE_STATE_FREEWHEEL) && (shortBlocked[group] != 0U))",
                               "if (false)"),
    "block: short stays selected": ("hardFaultHandler/src/SafeOutputSelection.cpp",
                                    "        nxSafeStateSelection[group] = 0U;\n", ""),
    "service: patterns swapped": ("hardFaultHandler/src/SafeOutputSelection.cpp",
                                  "return (state == NX_SAFE_STATE_SHORT_LOW)    ? NX_SAFE_STATE_WORD_SHORT_LOW",
                                  "return (state == NX_SAFE_STATE_SHORT_LOW)    ? NX_SAFE_STATE_WORD_SHORT_HIGH"),
    "start test: no read-back": ("hardFaultHandler/src/SafeOutputSelection.cpp",
                                 "if (*NX_SAFE_OUTPUT_REG(e.address) != value)", "if (false)"),
    "start test: end writes freewheel": ("hardFaultHandler/src/SafeOutputSelection.cpp",
                                         "*NX_SAFE_OUTPUT_REG(e.address) = testBefore[i];",
                                         "*NX_SAFE_OUTPUT_REG(e.address) = e.value;"),
    "start test: clock not checked": ("hardFaultHandler/src/SafeOutputSelection.cpp",
                                      "return (*NX_SAFE_OUTPUT_REG(e.groupGateAddress) & e.groupGateMask) != 0U;",
                                      "return e.groupGateMask != 0U;"),
    "start test: failure not blocked": ("hardFaultHandler/src/SafeOutputSelection.cpp",
                                        "        nxSafeStateBlockShort(group); // begin failed",
                                        "        static_cast<void>(group); // begin failed"),
    # SYS-172 (HFH-F06): the interrupt lock of every check-then-store and the end of a running test by a block
    "select: no lock": ("hardFaultHandler/src/SafeOutputSelection.cpp",
                        "    NX_SAFE_STATE_LOCK const lock; // SYS-172 select:", "    // SYS-172 select:"),
    "block: no lock": ("hardFaultHandler/src/SafeOutputSelection.cpp",
                       "        NX_SAFE_STATE_LOCK const lock; // SYS-172 block:", "        // SYS-172 block:"),
    "start test begin: no lock": ("hardFaultHandler/src/SafeOutputSelection.cpp",
                                  "    NX_SAFE_STATE_LOCK const lock; // SYS-172 start test begin:",
                                  "    // SYS-172 start test begin:"),
    "block: running start test not ended": ("hardFaultHandler/src/SafeOutputSelection.cpp",
                                            "        endTestInFreewheel(group);\n",
                                            "        if (testGroup == 0U)\n        {\n"
                                            "            endTestInFreewheel(group);\n        }\n"),
    "degraded walk ignores the selection": ("port/safety/safeIo/src/safeIo/SafeStateDegraded.cpp",
                                            "= nxSafeOutputSelectedValue(entry);", "= entry->value;"),
    # SYS-30: acceptance rule of the read-back of S1 with a selected short
    "read-back: the selected short is not accepted": ("port/safety/safeIo/include/safeIo/SafeIoRegisters.h",
                                                      " || (value == (selected & r.mask)))", ")"),
    "read-back: the bound enable is judged in the short": ("port/safety/safeIo/include/safeIo/SafeIoRegisters.h",
                                                           "if ((selected == 0U) || (writeAddress != r.address) || ",
                                                           "if ("),
    # table: a value for a short the group does not allow, and an entry that is not the one of nxSafeOutputs - the
    # compile-time checks of SafeOutputSelection.cpp must refuse both
    "table: short high value for a group without it": (
        "hardFaultHandler/test/host/selectStubs/bsp/safeOutputs/safeOutputsConfiguration.h",
        "0x40000000U, 0x0A0FU, 0x0FU)", "0x40000000U, 0x0A0FU, 0x050FU)"),
    "table: freewheel value differs from nxSafeOutputs": (
        "hardFaultHandler/test/host/selectStubs/bsp/safeOutputs/safeOutputsConfiguration.h",
        "X(MOT2, 0x40026094U, 0x0FU, 0x40065098U, 0x40000000U, 2U", "X(MOT2, 0x40026094U, 0x0EU, 0x40065098U, "
                                                                   "0x40000000U, 2U"),
}


@unittest.skipUnless(HAVE_ZIG, "ziglang not installed")
class HostTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_host(self, tree: Path, name: str) -> tuple[bool, str]:
        exe = self.tmp / f"{name}.exe"
        r = build_host(tree, exe)
        if r.returncode != 0:
            return False, "build: " + r.stderr[-1500:]
        r = subprocess.run([str(exe)], capture_output=True, text=True)
        return r.returncode == 0, r.stdout[-3000:]

    def test_host(self):
        ok, out = self.run_host(copy_tree(self.tmp / "clean"), "clean")
        print("\n" + out.strip().splitlines()[-1], file=sys.stderr)
        self.assertTrue(ok, out)
        self.assertIn(" 0 failures", out)

    def test_mutants_are_detected(self):
        for n, (name, (rel, old, new)) in enumerate(HOST_MUTANTS.items()):
            with self.subTest(mutant=name):
                tree = copy_tree(self.tmp / f"m{n}")
                path = tree / rel
                text = path.read_text(encoding="utf-8")
                self.assertEqual(text.count(old), 1, f"{name}: mutation point not unique/found in {rel}")
                path.write_text(text.replace(old, new), encoding="utf-8")
                ok, out = self.run_host(tree, f"m{n}")
                self.assertFalse(ok, f"mutant not detected: {name}\n{out}")


# ------------------------------------------------------------------------------------------------------------------
# Assembler walk: interpreter of the Thumb subset of the safe outputs loop
# ------------------------------------------------------------------------------------------------------------------

MASK32 = 0xFFFFFFFF
REG = r"(r\d+|ip)"


class Machine:
    """Executes customHardFaultHandler from its first instruction to the `dsb` that ends the walk."""

    def __init__(self, elf_path: Path, registers: dict[int, int], ram: dict[int, int]):
        self.elf = order.Elf(elf_path)
        self.code = order.disassemble(elf_path, "customHardFaultHandler")
        self.registers, self.ram = registers, ram
        self.writes: list[tuple[int, int]] = []

    def read(self, address: int) -> int:
        if address in self.registers:
            return self.registers[address]
        if address in self.ram:
            return self.ram[address]
        try:
            return self.elf.word(address)
        except KeyError:
            raise AssertionError(f"read of unmapped address 0x{address:08x}") from None

    def run(self, max_steps: int = 20000) -> list[tuple[int, int]]:
        index = {a: i for i, (a, _, _) in enumerate(self.code)}
        r = {f"r{i}": 0 for i in range(13)}
        z = False
        i = 0
        for _ in range(max_steps):
            address, op, args = self.code[i]
            base = op.split(".")[0]
            text = re.split(r"[;@]", args)[0].strip()
            nxt = i + 1

            def val(token: str) -> int:
                token = token.strip()
                return int(token[1:], 0) & MASK32 if token.startswith("#") else r[token.replace("ip", "r12")]

            if base == "dsb":
                return self.writes
            if base == "mrs":
                pass
            elif base == "ldr" and "[pc" in text:
                m = order.LITERAL.search(args)
                r[text.split(",")[0].strip().replace("ip", "r12")] = self.elf.word(int(m.group(1), 16))
            elif base == "ldr":
                m = re.fullmatch(rf"{REG}, \[{REG}(?:, (#-?\d+|{REG}))?\]", text)
                if m is None:
                    raise AssertionError(f"0x{address:x}: unsupported {op} {args}")
                offset = val(m.group(3)) if m.group(3) else 0
                r[m.group(1)] = self.read((r[m.group(2)] + offset) & MASK32)
            elif base == "str":
                m = re.fullmatch(rf"{REG}, \[{REG}(?:, (#\d+))?\]", text)
                if m is None:
                    raise AssertionError(f"0x{address:x}: unsupported {op} {args}")
                target = (r[m.group(2)] + (val(m.group(3)) if m.group(3) else 0)) & MASK32
                self.writes.append((target, r[m.group(1)]))
                if target in self.registers:
                    self.registers[target] = r[m.group(1)]
                elif target in self.ram:
                    raise AssertionError(f"0x{address:x}: store to RAM 0x{target:08x} in the walk")
                else:
                    raise AssertionError(f"0x{address:x}: store to unmodelled 0x{target:08x}")
            elif base == "cmp":
                a, b = [t.strip() for t in text.split(",")]
                z = ((val(a) - val(b)) & MASK32) == 0
            elif base == "tst":
                a, b = [t.strip() for t in text.split(",")]
                z = (val(a) & val(b)) == 0
            elif base in ("adds", "subs", "add", "sub"):
                parts = [t.strip() for t in text.split(",")]
                dst, src, operand = (parts[0], parts[0], parts[1]) if len(parts) == 2 else parts
                result = val(src) + val(operand) if base.startswith("add") else val(src) - val(operand)
                r[dst] = result & MASK32
                if base.endswith("s"):
                    z = r[dst] == 0
            elif base == "lsls":
                dst, src, amount = [t.strip() for t in text.split(",")]
                r[dst] = (val(src) << val(amount)) & MASK32
                z = r[dst] == 0
            elif base in ("b", "beq", "bne"):
                target = int(text.split()[0], 16)
                if base == "b" or (base == "beq" and z) or (base == "bne" and not z):
                    nxt = index[target]
            else:
                raise AssertionError(f"0x{address:x}: instruction {op} {args} is not expected in the walk")
            i = nxt
        raise AssertionError("the walk does not end (no dsb within the step limit)")


# Registers of the stub table (test/host/selectStubs) and what each scenario must write
FTM0_SWOCTRL, FTM3_SWOCTRL = 0x40038094, 0x40026094
PTC_PCOR, PTD_PSOR, PTB_PCOR, PTE_PCOR = 0x400FF088, 0x400FF0C4, 0x400FF048, 0x400FF108
PCC = {"FTM0": 0x40065060, "FTM3": 0x40065098, "PORTB": 0x40065120, "PORTC": 0x40065124, "PORTD": 0x40065128,
       "PORTE": 0x4006512C}
CGC = 0x40000000
FREEWHEEL = [(FTM0_SWOCTRL, 0x3F), (FTM3_SWOCTRL, 0x0F), (PTC_PCOR, 1 << 11), (PTD_PSOR, 1 << 17), (PTB_PCOR, 1 << 4)]

SCENARIOS = {
    # name: (word group 1, word group 2, gated clocks, expected writes in table order)
    "reset: freewheel": (0, 0, (), FREEWHEEL),
    "group 1 short low": (SHORT_LOW, 0, (), [(FTM0_SWOCTRL, 0x2A3F), (FTM3_SWOCTRL, 0x0F), (PTC_PCOR, 0),
                                             (PTD_PSOR, 1 << 17), (PTB_PCOR, 1 << 4)]),
    "group 1 short high, group 2 short low": (SHORT_HIGH, SHORT_LOW, (),
                                              [(FTM0_SWOCTRL, 0x153F), (FTM3_SWOCTRL, 0x0A0F), (PTC_PCOR, 0),
                                               (PTD_PSOR, 0), (PTB_PCOR, 1 << 4)]),
    "group 2 short high not allowed": (0, SHORT_HIGH, (), FREEWHEEL),
    "corrupted words": (SHORT_LOW ^ 1, SHORT_HIGH ^ 0x80000000, (), FREEWHEEL),
    "all ones": (MASK32, MASK32, (), FREEWHEEL),
    "timer clock of group 1 off": (SHORT_LOW, 0, ("FTM0",), [(FTM3_SWOCTRL, 0x0F), (PTC_PCOR, 1 << 11),
                                                              (PTD_PSOR, 1 << 17), (PTB_PCOR, 1 << 4)]),
    "port clock of the enable off": (SHORT_LOW, 0, ("PORTC",), [(FTM0_SWOCTRL, 0x2A3F), (FTM3_SWOCTRL, 0x0F),
                                                                 (PTD_PSOR, 1 << 17), (PTB_PCOR, 1 << 4)]),
}

STUB_C = """
void HardFault_Handler_Final(void) { for (;;) {} }
"""


def arm_gxx() -> str:
    return order.arm_tool("g++")


@unittest.skipUnless(order.ARM_GCC, "arm-none-eabi-gcc not found (PATH or NX_ARM_GCC_BIN)")
class AsmWalkTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def link(self, arch: str, source: str, name: str, select: bool = True) -> Path:
        asm = self.tmp / f"{name}.S"
        asm.write_text(source, encoding="utf-8")
        stub = self.tmp / "stub.c"
        stub.write_text(STUB_C, encoding="utf-8")
        elf = self.tmp / f"{name}.elf"
        cpu = order.ARCHITECTURES[arch]
        defines = ["-DNX_SAFE_OUTPUT_SELECT=1"] if select else []
        objects = [str(asm), str(stub)]
        if select:
            obj = self.tmp / f"{arch}_selection.o"
            if not obj.exists():
                r = subprocess.run([arm_gxx(), cpu, "-mthumb", "-O2", "-std=c++17", "-Wall", "-Wextra", "-Werror",
                                    "-fno-exceptions", "-fno-rtti", *defines,
                                    f"-I{MODULE / 'test/host/selectStubs'}", f"-I{MODULE / 'include'}", "-c",
                                    str(MODULE / "src/SafeOutputSelection.cpp"), "-o", str(obj)],
                                   capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr[-3000:])
            objects.append(str(obj))
        cmd = [order.ARM_GCC, cpu, "-mthumb", "-O1", "-nostdlib", "-nostartfiles", *defines,
               f"-I{MODULE / 'include'}", *objects, "-o", str(elf), "-Wl,-e,customHardFaultHandler",
               "-Wl,--defsym=__hard_fault_dump_start=0x20000000", "-Wl,--defsym=__hard_fault_ram_start=0x1FFF8000",
               "-Wl,--defsym=__hard_fault_ram_end=0x20007000", "-Wl,-Ttext=0x1000", "-Wl,-Tbss=0x20001000"]
        if not select:
            stub_table = self.tmp / "table.c"
            stub_table.write_text(order.STUB, encoding="utf-8")
            cmd.insert(cmd.index(str(stub)) + 1, str(stub_table))
            cmd.remove(str(stub))
        r = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr[-3000:])
        return elf

    def source(self, arch: str) -> str:
        return (MODULE / "src" / arch / "hardFaultHandler.S").read_text(encoding="utf-8").replace("\r\n", "\n")

    def walk(self, elf: Path, word1: int, word2: int, gated: tuple[str, ...]) -> list[tuple[int, int]]:
        syms = order.symbols(elf)
        words = syms["nxSafeStateSelection"]
        # PORTE is gated in every scenario, as in the host model: its entry must never be written
        registers = {address: (0 if name in gated or name == "PORTE" else CGC) for name, address in PCC.items()}
        for reg in (FTM0_SWOCTRL, FTM3_SWOCTRL, PTC_PCOR, PTD_PSOR, PTB_PCOR, PTE_PCOR):
            registers[reg] = 0xDEADBEEF
        ram = {words: word1, words + 4: word2}
        return Machine(elf, registers, ram).run()

    def scenario_findings(self, elf: Path) -> list[str]:
        found = []
        for name, (w1, w2, gated, expected) in SCENARIOS.items():
            try:
                writes = self.walk(elf, w1, w2, gated)
            except AssertionError as e:
                found.append(f"{name}: {e}")
                continue
            if writes != expected:
                found.append(f"{name}: {[(hex(a), hex(v)) for a, v in writes]} != "
                             f"{[(hex(a), hex(v)) for a, v in expected]}")
        return found

    def select_order_findings(self, elf: Path) -> list[str]:
        """The prologue rules of test_safe_outputs_order for the extended walk."""
        syms = order.symbols(elf)
        allowed = {syms["nxSafeOutputsSelect"], syms["nxSafeStateSelection"], SHORT_LOW, SHORT_HIGH}
        code = order.disassemble(elf, "customHardFaultHandler")
        end = next((i for i, (_, op, _) in enumerate(code) if op == "dsb"), None)
        if end is None:
            return ["no dsb"]
        elf_data = order.Elf(elf)
        prologue = code[:end]
        problems = []
        literals = [elf_data.word(int(m.group(1), 16)) for _, op, args in prologue if op.startswith("ldr")
                    for m in [order.LITERAL.search(args)] if m]
        if set(literals) - allowed or syms["nxSafeOutputsSelect"] not in literals:
            problems.append(f"literals {[hex(v) for v in literals]}")
        stores = [(op, args.replace(" ", "").replace(",#0]", "]")) for _, op, args in prologue if op.startswith("str")]
        if stores != [("str", "r2,[r1]")]:
            problems.append(f"stores before the barrier: {stores}")
        for address, op, args in prologue:
            if op.split(".")[0] in order.FORBIDDEN:
                problems.append(f"0x{address:x}: {op} {args}")
            if op != "mrs" and order.HIGH_REGISTERS.search(args.split(";")[0].split("@")[0].replace("[pc", "[")):
                problems.append(f"0x{address:x}: {op} {args} uses a register above R3")
        return problems

    def test_walk_of_both_architectures(self):
        for arch in order.ARCHITECTURES:
            with self.subTest(arch=arch):
                elf = self.link(arch, self.source(arch), arch)
                self.assertEqual(self.scenario_findings(elf), [])
                self.assertEqual(self.select_order_findings(elf), [])

    def test_without_selection_the_walk_is_the_old_one(self):
        """Without NX_SAFE_OUTPUT_SELECT the handler is the IO-32 walk: the order rules of test_safe_outputs_order
        hold and the preprocessed source names nothing of the selection."""
        for arch in order.ARCHITECTURES:
            with self.subTest(arch=arch):
                elf = self.link(arch, self.source(arch), f"{arch}_plain", select=False)
                self.assertEqual(order.order_findings(elf), [])
                r = subprocess.run([order.ARM_GCC, "-E", "-P", f"-I{MODULE / 'include'}", "-x", "assembler-with-cpp",
                                    str(MODULE / "src" / arch / "hardFaultHandler.S")],
                                   capture_output=True, text=True, check=True)
                self.assertNotIn("nxSafeOutputsSelect", r.stdout)
                self.assertNotIn("nxSafeStateSelection", r.stdout)

    def test_mutants_are_detected(self):
        block = re.compile(r"#if defined\(NX_SAFE_OUTPUT_SELECT\)\n(.*?)#else\n", re.S)
        mutants = {
            "timer clock of the group not checked": ("    tst     R2, R3\n    beq     9f\n", "    tst     R2, R3\n"),
            "short values swapped": ("    ldr     R2, [R0, #NX_SAFE_SELECT_SHORT_LOW]\n",
                                     "    ldr     R2, [R0, #NX_SAFE_SELECT_SHORT_HIGH]\n"),
            "no index shift": ("    lsls    R2, R2, #2\n", ""),
            "word of group 1 always": ("    ldr     R3, [R3, R2]\n", "    ldr     R3, [R3]\n"),
            "anything but short low is short high": ("    ldr     R2, =NX_SAFE_STATE_WORD_SHORT_HIGH\n    cmp     R3, R2\n"
                                                     "    bne     9f\n", ""),
            "freewheel value never written": ("    9:\n    ldr     R2, [R0, #NX_SAFE_SELECT_VALUE]\n",
                                              "    9:\n    ldr     R2, [R0, #NX_SAFE_SELECT_SHORT_LOW]\n"),
            "old entry size": ("    adds    R0, #NX_SAFE_SELECT_SIZE\n", "    adds    R0, #NX_SAFE_OUTPUT_SIZE\n"),
            "stack in the walk": ("    6:\n    ldr     R2, [R0, #NX_SAFE_SELECT_GROUP]\n",
                                  "    6:\n    push    {R4}\n    pop     {R4}\n    ldr     R2, [R0, #NX_SAFE_SELECT_GROUP]\n"),
        }
        for arch in order.ARCHITECTURES:
            src = self.source(arch)
            original = block.search(src).group(1)
            for n, (name, (old, new)) in enumerate(mutants.items()):
                with self.subTest(arch=arch, mutant=name):
                    self.assertEqual(original.count(old), 1, f"{name}: mutation point in {arch}")
                    mutant = src.replace(original, original.replace(old, new))
                    elf = self.link(arch, mutant, f"{arch}_m{n}")
                    findings = self.scenario_findings(elf) + self.select_order_findings(elf)
                    self.assertNotEqual(findings, [], f"mutant not detected: {name}")


if __name__ == "__main__":
    unittest.main()
