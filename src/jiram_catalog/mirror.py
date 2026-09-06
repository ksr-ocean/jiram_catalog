"""Concurrent per-orbit mirroring through wget with archive-aware checks."""

from __future__ import annotations

import hashlib
import logging
import subprocess
import tempfile
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from .labels import _integer, _load_pvl, _string
from .pds import build_manifest, coerce_manifest, mirror_root

LOGGER = logging.getLogger(__name__)
ARCHIVE_RELATIVE = Path("pds4/juno_jiram_bundle/data_calibrated")


@dataclass
class OrbitMirrorResult:
    orbit: int
    requested: int
    downloaded: int
    skipped: int
    failed: list[str]


def _last_nonblank_line(path: Path) -> str:
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return ""
    return next((line.strip() for line in reversed(lines) if line.strip()), "")


def declared_data_size(data_path: Path) -> int | None:
    """Read FILE_RECORDS * RECORD_BYTES from a sibling PDS3 label."""
    label = data_path.with_suffix(".LBL")
    if not label.is_file():
        return None
    try:
        module = _load_pvl(label)
        file_object = module.get("FILE") or {}
        records = _integer(file_object.get("FILE_RECORDS"))
        record_bytes = _integer(file_object.get("RECORD_BYTES"))
        if records is None or record_bytes is None:
            return None
        return records * record_bytes
    except Exception:
        return None


def is_complete(path: Path) -> bool:
    """Apply the suffix-specific completeness rule from the archive contract."""
    if not path.is_file():
        return False
    suffix = path.suffix.upper()
    if suffix == ".LBL":
        return _last_nonblank_line(path) == "END"
    if suffix == ".XML":
        try:
            with path.open("rb") as stream:
                stream.seek(max(0, path.stat().st_size - 4096))
                return stream.read().rstrip().endswith(b"</Product_Observational>")
        except OSError:
            return False
    if suffix in {".IMG", ".DAT", ".TAB"}:
        label = path.with_suffix(".LBL")
        expected = declared_data_size(path)
        if expected is not None:
            return path.stat().st_size == expected
        if label.exists():
            return False
        return path.stat().st_size > 0
    return path.stat().st_size > 0


def _expected_md5(data_path: Path) -> str | None:
    label = data_path.with_suffix(".LBL")
    if not label.is_file():
        return None
    try:
        module = _load_pvl(label)
        return _string(module.get("MD5_CHECKSUM"))
    except Exception:
        return None


def _md5(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _wget(
    orbit: int,
    records: list[dict[str, Any]],
    orbit_dir: Path,
    logs_dir: Path,
) -> int:
    if not records:
        return 0
    logs_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    log_path = logs_dir / f"wget_orbit{orbit:02d}_{timestamp}.log"
    list_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix=f"jiram_orbit{orbit:02d}_",
            suffix=".txt",
            dir="/tmp",
            delete=False,
        ) as stream:
            list_path = Path(stream.name)
            for record in records:
                stream.write(str(record["url"]) + "\n")
        command = [
            "wget",
            "-q",
            "-nd",
            "-P",
            str(orbit_dir),
            "--tries=3",
            "--timeout=60",
            "--waitretry=5",
            "-o",
            str(log_path),
            "-i",
            str(list_path),
        ]
        LOGGER.debug("running %s", " ".join(command))
        return subprocess.run(command, check=False).returncode
    finally:
        if list_path is not None:
            list_path.unlink(missing_ok=True)


