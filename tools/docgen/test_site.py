# SPDX-License-Identifier: Apache-2.0
"""Documentation website (DOC-71): `mkdocs build --strict` of the public site, built in the exported tree.

1. The real site: the repository is exported to a temp folder (tools/distcheck, profile open) and built there with
   --strict - no dead link, no dead anchor, no warning. Checked in the HTML: Mermaid blocks arrive as
   <pre class="mermaid"> with the B1 init line, private pages (review records, traceability matrix, licence paper,
   ...) are not in the site, the links to them carry the DOC-73 note, the DE/EN switch exists, doc/confidential is
   nowhere. Every page in the nav of mkdocs.yml is a published page.
2. Counter-tests on a small tree with the same mkdocs.yml (only the nav replaced): a broken link, a broken anchor and
   a link to a private page each make the strict build fail; doc/confidential is never staged, even if the rules
   would open it; a link to a folder goes to its README page, or becomes text plus path.

Skipped without the packages of tools/requirements-site.txt (tests.yaml: requires mkdocs).
"""

from __future__ import annotations

import importlib.util
import re
import shutil
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from tools.docgen import site          # noqa: E402

PACKAGES = ("mkdocs", "material", "mkdocs_static_i18n", "pymdownx")
HAVE_MKDOCS = all(importlib.util.find_spec(name) is not None for name in PACKAGES)
SKIP = "mkdocs, mkdocs-material, mkdocs-static-i18n missing: pip install -r tools/requirements-site.txt"

# private pages of distribution.yaml that must not appear in the public site
PRIVATE = [
    "doc/safety/reviews", "doc/safety/traceability", "doc/safety/iso26262-gap", "doc/safety/rtos-comparison",
    "doc/architecture/lizenz-und-release", "doc/architecture/fast-control-loop", "doc/architecture/multi-mcu-flash",
    "doc/requirements/migration-plan", "doc/requirements/poc", "doc/todo", "doc/TODO", "doc/briefs", "doc/changelog.d", "doc/koordinator-stand",
    "doc/hardware", "doc/confidential", "tools/studio",
]


def nav_pages(config_text: str) -> list[str]:
    """The Markdown files named in the nav of a mkdocs.yml (text after `nav:`)."""
    nav = config_text.split("\nnav:", 1)[1] if "\nnav:" in config_text else ""
    return re.findall(r"([\w./-]+\.md)\s*$", nav, flags=re.MULTILINE)


