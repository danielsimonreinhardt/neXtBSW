# SPDX-License-Identifier: Apache-2.0
"""Entry point: python tools/logcheck [check|hosttest] ... (see logcheck.py)."""

import sys
from pathlib import Path

if __package__ in (None, ""):  # started as a folder: python tools/logcheck ...
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from logcheck.logcheck import main
else:
    from .logcheck import main

if __name__ == "__main__":
    sys.exit(main())
