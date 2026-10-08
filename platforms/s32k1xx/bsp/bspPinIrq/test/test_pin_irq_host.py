"""Host test of the pin-interrupt dispatcher of the family s32k1xx (IO-52), compiled with zig c++ and run on the PC.

Run: .venv/Scripts/python -m unittest platforms/s32k1xx/bsp/bspPinIrq/test/test_pin_irq_host.py
     (tools/test -k pin-irq)

- host/PinIrqDispatcherHostTest.cpp: dispatcher against a PORT register model (write-1-to-clear flags, edge
  detection per IRQC) and an NVIC model, with a table of the S32K14x shape (vector per port, several pins per port,
  two consumers, one channel on two ports) and one of the S32K11x shape (one vector for all ports); the
  compile-time table checks as static_assert.
- Mutants: faults built into src/pinIrq/PinIrqDispatcher.cpp and include/pinIrq/PinIrqTable.h, each of which the
  host test has to notice (fail or not compile).
- IO-54: setEdge() of the dispatcher (edge chosen at run time) in the same test, and host/PinEventAdapterHostTest.cpp:
  the pin-event adapter (src/pinIrq/PinEventAdapter.cpp) with the real dispatcher and the real module
  libs/bsp/bspPinEvent - start-up order, inversion of the board, level sampled after the flag, rate limit switching
  the detector off from the interrupt and cycle() on again; its own mutants.
The Hall path through the dispatcher is tested in tools/bswgen/test_bswgen.py (BswGenHallHostTest).
"""

import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
MODULE = HERE.parent
REPO = HERE.parents[4]
OPENBSW = REPO / "external" / "openbsw"
VENDOR = OPENBSW / "platforms/s32k1xx/bsp/bspMcu/include/3rdparty/nxp"
TEST = HERE / "host" / "PinIrqDispatcherHostTest.cpp"
SOURCE = MODULE / "src" / "pinIrq" / "PinIrqDispatcher.cpp"
TABLE = MODULE / "include" / "pinIrq" / "PinIrqTable.h"
# IO-54: family end of the pin events and its host test
ADAPTER = MODULE / "src" / "pinIrq" / "PinEventAdapter.cpp"
ADAPTER_HEADER = MODULE / "include" / "pinIrq" / "PinEventAdapter.h"
ADAPTER_TEST = HERE / "host" / "PinEventAdapterHostTest.cpp"
PINEVENT = REPO / "libs" / "bsp" / "bspPinEvent"
FLAGS = ["-std=c++17", "-O1", "-Wall", "-Wextra", "-Wconversion", "-Wno-sign-conversion", "-Werror",
         "-Wno-deprecated-volatile"]

