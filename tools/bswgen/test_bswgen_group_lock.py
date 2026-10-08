"""SYS-123: the group lock of the PWM driver - generat and host test with mutants.

- The generat: tools/bswgen writes NX_PWM_GROUP_LOCK (and the declaration of ftmPwm::groupLocked()) into
  bsp/io/outputPwm/ftmPwmConfiguration.h only for a project with a degraded mapping (safety.degraded, tools/nxconfig
  derives the groups of the safe supervisor); the demo and every other project get the header byte for byte as before.
- The host test test/pwm_host/complementary_pwm_lock_host_test.cpp compiled against the generat of a copy of the demo
  with a mapping: ComplementaryPwm.h forces a locked group in init() before the pins are muxed and at every duty
  write, the guard's FtmPwmInhibit does not release it, the fast path does not touch it.
- Mutants of ComplementaryPwm.h and FtmPwmInhibit.h must fail it.

Run: .venv/Scripts/python -m unittest tools/bswgen/test_bswgen_group_lock.py
"""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "tools"))

import bswgen  # noqa: E402

# PLAT-62: the own full-featured test profile (synthetic testboard_full/testproject_full) instead of the eval board
# project, which stays private (PLAT-46 F10) - a test against it would be red in the exported open tree. What
# proves something about the eval board itself uses EVAL_DEMO and is skipped where it is missing.
DEMO = REPO / "tools" / "nxconfig" / "test" / "profiles" / "projects" / "testproject_full" / "project.yaml"
EVAL_DEMO = REPO / "projects" / "evalboard_s32k144_demo" / "project.yaml"
GENERATED = Path("bsp/io/outputPwm/ftmPwmConfiguration.h")
DRIVER = Path("bsp/io/outputPwm/ComplementaryPwm.h")
BSP_CONFIGURATION = REPO / "platforms/s32k1xx/port/bspConfiguration/include"
INTEGRATION = REPO / "platforms/s32k1xx/port/main/src/integration"
OPENBSW = REPO / "external" / "openbsw"
HOST = HERE / "test" / "pwm_host"

INIT_LOCK = "        lockIfDegraded();\n#endif\n        // channel modes"
PINS_MUXED = "            (void)Io::setDefaultConfiguration(p.lowPin);\n        }\n"
INHIBIT = INTEGRATION / "FtmPwmInhibit.h"
RELEASE = "::bios::ftmPwm::groupLocked() ? ::bios::ftmPwm::CHANNEL_MASK : 0U;"

# (name, file, [(old, new), ...]): every `old` must occur exactly once
MUTANTS = [
    ("init does not lock (the window after a safety reset)", DRIVER, [(INIT_LOCK, "#endif\n        // channel modes")]),
    ("init locks only after the pins are muxed", DRIVER, [
        (INIT_LOCK, "#endif\n        // channel modes"),
        (PINS_MUXED, PINS_MUXED + "#ifdef NX_PWM_GROUP_LOCK\n        lockIfDegraded();\n#endif\n")]),
    ("a duty write of a locked group is taken", DRIVER, [
        ("        if (lockIfDegraded())\n        {\n            return bsp::BSP_ERROR;\n        }\n",
         "        (void)lockIfDegraded();\n")]),
    ("the lock does not force the output control", DRIVER, [
        ("        _ftm.getFtm().SWOCTRL = ftmPwm::CHANNEL_MASK;\n        return true;", "        return true;")]),
    ("the guard releases a locked group", INHIBIT, [(RELEASE, "0U;")]),
    ("the guard keeps an unlocked group inhibited for ever", INHIBIT, [(RELEASE, "::bios::ftmPwm::CHANNEL_MASK;")]),
]


def demo_copy(tmp: Path, degraded: bool) -> Path:
    project = yaml.safe_load(DEMO.read_text(encoding="utf-8"))
    project["project"]["board"] = (REPO / "tools" / "nxconfig" / "test" / "profiles" / "boards" / "testboard_full").as_posix()
    project["project"]["rte"] = (DEMO.parent / project["project"]["rte"]).as_posix()
    project["project"]["diag"] = (DEMO.parent / project["project"]["diag"]).as_posix()
    if degraded:
        project["safety"]["output_groups"] = [{"name": "DRIVE", "pwm": "MOT1"}]
        project["safety"]["degraded"] = [{"event": "DEVICE_FAULT", "device": "MOT1_GD", "groups": ["DRIVE"]}]
    path = tmp / ("degraded" if degraded else "plain") / "project.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(project, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return path


class GroupLockTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="nx_grouplock_"))
        cls.gen = cls.tmp / "gen"
        bswgen.generate(demo_copy(cls.tmp, True), cls.gen)
        cls.plain = cls.tmp / "gen_plain"
        bswgen.generate(demo_copy(cls.tmp, False), cls.plain)
        cls.demo = cls.tmp / "gen_demo"
        bswgen.generate(DEMO, cls.demo)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def compile_and_run(self, name: str, first_include: Path | None = None):
        includes = ([first_include] if first_include else []) + [
            HOST / "stubs", self.gen, BSP_CONFIGURATION, REPO / "platforms/s32k1xx/bsp/bspFtmPwmGroup/include", INTEGRATION]
        system = [OPENBSW / "platforms/s32k1xx/bsp/bspFtm/include",
                  OPENBSW / "platforms/s32k1xx/bsp/bspMcu/include/3rdparty/nxp", OPENBSW / "libs/bsw/bsp/include"]
        exe = self.tmp / f"{name}.exe"
        cmd = [sys.executable, "-m", "ziglang", "c++", "-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror",
               "-Wconversion", "-Wno-deprecated-volatile", *[f"-I{i}" for i in includes],
               *[f"-isystem{i}" for i in system], str(HOST / "complementary_pwm_lock_host_test.cpp"), "-o", str(exe)]
        built = subprocess.run(cmd, capture_output=True, text=True)
        if built.returncode != 0:
            return built, "compile"
        return subprocess.run([str(exe)], capture_output=True, text=True, timeout=60), "run"

    def test_generat_only_with_a_mapping(self):
        locked = (self.gen / GENERATED).read_text(encoding="utf-8")
        self.assertIn("#define NX_PWM_GROUP_LOCK 1\n", locked)
        self.assertIn("bool groupLocked();\n", locked)
        for gen in (self.plain, self.demo):
            text = (gen / GENERATED).read_text(encoding="utf-8")
            self.assertNotIn("NX_PWM_GROUP_LOCK", text)
            self.assertNotIn("groupLocked", text)
        # the demo copy without a mapping renders exactly the header of the demo (the path in line 1 aside)
        body = lambda p: [x for x in (p / GENERATED).read_text(encoding="utf-8").split("\n")  # noqa: E731
                          if "Generated by" not in x]
        self.assertEqual(body(self.plain), body(self.demo))

    def test_host_test(self):
        r, stage = self.compile_and_run("lock")
        self.assertEqual(r.returncode, 0, f"{stage}: {r.stdout[-3000:]}{r.stderr[-3000:]}")
        print("\n" + r.stdout.strip(), file=sys.stderr)

    def test_without_the_lock_the_host_test_does_not_build(self):
        # built against the generat of a project without a mapping it must stop (it would test nothing)
        includes_gen, self.gen = self.gen, self.plain
        try:
            r, stage = self.compile_and_run("nolock")
        finally:
            self.gen = includes_gen
        self.assertEqual(stage, "compile")
        self.assertIn("NX_PWM_GROUP_LOCK", r.stderr)

    def test_mutants_are_detected(self):
        survivors = []
        for index, (name, file, changes) in enumerate(MUTANTS):
            with self.subTest(mutant=name):
                include = self.tmp / f"mut{index}"
                if file == DRIVER:
                    source, target = BSP_CONFIGURATION / DRIVER, include / DRIVER
                else:
                    source, target = file, include / file.name
                text = source.read_text(encoding="utf-8").replace("\r\n", "\n")
                for old, new in changes:
                    self.assertEqual(text.count(old), 1, f"mutation point of '{name}' not unique")
                    text = text.replace(old, new)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text, encoding="utf-8")
                r, stage = self.compile_and_run(f"mut{index}", include)
                self.assertEqual(stage, "run", f"mutant '{name}' must compile: {r.stderr[-2000:]}")
                if r.returncode == 0:
                    survivors.append(name)
                print(f"{'SURVIVED' if r.returncode == 0 else 'detected':>9}: {name}", file=sys.stderr)
        self.assertEqual(survivors, [])


if __name__ == "__main__":
    unittest.main()
