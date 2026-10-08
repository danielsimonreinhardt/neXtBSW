"""PLAT-79: configuration and generator identity of a project (tools/nxconfig/identity.py), proposal C of
doc/safety/tool-classification.md (TF4).

Own test profiles, not the eval board: testproject_safe_outputs (bswgen profile, gate drivers and PWM group on
S32K144) for the board generate and the location tests, projects/buildtest_s32k148_min (S32K148 without SBC, ECU
description with a component, default diagnostic description) for the manifests of rtegen and diaggen. The eval
board demo only for the one input kind no other project has (a DBC); skipped when it is missing (open export).

  ReproducibilityTest   generating twice gives identical files (bswgen in this process and in two processes with
                        another hash seed and working directory, rtegen, diaggen); the identity header and every
                        manifest hold no absolute path, no time and no git state
  LocationTest          a copy of project and board at another place, and a copy with CRLF line ends, has the same
                        identity; a changed project, board or ECU description changes the configuration sum only
  IdentityTest          the identity is the SHA-256 of the two sums; a changed generator file changes the generator
                        sum only; the header fits the PLAT-98 extension point (names in the info column, the short
                        form in the version line, 64 hex digits for DID 0xF1F1)
  ManifestTest          every file of a generator run is listed with its SHA-256, and all three generators name the
                        same identity
  CompileTest           the generated header compiles with the real ecu/consoleCommands Identity.cpp (zig c++) and
                        its entries come after application, neXtBSW and board
  CommandLineTest       nxconfig identity prints the identity of the build, --header the header bswgen writes

    .venv/Scripts/python -m unittest tools/nxconfig/test_identity.py
"""

import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "bswgen"))
sys.path.insert(0, str(HERE.parent / "rtegen"))
sys.path.insert(0, str(HERE.parent / "diaggen"))

import nxconfig  # noqa: E402
from nxconfig import identity as nxidentity  # noqa: E402

PROFILES = REPO / "tools" / "bswgen" / "test" / "profiles"
PROFILE = PROFILES / "projects" / "testproject_safe_outputs" / "project.yaml"
# PLAT-62: the own full-featured test profile (synthetic testboard_full/testproject_full) instead of the eval board
# project, which stays private (PLAT-46 F10) - a test against it would be red in the exported open tree. What
# proves something about the eval board itself uses EVAL_DEMO and is skipped where it is missing.
DEMO = REPO / "tools" / "nxconfig" / "test" / "profiles" / "projects" / "testproject_full" / "project.yaml"
EVAL_DEMO = REPO / "projects" / "evalboard_s32k144_demo" / "project.yaml"
BUILDTEST = REPO / "projects" / "buildtest_s32k148_min" / "project.yaml"
HEX64 = re.compile(r"^[0-9a-f]{64}$")


def identity_of(project: Path) -> nxidentity.Identity:
    config, messages = nxconfig.check(project)
    errors = [m for m in messages if m.severity == "error"]
    assert config is not None and not errors, errors
    return nxidentity.of(config)


def files_of(folder: Path) -> dict[str, bytes]:
    return {f.relative_to(folder).as_posix(): f.read_bytes() for f in sorted(folder.rglob("*")) if f.is_file()}


