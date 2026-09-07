"""Mirroring of JunoCam labels and images with wget, at most four at a time.

The mirror reproduces the archive path under ``<mirror>/junocam/`` so that a
product's location is derivable from its manifest row and nothing else:

    <mirror>/junocam/<volume>/<FILE_SPECIFICATION_NAME>

Labels are always fetched before images, because an image is only judged
complete against ``RECORD_BYTES * FILE_RECORDS`` read from its own label --
the archive serves no Content-Length we could trust for a resumed transfer,
and a short read is otherwise indistinguishable from a small image.

Politeness is structural rather than advisory: the work is sharded into at
most ``jobs`` (capped at 4) shards, each shard runs one ``wget`` at a time
over a URL list, so at most four connections exist at any moment.  Every
transfer is verified against ``certifi``'s CA bundle, which the PDS Imaging
node needs and the system trust store does not satisfy.
"""

from __future__ import annotations

import logging
import subprocess
import tempfile
import time
from collections.abc import Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from ..config import mirror_root
from .labels import load_pvl
from .pds import build_manifest, ca_bundle, load_manifest_files, manifest_path

LOGGER = logging.getLogger(__name__)

MAX_JOBS = 4
LEVEL_CHOICES = ("EDR", "RDR", "both")
KIND_CHOICES = ("labels", "data")

#: The node answers 429 (Too Many Requests) well before four connections are
#: saturated -- an unthrottled shard fetching small labels at ~4 files/s was
#: refused within seconds -- so every retrieval waits, and wget itself is told
#: to treat 429 as retryable rather than fatal.
WAIT_SECONDS = 0.6
RETRY_HTTP_ERRORS = "429,500,502,503,504"
#: Whole-selection retry passes for anything still incomplete afterwards.
MAX_PASSES = 4
#: Pause before pass *n*, in seconds: a refusal is a request to back off.
PASS_PAUSE_SECONDS = 20.0


@dataclass
class MirrorResult:
    """What one ``junocam mirror`` invocation did, per kind."""

    kind: str
    requested: int = 0
    already_complete: int = 0
    downloaded: int = 0
    bytes_downloaded: int = 0
    failed: list[str] = field(default_factory=list)


def junocam_root(mirror: str | Path | None = None) -> Path:
    """``<mirror>/junocam`` -- the only directory this layer ever writes."""
    return mirror_root(mirror) / "junocam"


def _last_nonblank_line(path: Path) -> str:
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return ""
    return next((line.strip() for line in reversed(lines) if line.strip()), "")


def declared_image_size(label_path: Path) -> int | None:
    """``RECORD_BYTES * FILE_RECORDS`` from a JunoCam label, or ``None``."""
    if not label_path.is_file():
        return None
    try:
        module = load_pvl(label_path)
        record_bytes = module.get("RECORD_BYTES")
        file_records = module.get("FILE_RECORDS")
        if record_bytes is None or file_records is None:
            return None
        return int(record_bytes) * int(file_records)
    except Exception:
        return None


def is_complete(path: Path) -> bool:
    """The archive's completeness contract, by suffix.

    ``.LBL``: the last non-blank line is ``END``.
    ``.IMG``: the size equals ``RECORD_BYTES * FILE_RECORDS`` of the sibling
    label; with no sibling label present nothing can be verified, so a
    non-empty file is accepted and the caller is expected to have mirrored
    labels first.
    """
    if not path.is_file():
        return False
    suffix = path.suffix.upper()
    if suffix == ".LBL":
        return _last_nonblank_line(path) == "END"
    if suffix == ".IMG":
        expected = declared_image_size(path.with_suffix(".LBL"))
        if expected is None:
            return path.stat().st_size > 0
        return path.stat().st_size == expected
    return path.stat().st_size > 0


def _wget(destination: Path, urls: Sequence[str], log_path: Path) -> int:
    if not urls:
        return 0
    destination.mkdir(parents=True, exist_ok=True)
    list_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", prefix="junocam_", suffix=".txt", delete=False
        ) as stream:
            list_path = Path(stream.name)
            stream.write("\n".join(urls) + "\n")
        command = [
            "wget",
            "-nv",
            "-nd",
            "-P",
            str(destination),
            f"--ca-certificate={ca_bundle()}",
            "--tries=5",
            "--timeout=180",
            f"--wait={WAIT_SECONDS}",
            "--random-wait",
            "--waitretry=15",
            f"--retry-on-http-error={RETRY_HTTP_ERRORS}",
            "-a",
            str(log_path),
            "-i",
            str(list_path),
        ]
        return subprocess.run(command, check=False).returncode
    finally:
        if list_path is not None:
            list_path.unlink(missing_ok=True)


def _download(tasks: Sequence[tuple[Path, str]], jobs: int, logs_dir: Path, tag: str) -> None:
    """Run at most ``jobs`` concurrent wgets over ``(destination, url)`` pairs."""
    if not tasks:
        return
    workers = max(1, min(int(jobs), MAX_JOBS))
    logs_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    shards: list[list[tuple[Path, str]]] = [[] for _ in range(workers)]
    for position, task in enumerate(tasks):
        shards[position % workers].append(task)

    def run(shard_index: int, shard: list[tuple[Path, str]]) -> None:
        log_path = logs_dir / f"wget_{tag}_{timestamp}_{shard_index}.log"
        grouped: dict[Path, list[str]] = {}
        for destination, url in shard:
            grouped.setdefault(destination, []).append(url)
        for destination, urls in grouped.items():
            code = _wget(destination, urls, log_path)
            if code:
                LOGGER.debug("wget shard %d returned %d", shard_index, code)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        list(executor.map(lambda item: run(*item), list(enumerate(shards))))


