"""SQLite persistence layer.

Everything the tool knows lives in one local SQLite file so data
accumulates across rounds, practice sessions, and swing analyses. The
file location is resolved once, in priority order:

    1. ``--db PATH`` on the command line
    2. the ``SCRATCH_DB`` environment variable (prefix from APP_NAME)
    3. the default ``~/.scratch/scratch.db``

Call :func:`connect` from a command; it ensures the directory exists,
applies the schema (idempotent), and hands back a ``sqlite3.Connection``
with ``row_factory`` set so rows behave like dicts.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from .constants import APP_NAME

ENV_VAR = f"{APP_NAME.upper()}_DB"


def resolve_db_path(cli_path: str | None = None) -> Path:
    """Resolve the database path from CLI arg, env var, or default."""
    raw = cli_path or os.environ.get(ENV_VAR) or f"~/.{APP_NAME}/{APP_NAME}.db"
    return Path(raw).expanduser()


def connect(cli_path: str | None = None) -> sqlite3.Connection:
    """Open (creating if needed) the database and ensure the schema exists."""
    path = resolve_db_path(cli_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    _init_schema(conn)
    return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS rounds (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    date          TEXT    NOT NULL,
    course        TEXT,
    score         INTEGER NOT NULL,
    course_rating REAL    NOT NULL,
    slope         INTEGER NOT NULL,
    holes         INTEGER NOT NULL DEFAULT 18,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- Shot-level detail for strokes gained. Distances are yards, except on
-- the green where `start_distance` is feet (set is_green_feet=1). Built
-- out in the strokes-gained phase; table exists now so the schema is
-- stable and the trainer can read it.
CREATE TABLE IF NOT EXISTS shots (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    round_id       INTEGER REFERENCES rounds(id) ON DELETE CASCADE,
    date           TEXT,
    hole           INTEGER,
    shot_num       INTEGER,
    start_distance REAL,
    start_lie      TEXT,
    end_distance   REAL,
    end_lie        TEXT,
    holed          INTEGER NOT NULL DEFAULT 0,
    penalty        INTEGER NOT NULL DEFAULT 0,
    category       TEXT,      -- off-the-tee / approach / short-game / putting
    sg             REAL,      -- strokes gained vs baseline (computed)
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Range / practice sessions, tagged to a fault or SG category so the
-- feedback loop can ask "did the work pay off?".
CREATE TABLE IF NOT EXISTS practice_sessions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    date         TEXT    NOT NULL,
    focus        TEXT,            -- SG category or swing-fault tag
    drills       TEXT,
    duration_min INTEGER,
    notes        TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Goal tracking (Phase 2). One active goal per kind; status reads the
-- handicap + strokes-gained engines to break a target into category work.
CREATE TABLE IF NOT EXISTS goals (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    kind         TEXT    NOT NULL DEFAULT 'handicap',
    target_value REAL    NOT NULL,
    target_date  TEXT,
    active       INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- Personal course book (Phase 2): per-course, per-hole notes + geometry.
-- The geometry fields double as saved inputs for `strategy tee`.
CREATE TABLE IF NOT EXISTS courses (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT    NOT NULL UNIQUE,
    created_at TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS course_holes (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id     INTEGER NOT NULL REFERENCES courses(id) ON DELETE CASCADE,
    hole          INTEGER NOT NULL,
    par           INTEGER,
    length        REAL,
    fairway_width REAL,
    ob_left       REAL,
    ob_right      REAL,
    forced_carry  REAL,
    note          TEXT,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (course_id, hole)
);

-- Per-club shots for distance + dispersion (Phase 2). carry in yards;
-- side is lateral offset in yards (- left / + right), nullable.
CREATE TABLE IF NOT EXISTS club_shots (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    date       TEXT    NOT NULL,
    club       TEXT    NOT NULL,
    carry      REAL    NOT NULL,
    side       REAL,
    notes      TEXT,
    created_at TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- Output of the swing analyzer. Metrics are nullable so a face-on clip
-- can omit down-the-line-only measures and vice versa.
CREATE TABLE IF NOT EXISTS swing_analyses (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    date              TEXT    NOT NULL,
    video_path        TEXT,
    view              TEXT,
    fps               REAL,
    tempo_ratio       REAL,
    x_factor          REAL,
    head_movement     REAL,
    spine_consistency REAL,
    faults            TEXT,       -- JSON list of fault tags
    output_path       TEXT,
    created_at        TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()
