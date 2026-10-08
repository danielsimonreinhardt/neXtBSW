"""Consistency of the derivative database (mcudb/<family>/*.yaml with their pin multiplexing tables).

Run: .venv/Scripts/python -m unittest mcudb/tools/test_mcudb.py
"""

import re
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

MCUDB = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MCUDB.parent / "tools"))

from nxconfig import checks, config as cfgmod, model  # noqa: E402

# Packages without a pin multiplexing source (reported in ARCH-11): hal_nxp has no S32K344 LQFP100 file.
NO_SOURCE = {("S32K344", "LQFP100")}

# ARCH-36: markers of a source README, in German (older READMEs) or English (language rule 2026-09-17). Same statement
# in both languages: "may not be redistributed" and "licence finding".
NO_REDISTRIBUTION = re.compile(r"\b(?:Weitergabe|Redistribution)\s*:\s*(?:nein|no)\b", re.IGNORECASE)
LICENCE_FINDING = re.compile(r"\b(?:Lizenzbefund|Licen[cs]e finding)\b", re.IGNORECASE)
ORIGIN_COMMIT = re.compile(r"\bCommit\b", re.IGNORECASE)

# ARCH-36 (PLAT-46 F8): mcudb values need public evidence. The device headers and linker files of the vendor IDE
# S32 Design Studio may not be redistributed; citing them ("[S32DS] S32K144.h ...", "S32DS FEATURE_...") is an error.
# Only the IDE's copy of the public reference manual may still be named by its path.
S32DS_RM_PATH = re.compile(r"[A-Za-z]:/\S*?S32DS\S*?/help/resources/\S*")
S32DS = re.compile(r"S32DS", re.IGNORECASE)
# Files that still cite the IDE files (number of citations); the count may only go down. Empty since ARCH-39
# (S32K116 and S32K344 cleaned) - do not add entries: a new citation has to be replaced, not listed.
S32DS_KNOWN = {}


def s32ds_citations(text):
    """Lines that cite S32 Design Studio files (the public reference manual path in the IDE does not count)."""
    return [line.strip() for line in text.splitlines() if S32DS.search(S32DS_RM_PATH.sub("", line))]


def derivatives():
    for file in sorted(MCUDB.glob("*/*.yaml")):
        # derivative files are named <DERIVATIVE>.yaml; tables and rule files are not
        if file.name.endswith(".pinmux.yaml") or file.stem != file.stem.upper():
            continue
        rep = cfgmod.Reporter()
        yield file, cfgmod._read(rep, "derivative", file, model.Derivative), rep