@unittest.skipUnless(HAVE_MKDOCS, SKIP)
class PublicSite(unittest.TestCase):
    """The site of the open export, strict."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="nx-site-test-"))
        cls.tree = REPO if site.is_export(REPO) else site.export_tree(REPO, cls.tmp / "export")
        cls.out = cls.tmp / "site"
        cls.done = site.build(cls.tree, cls.out, strict=True)
        cls.log = cls.done.stdout + cls.done.stderr

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def html(self, rel: str) -> str:
        path = self.out / rel
        self.assertTrue(path.is_file(), f"{rel} missing in the site")
        return path.read_text(encoding="utf-8")

    def test_strict_build_without_warnings(self):
        warnings = [line for line in self.log.splitlines() if line.startswith(("WARNING", "ERROR"))]
        self.assertEqual(self.done.returncode, 0, "\n".join(warnings) or self.log[-3000:])
        self.assertEqual(warnings, [])

    def test_mermaid_blocks_arrive_with_the_b1_init_line(self):
        for rel in ("doc/index.html", "doc/figures/index.html", "doc/figures/03-startablauf/index.html"):
            text = self.html(rel)
            self.assertIn('<pre class="mermaid">', text, rel)
            block = text.split('<pre class="mermaid">', 1)[1].split("</pre>", 1)[0]
            self.assertIn("%%{init: {'theme':'base'", block, rel)
        self.assertIn("classDef grp fill:#f8fafc,stroke:#cbd5e1,rx:8,ry:8", self.html("doc/index.html"))
        # a code block that is no Mermaid stays a highlighted code block
        self.assertNotIn('<pre class="mermaid"><code>```', self.html("doc/index.html"))

    def test_private_pages_are_not_in_the_site(self):
        for rel in PRIVATE:
            for lang in ("", "en/"):
                self.assertFalse((self.out / f"{lang}{rel}").exists(), f"{lang}{rel} is in the site")
                self.assertFalse((self.out / f"{lang}{rel}.html").exists(), f"{lang}{rel}.html is in the site")
        names = {p.relative_to(self.out).as_posix() for p in self.out.rglob("*")}
        self.assertFalse([n for n in names if "doc/confidential" in n])

    def test_links_to_private_pages_carry_the_export_note(self):
        # doc/README.md links the traceability matrix (private): the export replaced the link by text + note (DOC-73)
        text = self.html("doc/index.html")
        self.assertIn("not published, available on request", text)
        self.assertNotIn("traceability/", text)

    def test_language_switch(self):
        self.assertTrue((self.out / "en" / "index.html").is_file())
        self.assertTrue((self.out / "en" / "doc" / "safety" / "safety-manual" / "index.html").is_file())
        self.assertIn('hreflang="en"', self.html("index.html"))
        self.assertIn('lang="de"', self.html("index.html"))
        self.assertIn('lang="en"', self.html("en/index.html"))

    def test_module_documents_and_readme_are_pages(self):
        for rel in ("index.html", "libs/bsw/e2e/doc/e2e/index.html", "bootloader/doc/index.html",
                    "tools/test/index.html"):
            self.html(rel)

    def test_search_index(self):
        self.assertTrue((self.out / "search" / "search_index.json").is_file())

    def test_excluded_pages_are_nowhere_in_the_site(self):
        # DOC-83: project log, agent rules and the STM32 detail pages: no page, no search hit, no nav entry
        # (the short page doc/architecture/stm32-nucleo.md stays, status planned)
        for lang in ("", "en/"):
            for rel in ("CHANGELOG", "CLAUDE", "platforms/stm32", "mcudb/stm32", "boards/nucleo_f446re"):
                self.assertFalse((self.out / f"{lang}{rel}").exists(), f"{lang}{rel} is in the site")
        self.html("doc/architecture/stm32-nucleo/index.html")
        index = (self.out / "search" / "search_index.json").read_text(encoding="utf-8")
        for needle in ('"location": "CHANGELOG/', '"location": "CLAUDE/', '"location": "platforms/stm32',
                       '"location": "boards/nucleo_f446re'):
            self.assertNotIn(needle, index)
        home = self.html("index.html")
        for needle in ('href="CHANGELOG/', 'href="CLAUDE/', 'nucleo_f446re/"'):
            self.assertNotIn(needle, home)
        # also not in the 'not included in nav' list of the build
        self.assertNotIn("CHANGELOG.md", self.log)
        self.assertNotIn("CLAUDE.md", self.log)

    def test_third_party_and_issue_pages_are_excluded_and_module_pages_are_in_nav(self):
        # DOC-88: no third-party README/licence pages, no per-issue write-ups; the module READMEs are pages
        index = (self.out / "search" / "search_index.json").read_text(encoding="utf-8")
        for rel in ("platforms/common/3rdparty", "platforms/rm4x/3rdparty", "platforms/s32k1xx/3rdparty",
                    "platforms/s32k1xx/bsp/bspMcu/include/3rdparty", "mcudb/s32k1xx/sources", "mcudb/s32k3xx", "mcudb/rm4x",
                    "doc/openbsw-beitraege/01-mpu-region-count", "bootloader/test/layouts"):
            for lang in ("", "en/"):
                self.assertFalse((self.out / f"{lang}{rel}").exists(), f"{lang}{rel} is in the site")
            self.assertNotIn(f'"location": "{rel}', index)
        for rel in ("doc/openbsw-beitraege", "platforms/common/clockCheck", "platforms/common/interruptPriority",
                    "platforms/s32k1xx/bsp/bspCsec", "platforms/s32k1xx/bsp/bspMcu", "platforms/s32k1xx/startup",
                    "mcudb"):
            self.html(f"{rel}/index.html")
        # nothing is left in the 'not included in nav' list of the build
        self.assertNotRegex(self.log, r"(?i)pages exist in the docs directory, but are not included")


class Navigation(unittest.TestCase):
    """Every page of the nav is a published page (open profile), so the export build finds it."""

    def test_nav_pages_are_published(self):
        pages = set(site.plan(REPO).pages)
        nav = nav_pages((REPO / site.CONFIG).read_text(encoding="utf-8"))
        self.assertGreater(len(nav), 50)
        self.assertEqual([p for p in nav if p not in pages], [])
        self.assertEqual(len(nav), len(set(nav)), "a page appears twice in the nav")

    def test_never_staged(self):
        result = site.plan(REPO)
        staged = result.pages + result.assets
        self.assertFalse([p for p in staged if p.startswith("doc/confidential")])
        for rel in ("doc/safety/traceability.md", "doc/architecture/lizenz-und-release.md", "doc/todo.yaml",
                    "doc/koordinator-stand.md", "CLAUDE.md"):
            if not result.exported:
                self.assertNotIn(rel, staged)
        self.assertFalse([p for p in staged if p.startswith(("doc/safety/reviews/", "tools/studio/"))])

    def test_site_exclusions_are_not_pages_or_nav_entries(self):
        # DOC-83: stay in the release repository, never a page of the site
        result = site.plan(REPO)
        staged = result.pages + result.assets
        self.assertFalse([p for p in staged if p in ("CHANGELOG.md", "CLAUDE.md")
                          or p.startswith(("platforms/stm32/", "mcudb/stm32/", "boards/nucleo_f446re/"))])
        self.assertIn("doc/architecture/stm32-nucleo.md", result.pages)
        config = (REPO / site.CONFIG).read_text(encoding="utf-8")
        nav = nav_pages(config)
        self.assertFalse([p for p in nav if site._matches(p, site.SITE_EXCLUDE)])
        for rel in ("platforms/common/clockCheck/README.md", "platforms/common/interruptPriority/README.md",
                    "platforms/s32k1xx/bsp/bspCsec/README.md", "platforms/s32k1xx/bsp/bspMcu/README.md",
                    "platforms/s32k1xx/startup/README.md", "mcudb/README.md"):
            self.assertIn(rel, nav)
        # counter-check: the exclusion matches third-party and issue pages only, not the module pages next to them
        self.assertTrue(site._matches("platforms/s32k1xx/3rdparty/freertosPort/README.md", site.SITE_EXCLUDE))
        self.assertTrue(site._matches("doc/openbsw-beitraege/01-mpu-region-count/issue.md", site.SITE_EXCLUDE))
        self.assertFalse(site._matches("platforms/s32k1xx/bsp/bspMcu/README.md", site.SITE_EXCLUDE))
        self.assertFalse(site._matches("doc/openbsw-beitraege/README.md", site.SITE_EXCLUDE))
        head = config.split("\nnav:", 1)[0]
        for pattern in site.SITE_EXCLUDE:       # second barrier in mkdocs.yml (exclude_docs) names the same paths
            self.assertIn(pattern.removesuffix("**").rstrip("/"), head)


MINI_RULES = """\
version: 1
profiles:
  open: { licenses: [Apache-2.0] }
