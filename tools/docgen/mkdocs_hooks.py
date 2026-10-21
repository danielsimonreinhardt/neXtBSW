# SPDX-License-Identifier: Apache-2.0
"""MkDocs hook of the documentation website (DOC-71), named in mkdocs.yml under `hooks:`.

Stages the published pages of the tree (tools/docgen/site.py: open profile of tools/distcheck/distribution.yaml,
never doc/confidential) with their repository paths into a temporary folder and makes it the `docs_dir`, before any
plugin (i18n, search) looks at the files. `mkdocs build` removes the folder after the build, `mkdocs serve` at the end
and additionally watches the real sources.
"""

from __future__ import annotations

import logging
import shutil
import sys
import tempfile
from pathlib import Path

from mkdocs.plugins import event_priority

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.docgen import sanitize, site          # noqa: E402

log = logging.getLogger("mkdocs.hooks.nextbsw")
_state: dict[str, object] = {"command": "build", "staging": []}


def on_startup(command: str, dirty: bool) -> None:
    _state["command"] = command


@event_priority(100)          # before mkdocs-static-i18n and search read docs_dir
def on_config(config):
    root = Path(config.config_file_path).resolve().parent
    if _state["command"] == "build" and _state.get("plan") is not None and _state.get("root") == root:
        # mkdocs-static-i18n builds the second language with the same config: the folder is already staged
        config.docs_dir = str(_state["staging"][-1])
        return config
    staging = Path(tempfile.mkdtemp(prefix="nx-site-docs-"))
    result = site.stage(root, staging)
    _state["staging"].append(staging)
    _state["plan"] = result
    _state["root"] = root
    log.info("neXtBSW: %d page(s) and %d linked file(s) staged from the %s",
             len(result.pages), len(result.assets), "export" if result.exported else "development tree")
    config.docs_dir = str(staging)
    if _state["command"] == "serve":
        for sub in ("doc", "libs", "bootloader", "README.md"):
            if (root / sub).exists() and str(root / sub) not in config.watch:
                config.watch.append(str(root / sub))
    return config


def on_page_markdown(markdown: str, page, config, files) -> str:
    """Links to folders and other repository paths that are no site page (site.rewrite_links), then the internal
    traces out of the rendered text (tools/docgen/sanitize.py: task IDs, CLAUDE.md; the sources stay unchanged)."""
    plan = _state.get("plan")
    if plan is None:
        return markdown
    source_url = str((config.extra.get("nextbsw") or {}).get("source_url") or "")
    return sanitize.clean_page(site.rewrite_links(page.file.src_uri, markdown, plan, source_url),
                               path=page.file.src_uri)


def _cleanup(keep_last: bool) -> None:
    staged = _state["staging"]
    while len(staged) > (1 if keep_last else 0):
        shutil.rmtree(staged.pop(0), ignore_errors=True)


def on_post_build(config) -> None:
    # serve: the folders of earlier rebuilds go, the one being watched stays until the next rebuild or the end.
    # build: nothing here - mkdocs-static-i18n builds the second language from on_post_build with the same folder
    if _state["command"] == "serve":
        _cleanup(keep_last=True)


def on_shutdown() -> None:
    _cleanup(keep_last=False)
