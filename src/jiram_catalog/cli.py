"""Command-line interface for the JIRAM archive catalog."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .geo import build_geo, default_geo_jobs, geo_report_text
from .index import (
    _orbit_dirs,
    build_index,
    default_index_jobs,
    index_summary,
    load_frames,
    stats_text,
)
from .kernels import ensure_kernels, kernel_report_text
from .mirror import mirror_files, mirror_result_table
from .pds import build_manifest, manifest_count_table, mirror_root
from .regions import RegionGrid
from .stacks import (
    build_stack,
    composite_sequences,
    default_stack_jobs,
    read_stack,
    registry_table,
    select_frames,
    stack_output_path,
    valid_fraction,
    write_stack,
)


class OneLineArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        self.exit(2, f"error: {message}\n")


def parse_orbits(specification: str) -> list[int] | None:
    """Parse ``all`` or a comma-separated collection of integers/ranges."""
    text = specification.strip().lower()
    if text == "all":
        return None
    if not text:
        raise ValueError("bad --orbits: specification is empty")
    result: set[int] = set()
    for token in text.split(","):
        token = token.strip()
        if not token:
            raise ValueError(f"bad --orbits: {specification}")
        if "-" in token:
            pieces = token.split("-")
            if len(pieces) != 2 or not all(piece.isdigit() for piece in pieces):
                raise ValueError(f"bad --orbits: {specification}")
            start, stop = (int(piece) for piece in pieces)
            if start > stop:
                raise ValueError(f"bad --orbits range: {token}")
            values = range(start, stop + 1)
        elif token.isdigit():
            values = [int(token)]
        else:
            raise ValueError(f"bad --orbits: {specification}")
        for value in values:
            if value < 1 or value > 99:
                raise ValueError(f"bad --orbits value: {value}")
            result.add(value)
    return sorted(result)


def parse_kinds(specification: str) -> list[str]:
    values = [value.strip().lower() for value in specification.split(",")]
    if not values or any(value not in {"labels", "data"} for value in values):
        raise ValueError("--kinds must be labels, data, or labels,data")
    return list(dict.fromkeys(values))


def _common(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument("--mirror", help="local mirror root")
    subparser.add_argument(
        "--orbits", default="all", metavar="SPEC", help="all or e.g. 4,5,10-20"
    )
    subparser.add_argument("-v", action="store_true", help="enable debug logging")


def make_parser() -> argparse.ArgumentParser:
    parser = OneLineArgumentParser(prog="jiram-catalog")
    subparsers = parser.add_subparsers(dest="command", required=True)

    manifest = subparsers.add_parser("manifest")
    _common(manifest)
    manifest.add_argument("--refresh", action="store_true")

    mirror = subparsers.add_parser("mirror")
    _common(mirror)
    mirror.add_argument("--family", choices=["img", "spe", "log", "all"], default="img")
    mirror.add_argument("--kinds", default="labels,data")
    mirror.add_argument("--jobs", type=int, default=3)
    mirror.add_argument("--verify", action="store_true")

    index = subparsers.add_parser("index")
    _common(index)
    index.add_argument("--jobs", type=int, default=default_index_jobs())

    kernels = subparsers.add_parser("kernels")
    _common(kernels)
    kernels.add_argument("--jobs", type=int, default=3)
    kernels.add_argument("--dry-run", action="store_true")

    geo = subparsers.add_parser("geo")
    _common(geo)
    geo.add_argument("--jobs", type=int, default=default_geo_jobs())
    geo.add_argument(
        "--limit", type=int, help="process only the first K frames of each orbit"
    )

    stats = subparsers.add_parser("stats")
    _common(stats)

    regions = subparsers.add_parser("regions", help="list the region registry")
    regions.add_argument("--config", help="path to regions.yaml")
    regions.add_argument("-v", action="store_true", help="enable debug logging")

    stack = subparsers.add_parser("region-stack", help="reproject frames onto a region")
    _common(stack)
    stack.add_argument("--region", required=True, help="region name in the registry")
    stack.add_argument("--band", required=True, choices=["L", "M"])
    stack.add_argument("--level", default="frame", choices=["frame", "sequence"])
    stack.add_argument("--out", help="output NetCDF path")
    stack.add_argument("--config", help="path to regions.yaml")
    stack.add_argument("--no-crop", action="store_true", help="keep the full canvas")
    stack.add_argument("--margin-px", type=int, default=16)
    stack.add_argument("--max-emission", type=float, default=80.0)
    stack.add_argument("--min-on-planet", type=float, default=0.02)
    stack.add_argument("--jobs", type=int, default=default_stack_jobs())

    movie = subparsers.add_parser("movie", help="render a stack as a movie")
    movie.add_argument("stack", help="stack NetCDF written by region-stack")
    movie.add_argument("--out", required=True, help="output .mp4 or .gif")
    movie.add_argument("--fps", type=float, default=4.0)
    movie.add_argument(
        "--pct", type=float, nargs=2, default=[1.0, 99.0], metavar=("LOW", "HIGH")
    )
    movie.add_argument("--cmap", default="gray")
    movie.add_argument("-v", action="store_true", help="enable debug logging")

    export = subparsers.add_parser(
        "export-goflow", help="write constant-cadence runs in the velocity-model layout"
    )
    export.add_argument("stack", help="stack NetCDF written by region-stack")
    export.add_argument("--out", required=True, help="output dataset directory")
    export.add_argument("--dt-tol", type=float, default=0.05)
    export.add_argument("--min-frames", type=int, default=3)
    export.add_argument("--crop-to-valid", action="store_true")
    export.add_argument("-v", action="store_true", help="enable debug logging")
    # Modules that own their own subcommand (see docs/agent_harness.md).
    from jiram_catalog import config_cmd, gui_cmd, stats2d, strips

    strips.add_subparser(subparsers)
    stats2d.add_subparser(subparsers)
    gui_cmd.add_subparser(subparsers)
    config_cmd.add_subparser(subparsers)
    return parser


def _region_stack(args: argparse.Namespace, root: Path, orbits: list[int] | None) -> int:
    """Select, reproject, optionally composite, and write one region stack."""
    region = RegionGrid.from_yaml(args.region, args.config)
    frames = select_frames(
        root,
        region,
        orbits,
        args.band,
        max_emission=args.max_emission,
        min_on_planet=args.min_on_planet,
    )
    print(f"selected frames: {len(frames)}")
    if frames.empty:
        raise ValueError(f"no frame of orbits {args.orbits} overlaps region {args.region}")
    dataset = build_stack(
        root,
        region,
        frames,
        args.band,
        crop=not args.no_crop,
        margin_px=args.margin_px,
        jobs=args.jobs,
    )
    if args.level == "sequence":
        dataset = composite_sequences(dataset)
    output = (
        Path(args.out)
        if args.out
        else stack_output_path(root, args.region, args.band, args.orbits, args.level)
    )
    sizes = dataset.sizes
    print(f"stack shape: time={sizes['time']} y={sizes['y']} x={sizes['x']}")
    print(f"valid fraction: {valid_fraction(dataset):.6f}")
    write_stack(dataset, output)
    print(f"wrote: {output}")
    return 0


def _run(args: argparse.Namespace) -> int:
    func = getattr(args, "func", None)
    if callable(func):
        return int(func(args))
    orbits = parse_orbits(getattr(args, "orbits", "all"))
    root = mirror_root(getattr(args, "mirror", None))
    if args.command == "manifest":
        manifest = build_manifest(root, orbits, args.refresh)
        selected = (
            sorted(int(value) for value in manifest["orbit"].dropna().unique())
            if orbits is None
            else orbits
        )
        print(manifest_count_table(manifest, selected))
        return 0
    if args.command == "mirror":
        kinds = parse_kinds(args.kinds)
        results = mirror_files(
            root,
            orbits,
            family=args.family,
            kinds=kinds,
            jobs=args.jobs,
            verify=args.verify,
        )
        print(mirror_result_table(results))
        return 1 if any(result.failed for result in results) else 0
    if args.command == "index":
        frames = build_index(root, orbits, args.jobs)
        selected = _orbit_dirs(root) if orbits is None else orbits
        print(index_summary(frames, selected))
        return 0
    if args.command == "kernels":
        statuses = ensure_kernels(
            root, orbits, jobs=args.jobs, dry_run=args.dry_run
        )
        print(kernel_report_text(statuses, dry_run=args.dry_run))
        return 1 if any(status.failed for status in statuses) else 0
    if args.command == "geo":
        table = build_geo(root, orbits, jobs=args.jobs, limit=args.limit)
        selected = (
            sorted(int(value) for value in table["orbit_dir"].dropna().unique())
            if orbits is None
            else orbits
        )
        report = geo_report_text(
            load_frames(root, selected),
            table.loc[table["orbit_dir"].isin(selected)],
            selected,
        )
        output = root / "index" / "geo_report.md"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(report, encoding="utf-8")
        sys.stdout.write(report)
        return 0
    if args.command == "stats":
        frames = load_frames(root, orbits)
        report = stats_text(frames)
        output = root / "index" / "stats.md"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(report, encoding="utf-8")
        sys.stdout.write(report)
        return 0
    if args.command == "regions":
        print(registry_table(args.config))
        return 0
    if args.command == "region-stack":
        return _region_stack(args, root, orbits)
    if args.command == "movie":
        from .movie import write_movie

        with read_stack(args.stack) as dataset:
            summary = write_movie(
                dataset,
                args.out,
                fps=args.fps,
                percentiles=(args.pct[0], args.pct[1]),
                cmap=args.cmap,
            )
        print(
            f"wrote {summary['frames']} frame(s) at "
            f"{summary['width']}x{summary['height']} px, "
            f"{summary['duration_s']:.2f} s, "
            f"{summary['bytes'] / 1e6:.2f} MB: {summary['path']}"
        )
        return 0
    if args.command == "export-goflow":
        from .export_goflow import export_stack

        with read_stack(args.stack) as dataset:
            manifest = export_stack(
                dataset,
                args.out,
                dt_tol=args.dt_tol,
                min_frames=args.min_frames,
                crop_to_valid=args.crop_to_valid,
                source=args.stack,
            )
        print(f"realizations: {manifest['n_realizations']}")
        for entry in manifest["realizations"]:
            print(
                f"  {entry['name']}: {entry['n_frames']} frames, "
                f"dt = {entry['dt_img_s']:.3f} s, shape {entry['shape']}"
            )
        print(f"wrote: {args.out}")
        return 0
    raise ValueError(f"unknown command: {args.command}")


def main(argv: list[str] | None = None) -> int:
    parser = make_parser()
    try:
        args = parser.parse_args(argv)
        logging.basicConfig(
            level=logging.DEBUG if getattr(args, "v", False) else logging.INFO,
            format="%(levelname)s: %(message)s",
        )
        return _run(args)
    except (FileNotFoundError, OSError, ValueError) as exc:
        print(f"error: {' '.join(str(exc).split())}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
