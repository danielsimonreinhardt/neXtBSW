# SPDX-License-Identifier: Apache-2.0
"""Host tests of the reader of generated/model.sha256 (TOOL-169), without MATLAB.

Run: .venv/Scripts/python -m unittest tools/swc/test_modelhash.py
"""

import hashlib
import io
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import modelhash  # noqa: E402
from modelhash import CHANGED, MISSING, NEW, Change  # noqa: E402


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class ModelHashTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="modelhash_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        # repository layout: swc/Lib/Lib.slx next to the component swc/My Swc/MySwc.slx (a folder with a blank)
        self.lib = self.tmp / "swc" / "Lib" / "Lib.slx"
        self.lib.parent.mkdir(parents=True)
        self.lib.write_bytes(b"library v1")
        self.comp = self.tmp / "swc" / "My Swc"
        (self.comp / "init_data" / "sub").mkdir(parents=True)
        (self.comp / "generated").mkdir()
        self.files = {"MySwc.slx": b"model v1", "MySwc_init.m": b"gain = 2;\n", "init_data/a.m": b"a = 1;\n",
                      "init_data/sub/b.mat": b"MATLAB 5.0", "MySwc_data.m": b"p = 1;\n"}
        for rel, data in self.files.items():
            (self.comp / rel).write_bytes(data)

    def record(self, rel_paths, complete=True, extra=""):
        lines = []
        for rel in rel_paths:
            data = self.lib.read_bytes() if rel.endswith("Lib.slx") else self.files[rel]
            lines.append(f"{sha(data)}  {rel}")
        if complete:
            lines.append(modelhash.MARKER)
        modelhash.record_file(self.comp).write_text("\n".join(lines) + "\n" + extra, encoding="utf-8")

    def full_record(self):
        self.record(["MySwc.slx", "MySwc_init.m", "init_data/a.m", "init_data/sub/b.mat", "../Lib/Lib.slx"])

    def test_no_record(self):
        self.assertIsNone(modelhash.model_changes(self.comp, "MySwc"))
        self.assertIsNone(modelhash.read_record(self.comp))

    def test_current(self):
        self.full_record()
        self.assertEqual(modelhash.model_changes(self.comp, "MySwc"), [])
        self.assertEqual(modelhash.model_changes(self.comp), [])          # without the name: every .slx of the folder

    def test_each_input_is_named(self):
        self.full_record()
        (self.comp / "MySwc_init.m").write_bytes(b"gain = 3;\n")
        self.assertEqual(modelhash.model_changes(self.comp, "MySwc"), [Change("MySwc_init.m", CHANGED)])
        self.assertEqual(modelhash.describe(modelhash.model_changes(self.comp)), "geändert: MySwc_init.m")
        (self.comp / "init_data" / "sub" / "b.mat").write_bytes(b"MATLAB 5.0 changed")
        self.lib.write_bytes(b"library v2")
        self.assertEqual(modelhash.describe(modelhash.model_changes(self.comp, "MySwc")),
                         "geändert: MySwc_init.m, init_data/sub/b.mat, ../Lib/Lib.slx")

    def test_missing_and_new(self):
        self.full_record()
        (self.comp / "init_data" / "a.m").unlink()
        self.lib.unlink()
        (self.comp / "init_data" / "c.m").write_bytes(b"c = 1;\n")
        changes = modelhash.model_changes(self.comp, "MySwc")
        self.assertEqual(changes, [Change("init_data/a.m", MISSING), Change("../Lib/Lib.slx", MISSING),
                                   Change("init_data/c.m", NEW)])
        self.assertEqual(modelhash.describe(changes), "fehlt: init_data/a.m, ../Lib/Lib.slx; neu: init_data/c.m")

    def test_new_init_script(self):
        (self.comp / "MySwc_init.m").unlink()
        self.record(["MySwc.slx", "init_data/a.m", "init_data/sub/b.mat"])
        self.assertEqual(modelhash.model_changes(self.comp, "MySwc"), [])
        (self.comp / "MySwc_init.m").write_bytes(b"gain = 2;\n")
        self.assertEqual(modelhash.model_changes(self.comp, "MySwc"), [Change("MySwc_init.m", NEW)])

    def test_types_file_is_an_input(self):
        """RTE-15: <Swc>_types.m (named types of swc.yaml) is an input like _init.m - new, changed, missing."""
        self.full_record()
        self.assertEqual(modelhash.model_changes(self.comp, "MySwc"), [])
        self.files["MySwc_types.m"] = b"function MySwc_types()\nend\n"
        (self.comp / "MySwc_types.m").write_bytes(self.files["MySwc_types.m"])
        self.assertEqual(modelhash.model_changes(self.comp, "MySwc"), [Change("MySwc_types.m", NEW)])
        self.assertEqual(modelhash.model_changes(self.comp), [Change("MySwc_types.m", NEW)])
        self.record(["MySwc.slx", "MySwc_types.m", "MySwc_init.m", "init_data/a.m", "init_data/sub/b.mat",
                     "../Lib/Lib.slx"])
        self.assertEqual(modelhash.model_changes(self.comp, "MySwc"), [])
        (self.comp / "MySwc_types.m").write_bytes(b"function MySwc_types()\n% changed\nend\n")
        self.assertEqual(modelhash.model_changes(self.comp, "MySwc"), [Change("MySwc_types.m", CHANGED)])

    def test_data_file_is_no_input(self):
        """<Swc>_data.m: values the RTE reads - a change never makes the code stale."""
        self.full_record()
        (self.comp / "MySwc_data.m").write_bytes(b"p = 2;\n")
        self.assertEqual(modelhash.model_changes(self.comp, "MySwc"), [])

    def test_old_record_with_only_the_model(self):
        """Record of the swc_build.m before TOOL-169: its .slx line counts, init files are not reported as new."""
        self.record(["MySwc.slx"], complete=False)
        self.assertEqual(modelhash.model_changes(self.comp, "MySwc"), [])
        self.assertEqual(modelhash.model_changes(self.comp), [])
        (self.comp / "MySwc.slx").write_bytes(b"model v2")
        self.assertEqual(modelhash.model_changes(self.comp, "MySwc"), [Change("MySwc.slx", CHANGED)])

    def test_hash_without_path_and_star_and_crlf(self):
        """A bare hash stands for <Swc>.slx; sha256sum's binary marker '*' and CRLF line ends are accepted."""
        modelhash.record_file(self.comp).write_text(sha(b"model v1") + "\n", encoding="ascii")
        self.assertEqual(modelhash.model_changes(self.comp, "MySwc"), [])
        self.assertEqual(modelhash.model_changes(self.comp), [])
        modelhash.record_file(self.comp).write_bytes(
            f"{sha(b'model v1').upper()} *MySwc.slx\r\n{sha(b'gain = 2;' + bytes([10]))}  MySwc_init.m\r\n"
            f"{modelhash.MARKER}\r\n".encode("ascii"))
        self.assertEqual(modelhash.model_changes(self.comp, "MySwc"),
                         [Change("init_data/a.m", NEW), Change("init_data/sub/b.mat", NEW)])

    def test_unrecorded_model_is_changed(self):
        """A second .slx in the folder the code was not built from (Studio table: every model of the folder)."""
        self.full_record()
        (self.comp / "Other.slx").write_bytes(b"other")
        self.assertEqual(modelhash.model_changes(self.comp), [Change("Other.slx", CHANGED)])
        self.assertEqual(modelhash.model_changes(self.comp, "MySwc"), [])

    def test_garbage_lines_are_ignored(self):
        self.full_record()
        path = modelhash.record_file(self.comp)
        path.write_text("not a hash line\n# comment\n" + path.read_text(encoding="utf-8"), encoding="utf-8")
        self.assertEqual(modelhash.model_changes(self.comp, "MySwc"), [])
        path.write_bytes(b"\xff\xfe broken")
        self.assertIsNone(modelhash.model_changes(self.comp, "MySwc"))

    def test_command_line(self):
        self.full_record()
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(modelhash.main([str(self.comp), "MySwc"]), 0)
        self.assertEqual(out.getvalue().strip(), "aktuell")
        (self.comp / "MySwc_init.m").write_bytes(b"gain = 3;\n")
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(modelhash.main([str(self.comp), "MySwc"]), 1)
        self.assertEqual(out.getvalue().strip(), "geändert: MySwc_init.m")
        with redirect_stdout(io.StringIO()):
            self.assertEqual(modelhash.main([str(self.tmp)]), 2)


if __name__ == "__main__":
    unittest.main()
