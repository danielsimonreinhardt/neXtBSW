"""Host test of bsp/capture/InputCapture.h against the real generat of a charge project (IO-48): the own test
profile tools/nxconfig/test/profiles/projects/testproject_full_charge (PLAT-62; before: the private eval board
project evalboard_s32k144_charge, same channel PILOT_DUTY on FTM0 CH6/7).

Generates bsp/capture/inputCaptureConfiguration.h and bsp/io/io/ioConfiguration.h with tools/bswgen (unmodified:
this test only calls the generator, like tools/bswgen/test_bswgen.py does for its own host tests) and compiles
the real InputCapture.cpp against them with input_capture_host_test.cpp (stubs/io/Io.h records the pins muxed).

Not yet wired into tools/test/tests.yaml (outside the paths IO-48 may change) - the coordinator adds an entry
like:
  - name: input-capture-device
    group: host
    kind: zig
    flags: [-O1, -Wall, -Wextra, -Werror, -Wconversion, -Wno-deprecated-volatile]
    include: [<this dir>/stubs, <the generated dir>, platforms/s32k1xx/port/bspConfiguration/include]
    system_include: [external/openbsw/platforms/s32k1xx/bsp/bspFtm/include,
                      external/openbsw/platforms/s32k1xx/bsp/bspMcu/include/3rdparty/nxp,
                      external/openbsw/libs/bsw/bsp/include]
    sources: [platforms/s32k1xx/port/bspConfiguration/src/bsp/capture/InputCapture.cpp, .../input_capture_host_test.cpp]

Run directly: .venv/Scripts/python platforms/s32k1xx/port/bspConfiguration/src/bsp/capture/test/host/test_input_capture_host.py
"""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
# .../platforms/s32k1xx/port/bspConfiguration/src/bsp/capture/test/host -> repo root (8 levels up)
REPO = HERE.parents[8]
BSWGEN_DIR = REPO / "tools" / "bswgen"
OPENBSW = REPO / "external" / "openbsw"
PROJECT_FILE = REPO / "tools" / "nxconfig" / "test" / "profiles" / "projects" / "testproject_full_charge" / "project.yaml"

sys.path.insert(0, str(BSWGEN_DIR))
import bswgen  # noqa: E402


class InputCaptureHostTest(unittest.TestCase):
    """IO-48: without the fix, init() never calls Io::setDefaultConfiguration and pinCalls stays empty - the
    same fault the board found on 2026-09-28 (PORTE_PCR8 = 0 although FTM0 was running)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        bswgen.generate(PROJECT_FILE, self.tmp / "gen")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def compile(self):
        includes = [
            HERE / "stubs",
            self.tmp / "gen",
            REPO / "platforms/s32k1xx/port/bspConfiguration/include",
        ]
        system = [
            OPENBSW / "platforms/s32k1xx/bsp/bspFtm/include",
            OPENBSW / "platforms/s32k1xx/bsp/bspMcu/include/3rdparty/nxp",
            OPENBSW / "libs/bsw/bsp/include",
            OPENBSW / "libs/3rdparty/etl/include",
        ]
        sources = [
            REPO / "platforms/s32k1xx/port/bspConfiguration/src/bsp/capture/InputCapture.cpp",
            HERE / "input_capture_host_test.cpp",
        ]
        exe = self.tmp / "input_capture_host_test.exe"
        cmd = [sys.executable, "-m", "ziglang", "c++", "-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror",
               "-Wconversion", "-Wno-deprecated-volatile", *[f"-I{i}" for i in includes],
               *[f"-isystem{i}" for i in system], *[str(s) for s in sources], "-o", str(exe)]
        return subprocess.run(cmd, capture_output=True, text=True), exe

    def test_driver_muxes_the_pin(self):
        r, exe = self.compile()
        self.assertEqual(r.returncode, 0, r.stderr[-3000:])
        r = subprocess.run([str(exe)], capture_output=True, text=True)
        print("\n" + r.stdout.strip(), file=sys.stderr)
        self.assertEqual(r.returncode, 0, r.stdout[-3000:])


if __name__ == "__main__":
    unittest.main()
