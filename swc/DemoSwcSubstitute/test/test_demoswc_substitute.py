# SPDX-License-Identifier: Apache-2.0
"""Tests of the C substitute of DemoSwc (PLAT-119, decision F9 A).

The substitute stands in for the Simulink Coder output of swc/DemoSwc, which is not published. Checked on the folder
the export writes (assemble.py, from export.overlay of tools/distcheck/distribution.yaml):

  SubstituteFolderTest   swc.yaml and the sources are a valid C component without findings (swc_yaml, swc_skeleton);
                         the calibration value stays in swc/DemoSwc/DemoSwc_data.m only
  InterfaceTest          same RTE interface as the model: ports (direction, data type, accessing runnables),
                         runnables and periods, calibration parameter - against the ARXML of the model where the tree
                         has it (development repository; the export has no ARXML, the test says so and skips)
  HostTest               tools/rtegen generates Rte_DemoSwc.h for projects/buildtest_s32k148_min with the substitute,
                         src/DemoSwc.c and DemoSwcSubstituteHostTest.c are compiled against it (-Wall -Wextra
                         -Wconversion -Werror) and run: Led1, Led2 blink and release by the qualifier, low-pass

    python -m unittest swc/DemoSwcSubstitute/test/test_demoswc_substitute.py
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "tools" / "rtegen"))
sys.path.insert(0, str(REPO / "tools" / "swc"))

import assemble  # noqa: E402
import mdata  # noqa: E402
import rtegen  # noqa: E402
import swc_skeleton  # noqa: E402
import swc_yaml  # noqa: E402

PROJECT_FILE = REPO / "projects" / "buildtest_s32k148_min" / "project.yaml"
MODEL_ARXML = REPO / "swc" / "DemoSwc" / "generated" / "arxml"
HOST_TEST = HERE / "DemoSwcSubstituteHostTest.c"


def zig_available() -> bool:
    try:
        import ziglang  # noqa: F401
    except ImportError:
        return False
    return True


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="nx_demoswc_c_"))
        self.folder = assemble.assemble(self.tmp / "swc")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class SubstituteFolderTest(Case):
    def test_overlay_names_this_substitute(self):
        entry = assemble.overlay()
        self.assertEqual({"swc/DemoSwc/swc.yaml": "swc/DemoSwcSubstitute/swc.yaml",
                          "swc/DemoSwc/src/DemoSwc.c": "swc/DemoSwcSubstitute/src/DemoSwc.c"}, entry.files)
        self.assertEqual("swc/DemoSwc/generated", entry.replaces)

    def test_assembled_folder(self):
        self.assertFalse((self.folder / "generated").exists())
        for name in ("swc.yaml", "src/DemoSwc.c", "DemoSwc.slx", "DemoSwc_data.m"):
            self.assertTrue((self.folder / name).is_file(), name)
        self.assertEqual("c", rtegen.component_implementation(self.folder))

    def test_valid_c_component_without_findings(self):
        swc, messages = swc_yaml.load(self.folder / "swc.yaml")
        self.assertEqual([], [str(m) for m in messages])
        self.assertEqual("DemoSwc", swc.name)
        self.assertEqual([], [str(m) for m in swc_skeleton.check(self.folder)])

    def test_calibration_value_has_one_place(self):
        """FilterAlpha comes from swc/DemoSwc/DemoSwc_data.m (the host test uses its value); the substitute has no
        data file of its own that could drift."""
        self.assertFalse(any(HERE.parent.glob("*_data.m")))
        values = mdata.load(REPO / "swc" / "DemoSwc" / "DemoSwc_data.m").entries
        self.assertEqual([0.125], values["FilterAlpha"].values)
        self.assertIn("static float32 filterAlpha = 0.125F;", HOST_TEST.read_text(encoding="utf-8"))

    def test_hand_written_and_licensed(self):
        """No Simulink Coder header, SPDX Apache-2.0 - the R3 pattern of distcheck would stop the export otherwise."""
        rules = assemble.core.load_rules(REPO / "tools" / "distcheck" / "distribution.yaml")
        marker = next(p for p in rules.content_patterns if p.id == "simulink_generated")
        for path in (HERE.parent / "src" / "DemoSwc.c", HERE.parent / "swc.yaml", HOST_TEST):
            text = path.read_text(encoding="utf-8")
            self.assertIsNone(marker.regex.search(text), path.name)
            self.assertIn("SPDX-License-Identifier: Apache-2.0", text, path.name)


class InterfaceTest(Case):
    def describe(self, comp) -> dict:
        return {
            "ports": {key: (p.direction, p.type.name, p.init) for key, p in comp.ports.items()},
            "runnables": {r.name: (r.period_ms, sorted(r.accesses)) for r in comp.runnables},
            "parameters": {key: (p.type.name, p.dim) for key, p in comp.parameters.items()},
        }

    def test_same_interface_as_the_model(self):
        if not MODEL_ARXML.is_dir():
            self.skipTest("no ARXML of the model in this tree (exported tree without Simulink output, F9 A)")
        model = rtegen.parse_component("DemoSwc", MODEL_ARXML)
        substitute, warnings = rtegen.parse_c_component("DemoSwc", self.folder)
        self.assertEqual([], [str(w) for w in warnings])
        self.assertEqual(self.describe(model), self.describe(substitute))
        # only the C names of the periodic runnables differ (RTE-20: <Swc>_<Runnable>)
        self.assertEqual({"DemoSwc_Init": "DemoSwc_Init", "Run10ms": "Run10ms", "Run100ms": "Run100ms"},
                         {r.name: r.symbol for r in model.runnables})
        self.assertEqual({"DemoSwc_Init": "DemoSwc_Init", "Run10ms": "DemoSwc_Run10ms",
                          "Run100ms": "DemoSwc_Run100ms"}, {r.name: r.symbol for r in substitute.runnables})

    def test_interface_as_documented(self):
        """Without the ARXML (exported tree) the interface is checked against what swc/README.md documents."""
        comp, _ = rtegen.parse_c_component("DemoSwc", self.folder)
        self.assertEqual({"Button1.Button1": ("in", "boolean"), "MotorNtc_mV.MotorNtc_mV": ("in", "uint32"),
                          "PcCommandQlf.PcCommandQlf": ("in", "uint8"), "Led1.Led1": ("out", "boolean"),
                          "Led2.Led2": ("out", "boolean"),
                          "MotorNtcFiltered_mV.MotorNtcFiltered_mV": ("out", "uint32")},
                         {k: (p.direction, p.type.name) for k, p in comp.ports.items()})
        self.assertEqual({"DemoSwc_Init": None, "Run10ms": 10, "Run100ms": 100},
                         {r.name: r.period_ms for r in comp.runnables})
        self.assertEqual({"CalPrm.FilterAlpha": "float32"}, {k: p.type.name for k, p in comp.parameters.items()})


@unittest.skipUnless(zig_available(), "ziglang missing (pip install ziglang)")
class HostTest(Case):
    def generate(self) -> Path:
        ecu = yaml.safe_load((PROJECT_FILE.parent / "rte.yaml").read_text(encoding="utf-8"))
        entry = next(c for c in ecu["components"] if c["name"] == "DemoSwc")
        entry["path"] = self.folder.as_posix()
        ecu_file = self.tmp / "rte.yaml"
        ecu_file.write_text(yaml.safe_dump(ecu, sort_keys=False), encoding="utf-8")
        out = self.tmp / "gen"
        config = rtegen.nxconfig.load(PROJECT_FILE)
        rtegen.generate(ecu_file, config, out, REPO)
        return out

    def test_generated_header_and_host_test(self):
        out = self.generate()
        header = (out / "Rte_DemoSwc.h").read_text(encoding="utf-8")
        for decl in ("extern void DemoSwc_Init(void);", "extern void DemoSwc_Run10ms(void);",
                     "extern void DemoSwc_Run100ms(void);", "extern float32 Rte_Prm_DemoSwc_CalPrm_FilterAlpha(void);"):
            self.assertIn(decl, header)
        cmake = (out / "rte_components.cmake").read_text(encoding="utf-8")
        self.assertIn((self.folder / "src").as_posix(), cmake)
        self.assertNotIn("generated/src", cmake)

        zig = [sys.executable, "-m", "ziglang", "cc", "-std=c99", "-O1", "-Wall", "-Wextra", "-Wconversion",
               "-Werror", f"-I{out}", f"-I{REPO / 'libs/bsw/autosarBase/include'}"]
        exe = self.tmp / "demoswc_substitute_host_test.exe"
        r = subprocess.run(zig + [str(self.folder / "src" / "DemoSwc.c"), str(HOST_TEST), "-o", str(exe)],
                           capture_output=True, text=True)
        self.assertEqual(0, r.returncode, r.stderr[-3000:])
        r = subprocess.run([str(exe)], capture_output=True, text=True)
        print("\n" + r.stdout.strip(), file=sys.stderr)
        self.assertEqual(0, r.returncode, r.stdout[-3000:])
        match = re.search(r"(\d+) checks, (\d+) failures", r.stdout)
        self.assertIsNotNone(match, r.stdout)
        self.assertEqual("0", match.group(2))
        self.assertGreaterEqual(int(match.group(1)), 30)

    def test_counter_check_a_broken_substitute_fails(self):
        """Mutation: Led2 without the release by the qualifier must be found by the host test."""
        out = self.generate()
        source = self.folder / "src" / "DemoSwc.c"
        text = source.read_text(encoding="utf-8")
        broken = text.replace("&& (qualifier == QLF_OK)", "&& (qualifier != 0xFFU)")
        self.assertNotEqual(text, broken)
        source.write_text(broken, encoding="utf-8")
        zig = [sys.executable, "-m", "ziglang", "cc", "-std=c99", "-O1", f"-I{out}",
               f"-I{REPO / 'libs/bsw/autosarBase/include'}"]
        exe = self.tmp / "broken.exe"
        r = subprocess.run(zig + [str(source), str(HOST_TEST), "-o", str(exe)], capture_output=True, text=True)
        self.assertEqual(0, r.returncode, r.stderr[-3000:])
        r = subprocess.run([str(exe)], capture_output=True, text=True)
        self.assertNotEqual(0, r.returncode, r.stdout)
        self.assertNotIn(" 0 failures", r.stdout)


if __name__ == "__main__":
    unittest.main()