# (what, file, old, new): every fault must make the host test fail
MUTANTS = [
    ("flags of pins without consumer cleared", SOURCE, "regs.ISFR = flags;", "regs.ISFR = 0xFFFFFFFFU;"),
    ("own flags not cleared in the interrupt", SOURCE, "regs.ISFR = flags;", "(void)flags;"),
    ("time read for every port", SOURCE, "if (!stamped)", "if (!stamped || (flags != 0U))"),
    ("only one flag per interrupt", SOURCE, "flags &= flags - 1U;", "flags = 0U;"),
    ("shared vector serves only its first port", SOURCE,
     "for (uint8_t p = 0U; p < PORT_REG_COUNT; ++p) // bounded", "for (uint8_t p = 0U; p < 1U; ++p) // bounded"),
    ("vector serves ports of other vectors", SOURCE,
     "if ((ports & static_cast<uint8_t>(1U << p)) == 0U)", "if ((ports & static_cast<uint8_t>(1U << p)) == 0xFFU)"),
    ("pin mapped to the wrong use", SOURCE, "uint8_t const slot = map.slot[pin];",
     "uint8_t const slot = static_cast<uint8_t>(pin % _config.pinCount);"),
    ("channel of the use ignored", SOURCE, "use.consumer->onEdge(use.channel, now);",
     "use.consumer->onEdge(0U, now);"),
    ("stale flags not cleared at init", SOURCE, "PORT_REGS[p]->ISFR = mask;", "(void)mask;"),
    ("configured edge ignored at init", SOURCE,
     "writeIrqc(*PORT_REGS[use.port], use.pin, static_cast<uint32_t>(use.edge));",
     "writeIrqc(*PORT_REGS[use.port], use.pin, 11U);"),
    ("flag bit of the PCR written back", SOURCE, "~static_cast<uint32_t>(PORT_PCR_IRQC_MASK | PORT_PCR_ISF_MASK)",
     "~static_cast<uint32_t>(PORT_PCR_IRQC_MASK)"),
    ("vectors not switched off during init", SOURCE,
     "NVIC_DisableIRQ(_config.vectors[v].irq); // no interrupt", "(void)v; // no interrupt"),
    ("NVIC level of the rank ignored", SOURCE, "NVIC_SetPriority(vec.irq, vec.level);",
     "NVIC_SetPriority(vec.irq, 0U);"),
    ("disarm keeps the pending flag", SOURCE,
     "writeIrqc(regs, use.pin, 0U);\n            regs.ISFR = (1UL << use.pin);", "writeIrqc(regs, use.pin, 0U);"),
    ("re-arm reports the edge of the disarmed time", SOURCE,
     "regs.ISFR = (1UL << use.pin);\n            writeIrqc(regs, use.pin, static_cast<uint32_t>(use.edge));",
     "writeIrqc(regs, use.pin, static_cast<uint32_t>(use.edge));"),
    ("arming ignores the channel", SOURCE, "(use.channel != channel)", "(channel > 0xFFF0U)"),
    ("arming ignores the consumer", SOURCE, "(use.consumer != &consumer)",
     "(static_cast<void const*>(&consumer) == static_cast<void const*>(&use))"),
    ("arming stops at the first pin of a channel", SOURCE, "found           = true;",
     "if (found) { break; }\n        found = true;"),
    ("pin twice accepted", TABLE, "return TableError::PIN_TWICE;", "(void)k;"),
    ("port without vector accepted", TABLE, "return TableError::PORT_WITHOUT_VECTOR;", ";"),
    ("level interrupt accepted", TABLE, "&& (p.edge != Edge::BOTH)", ""),
    # IO-54: setEdge() (an edge chosen at run time)
    ("new edge keeps the flag of the old detection", SOURCE,
     "regs.ISFR = (1UL << use.pin);\n            writeIrqc(regs, use.pin, irqc);", "writeIrqc(regs, use.pin, irqc);"),
    ("armed edge again drops a real edge", SOURCE, "else if (now != irqc)", "else if (now != irqc + 99U)"),
    ("switching off keeps the flag", SOURCE,
     "writeIrqc(regs, use.pin, 0U);\n            regs.ISFR = (1UL << use.pin);\n        }\n        else if",
     "writeIrqc(regs, use.pin, 0U);\n        }\n        else if"),
    ("setEdge ignores the channel", SOURCE, "(use.channel != channel) || !portValid(use.port))\n        {\n"
     "            continue;\n        }\n        found           = true;\n        PORT_Type& regs = *PORT_REGS[use.port];\n"
     "        uint32_t const now",
     "(channel > 0xFFF0U) || !portValid(use.port))\n        {\n"
     "            continue;\n        }\n        found           = true;\n        PORT_Type& regs = *PORT_REGS[use.port];\n"
     "        uint32_t const now"),
]

