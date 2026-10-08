# SPDX-License-Identifier: Apache-2.0
"""python -m tools.csec - see README.md and cli.py."""

import sys
from pathlib import Path

if __package__ in (None, ""):  # started as "python tools/csec"
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.csec.cli import main  # noqa: E402

sys.exit(main())
