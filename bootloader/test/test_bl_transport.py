"""The bootloader core is transport neutral (BL-14, doc/architecture/multi-mcu-flash.md requirement R1).

Three things are checked, none of them needs a board:

1. **The core links and runs against a transport that is not CAN.** The core sources are compiled with a
   generated bl_diag.h that selects `link`, together with bootloader/test/host/bl_transport_link_host.c, which
   brings the transport and a HAL **without a single hal_can_* function**. A core that still called into CAN
   would not link, and src/core/isotp.c compiles to nothing under that selection.
2. **The interface carries the maximum message length** (ARCH-47, multi-mcu-flash.md section 17): the test
   transport carries 64 bytes, and RequestDownload must promise the tester 64, not BL_MAX_BLOCK_LENGTH.
3. **Only the interface couples the two.** Without the transport file the same build fails, and the message
   names the bl_transport_* symbols - the core reaches for nothing else.

Plus a source check that needs no compiler: no CAN identifier and no hal_can_* call above the transport.

Run: .venv/Scripts/python -m unittest bootloader/test/test_bl_transport.py
"""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
BOOTLOADER = REPO / "bootloader"
CORE = BOOTLOADER / "src" / "core"
TRANSPORT_HOST = HERE / "host" / "bl_transport_link_host.c"
LAYOUT = "s32k116_2k"

sys.path.insert(0, str(REPO / "tools" / "canflash"))
sys.path.insert(0, str(REPO / "tools" / "bswgen"))
import blkeys  # noqa: E402
import hostbuild  # noqa: E402

import blgen  # noqa: E402

try:
    import ziglang  # noqa: F401

    HAVE_ZIG = True
except ImportError:  # pragma: no cover - the test skips without the compiler
    HAVE_ZIG = False

# Same compiler, flags and host security part as the host build of tools/canflash/hostbuild.py, so this build
# differs from the CAN one in exactly one thing: the transport.
CFLAGS = ["-O1", "-std=c99", "-Wall", "-Wextra", "-Werror", "-DBL_HOST_BUILD"]


@unittest.skipUnless(HAVE_ZIG, "ziglang not installed (host compiler)")
class LinkTransportBuildTest(unittest.TestCase):
    """The core over a second transport, built from the generated selection."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory(prefix="bl_transport_")
        cls.tmp = Path(cls._tmp.name)
        cls.gen = hostbuild.write_test_layout(LAYOUT, cls.tmp / "gen")
        # The generator's own emitter decides the transport; nothing here writes C by hand (BL-14).
        diag = cls.gen / "bootloader" / "bl_diag.h"
        text = diag.read_text(encoding="utf-8")
        assert blgen.transport_block("can") in text, "the test layout is generated without a transport block"
        diag.write_text(text.replace(blgen.transport_block("can"), blgen.transport_block("link")),
                        encoding="utf-8", newline="\n")
        cls.platform, _ = hostbuild.family_sources(REPO, hostbuild.layout_family(cls.gen))

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def build(self, out: str, sources: list[Path]):
        key = blkeys.parse_key(blkeys.TEST_KEY_FILE.read_text(encoding="utf-8"), str(blkeys.TEST_KEY_FILE))
        cmd = [sys.executable, "-m", "ziglang", "cc", *CFLAGS,
               "-DBL_HOST_TEST_MASTER_KEY=" + ",".join(f"0x{b:02X}" for b in key),
               f"-I{BOOTLOADER / 'include'}", f"-I{CORE}", f"-I{self.platform}",
               f"-I{BOOTLOADER / 'platforms' / 'common'}", f"-I{self.gen}",
               *map(str, sources), "-o", str(self.tmp / out)]
        return subprocess.run(cmd, capture_output=True, text=True, cwd=self.tmp)

    def test_core_runs_over_a_transport_that_is_not_can(self):
        result = self.build("bl_link.exe",
                            [*sorted(CORE.glob("*.c")), TRANSPORT_HOST, hostbuild.HOST_SECURITY_SOURCE])
        self.assertEqual(result.returncode, 0, result.stderr[-4000:])
        run = subprocess.run([str(self.tmp / "bl_link.exe")], capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn("OK", run.stdout)

    def test_without_the_transport_the_core_does_not_link(self):
        """Only bl_transport.h couples core and transport - and the core needs every function of it."""
        result = self.build("bl_nolink.exe", [*sorted(CORE.glob("*.c")), hostbuild.HOST_SECURITY_SOURCE])
        self.assertNotEqual(result.returncode, 0, "a core without a transport must not link")
        self.assertIn("bl_transport_", result.stderr)


class TransportNeutralSourceTest(unittest.TestCase):
    """R1 as a source rule, so a regression shows without a compiler."""

    ABOVE_THE_TRANSPORT = ("uds.c", "uds.h", "bl_main.c", "bl_main.h", "bl_transport.h", "app_image.c")

    def test_no_can_in_the_transport_independent_core(self):
        for name in self.ABOVE_THE_TRANSPORT:
            with self.subTest(file=name):
                text = (CORE / name).read_text(encoding="utf-8")
                code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith(("*", "/*")))
                self.assertNotIn("hal_can", code, f"{name} calls the CAN HAL (multi-mcu-flash.md R1)")
                self.assertNotIn("BL_CAN_ID", code, f"{name} uses a CAN identifier (multi-mcu-flash.md R1)")

    def test_the_can_transport_is_one_file(self):
        isotp = (CORE / "isotp.c").read_text(encoding="utf-8")
        self.assertIn("#if BL_TRANSPORT == BL_TRANSPORT_CAN", isotp)
        for name in ("hal_can_send", "BL_CAN_ID_RESPONSE", "BL_CAN_ID_REQUEST_PHYS", "BL_CAN_ID_REQUEST_FUNC"):
            self.assertIn(name, isotp)


if __name__ == "__main__":
    unittest.main()
