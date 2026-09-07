"""``jiram-catalog junocam <sub>``: manifest, mirror, index, quality.

The JunoCam layer owns one subcommand group with four sub-subcommands, in the
order they are meant to be run:

    junocam manifest [--volumes 1-35] [--refresh]
    junocam mirror --orbits SPEC [--level RDR|EDR|both] [--kinds labels,data]
                   [--doy DDD,...] [--jobs N]
    junocam index --orbits SPEC [--jobs N]
    junocam quality --orbits SPEC [--jobs N]

It is registered on the main parser with :func:`add_subparser`, as the agent
harness requires of any module that owns a subcommand, and the same parser is
available stand-alone as ``python -m jiram_catalog.junocam.cli <sub>``.
"""

from __future__ import annotations

import argparse
import logging
import sys
from typing import Any

from .index import build_index, default_jobs, index_summary
from .mirror import LEVEL_CHOICES, mirror_files, mirror_result_table
from .pds import FIRST_VOLUME, LAST_VOLUME, build_manifest, manifest_summary
from .quality import build_quality, quality_summary

LOGGER = logging.getLogger(__name__)


def parse_int_spec(
    specification: str, *, low: int, high: int, label: str
) -> list[int] | None:
    """Parse ``all`` or a comma-separated set of integers and ``a-b`` ranges."""
    text = str(specification).strip().lower()
    if text == "all":
        return None
    if not text:
        raise ValueError(f"bad {label}: specification is empty")
    result: set[int] = set()
    for token in text.split(","):
        token = token.strip()
        if not token:
            raise ValueError(f"bad {label}: {specification}")
        if "-" in token:
            pieces = token.split("-")
            if len(pieces) != 2 or not all(piece.isdigit() for piece in pieces):
                raise ValueError(f"bad {label}: {specification}")
            start, stop = (int(piece) for piece in pieces)
            if start > stop:
                raise ValueError(f"bad {label} range: {token}")
            values: list[int] | range = range(start, stop + 1)
        elif token.isdigit():
            values = [int(token)]
        else:
            raise ValueError(f"bad {label}: {specification}")
        for value in values:
            if value < low or value > high:
                raise ValueError(f"bad {label} value: {value}")
            result.add(value)
    return sorted(result)


def parse_orbits(specification: str) -> list[int] | None:
    """Orbit numbers; 0 is the cruise/capture-orbit bucket of the archive."""
    return parse_int_spec(specification, low=0, high=99, label="--orbits")


def parse_volumes(specification: str) -> list[int] | None:
    return parse_int_spec(
        specification, low=FIRST_VOLUME, high=LAST_VOLUME, label="--volumes"
    )


def parse_kinds(specification: str) -> list[str]:
    values = [value.strip().lower() for value in str(specification).split(",")]
    if not values or any(value not in {"labels", "data"} for value in values):
        raise ValueError("--kinds must be labels, data, or labels,data")
    return list(dict.fromkeys(values))


def parse_doy(specification: str | None) -> list[str] | None:
    """``033,034`` -> ``["033", "034"]``; ``None`` means every day of year."""
    if specification is None:
        return None
    days = [value.strip().zfill(3) for value in str(specification).split(",") if value.strip()]
    if not days or any(not day.isdigit() or len(day) != 3 for day in days):
        raise ValueError("--doy must be one or more 3-digit days of year, e.g. 033,034")
    return days


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--mirror", help="local mirror root")
    parser.add_argument("-v", action="store_true", help="enable debug logging")


def _add_subcommands(subparsers: Any) -> None:
    manifest = subparsers.add_parser(
        "manifest", help="build the product manifest from the volumes' INDEX.TAB"
    )
    _add_common(manifest)
    manifest.add_argument(
        "--volumes",
        default="all",
        metavar="SPEC",
        help=f"all or e.g. 1-35, 3,28 (volumes {FIRST_VOLUME}..{LAST_VOLUME})",
    )
    manifest.add_argument(
        "--refresh", action="store_true", help="re-fetch cached INDEX/ERRATA files"
    )

    mirror = subparsers.add_parser("mirror", help="mirror labels and images with wget")
    _add_common(mirror)
    mirror.add_argument("--orbits", required=True, metavar="SPEC", help="all or e.g. 4,58")
    mirror.add_argument(
        "--level",
        default="RDR",
        choices=list(LEVEL_CHOICES),
        help="which level's images to mirror (labels are mirrored for both)",
    )
    mirror.add_argument("--kinds", default="labels,data")
    mirror.add_argument(
        "--doy", metavar="DDD,...", help="restrict image downloads to these days of year"
    )
    mirror.add_argument("--jobs", type=int, default=3, help="concurrent wgets, at most 4")

    index = subparsers.add_parser("index", help="parse mirrored labels into the index")
    _add_common(index)
    index.add_argument("--orbits", required=True, metavar="SPEC")
    index.add_argument("--jobs", type=int, default=default_jobs())

    quality = subparsers.add_parser("quality", help="epoch flags and measured metrics")
    _add_common(quality)
    quality.add_argument("--orbits", required=True, metavar="SPEC")
    quality.add_argument("--jobs", type=int, default=default_jobs())


def add_subparser(subparsers: Any) -> None:
    """Register the ``junocam`` group on the main parser's subparser action."""
    parser = subparsers.add_parser("junocam", help="JunoCam acquisition, index, quality")
    group = parser.add_subparsers(dest="junocam_command", required=True)
    _add_subcommands(group)
    parser.set_defaults(func=run)


def make_parser() -> argparse.ArgumentParser:
    """The stand-alone parser of ``python -m jiram_catalog.junocam.cli``."""
    parser = argparse.ArgumentParser(
        prog="jiram-catalog junocam",
        description="JunoCam acquisition, index, and quality table",
    )
    group = parser.add_subparsers(dest="junocam_command", required=True)
    _add_subcommands(group)
    parser.set_defaults(func=run)
    return parser


def run(args: argparse.Namespace) -> int:
    """Dispatch one JunoCam sub-subcommand; returns a process exit status."""
    command = getattr(args, "junocam_command", None)
    mirror = getattr(args, "mirror", None)
    if command == "manifest":
        volumes = parse_volumes(args.volumes)
        manifest = build_manifest(mirror, volumes, args.refresh)
        print(manifest_summary(manifest))
        return 0
    if command == "mirror":
        orbits = parse_orbits(args.orbits)
        results = mirror_files(
            mirror,
            orbits,
            level=args.level,
            kinds=parse_kinds(args.kinds),
            doy=parse_doy(args.doy),
            jobs=args.jobs,
        )
        print(mirror_result_table(results))
        return 1 if any(result.failed for result in results) else 0
    if command == "index":
        orbits = parse_orbits(args.orbits)
        images = build_index(mirror, orbits, args.jobs)
        print(index_summary(images, orbits or []))
        return 0
    if command == "quality":
        orbits = parse_orbits(args.orbits)
        table = build_quality(mirror, orbits, args.jobs)
        selected = table if orbits is None else table.loc[table["orbit"].isin(orbits)]
        print(quality_summary(selected))
        return 0
    raise ValueError(f"unknown junocam subcommand: {command}")


def main(argv: list[str] | None = None) -> int:
    parser = make_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if getattr(args, "v", False) else logging.INFO,
        format="%(levelname)s: %(message)s",
    )
    try:
        return run(args)
    except (FileNotFoundError, OSError, ValueError) as exc:
        print(f"error: {' '.join(str(exc).split())}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
