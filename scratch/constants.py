"""Single source of truth for naming and a few global defaults.

RENAME POINT
------------
``APP_NAME`` controls every user-facing string: the CLI program name in
``--help``, the default database location (``~/.<APP_NAME>/<APP_NAME>.db``),
and the environment-variable prefix used to override the DB path.

Change it here and the whole tool re-brands. The only things this does NOT
rename are the Python package directory (``scratch/``) and the optional
``pip``-installed shell command in ``pyproject.toml`` — those are packaging
concerns, not app behavior. The canonical, rename-proof way to run the tool
is always ``python -m scratch`` (or ``python -m <package>`` after a dir
rename).
"""

APP_NAME = "scratch"
APP_VERSION = "0.1.0"

# Lie types used across strokes-gained and the baseline table.
LIES = ("tee", "fairway", "rough", "sand", "recovery", "green")

# Strokes-gained categories every shot is attributed to.
SG_CATEGORIES = ("off-the-tee", "approach", "short-game", "putting")
