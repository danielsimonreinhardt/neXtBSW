"""Entry point so that `python tools/test` and `python -m tools.test` both work."""

import sys

try:
    from .run_tests import main
except ImportError:  # started as a directory: sys.path[0] is tools/test itself
    from run_tests import main

if __name__ == "__main__":
    sys.exit(main())