def copy_profile(root: Path, crlf: bool = False) -> Path:
    """Project and board of PROFILE below `root` in the same relative layout (projects/x, boards/y)."""
    project = PROFILE.parent
    text = PROFILE.read_text(encoding="utf-8")
    board_rel = re.search(r"board:\s*(\S+)", text).group(1)
    board = (project / board_rel).resolve()
    shutil.copytree(project, root / "projects" / project.name)
    shutil.copytree(board, root / "boards" / board.name)
    if crlf:
        for f in root.rglob("*.yaml"):
            f.write_bytes(f.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
    return root / "projects" / project.name / "project.yaml"


class ReproducibilityTest(unittest.TestCase):
    def test_bswgen_twice_identical(self):
        import bswgen
        with tempfile.TemporaryDirectory() as tmp:
            a, b = Path(tmp) / "a", Path(tmp) / "b"
            bswgen.generate(PROFILE, a)
            bswgen.generate(PROFILE, b)
            fa, fb = files_of(a), files_of(b)
            self.assertIn(nxidentity.FIELDS_HEADER, fa)
            self.assertIn(nxidentity.manifest_path("bswgen"), fa)
            self.assertEqual(sorted(fa), sorted(fb))
            for name in fa:
                self.assertEqual(fa[name], fb[name], name)
            for name in (nxidentity.FIELDS_HEADER, nxidentity.manifest_path("bswgen")):
                text = fa[name].decode("utf-8")
                self.assertNotIn(tmp.replace("\\", "/"), text.replace("\\", "/"))
                self.assertNotIn(REPO.as_posix(), text.replace("\\", "/"))
                self.assertNotRegex(text, r"20\d\d-\d\d-\d\d|\d\d:\d\d:\d\d")   # no date or time

    def test_bswgen_in_other_processes_identical(self):
        """Another hash seed, working directory and output directory: the same identity files."""
        with tempfile.TemporaryDirectory() as tmp:
            outputs = []
            for seed, cwd in (("1", REPO), ("4711", Path(tmp))):
                out = Path(tmp) / f"out{seed}"
                env = {**os.environ, "PYTHONHASHSEED": seed, "PYTHONUTF8": "1"}
                done = subprocess.run([sys.executable, str(REPO / "tools" / "bswgen" / "bswgen.py"), str(PROFILE),
                                       str(out)], cwd=cwd, env=env, capture_output=True, text=True, timeout=600)
                self.assertEqual(done.returncode, 0, done.stderr)
                outputs.append(files_of(out))
            self.assertEqual(outputs[0], outputs[1])

    def test_rtegen_and_diaggen_twice_identical(self):
        import diaggen
        import rtegen
        with tempfile.TemporaryDirectory() as tmp:
            runs = []
            for name in ("a", "b"):
                out = Path(tmp) / name
                rtegen.generate_project(BUILDTEST, out / "rte", REPO)
                d = diaggen.load_project(BUILDTEST)
                diaggen.write(diaggen.plan(d), out / "diag", identity=d.identity)
                runs.append(files_of(out))
            self.assertIn("rte/" + nxidentity.manifest_path("rtegen"), runs[0])
            self.assertIn("diag/" + nxidentity.manifest_path("diaggen"), runs[0])
            self.assertEqual(runs[0], runs[1])


class LocationTest(unittest.TestCase):
    def test_copy_elsewhere_and_crlf_same_identity(self):
        """Two copies of project and board (same relative layout) at different places, one with CRLF line ends.

        Not compared with the profile in the tree: there its board lies in the neXtBSW tree (label tree/...), in
        the copies outside it (label relative to the project) - a board that moves out of the tree is another
        input on purpose."""
        here = identity_of(PROFILE)
        with tempfile.TemporaryDirectory() as t1, tempfile.TemporaryDirectory() as t2:
            moved = identity_of(copy_profile(Path(t1) / "x"))
            crlf = identity_of(copy_profile(Path(t2) / "deeper" / "y", crlf=True))
        self.assertEqual(crlf.configuration_sha256, moved.configuration_sha256)
        self.assertEqual(crlf.identity, moved.identity)
        self.assertEqual(moved.generators_sha256, here.generators_sha256)
        self.assertIn("../../boards/testboard_safe_outputs/board.yaml", [i.label for i in moved.configuration])
        labels = [i.label for i in here.configuration]
        self.assertIn("project/project.yaml", labels)
        self.assertTrue(all(not Path(label).is_absolute() and ":" not in label for label in labels), labels)
        self.assertTrue(any(label.startswith("tree/mcudb/") for label in labels), labels)

    def test_changed_input_changes_configuration_only(self):
        here = identity_of(PROFILE)
        with tempfile.TemporaryDirectory() as tmp:
            project = copy_profile(Path(tmp))
            board = next((Path(tmp) / "boards").glob("*/board.yaml"))
            for changed in (project, board):
                with self.subTest(changed=changed.name):
                    original = changed.read_bytes()
                    changed.write_bytes(original + b"\n# changed\n")
                    try:
                        other = identity_of(project)
                    finally:
                        changed.write_bytes(original)
                    self.assertNotEqual(other.configuration_sha256, here.configuration_sha256)
                    self.assertEqual(other.generators_sha256, here.generators_sha256)
                    self.assertNotEqual(other.identity, here.identity)

    def test_ecu_description_and_components_are_inputs(self):
        labels = {i.label for i in identity_of(BUILDTEST).configuration}
        self.assertIn("project/rte.yaml", labels)
        self.assertIn("tree/ecu/asyncCoreConfiguration/tasks.yaml", labels)
        self.assertIn("tree/boards/buildtest_s32k148/board.yaml", labels)
        self.assertIn("tree/mcudb/s32k1xx/S32K148.yaml", labels)
        # TOOL-245: every description file of the component DemoSwc on disk is an input - in the development tree the
        # Embedded Coder ARXML, in the exported tree the swc.yaml of the C substitute (PLAT-119); one of them must exist
        folder = REPO / "swc" / "DemoSwc"
        descriptions = sorted((folder / "generated" / "arxml").glob("*.arxml"))
        descriptions += [folder / "swc.yaml"] if (folder / "swc.yaml").is_file() else []
        self.assertTrue(descriptions, f"{folder}: neither generated/arxml/*.arxml nor swc.yaml")
        for description in descriptions:
            self.assertIn(f"tree/{description.relative_to(REPO).as_posix()}", labels)
        self.assertIn("tree/swc/DemoSwc/DemoSwc_data.m", labels)

    def test_dbc_and_diag_description_are_inputs(self):
        labels = {i.label for i in identity_of(DEMO).configuration}   # PLAT-62: the full test profile
        self.assertIn("project/TestFull.dbc", labels)
        self.assertIn("project/diag.yaml", labels)


class IdentityTest(unittest.TestCase):
    def setUp(self):
        self.identity = identity_of(PROFILE)

    def test_identity_of_the_two_sums(self):
        i = self.identity
        text = f"configuration {i.configuration_sha256}\ngenerators {i.generators_sha256}\n"
        self.assertEqual(i.identity, hashlib.sha256(text.encode()).hexdigest())
        self.assertRegex(i.identity, HEX64)
        self.assertEqual(i.short, i.identity[:16])
        self.assertEqual(i.version, nxconfig.nextbsw_version())

    def test_generator_change_changes_generators_only(self):
        i = self.identity
        first = i.generators[0]
        changed = nxidentity.Identity(i.version, i.configuration,
                                      (nxidentity.Input(first.label, first.file, "0" * 64),) + i.generators[1:])
        self.assertNotEqual(changed.generators_sha256, i.generators_sha256)
        self.assertEqual(changed.configuration_sha256, i.configuration_sha256)
        self.assertNotEqual(changed.identity, i.identity)

    def test_generators_are_the_tools_not_their_tests(self):
        labels = [g.label for g in self.identity.generators]
        for expected in ("tree/tools/bswgen/bswgen.py", "tree/tools/bswgen/blgen.py", "tree/tools/rtegen/rtegen.py",
                         "tree/tools/diaggen/diaggen.py", "tree/tools/diaggen/standard_diag.yaml",
                         "tree/tools/dbcgen/dbcgen.py", "tree/tools/nxconfig/identity.py", "tree/VERSION"):
            self.assertIn(expected, labels)
        self.assertTrue(any(label.startswith("tree/tools/bswgen/templates/") for label in labels))
        self.assertFalse([label for label in labels if "/test" in label or "__pycache__" in label])

    def test_file_sum_ignores_line_ends_of_text_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            lf, crlf, binary = Path(tmp) / "lf", Path(tmp) / "crlf", Path(tmp) / "bin"
            lf.write_bytes(b"a\nb\n")
            crlf.write_bytes(b"a\r\nb\r\n")
            binary.write_bytes(b"a\r\n\0")
            self.assertEqual(nxidentity.file_sha256(lf), nxidentity.file_sha256(crlf))
            self.assertEqual(nxidentity.file_sha256(binary), hashlib.sha256(b"a\r\n\0").hexdigest())

    def test_header_fits_the_extension_point(self):
        header = self.identity.fields_header()
        self.assertIn(f'#define NX_IDENTITY_CONFIGURATION "{self.identity.identity}"', header)
        rows = re.findall(r'\{"([^"]*)", "([^"]*)", (true|false)\},', header)
        self.assertEqual([r[0] for r in rows], ["configuration", "identity", "config inputs", "generators"])
        self.assertEqual(rows[0], ("configuration", self.identity.short, "true"))     # in the line of "version"
        self.assertEqual(rows[1][1], self.identity.identity)
        self.assertTrue(all(r[2] == "false" for r in rows[1:]))
        self.assertTrue(all(len(r[0]) < 15 for r in rows))           # INFO_NAME_COLUMN of identity/IdentityText.h
        body = header.split("#define NX_IDENTITY_GENERATED_FIELDS \\\n", 1)[1]
        self.assertTrue(body.rstrip("\n").endswith("},"), body)     # every entry followed by a comma, no dangling \


class ManifestTest(unittest.TestCase):
    def test_every_file_listed_and_one_identity(self):
        import bswgen
        import diaggen
        import rtegen
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            bswgen.generate(BUILDTEST, out / "board")
            rtegen.generate_project(BUILDTEST, out / "rte", REPO)
            d = diaggen.load_project(BUILDTEST)
            diaggen.write(diaggen.plan(d), out / "diag", identity=d.identity)
            expected = identity_of(BUILDTEST).identity
            for folder, generator in (("board", "bswgen"), ("rte", "rtegen"), ("diag", "diaggen")):
                with self.subTest(generator=generator):
                    manifest = out / folder / nxidentity.manifest_path(generator)
                    text = manifest.read_text(encoding="utf-8")
                    self.assertIn(f"# identity {expected}\n", text)
                    self.assertIn(f"# generator {generator}, neXtBSW {nxconfig.nextbsw_version()}\n", text)
                    listed = dict(reversed(line.split("  ", 1)) for line in text.splitlines()
                                  if not line.startswith("#"))
                    written = {f.relative_to(out / folder).as_posix() for f in (out / folder).rglob("*")
                               if f.is_file() and f != manifest}
                    # rtegen also leaves files other runs wrote (none here: a fresh folder)
                    self.assertEqual(set(listed), written)
                    for name, digest in listed.items():
                        content = (out / folder / name).read_bytes()
                        self.assertEqual(digest, hashlib.sha256(content).hexdigest(), name)
            header = (out / "board" / nxidentity.FIELDS_HEADER).read_text(encoding="utf-8")
            self.assertIn(f'NX_IDENTITY_CONFIGURATION "{expected}"', header)


def zig_available() -> bool:
    try:
        import ziglang  # noqa: F401
    except ImportError:
        return False
    return (REPO / "external" / "openbsw" / "libs" / "3rdparty" / "etl" / "include").is_dir()


@unittest.skipUnless(zig_available(), "ziglang or external/openbsw missing (requires: ziglang, openbsw)")
class CompileTest(unittest.TestCase):
    PROGRAM = r"""
#define NX_IDENTITY_APPLICATION     "plat79_profile"
#define NX_IDENTITY_NEXTBSW_VERSION "4.5.6"
#define NX_IDENTITY_BOARD           "board_x/S32K148"
#include "identity/Identity.cpp"
#include <cstdio>
int main()
{
    for (auto const& f : ::identity::buildFields())
    {
        std::printf("%s|%s|%d\n", f.name, f.value, f.versionLine ? 1 : 0);
    }
    return 0;
}
"""

    def test_header_compiles_with_identity_cpp(self):
        identity = identity_of(PROFILE)
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "identity").mkdir()
            (tmp / nxidentity.FIELDS_HEADER).write_text(identity.fields_header(), encoding="utf-8")
            main = tmp / "main.cpp"
            main.write_text(self.PROGRAM, encoding="utf-8")
            exe = tmp / "identity.exe"
            src = REPO / "ecu" / "consoleCommands" / "src"
            done = subprocess.run([sys.executable, "-m", "ziglang", "c++", "-std=c++17", "-O1", "-Wall", "-Wextra",
                                   "-Werror", f"-I{tmp}", f"-I{src}", f"-I{REPO / 'ecu' / 'consoleCommands' / 'include'}",
                                   "-isystem", str(REPO / "external" / "openbsw" / "libs" / "3rdparty" / "etl" / "include"),
                                   str(main), "-o", str(exe)], capture_output=True, text=True, timeout=600)
            self.assertEqual(done.returncode, 0, done.stderr)
            run = subprocess.run([str(exe)], capture_output=True, text=True, timeout=60)
            self.assertEqual(run.returncode, 0)
            rows = [line.split("|") for line in run.stdout.splitlines()]
        self.assertEqual([r[0] for r in rows], ["application", "neXtBSW", "board", "configuration", "identity",
                                                "config inputs", "generators"])
        self.assertEqual(rows[3], ["configuration", identity.short, "1"])
        self.assertEqual(rows[4], ["identity", identity.identity, "0"])


class CommandLineTest(unittest.TestCase):
    def run_nxconfig(self, *args):
        return subprocess.run([sys.executable, str(REPO / "tools" / "nxconfig"), *args], capture_output=True,
                              text=True, timeout=600, env={**os.environ, "PYTHONUTF8": "1"})

    def test_prints_the_identity_without_building(self):
        identity = identity_of(PROFILE)
        done = self.run_nxconfig("identity", str(PROFILE.parent), "--files")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn(f"identity:      {identity.identity}", done.stdout)
        self.assertIn(f"short:         {identity.short}", done.stdout)
        self.assertIn(f"  {identity.configuration[0].sha256}  ", done.stdout)
        header = self.run_nxconfig("identity", str(PROFILE), "--header")
        self.assertEqual(header.returncode, 0, header.stderr)
        self.assertEqual(header.stdout, identity.fields_header())

    def test_configuration_error_no_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = copy_profile(Path(tmp))
            project.write_text(project.read_text(encoding="utf-8").replace("board:", "board_missing:", 1),
                               encoding="utf-8")
            done = self.run_nxconfig("identity", str(project))
        self.assertEqual(done.returncode, 1)
        self.assertIn("no identity", done.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
