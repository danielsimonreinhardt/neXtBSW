# SPDX-License-Identifier: Apache-2.0
"""Entry point: python tools/fmtcheck [--all] (see fmtcheck.py)."""

import sys
from pathlib import Path

if __package__ in (None, ""):  # started as a folder: python tools/fmtcheck
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from tools.fmtcheck.fmtcheck import main
else:
    from .fmtcheck import main

if __name__ == "__main__":
    sys.exit(main())