paths:
  - { glob: "**", class: private }
  - { glob: "doc/**", class: open, license: Apache-2.0 }
  - { glob: "mkdocs.yml", class: open, license: Apache-2.0 }
  - { glob: "README.md", class: open, license: Apache-2.0 }
  - { glob: "doc/private.md", class: private }
  - { glob: "CHANGELOG.md", class: open, license: Apache-2.0 }
  - { glob: "platforms/**", class: open, license: Apache-2.0 }
"""


@unittest.skipUnless(HAVE_MKDOCS, SKIP)
class CounterTests(unittest.TestCase):
    """The same mkdocs.yml (only the nav replaced) on a small tree: what --strict has to catch."""

    MERMAID = textwrap.dedent("""\
        ```mermaid
        %%{init: {'theme':'base','themeVariables':{'clusterBkg':'#f8fafc'}}}%%
        flowchart LR
          A("a") --> B("b")
            classDef grp fill:#f8fafc,stroke:#cbd5e1,rx:8,ry:8
        ```
        """)

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="nx-site-counter-"))
        self.root = self.tmp / "tree"
        config = (REPO / site.CONFIG).read_text(encoding="utf-8").split("\nnav:", 1)[0]
        hook = (REPO / "tools" / "docgen" / "mkdocs_hooks.py").as_posix()
        config = config.replace("  - tools/docgen/mkdocs_hooks.py", f"  - {hook}")
        self.write("mkdocs.yml", config + "\nnav:\n  - Start: README.md\n  - doc/README.md\n  - doc/guide/README.md\n")
        self.write("tools/distcheck/distribution.yaml", MINI_RULES)
        self.write("README.md", "# Home\n\n[documentation](doc/README.md)\n")
        self.write("doc/README.md", "# Start\n\n" + self.MERMAID + "\n[guide](guide/README.md#setup)\n"
                   "[the guide folder](guide/)\n[code folder](code/)\n")
        self.write("doc/guide/README.md", "# Guide\n\n## Setup\n\ntext\n")
        self.write("doc/code/main.c", "int main(void) { return 0; }\n")
        self.write("doc/private.md", "# Private\n")
        self.write("doc/confidential/standard.md", "# must never be staged\n")
        self.write("CHANGELOG.md", "# Changelog\n\nneedle-changelog-text\n")
        self.write("platforms/stm32/README.md", "# STM32 platform\n\nneedle-stm32-text\n")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, rel: str, text: str) -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def build(self):
        done = site.build(self.root, self.tmp / "site", strict=True)
        return done.returncode, done.stdout + done.stderr

    def test_clean_tree_builds_and_rewrites_folder_links(self):
        code, log = self.build()
        self.assertEqual(code, 0, log[-3000:])
        html = (self.tmp / "site" / "doc" / "index.html").read_text(encoding="utf-8")
        self.assertIn('<pre class="mermaid">', html)
        self.assertIn("%%{init:", html)
        self.assertIn('href="guide/"', html)                       # folder link -> its README page
        self.assertIn("code folder (repository: <code>doc/code</code>)", html)
        self.assertFalse((self.tmp / "site" / "doc/confidential").exists())
        self.assertFalse((self.tmp / "site" / "doc" / "private").exists())

    def test_confidential_is_never_staged_even_if_the_rules_open_it(self):
        result = site.plan(self.root)
        self.assertIn("doc/README.md", result.pages)
        self.assertNotIn("doc/confidential/standard.md", result.pages)
        self.assertNotIn("doc/private.md", result.pages)

    def test_excluded_files_have_no_page_nav_entry_or_search_hit(self):
        # DOC-83: open in the profile, yet no page; a link to them becomes text plus path (the build stays strict)
        self.write("doc/guide/README.md", "# Guide\n\n## Setup\n\n[log](../../CHANGELOG.md) and "
                   "[stm32](../../platforms/stm32/README.md)\n")
        result = site.plan(self.root)
        self.assertNotIn("CHANGELOG.md", result.pages)
        self.assertNotIn("platforms/stm32/README.md", result.pages)
        code, log = self.build()
        self.assertEqual(code, 0, log[-3000:])
        out = self.tmp / "site"
        for lang in ("", "en/"):
            self.assertFalse((out / f"{lang}CHANGELOG").exists())
            self.assertFalse((out / f"{lang}platforms").exists())
        html = (out / "doc" / "guide" / "index.html").read_text(encoding="utf-8")
        self.assertIn("repository: <code>CHANGELOG.md</code>", html)
        index = (out / "search" / "search_index.json").read_text(encoding="utf-8")
        self.assertNotIn("needle-changelog-text", index)
        self.assertNotIn("needle-stm32-text", index)

    def test_nav_entry_of_an_excluded_page_fails(self):
        config = (self.root / "mkdocs.yml").read_text(encoding="utf-8")
        self.write("mkdocs.yml", config + "  - CHANGELOG.md\n")
        code, log = self.build()
        self.assertNotEqual(code, 0)
        self.assertIn("CHANGELOG.md", log)

    def test_broken_link_fails(self):
        self.write("doc/guide/README.md", "# Guide\n\n## Setup\n\n[gone](missing-page.md)\n")
        code, log = self.build()
        self.assertNotEqual(code, 0)
        self.assertIn("missing-page.md", log)

    def test_broken_anchor_fails(self):
        self.write("doc/guide/README.md", "# Guide\n\n## Set-up\n\ntext\n")
        code, log = self.build()
        self.assertNotEqual(code, 0)
        self.assertIn("#setup", log)

    def test_link_to_a_private_page_fails(self):
        # in the development tree a link to a private page is dead; only the export replaces it (DOC-73)
        self.write("doc/guide/README.md", "# Guide\n\n## Setup\n\n[internal](../private.md)\n")
        code, log = self.build()
        self.assertNotEqual(code, 0)
        self.assertIn("private.md", log)

    def test_nav_entry_of_an_unpublished_page_fails(self):
        config = (self.root / "mkdocs.yml").read_text(encoding="utf-8")
        self.write("mkdocs.yml", config + "  - doc/private.md\n")
        code, log = self.build()
        self.assertNotEqual(code, 0)
        self.assertIn("private.md", log)


if __name__ == "__main__":
    unittest.main()