# IO-54: faults of the pin-event adapter (src/pinIrq/PinEventAdapter.cpp, include/pinIrq/PinEventAdapter.h) that
# PinEventAdapterHostTest.cpp has to notice
ADAPTER_MUTANTS = [
    ("inversion of the board ignored", ADAPTER, "return high != p.activeLow;", "return high;"),
    ("not switched off when the rate limit trips", ADAPTER,
     "(void)_dispatcher.setEdge(*this, channel, pinirq::Edge::NONE);", "(void)channel;"),
    ("level of the other input sampled", ADAPTER, "pinirq::EventPin const& p = _pins[input];",
     "pinirq::EventPin const& p = _pins[(input + 1U) % _count];"),
    ("armed without the inversion", ADAPTER, "pinirq::physicalEdge(edges, _pins[input].activeLow)",
     "pinirq::physicalEdge(edges, false)"),
    ("arm ignored", ADAPTER, "if (input < _count)\n    {\n        (void)_dispatcher.setEdge",
     "if (input > 250U)\n    {\n        (void)_dispatcher.setEdge"),
    ("level passed before the edge (inverted)", ADAPTER, "_events.onEdge(input, timestampUs, level(input))",
     "_events.onEdge(input, timestampUs, !level(input))"),
    ("activate and deactivate swapped", ADAPTER_HEADER, "(activeLow ? Edge::FALLING : Edge::RISING)",
     "(activeLow ? Edge::RISING : Edge::FALLING)"),
    ("both requested, one edge armed", ADAPTER_HEADER, "(edges == ::pinevent::Detect::BOTH)     ? Edge::BOTH",
     "(edges == ::pinevent::Detect::BOTH)     ? Edge::RISING"),
]


def compile_test(tmp: Path, name: str, source: Path = SOURCE, include_override: Path | None = None):
    exe = tmp / f"{name}.exe"
    includes = ([include_override] if include_override else []) + [
        HERE / "host" / "stubs", MODULE / "include", REPO / "libs/bsp/bspPinEdge/include"]
    cmd = [sys.executable, "-m", "ziglang", "c++", *FLAGS, *[f"-I{i}" for i in includes], f"-isystem{VENDOR}",
           str(TEST), str(source), "-o", str(exe)]
    return subprocess.run(cmd, capture_output=True, text=True), exe


def compile_adapter_test(tmp: Path, name: str, adapter: Path = ADAPTER, include_override: Path | None = None,
                         dispatcher: Path = SOURCE):
    """IO-54: adapter + dispatcher + the real pin-event module (libs/bsp/bspPinEvent)."""
    exe = tmp / f"{name}.exe"
    includes = ([include_override] if include_override else []) + [
        HERE / "host" / "stubs", MODULE / "include", REPO / "libs/bsp/bspPinEdge/include", PINEVENT / "include"]
    cmd = [sys.executable, "-m", "ziglang", "c++", *FLAGS, *[f"-I{i}" for i in includes], f"-isystem{VENDOR}",
           str(ADAPTER_TEST), str(adapter), str(dispatcher), str(PINEVENT / "src/pinEvent/PinEvent.cpp"),
           "-o", str(exe)]
    return subprocess.run(cmd, capture_output=True, text=True), exe


