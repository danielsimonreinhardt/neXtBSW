"""The inter-MCU link protocol (BL-15, doc/architecture/multi-mcu-flash.md R9).

Two programs are compiled and run, and a few rules are checked without a compiler:

1. **The protocol against a simulated SPI bus** (``libs/bsw/mcuLink/test/host/mcuLinkBusHost.c``): both sides
   with bit errors, lost frames, a slow slave and a restart in the middle of a message - the four fault
   pictures R9 names - plus a clean run, a dead wire and the configuration limits. The simulated bus
   reproduces the one-exchange lag of a full-duplex link, so acknowledgements cannot travel earlier in the
   model than they can on a wire.

2. **The bootloader's two ends against each other** (``bootloader/src/link/test/host/bl_link_host.c``):
   ``bl_link_master.c`` as the master and ``bl_transport_link.c`` as the slave, compiled with a generated
   ``bl_diag.h`` that selects ``BL_TRANSPORT_LINK`` exactly as a slave's bootloader will be. This is what
   shows that the link really satisfies BL-14's transport interface, rather than merely resembling it.

3. **Throughput**, counted and not estimated: the transactions of a 256 KB download are counted and turned
   into a time per SPI clock. multi-mcu-flash.md section 9.4 marks its own figures as an estimate and F6 asks
   BL-15 to replace them; the numbers printed here are that replacement, for the link's own share.

Run: .venv/Scripts/python -m unittest libs/bsw/mcuLink/test/test_mcu_link_host.py
"""

import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[3]
MODULE = HERE.parent
CORE_C = MODULE / "src" / "mcuLink" / "McuLink.c"
CORE_H = MODULE / "include" / "mcuLink" / "McuLink.h"
BUS_HOST = HERE / "host" / "mcuLinkBusHost.c"

BOOTLOADER = REPO / "bootloader"
LINK_DIR = BOOTLOADER / "src" / "link"
LINK_HOST = LINK_DIR / "test" / "host" / "bl_link_host.c"
TRANSPORT_H = BOOTLOADER / "src" / "core" / "bl_transport.h"
LAYOUT = "s32k116_2k"  # the smallest supported derivative, the one the flash budget is measured against

sys.path.insert(0, str(REPO / "tools" / "canflash"))
sys.path.insert(0, str(REPO / "tools" / "bswgen"))
import hostbuild  # noqa: E402

import blgen  # noqa: E402

try:
    import ziglang  # noqa: F401

    HAVE_ZIG = True
except ImportError:  # pragma: no cover - the test skips without the compiler
    HAVE_ZIG = False

CFLAGS = ["-O1", "-std=c99", "-Wall", "-Wextra", "-Wconversion", "-Wno-sign-conversion", "-Werror"]


def compile_and_run(tmp: Path, out: str, sources: list[Path], includes: list[Path]):
    cmd = [sys.executable, "-m", "ziglang", "cc", *CFLAGS, *[f"-I{p}" for p in includes],
           *map(str, sources), "-o", str(tmp / out)]
    build = subprocess.run(cmd, capture_output=True, text=True, cwd=tmp)
    if build.returncode != 0:
        return build, None
    return build, subprocess.run([str(tmp / out)], capture_output=True, text=True)


