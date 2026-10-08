"""NVM-07: generated nvm/nvmPartition.h and the display of the partition before writing (bswgen --nvm-partition).

Profiles: the demo project of the eval board as it is (no nvm section, S32K144), a copy of it with the decision
nvm01-eeesize (4 KB EEPROM, 32 KB data flash, DEPART 1011b, load on reset), and the project buildtest_s32k148_min
(other derivative, other code table). The header is compiled with the host compiler as C and as C++.

Run: .venv/Scripts/python -m unittest tools/bswgen/test_bswgen_nvm.py
"""

import io
import re
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
sys.path.insert(0, str(HERE.parent))

import bswgen  # noqa: E402

# PLAT-62: the own full-featured test profile (synthetic testboard_full/testproject_full) instead of the eval board
# project, which stays private (PLAT-46 F10) - a test against it would be red in the exported open tree. What
# proves something about the eval board itself uses EVAL_DEMO and is skipped where it is missing.
DEMO = REPO / "tools" / "nxconfig" / "test" / "profiles" / "projects" / "testproject_full"
EVAL_DEMO = REPO / "projects" / "evalboard_s32k144_demo"
K148 = REPO / "projects" / "buildtest_s32k148_min" / "project.yaml"
DECISION = {"partition": {"eeprom_bytes": 4096, "data_flash_bytes": 0x8000, "partition_code": 0xB,
                          "load_on_reset": True, "key_slots": 20}}   # NVM-11: CSEc key space (user 2026-09-23)


def demo_copy(tmp: Path, nvm_use=None, base: Path = DEMO / "project.yaml") -> Path:
    project = yaml.safe_load(base.read_text(encoding="utf-8"))
    info = project["project"]
    for key in ("board", "rte", "diag"):
        if key in info:
            info[key] = str((base.parent / info[key]).resolve())
    if nvm_use is not None:
        project["nvm"] = nvm_use
    else:
        project.pop("nvm", None)           # the demo carries the partition since NVM-07; "without" removes it
    path = tmp / "project.yaml"
    path.write_text(yaml.safe_dump(project, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return path


def defines(text: str) -> dict[str, str]:
    return dict(re.findall(r"^#define (NX_NVM_\w+) (.*)$", text, re.M))


class NvmPartitionHeaderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def generate(self, project: Path) -> str:
        out = self.tmp / "out"
        bswgen.generate(project, out)
        return (out / "nvm" / "nvmPartition.h").read_text(encoding="utf-8")

    def compiles(self, text: str):
        header = self.tmp / "nvmPartition.h"
        header.write_text(text, encoding="utf-8")
        for lang, std in (("c", "-std=c11"), ("c++", "-std=c++17")):
            src = self.tmp / ("probe.c" if lang == "c" else "probe.cpp")
            src.write_text('#include "nvmPartition.h"\n#include <string.h>\n'
                           "#ifdef NX_NVM_PARTITION\nunsigned long probe(void) { return (unsigned long)"
                           "strlen(NX_NVM_PARTITION_TEXT) + NX_NVM_PARTITION_CODE; }\n#endif\n"
                           "int main(void) { return 0; }\n", encoding="utf-8")
            r = subprocess.run([sys.executable, "-m", "ziglang", "c++" if lang == "c++" else "cc", std, "-Wall",
                                "-Wextra", "-Werror", f"-I{self.tmp}", str(src), "-o", str(self.tmp / f"p_{lang}.exe")],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr[-2000:])

    def test_demo_without_partition(self):
        text = self.generate(demo_copy(self.tmp))
        d = defines(text)
        self.assertEqual(d, {"NX_NVM_WRITE_LIMIT_US": "4500U",    # largest backup 64 KB: 2250 us x 2
                             "NX_NVM_WINDOW_START": "0x14000000U", "NX_NVM_WINDOW_SIZE": "0x1000U"})
        self.assertIn("No partition in the project", text)
        self.compiles(text)

    def test_decision_nvm01_eeesize(self):
        text = self.generate(demo_copy(self.tmp, DECISION))
        d = defines(text)
        self.assertEqual(d["NX_NVM_WRITE_LIMIT_US"], "4000U")        # 32 KB backup: 2000 us x 2
        self.assertEqual(d["NX_NVM_PARTITION_EEPROM_CODE"], "0x2U")    # EEESIZE 0010b
        self.assertEqual(d["NX_NVM_PARTITION_CODE"], "0xBU")           # DEPART 1011b
        self.assertEqual(d["NX_NVM_PARTITION_LOAD_CODE"], "0U")        # FCCOB3 = 0: FlexRAM loaded during reset
        self.assertEqual(d["NX_NVM_PARTITION_EEPROM_BYTES"], "4096U")
        self.assertEqual(d["NX_NVM_PARTITION_DATA_FLASH_BYTES"], "0x8000U")
        self.assertEqual(d["NX_NVM_PARTITION_BACKUP_BYTES"], "0x8000U")
        self.assertEqual(d["NX_NVM_PARTITION_VERIFY"], "0")         # confirmed 2026-09-22 (nvm07-mcudb-verify)
        self.assertEqual(d["NX_NVM_PARTITION_CONFIRM"], '"PGMPART-3-0-0-2-B"')
        self.assertIn('"IRREVERSIBLE: with key space the reference manual refuses mass erase', text)
        self.compiles(text)

    def test_other_derivative_other_codes(self):
        project = demo_copy(self.tmp, {"partition": {"eeprom_bytes": 4096, "data_flash_bytes": 0x70000}}, base=K148)
        d = defines(self.generate(project))
        self.assertEqual((d["NX_NVM_PARTITION_CODE"], d["NX_NVM_WRITE_LIMIT_US"]), ("0x4U", "9000U"))

    def test_display_before_writing(self):
        out = io.StringIO()
        self.assertEqual(bswgen.show_nvm_partition(demo_copy(self.tmp, DECISION), out=out), 0)
        text = out.getvalue()
        self.assertIn("emulated EEPROM 4096 bytes (size code 0x2)", text)
        self.assertIn("partition code 0xB = 1011b", text)
        self.assertIn("flexnvm partition PGMPART-3-0-0-2-B", text)
        out = io.StringIO()
        self.assertEqual(bswgen.show_nvm_partition(demo_copy(self.tmp), out=out), 1)
        self.assertIn("nothing to write", out.getvalue())


if __name__ == "__main__":
    unittest.main()
