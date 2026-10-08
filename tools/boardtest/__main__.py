"""python tools/boardtest --project <project> --port <COMx> <test> ... (README.md)"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cli import main  # noqa: E402

sys.exit(main())