@unittest.skipUnless(VENDOR.is_dir(), "external/openbsw missing (git submodule update --init)")
class PinIrqDispatcherHostTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_dispatcher(self):
        r, exe = compile_test(self.tmp, "pin_irq")
        self.assertEqual(r.returncode, 0, r.stderr[-4000:])
        r = subprocess.run([str(exe)], capture_output=True, text=True)
        print("\n" + r.stdout.strip(), file=sys.stderr)
        self.assertEqual(r.returncode, 0, r.stdout[-4000:])
        self.assertRegex(r.stdout, r"\d+ checks, 0 failures")

    def test_dispatcher_is_bounded_and_family_only_in_its_place(self):
        """No loop over all pins in the interrupt, no RTOS, no heap; the contract names no family."""
        text = SOURCE.read_text(encoding="utf-8")
        isr = text[text.index("void PinIrqDispatcher::isr"):text.index("bool PinIrqDispatcher::setArmed")]
        self.assertNotIn("PINS_PER_PORT", isr)
        self.assertNotIn("< 32", isr)
        code = re.sub(r"//[^\n]*", "", text)        # comments may say anything
        self.assertIsNone(re.search(r"\bnew\b|\bmalloc\b|\bxTask|\bxQueue|\bvTask", code))
        contract = (REPO / "libs/bsp/bspPinEdge/include/pinEdge/IPinEdgeConsumer.h").read_text(encoding="utf-8")
        for word in ("PORT", "IRQn", "mcu", "s32k", "NVIC"):
            self.assertNotIn(word, contract)

    def test_mutants_are_detected(self):
        survivors = []
        for index, (what, path, old, new) in enumerate(MUTANTS):
            with self.subTest(mutant=what):
                text = path.read_text(encoding="utf-8")
                self.assertIn(old, text, f"mutant '{what}' does not apply any more")
                mutated = text.replace(old, new, 1)
                if path == SOURCE:
                    patched = self.tmp / f"mutant{index}.cpp"
                    patched.write_text(mutated, encoding="utf-8")
                    r, exe = compile_test(self.tmp, f"mutant{index}", source=patched)
                    # a fault of the dispatcher must be found by running the test, not by a warning of the compiler
                    self.assertEqual(r.returncode, 0, f"mutant '{what}' does not compile:\n{r.stderr[-1500:]}")
                else:
                    include = self.tmp / f"mutant{index}"
                    (include / "pinIrq").mkdir(parents=True)
                    (include / "pinIrq" / path.name).write_text(mutated, encoding="utf-8")
                    # the dispatcher header includes the table with quotes, which would find the original next to
                    # it first: put a copy of the dispatcher header next to the mutant as well
                    shutil.copy(MODULE / "include/pinIrq/PinIrqDispatcher.h", include / "pinIrq")
                    r, exe = compile_test(self.tmp, f"mutant{index}", include_override=include)
                if r.returncode != 0:
                    continue        # does not even compile: found
                if subprocess.run([str(exe)], capture_output=True, text=True).returncode == 0:
                    survivors.append(what)
        self.assertEqual(survivors, [], f"the dispatcher host test did not notice these faults: {survivors}")

    def test_pin_event_adapter(self):
        """IO-54: adapter, dispatcher and the real pin-event module end to end against the register models."""
        r, exe = compile_adapter_test(self.tmp, "pin_event_adapter")
        self.assertEqual(r.returncode, 0, r.stderr[-4000:])
        r = subprocess.run([str(exe)], capture_output=True, text=True)
        print("\n" + r.stdout.strip(), file=sys.stderr)
        self.assertEqual(r.returncode, 0, r.stdout[-4000:])
        self.assertRegex(r.stdout, r"pin_event_adapter_host_test: \d+ checks, 0 failures")

    def test_adapter_mutants_are_detected(self):
        survivors = []
        for index, (what, path, old, new) in enumerate(ADAPTER_MUTANTS):
            with self.subTest(mutant=what):
                text = path.read_text(encoding="utf-8")
                self.assertIn(old, text, f"mutant '{what}' does not apply any more")
                mutated = text.replace(old, new, 1)
                if path == ADAPTER:
                    patched = self.tmp / f"adapter_mutant{index}.cpp"
                    patched.write_text(mutated, encoding="utf-8")
                    r, exe = compile_adapter_test(self.tmp, f"adapter_mutant{index}", adapter=patched)
                    # a fault of the adapter must be found by running the test, not by a warning of the compiler
                    self.assertEqual(r.returncode, 0, f"mutant '{what}' does not compile:\n{r.stderr[-1500:]}")
                else:
                    include = self.tmp / f"adapter_mutant{index}"
                    (include / "pinIrq").mkdir(parents=True)
                    (include / "pinIrq" / path.name).write_text(mutated, encoding="utf-8")
                    r, exe = compile_adapter_test(self.tmp, f"adapter_mutant{index}", include_override=include)
                    if r.returncode != 0:
                        # the edge mapping is constexpr: the static_asserts of the test find it at compile time
                        self.assertIn("static assertion failed", r.stderr, f"mutant '{what}':\n{r.stderr[-1500:]}")
                        continue
                if subprocess.run([str(exe)], capture_output=True, text=True).returncode == 0:
                    survivors.append(what)
        self.assertEqual(survivors, [], f"the adapter host test did not notice these faults: {survivors}")


if __name__ == "__main__":
    unittest.main()