@unittest.skipUnless(HAVE_ZIG, "ziglang not installed (host compiler)")
class SimulatedBusTest(unittest.TestCase):
    """The protocol itself, over a bus that is allowed to damage, drop and stall."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory(prefix="mculink_")
        cls.tmp = Path(cls._tmp.name)
        cls.build, cls.ran = compile_and_run(cls.tmp, "bus.exe", [CORE_C, BUS_HOST],
                                             [MODULE / "include"])

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_it_builds_warning_free(self):
        self.assertEqual(self.build.returncode, 0, self.build.stderr[-4000:])

    def test_every_scenario_passes(self):
        self.assertIsNotNone(self.ran)
        self.assertEqual(self.ran.returncode, 0, self.ran.stdout + self.ran.stderr)
        self.assertIn("OK", self.ran.stdout)
        self.assertNotIn("FAIL", self.ran.stdout)

    def test_the_faults_really_happened(self):
        """A green run over a bus that damaged nothing would prove nothing."""
        self.assertIsNotNone(self.ran)
        crc = re.search(r"bit errors  crc=(\d+)/(\d+)", self.ran.stdout)
        self.assertIsNotNone(crc, self.ran.stdout)
        self.assertGreater(int(crc.group(1)), 0, "no frame was damaged - the injection is not working")
        lost = re.search(r"lost frames lost=(\d+)", self.ran.stdout)
        self.assertGreater(int(lost.group(1)), 0, "no frame was lost - the injection is not working")
        self.assertIn("restart     detected", self.ran.stdout)

    def test_throughput_is_measured_for_the_frame_sizes(self):
        """section 9.4 / F6: the estimate of the target picture is replaced by counted transactions."""
        self.assertIsNotNone(self.ran)
        rows = re.findall(r"THROUGHPUT frame=(\d+) exchanges=(\d+) wirebytes=(\d+) payload=(\d+) "
                          r"efficiency=([\d.]+)%", self.ran.stdout)
        self.assertEqual([r[0] for r in rows], ["32", "64", "248"], self.ran.stdout)
        for frame, _exchanges, wire, payload, efficiency in rows:
            with self.subTest(frame=frame):
                self.assertEqual(int(payload), 256 * 1024)
                self.assertGreater(int(wire), int(payload))  # there is always some overhead
                self.assertGreater(float(efficiency), 55.0, "the protocol wastes more of the wire than it carries")
        # 64 is the default of bl_transport_link.c, and it has to be the best of the three: 1026 bytes divide
        # exactly into segments of 57, so nothing is wasted on padding.
        best = max(rows, key=lambda r: float(r[4]))
        self.assertEqual(best[0], "64", f"the default frame length is no longer the best choice: {rows}")

    def test_a_256k_image_stays_well_under_a_second_at_4_mhz(self):
        """The claim of section 9.4 - the link is not the bottleneck, CAN is - has to survive the measurement."""
        self.assertIsNotNone(self.ran)
        row = re.search(r"THROUGHPUT frame=64 clock=4MHz image256k=([\d.]+)s", self.ran.stdout)
        self.assertIsNotNone(row, self.ran.stdout)
        self.assertLess(float(row.group(1)), 1.0, "a 256 KB image takes over a second of link time at 4 MHz")


@unittest.skipUnless(HAVE_ZIG, "ziglang not installed (host compiler)")
class BootloaderBindingTest(unittest.TestCase):
    """bl_link_master.c and bl_transport_link.c, the real topology of section 3(a)."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory(prefix="mculink_bl_")
        cls.tmp = Path(cls._tmp.name)
        cls.gen = hostbuild.write_test_layout(LAYOUT, cls.tmp / "gen")
        # The generator's own emitter decides the transport; nothing here writes C by hand (as in BL-14).
        diag = cls.gen / "bootloader" / "bl_diag.h"
        text = diag.read_text(encoding="utf-8")
        assert blgen.transport_block("can") in text, "the test layout is generated without a transport block"
        diag.write_text(text.replace(blgen.transport_block("can"), blgen.transport_block("link")),
                        encoding="utf-8", newline="\n")
        platform, _ = hostbuild.family_sources(REPO, hostbuild.layout_family(cls.gen))
        cls.build, cls.ran = compile_and_run(
            cls.tmp, "bl_link.exe",
            [CORE_C, LINK_DIR / "bl_transport_link.c", LINK_DIR / "bl_link_master.c", LINK_HOST],
            [MODULE / "include", BOOTLOADER / "include", BOOTLOADER / "src" / "core", LINK_DIR,
             platform, BOOTLOADER / "platforms" / "common", cls.gen])

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_the_bootloader_binding_builds(self):
        self.assertEqual(self.build.returncode, 0, self.build.stderr[-4000:])

    def test_a_uds_message_crosses_the_link_between_the_two_bootloaders(self):
        self.assertIsNotNone(self.ran)
        self.assertEqual(self.ran.returncode, 0, self.ran.stdout + self.ran.stderr)
        self.assertIn("OK", self.ran.stdout)
        self.assertNotIn("FAIL", self.ran.stdout)


class SourceRuleTest(unittest.TestCase):
    """R2 and the "one core in bootloader and application" requirement, as rules that need no compiler."""

    def test_the_protocol_names_no_family_board_or_derivative(self):
        """multi-mcu-flash.md R2: nothing in the link names a family, a board or a derivative."""
        for path in (CORE_C, CORE_H):
            text = path.read_text(encoding="utf-8").lower()
            for word in ("s32k", "stm32", "rm4x", "nucleo", "evalboard", "lpspi", "freertos"):
                with self.subTest(file=path.name, word=word):
                    self.assertNotIn(word, text, f"{path.name} names {word} (multi-mcu-flash.md R2)")

    def test_the_protocol_depends_on_nothing_but_the_c_library(self):
        """Section 6.3: the same core has to build in a bootloader (no OpenBSW, no RTOS) and in an
        application. A single unexpected include would end that, so the include list is pinned."""
        includes = re.findall(r'^\s*#\s*include\s+([<"][^>"]+[>"])', CORE_C.read_text(encoding="utf-8"),
                              re.MULTILINE)
        self.assertEqual(includes, ['"mcuLink/McuLink.h"', "<string.h>"])
        header_includes = re.findall(r'^\s*#\s*include\s+([<"][^>"]+[>"])',
                                     CORE_H.read_text(encoding="utf-8"), re.MULTILINE)
        self.assertEqual(header_includes, ["<stdbool.h>", "<stdint.h>"])

    def test_the_transport_implements_every_function_of_the_interface(self):
        """BL-14's interface is the only coupling to the core; a missing function would only show at link
        time in a project, which is too late."""
        declared = set(re.findall(r"^\w[\w \*]*?\b(bl_transport_\w+)\(", TRANSPORT_H.read_text(encoding="utf-8"),
                                  re.MULTILINE))
        self.assertTrue(declared)
        implementation = (LINK_DIR / "bl_transport_link.c").read_text(encoding="utf-8")
        for name in sorted(declared):
            with self.subTest(function=name):
                self.assertRegex(implementation, rf"\b{name}\(", f"{name} is not implemented by the link")

    def test_the_link_is_only_built_where_a_project_asks_for_it(self):
        """R7: a project without slaves links nothing new. The transport is behind the generated selection,
        so a CAN bootloader compiles it to nothing."""
        text = (LINK_DIR / "bl_transport_link.c").read_text(encoding="utf-8")
        self.assertIn("#if BL_TRANSPORT == BL_TRANSPORT_LINK", text)
        self.assertIn("#endif", text)


if __name__ == "__main__":
    unittest.main()
