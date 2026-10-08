# SPDX-License-Identifier: Apache-2.0
"""Entry point: python tools/trace [check|matrix|tags] ... (see tracecheck.py).

Started as a folder, sys.path[0] is tools/trace itself; the module is imported as `tracecheck` and never as a
package called `trace`, which would shadow the standard library module of that name."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tracecheck import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
