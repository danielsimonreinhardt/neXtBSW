"""Entry point so that `python -m tools.setup` and `python tools/setup` both work."""

import sys

try:
    from .setup_env import main
except ImportError:  # started as a directory: sys.path[0] is tools/setup itself
    from setup_env import main

if __name__ == "__main__":
    sys.exit(main())
