"""``jiram-catalog gui``: serve the catalog browser.

Two front ends live behind this one subcommand.  The default is GUI v2 --
a FastAPI service (:mod:`jiram_catalog.api.app`) that answers Arrow, JSON
and PNG under ``/api`` and hands the compiled single-page front end to
the browser at ``/``.  ``--legacy`` serves the Panel application of GUI
v1 instead, which is still the fastest way to look at something without a
front-end build.

The subcommand lives in its own module, as the harness requires, and is
registered with :func:`add_subparser`; ``python -m jiram_catalog.gui_cmd``
runs the same thing stand-alone.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
import webbrowser
from typing import Any

LOGGER = logging.getLogger(__name__)

DEFAULT_PORT = 5006
DEFAULT_ADDRESS = "127.0.0.1"

#: Import string uvicorn's reloader needs; it re-imports in each child.
APP_FACTORY = "jiram_catalog.api.app:create_app"


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
    parser.add_argument(
        "--legacy", action="store_true", help="serve the Panel GUI v1 instead of the v2 app"
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="restart the server when the source changes (development only)",
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


def _banner(address: str, port: int) -> str:
    return (
        f"serving http://{address}:{port} "
        f"(tunnel with: ssh -N -L {port}:$(hostname):{port} <login node>)"
    )


def _open_browser(address: str, port: int, delay: float = 1.5) -> None:
    """Open the page once the server has had a moment to bind."""
    host = "localhost" if address in ("0.0.0.0", "::") else address
    threading.Timer(delay, lambda: webbrowser.open(f"http://{host}:{port}")).start()


def _serve_legacy(args: argparse.Namespace) -> int:
    from .gui import app

    app.serve(
        mirror=getattr(args, "mirror", None),
        port=args.port,
        address=args.address,
        show=not getattr(args, "no_browser", False),
    )
    return 0


def _serve_v2(args: argparse.Namespace) -> int:
    import uvicorn

    from .api.app import create_app

    if not getattr(args, "no_browser", False):
        _open_browser(args.address, args.port)
    level = "debug" if getattr(args, "v", False) else "info"
    if getattr(args, "reload", False):
        # The reloader re-imports the app in a child process, so it needs
        # an import string rather than an object -- and the mirror has to
        # travel through the environment, which is where ``config`` looks
        # for it anyway.
        if getattr(args, "mirror", None):
            os.environ["JIRAM_MIRROR"] = str(args.mirror)
        uvicorn.run(
            APP_FACTORY,
            factory=True,
            host=args.address,
            port=int(args.port),
            reload=True,
            log_level=level,
        )
    else:
        uvicorn.run(
            create_app(getattr(args, "mirror", None)),
            host=args.address,
            port=int(args.port),
            log_level=level,
        )
    return 0


def run(args: argparse.Namespace) -> int:
    """Serve until interrupted."""
    LOGGER.info("%s", _banner(args.address, args.port))
    print(_banner(args.address, args.port), flush=True)
    try:
        if getattr(args, "legacy", False):
            return _serve_legacy(args)
        return _serve_v2(args)
    except KeyboardInterrupt:
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