class McudbTest(unittest.TestCase):
    def test_every_package_passes_the_derivative_checks(self):
        count = 0
        for file, d, rep in derivatives():
            self.assertIsNotNone(d, [str(m) for m in rep.messages])
            for package in d.derivative.packages:
                with self.subTest(derivative=d.derivative.name, package=package):
                    cfg = SimpleNamespace(derivative=d, derivative_file=file,
                                          board=SimpleNamespace(board=SimpleNamespace(package=package)))
                    rep = cfgmod.Reporter()
                    patterns = checks._derivative(cfg, rep)
                    self.assertEqual([str(m) for m in rep.messages if m.severity == "error"], [])
                    self.assertEqual(patterns.pinmux is None, (d.derivative.name, package) in NO_SOURCE)
                    count += 1
        # PLAT-11: + S32K116 LQFP48/QFN32, PLAT-14: + STM32F446RE LQFP64, PLAT-36: + RM46L852 LQFP144/NFBGA337
        # TOOL-244: the families stm32, rm4x and s32k3xx are private (7 packages) and missing in the exported open tree
        private = [f for f in ("stm32", "rm4x", "s32k3xx") if (MCUDB / f).is_dir()]
        self.assertEqual(count, 17 + (7 if len(private) == 3 else 0), private)

    def test_every_table_is_used_once(self):
        tables = {p.relative_to(MCUDB).as_posix() for p in MCUDB.glob("*/*.pinmux.yaml")}
        used = [(file.parent / ref.file).relative_to(MCUDB).as_posix()
                for file, d, _ in derivatives() for ref in d.pinmux.values() if ref.file]
        self.assertEqual(sorted(used), sorted(tables))

    def test_sources_carry_licence_and_origin(self):
        for folder in MCUDB.glob("*/sources/*"):
            with self.subTest(folder=folder.relative_to(MCUDB).as_posix()):
                readme = (folder / "README.md").read_text(encoding="utf-8")
                if NO_REDISTRIBUTION.search(readme):
                    # PLAT-11: source that must not be redistributed (licence unclear); only the README is checked in,
                    # it has to name the download, the licence finding and the checksum of the file
                    self.assertFalse((folder / "LICENSE").is_file())
                    self.assertEqual(sorted(p.name for p in folder.iterdir()), ["README.md"])
                    self.assertRegex(readme, LICENCE_FINDING)
                    for expected in ("SHA-256", "http"):
                        self.assertIn(expected, readme)
                else:
                    self.assertTrue((folder / "LICENSE").is_file())
                    self.assertRegex(readme, ORIGIN_COMMIT)

    def test_readme_markers_in_both_languages(self):
        """ARCH-36: the README markers mean the same in German and English, and nothing weaker matches."""
        for text in ("**Weitergabe: nein** – nur diese Anleitung", "**Redistribution: no** - only this guide",
                     "redistribution : NO"):
            self.assertRegex(text, NO_REDISTRIBUTION)
        for text in ("Weitergabe: ja", "Redistribution: yes", "Redistribution: not decided", "no redistribution",
                     "Redistribution: none of the files is modified"):
            self.assertNotRegex(text, NO_REDISTRIBUTION)
        for text in ("## Lizenzbefund (2026-09-16)", "## Licence finding (2026-09-16)", "## License finding"):
            self.assertRegex(text, LICENCE_FINDING)
        for text in ("Lizenz: Apache-2.0", "Licence: Apache-2.0", "licence text in LICENSE"):
            self.assertNotRegex(text, LICENCE_FINDING)
        self.assertRegex("Herkunft: ..., Commit `bb740b2`", ORIGIN_COMMIT)
        self.assertRegex("Origin: ..., commit `bb740b2`", ORIGIN_COMMIT)
        self.assertNotRegex("Origin: https://github.com/zephyrproject-rtos/hal_nxp", ORIGIN_COMMIT)

    def test_no_evidence_from_confidential_vendor_files(self):
        """ARCH-36 (PLAT-46 F8): no value in mcudb may cite the S32 Design Studio device headers or linker files."""
        seen = {}
        for file in sorted(MCUDB.rglob("*.yaml")):
            rel = file.relative_to(MCUDB).as_posix()
            found = s32ds_citations(file.read_text(encoding="utf-8"))
            if found or rel in S32DS_KNOWN:
                seen[rel] = len(found)
            with self.subTest(file=rel):
                if rel in S32DS_KNOWN:
                    self.assertLessEqual(len(found), S32DS_KNOWN[rel], found)
                else:
                    self.assertEqual(found, [], "citation of S32DS files - replace by a public source "
                                                 "(doc/guide/new-derivative.md, 'The rule on sources')")
        # a known entry that got cleaner has to shrink the list, so it cannot hide a new citation later
        self.assertEqual(seen, S32DS_KNOWN)

    def test_s32ds_citation_detection(self):
        """ARCH-36: the check finds tag and plain mentions, but not the IDE path of the public reference manual."""
        cited = ("nvic_priority_bits: 4   # PLAT-10: [S32DS] S32K144.h:307 __NVIC_PRIO_BITS 4",
                 'source: "[RM] 36.1.2 4 KB; [s32ds] FEATURE_FLS_FLEX_RAM_START_ADDRESS/SIZE"',
                 "#   [S32DS]  S32 Design Studio 3.6.9, Plugin com.nxp.s32ds.cle.s32k1.s32k146.dev_3.6.6",
                 "(nur gelesen; S32DS FEATURE_FLS_DF_START_ADDRESS)")
        for line in cited:
            self.assertEqual(s32ds_citations(line), [line.strip()])
        public = ("#           C:/ide/S32DS.3.6.9/S32DS/help/resources/manuals/k1dev/S32K-RM.pdf) - Kapitel",
                  "#   [RM27] Reference Manual Rev. 14 (09/2021, lokal C:/ide/S32DS.3.6.9/S32DS/help/resources/",
                  '#   [MAP]   S32K1xx_Memory_Map.xlsx, Dateianhang des [RM13]')
        for line in public:
            self.assertEqual(s32ds_citations(line), [])
        self.assertEqual(len(s32ds_citations("C:/ide/S32DS.3.6.9/S32DS/help/resources/x.pdf; [S32DS] S32K148.h")), 1)

    def test_mpu_region_count_of_every_s32k1_derivative(self):
        """ARCH-16: the MPU region descriptors are a derivative value with evidence, not a constant in the code.

        Values from the S32K1xx Reference Manual Rev. 14 (09/2021), Table 15-4 "MPU configurations"; the S32K148
        is the only S32K1 with 16 (finding ARCH-09 B3).
        """
        expected = {"S32K116": 8, "S32K142": 8, "S32K144": 8, "S32K146": 8, "S32K148": 16}
        seen = {}
        for file, d, _ in derivatives():
            if d.derivative.family != "s32k1xx":
                continue
            name = d.derivative.name
            with self.subTest(derivative=name):
                self.assertIsNotNone(d.mpu, "s32k1xx derivative without mpu entry (ARCH-16)")
                self.assertIn("Table 15-4", d.mpu.source or "")     # evidence per entry, like ARCH-11/PLAT-22
                self.assertIn("NRGD", d.mpu.source or "")
                seen[name] = d.mpu.regions
        self.assertEqual(seen, expected)

    def test_s32k148_lqfp100_has_no_quadspi(self):
        """Data sheet footnote: QuadSPI is not supported for S32K148 in 100-pin LQFP; the tables agree."""
        file = MCUDB / "s32k1xx" / "S32K148.yaml"
        d = cfgmod._read(cfgmod.Reporter(), "derivative", file, model.Derivative)

        def prefixes(package):
            cfg = SimpleNamespace(derivative=d, derivative_file=file,
                                  board=SimpleNamespace(board=SimpleNamespace(package=package)))
            return {s.split("_")[0] for s in checks.pinmux(cfg).signals}
        self.assertNotIn("QSPI", prefixes("LQFP100"))
        self.assertIn("QSPI", prefixes("LQFP144"))


if __name__ == "__main__":
    unittest.main()
