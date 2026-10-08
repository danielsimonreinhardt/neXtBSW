"""PLAT-77 (IO-32, finding TF5 of doc/safety/tool-classification.md, SSR-080): the safe outputs table in the linked image,
entry by entry, against an expectation that does not come from bswgen.

test_safe_outputs_order.py checks the order of the fault handlers and, for the table, only the placement, the count
and the end entry. This test reads every entry of nxSafeOutputs from a linked ELF - register address, value, gate
register, gate mask and position - and compares it with tools/bswgen/test/safe_outputs_expectation.py, which derives
the table from board.yaml, the project and the module manifests with reference manual addresses and imports neither
bswgen nor nxconfig ("detect instead of qualify": a wrong generat is found in the image, whatever the generator did).

- ReferenceFactsTest: the reference manual constants of the expectation equal the NXP device headers (S32K148 of
  OpenBSW, S32K116 RTD header of mcudb) - two independent sources for every address.
- ExpectationTest: known tables of the profiles written out by hand; since SYS-49 BswGenSafeOutputsHostTest (host
  build of the definition) uses this same expectation, the equality check per profile only guards that wiring.
- LinkedTableTest: for every profile bswgen generates the configuration, the target compiler builds the real table
  definition of the port (platforms/s32k1xx/port/main/src/os/isr/isr_wdg.cpp) against the derivative's own device
  header and links it with the real fault handler of the derivative's core (armv7m or armv6m) into a small image;
  the table is read from that image. Profiles: two own (testproject_safe_outputs: two gate drivers active high and
  active low, PWM group on FTM1; testproject_safe_outputs_k116: Cortex-M0+, FTM0 group with a non-contiguous channel
  mask, an unused group on the board, an active-low enable on port C) and the four projects of the repository (demo,
  charge, S32K148 build test and S32K116 minimal, the last two without power outputs: empty table).
  Generator mutants (monkeypatch of bswgen and edited copies of its output: PCOR/PSOR swapped, entry dropped, order
  changed, wrong gate, wrong gate mask, gate missing, wrong bit, wrong FTM register, an extra output, table not
  linked) and mutants of the image bytes must turn it red.
- ApplicationImageTest: the application ELF of a target build (--elf, NX_APP_ELF, otherwise the newest
  build/**/app.ecu.elf); its project is taken from CMakeCache.txt of the build (NX_PROJECT) or from NX_APP_PROJECT.

Run: .venv/Scripts/python platforms/s32k1xx/hardFaultHandler/test/test_safe_outputs_table.py [--elf <app.ecu.elf>]
(needs arm-none-eabi-gcc from PATH or NX_ARM_GCC_BIN for everything but the first two classes)
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
sys.path.insert(0, str(HERE))
import test_safe_outputs_order as order  # noqa: E402  (Elf reader, disassembly check, toolchain lookup)

REPO = order.REPO
sys.path.insert(0, str(REPO / "tools" / "bswgen" / "test"))
import safe_outputs_expectation as expectation  # noqa: E402

MODULE = order.MODULE
ARM_GCC = order.ARM_GCC
ELF_ARG: list[str] = []

ISR_WDG = REPO / "platforms" / "s32k1xx" / "port" / "main" / "src" / "os" / "isr" / "isr_wdg.cpp"
OPENBSW_NXP = REPO / "external" / "openbsw" / "platforms" / "s32k1xx" / "bsp" / "bspMcu" / "include" / "3rdparty" / "nxp"
RTD_NXP = REPO / "platforms" / "s32k1xx" / "bsp" / "bspMcu" / "include" / "3rdparty" / "nxp" / "rtd"
PROFILES = REPO / "tools" / "bswgen" / "test" / "profiles" / "projects"
OWN = PROFILES / "testproject_safe_outputs" / "project.yaml"
OWN_K116 = PROFILES / "testproject_safe_outputs_k116" / "project.yaml"
DEMO = REPO / "projects" / "evalboard_s32k144_demo" / "project.yaml"
CHARGE = REPO / "projects" / "evalboard_s32k144_charge" / "project.yaml"
K148 = REPO / "projects" / "buildtest_s32k148_min" / "project.yaml"
K116 = REPO / "projects" / "s32k116evb_minimal" / "project.yaml"
# profile -> number of entries (a profile must be what it claims to be, counted independently of the generat)
PROFILE_COUNTS = {OWN: 3, OWN_K116: 2, DEMO: 3, CHARGE: 2, K148: 0, K116: 0}

CORES = {"cortex-m4f": ("armv7m", ["-mcpu=cortex-m4"]), "cortex-m4": ("armv7m", ["-mcpu=cortex-m4"]),
         "cortex-m0plus": ("armv6m", ["-mcpu=cortex-m0plus"])}
ENTRY_SIZE = 16


def table_symbol(elf_path: Path) -> tuple[int, int] | None:
    """Address and size of the symbol nxSafeOutputs that the linker resolved (the strong table if there is one)."""
    out = subprocess.run([order.arm_tool("nm"), "-S", str(elf_path)], capture_output=True, text=True,
                         check=True).stdout
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 4 and parts[3] == "nxSafeOutputs":
            return int(parts[0], 16), int(parts[1], 16)
    return None


def read_table(elf_path: Path) -> tuple[list[tuple[int, int, int, int]], list[str]]:
    """Entries of nxSafeOutputs before the end entry, and problems of the table as a whole (size, end entry,
    placement)."""
    symbol = table_symbol(elf_path)
    if symbol is None:
        return [], ["no symbol nxSafeOutputs with a size"]
    address, size = symbol
    elf = order.Elf(elf_path)
    problems = []
    if size == 0 or size % ENTRY_SIZE:
        problems.append(f"nxSafeOutputs has {size} bytes, not a multiple of {ENTRY_SIZE}")
    words = struct.unpack(f"<{size // 4}I", elf.read(address, size - size % 4))
    rows = [tuple(words[i:i + 4]) for i in range(0, len(words) - len(words) % 4, 4)]
    if not rows or rows[-1] != (0, 0, 0, 0):
        problems.append(f"the last entry of nxSafeOutputs is {rows[-1] if rows else None}, not the end entry 0,0,0,0")
    entries = []
    for row in rows:
        if row[0] == 0:
            break
        entries.append(row)
    if len(entries) + 1 != len(rows):
        problems.append(f"nxSafeOutputs ends after {len(entries)} entries, but the symbol holds {len(rows)} rows")
    if elf.section_of(address)["flags"] & 1:
        problems.append(f"nxSafeOutputs lies in the writable section {elf.section_of(address)['name']}")
    return entries, problems


def image_findings(elf_path: Path, project_file: Path) -> list[str]:
    """Everything wrong with the table in the image, compared entry by entry with the independent expectation."""
    entries, problems = read_table(elf_path)
    return problems + expectation.compare(entries, expectation.expected(project_file))


def project_of(elf_path: Path) -> Path | None:
    """The project a target build was configured for (NX_APP_PROJECT, otherwise NX_PROJECT of its CMakeCache.txt)."""
    if os.environ.get("NX_APP_PROJECT"):
        return Path(os.environ["NX_APP_PROJECT"])
    for parent in elf_path.parents:
        cache = parent / "CMakeCache.txt"
        if cache.exists():
            m = re.search(r"^NX_PROJECT:[A-Z]*=(.+)$", cache.read_text(encoding="utf-8", errors="replace"), re.M)
            return Path(m.group(1).strip()) if m else None
    return None


def header_values(headers: list[Path]) -> dict[str, int]:
    """#define NAME (0x...u) / NAME 56 / NAME (0x...U) of the device headers, and the register offsets from the
    member comments of FTM_Type (SWOCTRL) and GPIO_Type (PSOR, PCOR)."""
    values = {}
    for path in headers:
        text = path.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r"^#define\s+(\w+)\s+\(?(0x[0-9A-Fa-f]+|\d+)[uU]?\)?\s*$", text, re.M):
            values[m.group(1)] = int(m.group(2), 0)
        for m in re.finditer(r"uint32_t\s+(SWOCTRL|PSOR|PCOR);\s*/\*\*<.*?offset: (0x[0-9A-Fa-f]+)", text):
            values[f"OFFSET_{m.group(1)}"] = int(m.group(2), 16)
    return values


class ReferenceFactsTest(unittest.TestCase):
    """The reference manual constants of the expectation against the NXP device headers."""

    def check_header(self, values: dict[str, int], ftms: list[str]):
        for ftm in ftms:
            self.assertEqual(expectation.RM_FTM_BASE[ftm], values[f"IP_{ftm}_BASE"], ftm)
            self.assertEqual(expectation.RM_PCC_INDEX[ftm], values[f"PCC_{ftm}_INDEX"], ftm)
        for port in "ABCDE":
            self.assertEqual(expectation.RM_GPIO_BASE[port], values[f"IP_PT{port}_BASE"], port)
            self.assertEqual(expectation.RM_PCC_INDEX[f"PORT{port}"], values[f"PCC_PORT{port}_INDEX"], port)
        self.assertEqual(expectation.RM_PCC_BASE, values["IP_PCC_BASE"])
        self.assertEqual(expectation.RM_CGC, values["PCC_PCCn_CGC_MASK"])
        self.assertEqual((expectation.RM_SWOCTRL, expectation.RM_PSOR, expectation.RM_PCOR),
                         (values["OFFSET_SWOCTRL"], values["OFFSET_PSOR"], values["OFFSET_PCOR"]))

    def test_s32k148_header(self):
        if not OPENBSW_NXP.exists():
            self.skipTest("submodule external/openbsw missing")
        self.check_header(header_values([OPENBSW_NXP / f"S32K148_{p}.h" for p in ("FTM", "GPIO", "PCC")]),
                          [f"FTM{i}" for i in range(8)])

    def test_s32k116_header(self):
        self.check_header(header_values([RTD_NXP / f"S32K116_{p}.h" for p in ("FTM", "GPIO", "PCC")]),
                          ["FTM0", "FTM1"])

    def test_every_derivative_ftm_is_known(self):
        """Every FTM instance of an s32k1xx derivative in mcudb has reference manual facts in the expectation."""
        for path in sorted((REPO / "mcudb" / "s32k1xx").glob("S32K1*.yaml")):
            if "pinmux" in path.name:
                continue
            instances = expectation.load(path).get("instances", {})
            for name in (n for n in instances if n.startswith("FTM")):
                with self.subTest(derivative=path.stem, instance=name):
                    self.assertIn(name, expectation.RM_FTM_BASE)
                    self.assertIn(name, expectation.RM_PCC_INDEX)


class ExpectationTest(unittest.TestCase):
    def test_own_profiles_written_out(self):
        rm = expectation
        pcc = rm.RM_PCC_BASE
        self.assertEqual([e.words for e in rm.expected(OWN)], [
            (0x40039094, 0x3C, pcc + 4 * 57, 1 << 30),        # BRIDGE: FTM1 SWOCTRL, channels 2..5
            (0x400FF0C8, 1 << 16, pcc + 4 * 76, 1 << 30),     # GD_A DRV_EN PTD16 active high: PTD PCOR
            (0x400FF004, 1 << 7, pcc + 4 * 73, 1 << 30)])     # GD_B DRV_EN PTA7 active low: PTA PSOR
        self.assertEqual([e.words for e in rm.expected(OWN_K116)], [
            (0x40038094, 0xCC, pcc + 4 * 56, 1 << 30),        # DUAL: FTM0 SWOCTRL, channels 2, 3, 6, 7
            (0x400FF084, 1 << 9, pcc + 4 * 75, 1 << 30)])     # GD_C DRV_EN PTC9 active low: PTC PSOR
        self.assertEqual([e.words for e in rm.expected(DEMO)][:2], [
            (0x40038094, 0x3F, pcc + 4 * 56, 1 << 30),        # MOT1: FTM0 SWOCTRL, channels 0..5
            (0x400FF088, 1 << 11, pcc + 4 * 75, 1 << 30)])    # MOT1 DRV_EN PTC11 active high: PTC PCOR

    def test_counts_of_the_profiles(self):
        for project_file, count in PROFILE_COUNTS.items():
            with self.subTest(project=project_file.parent.name):
                self.assertEqual(len(expectation.expected(project_file)), count)

    def test_equals_the_host_test_expectation(self):
        """The rules of BswGenSafeOutputsHostTest (tools/bswgen/test_bswgen.py) and of this module give the same
        table for every profile (that test still holds its own copy; see safe_outputs_expectation.py)."""
        sys.path.insert(0, str(REPO / "tools" / "bswgen"))
        import test_bswgen
        for project_file in PROFILE_COUNTS:
            with self.subTest(project=project_file.parent.name):
                self.assertEqual([e.words for e in expectation.expected(project_file)],
                                 test_bswgen.BswGenSafeOutputsHostTest.expected(project_file))

    def test_unknown_facts_are_refused(self):
        with self.assertRaises(expectation.ExpectationError):
            expectation.gate("FTM9")


@unittest.skipUnless(ARM_GCC, "arm-none-eabi-gcc not found (PATH or NX_ARM_GCC_BIN)")
class LinkedTableTest(unittest.TestCase):
    """The table as the target compiler and linker build it from the generat of each profile."""

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(REPO / "tools" / "bswgen"))
        sys.path.insert(0, str(REPO / "tools"))
        import bswgen
        cls.bswgen = bswgen
        cls.tmp = Path(tempfile.mkdtemp(prefix="plat77_"))
        cls.generated: dict[Path, Path] = {}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def generate(self, project_file: Path, name: str | None = None) -> Path:
        if name is None and project_file in self.generated:
            return self.generated[project_file]
        gen = self.tmp / (name or f"gen_{project_file.parent.name}")
        self.bswgen.generate(project_file, gen)
        if name is None:
            self.generated[project_file] = gen
        return gen

    def link(self, project_file: Path, gen: Path, name: str, isr_wdg: Path | None = ISR_WDG) -> Path:
        """isr_wdg.cpp (the real strong table of the port) and the fault handler of the derivative's core, linked
        with the target toolchain into a small image; isr_wdg None links the handler alone (weak empty table)."""
        arch, cpu = CORES[expectation.core_of(project_file)]
        out = self.tmp / f"image_{name}"
        out.mkdir()
        stub = out / "stub.c"
        stub.write_text("void HardFault_Handler_Final(void) { for (;;) {} }\n", encoding="utf-8")
        includes = [gen, REPO / "platforms" / "s32k1xx" / "bsp" / "bspMcu" / "include",
                    REPO / "external" / "openbsw" / "platforms" / "s32k1xx" / "bsp" / "bspMcu" / "include",
                    REPO / "external" / "openbsw" / "libs" / "3rdparty" / "cmsis",
                    REPO / "platforms" / "common" / "3rdparty" / "cmsis", MODULE / "include"]
        objects = []
        if isr_wdg is not None:
            obj = out / "isr_wdg.o"
            cmd = [str(Path(ARM_GCC).with_name(Path(ARM_GCC).name.replace("gcc", "g++"))), *cpu, "-mthumb",
                   "-std=gnu++17", "-O2", "-Wall", "-Wextra", "-Werror", "-fno-exceptions", "-fno-rtti",
                   *[f"-I{i}" for i in includes], "-c", str(isr_wdg), "-o", str(obj)]
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode != 0:
                raise CompileError(r.stderr[-3000:])
            objects.append(str(obj))
        elf = out / "table.elf"
        cmd = [ARM_GCC, *cpu, "-mthumb", "-O1", "-nostdlib", "-nostartfiles", f"-I{MODULE / 'include'}",
               str(MODULE / "src" / arch / "hardFaultHandler.S"), str(stub), *objects, "-o", str(elf),
               "-Wl,-e,customHardFaultHandler", "-Wl,--defsym=__hard_fault_dump_start=0x20000000",
               "-Wl,--defsym=__hard_fault_ram_start=0x1FFF8000", "-Wl,--defsym=__hard_fault_ram_end=0x20007000",
               "-Wl,-Ttext=0x1000"]
        r = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr[-3000:])
        return elf

    def test_every_profile(self):
        for index, (project_file, count) in enumerate(PROFILE_COUNTS.items()):
            with self.subTest(project=project_file.parent.name):
                elf = self.link(project_file, self.generate(project_file), f"profile{index}")
                entries, _ = read_table(elf)
                print(f"\n{project_file.parent.name} ({expectation.core_of(project_file)}): "
                      + (", ".join(f"[0x{a:08X}] = 0x{v:X} if [0x{g:08X}]" for a, v, g, _ in entries) or "empty"),
                      file=sys.stderr)
                self.assertEqual(len(entries), count)
                self.assertEqual(image_findings(elf, project_file), [])
                self.assertEqual(order.order_findings(elf), [])

    # --- generator mutants: bswgen itself patched (monkeypatch of Board.collect_safe_outputs) ---

    def mutated_generat(self, project_file: Path, name: str, mutate) -> Path:
        original = self.bswgen.Board.collect_safe_outputs

        def patched(board):
            original(board)
            mutate(board.safe_outputs)

        self.bswgen.Board.collect_safe_outputs = patched
        try:
            return self.generate(project_file, name)
        finally:
            self.bswgen.Board.collect_safe_outputs = original

    def generator_mutants(self) -> dict:
        def swap_pcor_psor(entries):
            for e in entries:
                e["address"] = e["address"].replace("PSOR", "@").replace("PCOR", "PSOR").replace("@", "PCOR")

        def drop_last(entries):
            entries.pop()

        def reverse(entries):
            entries.reverse()

        def wrong_gate(entries):
            entries[-1]["gate"] = "PORTE" if entries[-1]["gate"] != "PORTE" else "PORTA"

        def wrong_bit(entries):
            entries[-1]["value"] = entries[-1]["value"].replace("(1UL << ", "(2UL << ")

        def wrong_ftm_register(entries):
            entries[0]["address"] = entries[0]["address"].replace("SWOCTRL", "OUTMASK")

        def wrong_channel_mask(entries):
            entries[0]["value"] = "0xFFU"

        def extra_output(entries):
            entries.append(dict(entries[-1], name="EXTRA", what="an output without safe_off",
                                address="IP_PTD_BASE + offsetof(GPIO_Type, PCOR)", value="(1UL << 15U)",
                                gate="PORTD"))

        return {"PCOR and PSOR swapped": swap_pcor_psor, "last entry dropped": drop_last,
                "order reversed": reverse, "gate of another port": wrong_gate, "wrong bit": wrong_bit,
                "OUTMASK instead of SWOCTRL": wrong_ftm_register, "wrong channel mask": wrong_channel_mask,
                "an extra output": extra_output}

    def test_generator_mutants_are_detected(self):
        index = 0
        for project_file in (OWN, OWN_K116):
            for what, mutate in self.generator_mutants().items():
                index += 1
                with self.subTest(project=project_file.parent.name, mutant=what):
                    gen = self.mutated_generat(project_file, f"mutant_gen{index}", mutate)
                    findings = image_findings(self.link(project_file, gen, f"mutant{index}"), project_file)
                    print(f"\n{project_file.parent.name}, {what}: {findings[0] if findings else 'NOT DETECTED'}",
                          file=sys.stderr)
                    self.assertNotEqual(findings, [], f"mutant not detected: {what}")

    # --- mutants of the generated file (an edited copy of the bswgen output) ---

    def test_generat_mutants_are_detected(self):
        clean = self.generate(OWN)
        header = clean / "bsp" / "safeOutputs" / "safeOutputsConfiguration.h"
        text = header.read_text(encoding="utf-8")
        gate = "IP_PCC_BASE + offsetof(PCC_Type, PCCn) + 4UL * PCC_PORTA_INDEX, PCC_PCCn_CGC_MASK)"
        # SYS-30: the read-back table NX_SAFE_OUTPUT_READBACKS repeats the clock gate of every entry; the mutants
        # edit the gate of the safe output table NX_SAFE_OUTPUTS only (the one the fault handler works through)
        start = text.index("#define NX_SAFE_OUTPUTS(X)")
        end = text.find("#define NX_SAFE_OUTPUT_READBACKS(X)", start)
        end = len(text) if end < 0 else end
        table = text[start:end]
        self.assertEqual(table.count(gate), 1)

        def in_table(new: str) -> str:
            return text[:start] + table.replace(gate, new) + text[end:]

        mutants = {
            "gate mask PR instead of CGC": in_table(gate.replace("CGC_MASK", "PR_MASK")),
            "gate of the FTM for a port": in_table(gate.replace("PORTA", "FTM1")),
        }
        for index, (what, mutant) in enumerate(mutants.items()):
            with self.subTest(mutant=what):
                self.assertNotEqual(mutant, text)
                gen = self.tmp / f"generat_mutant{index}"
                shutil.copytree(clean, gen)
                (gen / header.relative_to(clean)).write_text(mutant, encoding="utf-8")
                findings = image_findings(self.link(OWN, gen, f"generat_mutant{index}"), OWN)
                print(f"\n{what}: {findings[0] if findings else 'NOT DETECTED'}", file=sys.stderr)
                self.assertNotEqual(findings, [])
        with self.subTest(mutant="gate missing"):
            # a gate of 0 would make the handler write to a gated peripheral: the port refuses it at compile time
            gen = self.tmp / "generat_mutant_nogate"
            shutil.copytree(clean, gen)
            (gen / header.relative_to(clean)).write_text(
                in_table("0UL, PCC_PCCn_CGC_MASK)"), encoding="utf-8")
            with self.assertRaises(CompileError) as ctx:
                self.link(OWN, gen, "generat_mutant_nogate")
            self.assertIn("every entry needs an aligned register, a value and a clock gate", str(ctx.exception))

    def test_table_not_linked_is_detected(self):
        """Without the strong table of the port the image holds the weak empty default of the handler library."""
        elf = self.link(OWN, self.generate(OWN), "weak_only", isr_wdg=None)
        findings = image_findings(elf, OWN)
        self.assertEqual(findings[:3], [f"entry {i}: missing, expected {e}"
                                         for i, e in enumerate(expectation.expected(OWN))])

    # --- mutants of the image bytes: the reader sees every field of every entry ---

    def test_image_byte_mutants_are_detected(self):
        elf = self.link(OWN, self.generate(OWN), "bytes")
        address, size = table_symbol(elf)
        section = order.Elf(elf).section_of(address)
        base = section["offset"] + address - section["addr"]
        data = elf.read_bytes()
        count = len(expectation.expected(OWN))
        for offset in range(0, size, 4):
            entry, field = divmod(offset, ENTRY_SIZE)
            with self.subTest(entry=entry, field=field // 4):
                mutant = bytearray(data)
                word, = struct.unpack_from("<I", data, base + offset)
                struct.pack_into("<I", mutant, base + offset, word ^ (1 << 30 if entry == count else 1 << 2))
                path = elf.with_name(f"mutant_{offset}.elf")
                path.write_bytes(bytes(mutant))
                self.assertNotEqual(image_findings(path, OWN), [], f"entry {entry} word {field // 4}")


class CompileError(Exception):
    pass


def application_elf() -> Path | None:
    for candidate in [*ELF_ARG, os.environ.get("NX_APP_ELF")]:
        if candidate:
            return Path(candidate)
    found = sorted((REPO / "build").glob("**/app.ecu.elf"), key=lambda p: p.stat().st_mtime, reverse=True)
    return found[0] if found else None


@unittest.skipUnless(ARM_GCC, "arm-none-eabi-gcc not found (PATH or NX_ARM_GCC_BIN)")
class ApplicationImageTest(unittest.TestCase):
    def test_table_of_the_application_image(self):
        elf = application_elf()
        if elf is None or not elf.exists():
            self.skipTest("no application ELF (--elf, NX_APP_ELF or build/**/app.ecu.elf)")
        project_file = project_of(elf)
        self.assertIsNotNone(project_file, f"{elf}: no NX_PROJECT in the CMakeCache.txt of the build "
                                           "(or set NX_APP_PROJECT)")
        entries, _ = read_table(elf)
        print(f"\nELF {elf}\nproject {project_file}\n"
              + "\n".join(f"  {e}" for e in expectation.expected(project_file)), file=sys.stderr)
        self.assertEqual(len(entries), len(expectation.expected(project_file)))
        self.assertEqual(image_findings(elf, project_file), [])


if __name__ == "__main__":
    if "--elf" in sys.argv:
        i = sys.argv.index("--elf")
        ELF_ARG.append(sys.argv[i + 1])
        del sys.argv[i:i + 2]
    unittest.main()
