# SPDX-License-Identifier: Apache-2.0
"""Copy check of neXtBSW against Eclipse OpenBSW (TOOL-21, proposal V4 of doc/architecture/openbsw-naehe.md).

Parts of neXtBSW are copies of the OpenBSW reference application (ecu/ from executables/referenceApp, the family
port from referenceApp/platforms/s32k148evb). tools/openbswsync/copies.yaml records every such copy with its
original, its status (identical, modified with task id and reason, new) and the hash of the original at the time
the copy was last compared. The check compares the repository against that list and the submodule.

Core logic without Qt (sync.py), command line in __main__.py:
    python -m tools.openbswsync check  [--strict] [--json]
    python -m tools.openbswsync report [--format text|markdown|json] [--all]
    python -m tools.openbswsync accept [--commit] [--all-upstream] [PATH ...]
    python -m tools.openbswsync skeleton
"""

from .sync import (  # noqa: F401
    CopyList, Finding, ListError, Report, accept, check, format_markdown, format_text, load_list, normalized_hash,
    skeleton,
)
