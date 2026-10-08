"""Entry point so that `python -m tools.ci` and `python tools/ci` both work."""

import sys

try:
    from .ci import main
except ImportError:  # started as a directory: sys.path[0] is tools/ci itself
    from ci import main

if __name__ == "__main__":
    sys.exit(main())
