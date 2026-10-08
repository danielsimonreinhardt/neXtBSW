"""Bootloader image changed -> BL_VERSION_NUMBER changed (BL-32).

On 2026-09-29 the Eval Board and dev both reported `nextbsw-bl 0.3.4` and differed in 81 bytes: the same sources,
linked with two toolchains whose newlib archives order memset/memcpy differently. A bootloader in the field is known
only by its number, so every changed image needs a new one. bootloader/test/image_version.sha256 holds the hash of
the reference image (preset evalboard-s32k144-demo) per version; tools/elfcheck/blversion.py compares.

* Rule tests (no build): table format, unchanged / changed / missing / lower number / other compiler, image bytes
  with gaps, and that the table has an entry for the current BL_VERSION_NUMBER (a bump without hash is red at once).
* Linker scripts: library members sorted by section name (the fix of BL-32; without it the archive member order of
  the toolchain build decides the layout).
* Build test: bootloader.hex of the demo build against the table. Skipped without a build (run tools/test -k
  target-demo first) or with a compiler other than the one of the entry (other GCC, other code - not comparable).
  BL_IMAGE_HEX=<bootloader.hex> checks another file.

Run: .venv/Scripts/python -m unittest bootloader/test/test_bl_image_version.py
"""

import hashlib
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO / "tools" / "elfcheck"))
sys.path.insert(0, str(REPO / "tools" / "test"))
import blversion  # noqa: E402
import build_dirs  # noqa: E402

TABLE = HERE / "image_version.sha256"
CONFIG = REPO / "bootloader" / "include" / "bl_config.h"
LINKER_SCRIPTS = [REPO / "bootloader" / "platforms" / "s32k1xx" / "linker.ld",
                  REPO / "bootloader" / "platforms" / "stm32" / "linker.ld"]

H1, H2, H3 = "1" * 64, "2" * 64, "3" * 64


def table(*lines):
    return blversion.parse_table("\n".join(["# comment", ""] + list(lines)))


