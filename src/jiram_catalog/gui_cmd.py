"""``jiram-catalog gui``: serve the Panel application.

The subcommand lives in its own module, as the harness requires, and is
registered with :func:`add_subparser`; ``python -m jiram_catalog.gui_cmd``
runs the same thing stand-alone.
"""

from __future__ import annotations

import argparse
import logging
import sys
from typing import Any

DEFAULT_PORT = 5006
DEFAULT_ADDRESS = "127.0.0.1"


def _add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--mirror", help="local mirror root")
    parser.add_argument(
        "--port", type=int, default=DEFAULT_PORT, help=f"HTTP port (default {DEFAULT_PORT})"
    )
    parser.add_argument(
        "--address",
        default=DEFAULT_ADDRESS,
        help=(
            "interface to bind; the default is loopback only, which is what "
            "an SSH tunnel needs"
        ),
    )
    parser.add_argument(
        "--no-browser", action="store_true", help="do not try to open a browser"
    )
    parser.add_argument("-v", action="store_true", help="enable debug logging")


def add_subparser(subparsers: Any) -> None:
    """Register the ``gui`` subcommand on an argparse subparser action."""
    parser = subparsers.add_parser("gui", help="serve the catalog browser")
    _add_arguments(parser)
    parser.set_defaults(func=run)


def make_parser() -> argparse.ArgumentParser:
    """The stand-alone parser of ``python -m jiram_catalog.gui_cmd``."""
    parser = argparse.ArgumentParser(
        prog="jiram-catalog gui", description="serve the catalog browser"
    )
    _add_arguments(parser)
    parser.set_defaults(func=run)
    return parser


def run(args: argparse.Namespace) -> int:
    """Serve until interrupted."""
    from .gui import app  # imported here so that --help costs nothing

    print(
        f"serving http://{args.address}:{args.port} "
        f"(tunnel with: ssh -N -L {args.port}:$(hostname):{args.port} <login node>)"
    )
    try:
        app.serve(
            mirror=getattr(args, "mirror", None),
            port=args.port,
            address=args.address,
            show=not getattr(args, "no_browser", False),
        )
    except KeyboardInterrupt:
        pass
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = make_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.v else logging.INFO, format="%(levelname)s: %(message)s"
    )
    try:
        return run(args)
    except (FileNotFoundError, OSError, ValueError) as exc:
        print(f"error: {' '.join(str(exc).split())}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
