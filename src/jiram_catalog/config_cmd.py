"""``jiram-catalog config``: print the resolved paths and where each came from.

The subcommand lives in its own module, as the harness requires, and is
registered with :func:`add_subparser`; ``python -m jiram_catalog.config_cmd``
runs the same thing stand-alone.
"""

from __future__ import annotations

import argparse
from typing import Any

from .config import describe


def add_subparser(subparsers: Any) -> None:
    """Register the ``config`` subcommand on an argparse subparser action."""
    parser = subparsers.add_parser(
        "config", help="show the resolved mirror/paper-data paths and their source"
    )
    parser.set_defaults(func=run)


def make_parser() -> argparse.ArgumentParser:
    """The stand-alone parser of ``python -m jiram_catalog.config_cmd``."""
    parser = argparse.ArgumentParser(
        prog="jiram-catalog config",
        description="show the resolved mirror/paper-data paths and their source",
    )
    parser.set_defaults(func=run)
    return parser


def run(args: argparse.Namespace) -> int:
    print(describe())
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = make_parser()
    args = parser.parse_args(argv)
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
