"""Command-line entry point and subcommand dispatcher.

Each command module under ``scratch.commands`` exposes a single
``register(subparsers)`` function that attaches its own subparser(s) and
sets ``func`` via ``set_defaults``. ``main`` builds the parser, parses
args, and calls the selected ``func``. Adding a command means writing a
module and listing it in ``COMMANDS`` — nothing else changes.
"""

from __future__ import annotations

import argparse
import sys

from . import constants
from .commands import (
    analyze as analyze_cmd,
    course as course_cmd,
    dispersion as dispersion_cmd,
    goal as goal_cmd,
    handicap as handicap_cmd,
    practice as practice_cmd,
    round as round_cmd,
    sg as sg_cmd,
    strategy as strategy_cmd,
    train as train_cmd,
)

# Order here is the order subcommands appear in --help.
COMMANDS = [
    round_cmd,
    handicap_cmd,
    sg_cmd,
    practice_cmd,
    analyze_cmd,
    train_cmd,
    goal_cmd,
    dispersion_cmd,
    strategy_cmd,
    course_cmd,
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=constants.APP_NAME,
        description="Terminal-first, all-in-one golf coaching tool.",
    )
    parser.add_argument(
        "--db",
        metavar="PATH",
        help="Override the SQLite database path "
        f"(default ~/.{constants.APP_NAME}/{constants.APP_NAME}.db).",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"{constants.APP_NAME} {constants.APP_VERSION}",
    )
    subparsers = parser.add_subparsers(dest="command", metavar="<command>")
    subparsers.required = True
    for module in COMMANDS:
        module.register(subparsers)
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(argv)
    # Every command's run() takes the parsed args namespace and returns an
    # int exit code. The DB path lives on args.db and commands open their
    # own connection via db.connect(args.db).
    return args.func(args) or 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
