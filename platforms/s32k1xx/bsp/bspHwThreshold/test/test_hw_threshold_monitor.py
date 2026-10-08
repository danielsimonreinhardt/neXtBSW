"""SYS-128/SYS-130: host test of the detection and the report of the hardware threshold path (family s32k1xx).

Compiles platforms/s32k1xx/bsp/bspHwThreshold/test/host/HwThresholdMonitorHostTest.cpp with zig c++ against the
generat of the own test profile tools/bswgen/test/profiles/projects/testproject_hw_threshold (CMP0_IN3 -> FTM3 fault
input 0, 40 A) and runs it: the start test of the comparator output, the register check, the consistency of the
output with the timer's fault latch, the findings latch, the real PWM driver ComplementaryPwm.h (start test in init,
group forced) and the fast check of the power stage poll integration/HwThresholdWatch.h - with fault injection.
The mutants are in test_hw_threshold_monitor_mutations.py (slow).

    .venv/Scripts/python -m unittest platforms/s32k1xx/bsp/bspHwThreshold/test/test_hw_threshold_monitor.py
"""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[4]
sys.path.insert(0, str(REPO / "tools" / "bswgen"))
sys.path.insert(0, str(REPO / "tools"))

import bswgen  # noqa: E402

PROFILE = REPO / "tools/bswgen/test/profiles/projects/testproject_hw_threshold/project.yaml"
MODULE = REPO / "platforms/s32k1xx/bsp/bspHwThreshold"
HOST = MODULE / "test" / "host"
TEST = HOST / "HwThresholdMonitorHostTest.cpp"
PORT_INCLUDE = REPO / "platforms/s32k1xx/port/bspConfiguration/include"
INTEGRATION = REPO / "platforms/s32k1xx/port/main/src/integration"
PWM_STUBS = REPO / "tools/bswgen/test/pwm_host/stubs"
PWM_GROUP = REPO / "platforms/s32k1xx/bsp/bspFtmPwmGroup/include"
OPENBSW = REPO / "external" / "openbsw"
FLAGS = ["-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror", "-Wconversion", "-Wno-deprecated-volatile"]
RESULT = r"hardware threshold monitor host test: \d+ checks, 0 failures"


def zig_available() -> bool:
    try:
        import ziglang  # noqa: F401
        return True
    except ImportError:
        return False


def generate(target: Path) -> Path:
    """The generat of the test profile in `target`."""
    bswgen.generate(PROFILE, target)
    return target


def build_and_run(gen: Path, tmp: Path, name: str, family_include: Path = MODULE / "include",
                  port_include: Path = PORT_INCLUDE, integration: Path = INTEGRATION) -> tuple[str, str]:
    """('compile' | 'fail' | 'ok', output) of the host test built with the given (possibly mutated) sources."""
    includes = [HOST / "stubs", PWM_STUBS, gen, port_include, family_include, PWM_GROUP, integration]
    system = [OPENBSW / "platforms/s32k1xx/bsp/bspFtm/include",
              OPENBSW / "platforms/s32k1xx/bsp/bspMcu/include/3rdparty/nxp",
              OPENBSW / "libs/bsw/bsp/include"]
    exe = tmp / f"{name}.exe"
    r = subprocess.run([sys.executable, "-m", "ziglang", "c++", *FLAGS, *[f"-I{i}" for i in includes],
                        *[f"-isystem{i}" for i in system], str(TEST), "-o", str(exe)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return "compile", r.stderr
    r = subprocess.run([str(exe)], capture_output=True, text=True, timeout=120)
    return ("ok" if r.returncode == 0 else "fail"), r.stdout


@unittest.skipUnless(zig_available(), "ziglang missing (requires: ziglang)")
@unittest.skipUnless((OPENBSW / "platforms").is_dir(), "external/openbsw missing (git submodule update)")
class HostRuntimeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.gen = generate(cls.tmp / "gen")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_host_runtime(self):
        verdict, output = build_and_run(self.gen, self.tmp, "monitor")
        print("\n" + output.strip()[-2000:], file=sys.stderr)
        self.assertEqual(verdict, "ok", output[-3000:])
        self.assertRegex(output, RESULT)


if __name__ == "__main__":
    unittest.main()