class TableTest(unittest.TestCase):
    def test_parse(self):
        entries = table(f"0.3.4 {H1} 11.3.1", f"0.3.5 {H2} 11.3.1  # note")
        self.assertEqual(list(entries), ["0.3.4", "0.3.5"])
        self.assertEqual(entries["0.3.5"].sha256, H2)
        self.assertEqual(entries["0.3.5"].compiler, "11.3.1")

    def test_version_twice_is_an_error(self):
        with self.assertRaisesRegex(ValueError, "twice"):
            table(f"0.3.4 {H1} 11.3.1", f"0.3.4 {H2} 11.3.1")

    def test_versions_ascend(self):
        with self.assertRaisesRegex(ValueError, "not above"):
            table(f"0.3.10 {H1} 11.3.1", f"0.3.9 {H2} 11.3.1")
        self.assertEqual(list(table(f"0.3.9 {H1} 11.3.1", f"0.3.10 {H2} 11.3.1")), ["0.3.9", "0.3.10"])

    def test_malformed_line(self):
        for line in (f"0.3 {H1} 11.3.1", "0.3.4 abc 11.3.1", f"0.3.4 {H1}", f"0.3.4 {'A' * 64} 11.3.1"):
            with self.subTest(line=line), self.assertRaisesRegex(ValueError, "expected"):
                table(line)

    def test_repository_table(self):
        """The table of the repository parses and has an entry for the current number - bumping
        BL_VERSION_NUMBER without appending the hash is red here already, without a build."""
        entries = blversion.parse_table(TABLE.read_text(encoding="utf-8"), TABLE.name)
        version = blversion.read_version(CONFIG)
        self.assertIn(version, entries, f"{TABLE.name} has no entry for BL_VERSION_NUMBER {version}: build the "
                      "demo preset and run tools/elfcheck blversion --add <bootloader.hex>")
        self.assertEqual(max(entries, key=blversion.version_key), version,
                         "the newest entry of the table must be the current BL_VERSION_NUMBER")
        digests = [e.sha256 for e in entries.values()]
        self.assertEqual(len(digests), len(set(digests)), "two versions with one image")

    def test_read_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bl_config.h"
            path.write_text('/* 0.9.9 */\n#define BL_VERSION_NUMBER "1.2.3"\n', encoding="utf-8")
            self.assertEqual(blversion.read_version(path), "1.2.3")
            path.write_text("#define BL_VERSION_NUMBER 3\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                blversion.read_version(path)


class RuleTest(unittest.TestCase):
    entries = table(f"0.3.4 {H1} 11.3.1", f"0.3.5 {H2} 11.3.1")

    def test_unchanged(self):
        self.assertEqual(blversion.evaluate("0.3.5", H2, "11.3.1", self.entries).code, blversion.EXIT_OK)

    def test_changed_image_same_number_is_red(self):
        result = blversion.evaluate("0.3.5", H3, "11.3.1", self.entries)
        self.assertEqual(result.code, blversion.EXIT_CHANGED)
        self.assertIn("BL_VERSION_NUMBER hochziehen", result.message)
        self.assertIn("still 0.3.5", result.message)

    def test_new_number_without_entry_names_the_line(self):
        result = blversion.evaluate("0.3.6", H3, "11.3.1", self.entries)
        self.assertEqual(result.code, blversion.EXIT_CHANGED)
        self.assertIn(f"0.3.6 {H3} 11.3.1", result.message)

    def test_number_must_not_go_down(self):
        result = blversion.evaluate("0.3.3", H3, "11.3.1", self.entries)
        self.assertEqual(result.code, blversion.EXIT_CHANGED)
        self.assertIn("only go up", result.message)

    def test_old_image_under_new_number(self):
        entries = table(f"0.3.4 {H1} 11.3.1", f"0.3.5 {H1} 11.3.1")
        self.assertEqual(blversion.evaluate("0.3.5", H1, "11.3.1", entries).code, blversion.EXIT_CHANGED)

    def test_other_compiler_not_comparable(self):
        result = blversion.evaluate("0.3.5", H3, "13.3.1", self.entries)
        self.assertEqual(result.code, blversion.EXIT_OTHER_COMPILER)

    def test_version_text_must_be_in_the_image(self):
        image = b"\x00nextbsw-bl 0.3.4\x00"
        self.assertEqual(blversion.evaluate("0.3.5", H2, "11.3.1", self.entries, image).code, blversion.EXIT_INPUT)
        image = b"\x00nextbsw-bl 0.3.5\x00"
        self.assertEqual(blversion.evaluate("0.3.5", H2, "11.3.1", self.entries, image).code, blversion.EXIT_OK)

    def test_image_bytes_fill_gaps(self):
        self.assertEqual(blversion.image_bytes({0x10: 1, 0x13: 4}), b"\x01\xff\xff\x04")
        with self.assertRaises(ValueError):
            blversion.image_bytes({})

    def test_add_appends_but_never_rewrites(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            hex_file = tmp / "bl.hex"
            data = b"nextbsw-bl 0.3.6"
            write_hex(hex_file, data)
            config = tmp / "bl_config.h"
            config.write_text('#define BL_VERSION_NUMBER "0.3.6"\n', encoding="utf-8")
            tab = tmp / "table.sha256"
            tab.write_text(f"0.3.5 {H2} {self.compiler()}\n", encoding="utf-8")
            args = [str(hex_file), "--config", str(config), "--table", str(tab), "--compiler", self.fake_compiler(tmp)]
            self.assertEqual(blversion.main(args), blversion.EXIT_CHANGED)
            self.assertEqual(blversion.main(args + ["--add"]), blversion.EXIT_OK)
            digest = hashlib.sha256(data).hexdigest()
            self.assertTrue(tab.read_text(encoding="utf-8").endswith(f"0.3.6 {digest} {self.compiler()}\n"))
            self.assertEqual(blversion.main(args), blversion.EXIT_OK)
            # the image changes, the number stays: red, and --add leaves the entry as it was
            write_hex(hex_file, data + b"!")
            before = tab.read_text(encoding="utf-8")
            self.assertEqual(blversion.main(args + ["--add"]), blversion.EXIT_CHANGED)
            self.assertEqual(tab.read_text(encoding="utf-8"), before)

    @staticmethod
    def compiler():
        return "9.9.9"

    def fake_compiler(self, tmp: Path) -> str:
        """A 'compiler' that answers -dumpversion with 9.9.9 (a batch file on Windows, a script elsewhere)."""
        if os.name == "nt":
            path = tmp / "gcc.bat"
            path.write_text(f"@echo {self.compiler()}\n", encoding="ascii")
        else:
            path = tmp / "gcc"
            path.write_text(f"#!/bin/sh\necho {self.compiler()}\n", encoding="ascii")
            path.chmod(0o755)
        return str(path)


class LinkerScriptTest(unittest.TestCase):
    def test_library_members_sorted(self):
        """BL-32: without sorting, ld places archive members in the order it pulls them, i.e. in the member order
        of the toolchain's archives (NXP GCC 11.4 and xPack GCC 11.3.1 swap memset and memcpy)."""
        for script in LINKER_SCRIPTS:
            with self.subTest(script=script.relative_to(REPO).as_posix()):
                text = re.sub(r"/\*.*?\*/", "", script.read_text(encoding="utf-8"), flags=re.S)
                self.assertRegex(text, r"\*\(EXCLUDE_FILE\(\*\.a:\*\)\s*\.text\*\)")
                self.assertRegex(text, r"\*\.a:\*\(SORT_BY_NAME\(\.text\*\)\)")
                self.assertNotRegex(text, r"^\s*\*\(\.text\*\)", "unsorted *(.text*) left in .text")


class BuildImageTest(unittest.TestCase):
    def test_demo_bootloader_matches_its_version(self):
        hex_path = Path(os.environ.get("BL_IMAGE_HEX") or build_dirs.bootloader_hex(REPO))
        if not hex_path.is_file():
            self.skipTest(f"no bootloader.hex ({hex_path}) - run tools/test -k target-demo first")
        compiler = compiler_of_build(hex_path)
        version = blversion.read_version(CONFIG)
        digest, data = blversion.image_sha256(hex_path)
        entries = blversion.parse_table(TABLE.read_text(encoding="utf-8"), TABLE.name)
        result = blversion.evaluate(version, digest, blversion.compiler_version(compiler), entries, data, TABLE.name)
        if result.code == blversion.EXIT_OTHER_COMPILER:
            self.skipTest(result.message)
        self.assertEqual(result.code, blversion.EXIT_OK, f"{hex_path}: {result.message}")


def write_hex(path: Path, data: bytes) -> None:
    """Intel hex with one data record at address 0."""
    record = bytes([len(data), 0, 0, 0]) + data
    path.write_text(f":{record.hex().upper()}{(-sum(record)) & 0xFF:02X}\n:00000001FF\n", encoding="ascii")


def compiler_of_build(hex_path: Path) -> str:
    """CMAKE_C_COMPILER of the build directory the hex file belongs to (<build>/bootloader/<config>/bootloader.hex),
    else arm-none-eabi-gcc from PATH."""
    for parent in hex_path.resolve().parents:
        cache = parent / "CMakeCache.txt"
        if cache.is_file():
            match = re.search(r"^CMAKE_C_COMPILER:\w+=(.+)$", cache.read_text(encoding="utf-8", errors="replace"),
                              re.M)
            if match and Path(match.group(1).strip()).is_file():
                return match.group(1).strip()
            break
    return "arm-none-eabi-gcc"


if __name__ == "__main__":
    unittest.main()
