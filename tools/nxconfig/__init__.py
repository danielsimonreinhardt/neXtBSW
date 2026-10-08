"""neXtBSW configuration model: derivative -> board -> project, loading and checks.

Used by all generators, the command line and neXtBSW Studio (no Qt dependency).

    config, messages = nxconfig.check("projects/<p>/project.yaml")   # never raises for config problems
    config = nxconfig.load("projects/<p>/project.yaml")              # raises ConfigErrors
"""

from .config import MCUDB, REPO, Config, check, find_derivative, load
from .messages import ConfigErrors, Message, format_path
from .model import SCHEMA_VERSION
from .version import VersionError, read_version


def nextbsw_version(tree=REPO) -> str:
    """The neXtBSW version of a tree (default: the one this package runs from), from its VERSION file (ARCH-55)."""
    return read_version(tree)


__all__ = ["MCUDB", "REPO", "SCHEMA_VERSION", "Config", "ConfigErrors", "Message", "VersionError", "check",
           "find_derivative", "format_path", "load", "nextbsw_version"]
