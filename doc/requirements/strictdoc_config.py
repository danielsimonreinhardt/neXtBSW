# SPDX-License-Identifier: Apache-2.0
"""StrictDoc project configuration of the neXtBSW requirements (DOC-21, StrictDoc 0.30.1, Python config format).

Exported by tools/trace, never by hand into the working tree (the cache and the output go to temporary folders):

    python tools/trace export --out <dir>         JSON export, the input of the checks (fast, no source scan)
    python tools/trace html --out <dir>           HTML export with the scan of the @relation markers in the code

The source scan is slow (minutes over the source roots), so it is switched on only when the environment variable
NX_SDOC_SOURCE_SCAN is set; tools/trace sets it for the HTML export (tools/test entry `requirements-html`, --slow).
The proof of concept of step 1 (poc/) has its own grammar and configuration and is not part of this project.
"""

import os

from strictdoc.core.project_config import ProjectConfig

# Source roots scanned for @relation markers (relative to the repository root, the source_root_path below).
SOURCE_ROOTS = ["ecu", "platforms", "libs", "bootloader", "tools", "cmake", "swc"]
SOURCE_SUFFIXES = ["c", "h", "cpp", "hpp", "S", "s", "py", "cmake", "j2", "ld"]
SOURCE_EXCLUDES = ["**/generated/**", "**/build/**", "**/__pycache__/**", "tools/trace/**", "external/**"]


def create_config() -> ProjectConfig:
    features = ["TABLE_SCREEN", "TRACEABILITY_SCREEN", "DEEP_TRACEABILITY_SCREEN"]
    options = {}
    if os.environ.get("NX_SDOC_SOURCE_SCAN"):
        features += ["REQUIREMENT_TO_SOURCE_TRACEABILITY", "SOURCE_FILE_LANGUAGE_PARSERS"]
        options = {
            "source_root_path": "../..",
            "include_source_paths": [f"{root}/**/*.{suffix}" for root in SOURCE_ROOTS for suffix in SOURCE_SUFFIXES],
            "exclude_source_paths": SOURCE_EXCLUDES,
        }
    return ProjectConfig(
        project_title="neXtBSW requirements",
        project_features=features,
        include_doc_paths=["**.sdoc", "**.sgra"],
        exclude_doc_paths=["poc/**"],
        grammars={"@nextbsw": "nextbsw.sgra"},   # documents in sub-folders: IMPORT_FROM_FILE: @nextbsw
        dir_for_sdoc_cache="$TMPDIR",
        **options,
    )