def _mirror_kind(
    kind: str,
    rows: pd.DataFrame,
    root: Path,
    jobs: int,
) -> MirrorResult:
    """Fetch one kind (``labels`` or ``data``), skipping what is complete."""
    result = MirrorResult(kind=kind, requested=len(rows))
    suffix = ".LBL" if kind == "labels" else ".IMG"
    url_column = "url_lbl" if kind == "labels" else "url_img"
    wanted: list[tuple[Path, str]] = []
    destinations: list[Path] = []
    for row in rows.itertuples(index=False):
        file_spec = str(getattr(row, "file_spec"))
        relative = Path(str(getattr(row, "volume"))) / Path(file_spec).with_suffix(suffix)
        destination = root / relative
        destinations.append(destination)
        wanted.append((destination, str(getattr(row, url_column))))

    remaining = wanted
    for attempt in range(1, MAX_PASSES + 1):
        pending: list[tuple[Path, str]] = []
        for destination, url in remaining:
            if is_complete(destination):
                if attempt == 1:
                    result.already_complete += 1
                continue
            if destination.exists():
                destination.unlink()
            pending.append((destination, url))
        if not pending:
            break
        if attempt > 1:
            LOGGER.info(
                "%s: pass %d for %d incomplete file(s); pausing %.0f s",
                kind,
                attempt,
                len(pending),
                PASS_PAUSE_SECONDS,
            )
            time.sleep(PASS_PAUSE_SECONDS)
        _download(
            [(destination.parent, url) for destination, url in pending],
            jobs,
            root / "logs",
            kind,
        )
        remaining = pending

    for destination in destinations:
        if not is_complete(destination):
            result.failed.append(destination.name)
    result.downloaded = len(destinations) - result.already_complete - len(result.failed)
    result.bytes_downloaded = sum(
        destination.stat().st_size
        for destination in destinations
        if destination.is_file()
    )
    return result


def select_products(
    manifest: pd.DataFrame,
    orbits: Iterable[int] | None,
    level: str = "RDR",
    doy: Iterable[str] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """``(labels, data)`` selections: every product of the orbits, and a subset.

    Labels are mirrored for *every* product of the selected orbits regardless
    of ``--level``, because the index and every later join key off them and a
    label is a few kilobytes.  Images are mirrored only for the requested
    level and, if ``--doy`` is given, only for those days of year.  When the
    caller passes the every-version file table (which ``mirror_files`` does),
    a reprocessed product is fetched at every version the archive still
    serves, not only at its latest.
    """
    normalized = str(level).upper()
    if normalized not in {value.upper() for value in LEVEL_CHOICES}:
        raise ValueError(f"--level must be one of {', '.join(LEVEL_CHOICES)}")
    selected = manifest
    if orbits is not None:
        wanted = sorted(set(int(value) for value in orbits))
        selected = selected.loc[selected["orbit"].isin(wanted)]
    labels = selected
    data = selected
    if normalized != "BOTH":
        data = data.loc[data["level"] == normalized]
    if doy is not None:
        days = {str(value).strip().zfill(3) for value in doy if str(value).strip()}
        if days:
            data = data.loc[data["doy_dir"].isin(days)]
    return labels, data


def mirror_files(
    mirror: str | Path | None = None,
    orbits: Iterable[int] | None = None,
    level: str = "RDR",
    kinds: Iterable[str] = ("labels", "data"),
    doy: Iterable[str] | None = None,
    jobs: int = 3,
) -> list[MirrorResult]:
    """Mirror the selected labels and images; idempotent, resumable."""
    requested = list(dict.fromkeys(str(value).strip().lower() for value in kinds))
    invalid = [value for value in requested if value not in KIND_CHOICES]
    if invalid or not requested:
        raise ValueError("--kinds must be labels, data, or labels,data")
    if jobs < 1:
        raise ValueError("--jobs must be at least 1")

    root = junocam_root(mirror)
    if not manifest_path(mirror).exists():
        LOGGER.info("no manifest yet; building it for all volumes first")
        build_manifest(mirror)
    manifest = load_manifest_files(mirror)
    labels, data = select_products(manifest, orbits, level=level, doy=doy)

    results: list[MirrorResult] = []
    # Labels first, unconditionally when data was asked for: the image
    # completeness test reads the sibling label.
    if "labels" in requested or "data" in requested:
        rows = labels if "labels" in requested else data
        results.append(_mirror_kind("labels", rows, root, jobs))
    if "data" in requested:
        results.append(_mirror_kind("data", data, root, jobs))
    return results


def mirror_result_table(results: Sequence[MirrorResult]) -> str:
    lines = ["kind    requested  present  downloaded          bytes  failed"]
    for result in results:
        lines.append(
            f"{result.kind:7s}  {result.requested:9d}  {result.already_complete:7d}  "
            f"{result.downloaded:10d}  {result.bytes_downloaded:13d}  {len(result.failed):6d}"
        )
        if result.failed:
            lines.append("  failed: " + ", ".join(result.failed[:10]))
    return "\n".join(lines)


def mirrored_label_paths(
    manifest: pd.DataFrame, root: Path
) -> list[tuple[Path, dict[str, Any]]]:
    """Every manifest row whose label is present on disk, with that row."""
    found: list[tuple[Path, dict[str, Any]]] = []
    for row in manifest.to_dict(orient="records"):
        relative = Path(str(row["volume"])) / Path(str(row["file_spec"])).with_suffix(".LBL")
        path = root / relative
        if path.is_file():
            found.append((path, row))
    return found
