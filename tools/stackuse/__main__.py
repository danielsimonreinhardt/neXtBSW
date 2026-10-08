"""Entry point: python tools/stackuse ... (see cli.py)."""

import sys
from pathlib import Path

if __package__ in (None, ""):  # started as a folder: python tools/stackuse ...
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from stackuse.cli import main
else:
    from .cli import main

if __name__ == "__main__":
    sys.exit(main())
