"""scratch — a terminal-first, all-in-one golf coaching tool.

The package name and the user-facing command name are intentionally
decoupled: see ``scratch.constants.APP_NAME`` for the single place to
change the displayed/command name without touching every module.
"""

from .constants import APP_NAME, APP_VERSION

__all__ = ["APP_NAME", "APP_VERSION"]
__version__ = APP_VERSION