def _mirror_orbit(
    orbit: int,
    records: list[dict[str, Any]],
    root: Path,
    verify: bool,
) -> OrbitMirrorResult:
    orbit_dir = root / ARCHIVE_RELATIVE / f"orbit{orbit:02d}"
    orbit_dir.mkdir(parents=True, exist_ok=True)
    logs_dir = root / "logs"
    ordered = sorted(
        records,
        key=lambda record: (
            0 if str(record["kind"]) == "labels" else 1,
            str(record["basename"]),
        ),
    )
    initially_skipped: set[str] = set()
    attempted: list[dict[str, Any]] = []
    for record in ordered:
        basename = str(record["basename"])
        destination = orbit_dir / basename
        if is_complete(destination):
            initially_skipped.add(basename)
        else:
            if destination.exists():
                destination.unlink()
            attempted.append(record)

    returncode = _wget(orbit, attempted, orbit_dir, logs_dir)
    if returncode:
        LOGGER.debug("wget for orbit%02d returned %d", orbit, returncode)

    complete = {
        str(record["basename"])
        for record in ordered
        if is_complete(orbit_dir / str(record["basename"]))
    }
    record_by_name = {str(record["basename"]): record for record in ordered}

    if verify:
        mismatches: list[str] = []
        for record in ordered:
            if str(record["kind"]) != "data":
                continue
            basename = str(record["basename"])
            path = orbit_dir / basename
            expected = _expected_md5(path)
            if basename not in complete or expected is None or _md5(path) != expected.lower():
                mismatches.append(basename)
        if mismatches:
            LOGGER.info("orbit%02d: retrying %d MD5 mismatch(es)", orbit, len(mismatches))
            retry_records = [record_by_name[name] for name in mismatches]
            for name in mismatches:
                (orbit_dir / name).unlink(missing_ok=True)
                complete.discard(name)
                initially_skipped.discard(name)
            _wget(orbit, retry_records, orbit_dir, logs_dir)
            for name in mismatches:
                path = orbit_dir / name
                expected = _expected_md5(path)
                if is_complete(path) and expected is not None and _md5(path) == expected.lower():
                    complete.add(name)

    failed = [
        str(record["basename"])
        for record in ordered
        if str(record["basename"]) not in complete
    ]
    skipped = len(initially_skipped & complete)
    downloaded = len(complete) - skipped
    return OrbitMirrorResult(
        orbit=orbit,
        requested=len(ordered),
        downloaded=downloaded,
        skipped=skipped,
        failed=failed,
    )


def _family_names(family: str) -> set[str]:
    return {
        "img": {"IMG"},
        "spe": {"SPE"},
        "log": {"LOG_IMG", "LOG_SPE"},
        "all": {"IMG", "SPE", "LOG_IMG", "LOG_SPE"},
    }[family]


def mirror_files(
    mirror: str | Path | None = None,
    orbits: Iterable[int] | None = None,
    family: str = "img",
    kinds: Iterable[str] = ("labels", "data"),
    jobs: int = 3,
    verify: bool = False,
) -> list[OrbitMirrorResult]:
    """Mirror selected manifest files with at most four concurrent wget jobs."""
    if jobs < 1 or jobs > 4:
        raise ValueError("--jobs must be between 1 and 4")
    root = mirror_root(mirror)
    manifest_path = root / "manifest" / "manifest.parquet"
    selected_input = None if orbits is None else sorted(set(int(value) for value in orbits))
    if selected_input is None:
        # "all" means every orbit in the live collection, not merely every
        # orbit already represented by a possibly partial manifest.
        manifest = build_manifest(root, None)
    elif manifest_path.exists():
        manifest = coerce_manifest(pd.read_parquet(manifest_path))
    else:
        manifest = build_manifest(root, selected_input)
    if selected_input is None:
        selected = sorted(int(value) for value in manifest["orbit"].dropna().unique())
    else:
        selected = selected_input
        missing = sorted(set(selected) - set(int(value) for value in manifest["orbit"].dropna().unique()))
        if missing:
            manifest = build_manifest(root, missing)

    requested_kinds = set(kinds)
    invalid_kinds = sorted(requested_kinds - {"labels", "data"})
    if invalid_kinds or not requested_kinds:
        raise ValueError("--kinds must be labels, data, or labels,data")
    family_names = _family_names(family)
    filtered = manifest.loc[
        manifest["orbit"].isin(selected)
        & manifest["family"].isin(family_names)
        & manifest["kind"].isin(requested_kinds)
    ]
    by_orbit = {
        orbit: filtered.loc[filtered["orbit"] == orbit].to_dict(orient="records")
        for orbit in selected
    }
    results: list[OrbitMirrorResult] = []
    with ThreadPoolExecutor(max_workers=min(jobs, 4)) as executor:
        futures = {
            executor.submit(_mirror_orbit, orbit, records, root, verify): orbit
            for orbit, records in by_orbit.items()
        }
        for future in as_completed(futures):
            results.append(future.result())
    return sorted(results, key=lambda result: result.orbit)


def mirror_result_table(results: list[OrbitMirrorResult]) -> str:
    lines = ["orbit  requested  downloaded  skipped  failed"]
    for result in results:
        lines.append(
            f"{result.orbit:5d}  {result.requested:9d}  {result.downloaded:10d}  "
            f"{result.skipped:7d}  {len(result.failed):6d}"
        )
        if result.failed:
            lines.append("  failed files: " + ", ".join(result.failed[:10]))
    return "\n".join(lines)
