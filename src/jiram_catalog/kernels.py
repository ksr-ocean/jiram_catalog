"""SPICE kernel manifest and mirror-side download for the geometry engine.

The archive names the kernels it used in every label, but only the
date-specific attitude CK and the reconstructed trajectory SPK are worth
following; every other entry is superseded by the static set kept in the
mirror. ``naif.jpl.nasa.gov`` fails TLS verification against the system CA
bundle, so every fetch is pinned to the ``certifi`` bundle.
"""

from __future__ import annotations

import logging
import re
import ssl
import subprocess
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from urllib.request import Request, urlopen

import certifi
import pandas as pd

from .geometry import OPTIONAL_STATIC_SPKS, orbit_kernel_names, spk_aliases

LOGGER = logging.getLogger(__name__)

OPS_BASE = "https://naif.jpl.nasa.gov/pub/naif/JUNO/kernels"
PDS_BASE = (
    "https://naif.jpl.nasa.gov/pub/naif/pds/data/jno-j_e_ss-spice-6-v1.0"
    "/jnosp_1000/data"
)
KINDS = ("lsk", "pck", "fk", "ik", "sclk", "spk", "ck")
MAX_JOBS = 4
#: Static kernels other than the spacecraft clock, whose version is discovered.
STATIC_KERNELS: tuple[tuple[str, str], ...] = (
    ("lsk", "naif0012.tls"),
    ("pck", "pck00010.tpc"),
    ("fk", "juno_v12.tf"),
    ("ik", "juno_jiram_v02.ti"),
)


@dataclass
class KernelStatus:
    """One needed kernel and what happened to it."""

    kind: str
    name: str
    present: bool = False
    path: Path | None = None
    remote_bytes: int | None = None
    downloaded: bool = False
    failed: bool = False
    urls: list[str] = field(default_factory=list)


def ca_bundle() -> str:
    """Path of the certifi CA bundle NAIF's TLS chain needs."""
    return certifi.where()


def _fetch_text(url: str, *, timeout: float = 120.0) -> str:
    context = ssl.create_default_context(cafile=ca_bundle())
    request = Request(url, headers={"User-Agent": "Wget/1.19.5"})
    with urlopen(request, timeout=timeout, context=context) as response:
        return response.read().decode("utf-8", errors="replace")


def latest_sclk_name() -> str:
    """Highest-numbered ``JNO_SCLKSCET.*.tsc`` in the operational listing."""
    html = _fetch_text(f"{OPS_BASE}/sclk/")
    versions = re.findall(r"JNO_SCLKSCET\.(\d+)\.tsc", html)
    if not versions:
        raise RuntimeError(f"no JNO_SCLKSCET kernels listed at {OPS_BASE}/sclk/")
    return f"JNO_SCLKSCET.{max(versions, key=int)}.tsc"


def static_kernels() -> list[tuple[str, str]]:
    """The mission-static kernels, with the current spacecraft clock version."""
    entries = list(STATIC_KERNELS)
    entries.append(("sclk", latest_sclk_name()))
    entries.extend(("spk", name) for name in OPTIONAL_STATIC_SPKS)
    return entries


def needed_kernels(frames: pd.DataFrame) -> list[tuple[str, str]]:
    """Static kernels plus the CK/SPK names the selected frames' labels cite."""
    names = orbit_kernel_names(frames)
    dynamic = [("spk", name) for name in names["spk"]]
    dynamic += [("ck", name) for name in names["ck"]]
    return static_kernels() + dynamic


def resolve_present(mirror: str | Path, kind: str, name: str) -> Path | None:
    """Return the mirrored path of a kernel, honouring the SPK rename."""
    root = Path(mirror) / "spice" / kind
    candidates = spk_aliases(name) if kind == "spk" else [name]
    for candidate in candidates:
        path = root / candidate
        if path.is_file() and path.stat().st_size > 0:
            return path
    return None


def kernel_urls(kind: str, name: str) -> list[str]:
    """Operational URL first, then the PDS archive URL (SPKs renamed there)."""
    archive_name = name
    if kind == "spk":
        for prefix, renamed in (("spk_rec_", "juno_rec_"), ("spk_pre_", "juno_pre_")):
            if name.startswith(prefix):
                archive_name = renamed + name[len(prefix) :]
    return [f"{OPS_BASE}/{kind}/{name}", f"{PDS_BASE}/{kind}/{archive_name}"]


def remote_size(url: str, *, timeout: float = 60.0) -> int | None:
    """Content-Length reported by ``wget --spider``, or None if unavailable."""
    command = [
        "wget",
        "-S",
        "--spider",
        f"--ca-certificate={ca_bundle()}",
        "--tries=1",
        f"--timeout={int(timeout)}",
        url,
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout + 30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    matches = re.findall(r"Content-Length:\s*(\d+)", result.stdout + result.stderr)
    return int(matches[-1]) if matches else None


def download_kernel(mirror: str | Path, kind: str, name: str) -> Path | None:
    """Download one kernel through wget, trying the PDS archive as a fallback."""
    destination = Path(mirror) / "spice" / kind / name
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".part")
    for index, url in enumerate(kernel_urls(kind, name)):
        if index > 0:
            partial.unlink(missing_ok=True)
        command = [
            "wget",
            "-q",
            f"--ca-certificate={ca_bundle()}",
            "--tries=3",
            "--timeout=120",
            "-c",
            "-O",
            str(partial),
            url,
        ]
        LOGGER.debug("downloading %s", url)
        try:
            result = subprocess.run(command)
        except OSError as exc:
            LOGGER.warning("wget failed for %s: %s", url, exc)
            continue
        if result.returncode == 0 and partial.is_file() and partial.stat().st_size > 0:
            partial.replace(destination)
            return destination
        LOGGER.warning("download failed (%s): %s", result.returncode, url)
    partial.unlink(missing_ok=True)
    return None


def ensure_kernels(
    mirror: str | Path,
    orbits: Iterable[int] | None,
    *,
    frames: pd.DataFrame | None = None,
    jobs: int = 3,
    dry_run: bool = False,
) -> list[KernelStatus]:
    """Report, and unless ``dry_run``, download every kernel the orbits need."""
    root = Path(mirror)
    if frames is None:
        from .index import load_frames

        frames = load_frames(root, orbits)
    statuses = []
    for kind, name in needed_kernels(frames):
        path = resolve_present(root, kind, name)
        statuses.append(
            KernelStatus(
                kind=kind,
                name=name,
                present=path is not None,
                path=path,
                urls=kernel_urls(kind, name),
            )
        )

    outstanding = [status for status in statuses if not status.present]
    if not outstanding:
        return statuses
    worker_count = max(1, min(MAX_JOBS, int(jobs)))

    if dry_run:
        with ThreadPoolExecutor(max_workers=worker_count) as pool:
            sizes = pool.map(lambda status: remote_size(status.urls[0]), outstanding)
        for status, size in zip(outstanding, sizes, strict=True):
            status.remote_bytes = size
        return statuses

    LOGGER.info("downloading %d missing kernel(s) with %d job(s)", len(outstanding), worker_count)
    with ThreadPoolExecutor(max_workers=worker_count) as pool:
        results = list(
            pool.map(
                lambda status: download_kernel(root, status.kind, status.name),
                outstanding,
            )
        )
    for status, path in zip(outstanding, results, strict=True):
        if path is None:
            status.failed = True
        else:
            status.present = True
            status.downloaded = True
            status.path = path
    return statuses


def _format_bytes(size: int) -> str:
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024.0 or unit == "GiB":
            return f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{value:.1f} GiB"


def kernel_report_text(statuses: list[KernelStatus], *, dry_run: bool = False) -> str:
    """Per-kernel listing (dry run only) plus the per-kind count table."""
    lines: list[str] = []
    if dry_run:
        missing_bytes = 0
        unknown = 0
        for status in statuses:
            if status.present:
                lines.append(f"present  {status.kind:4s}  {status.name}")
                continue
            if status.remote_bytes is None:
                unknown += 1
                lines.append(f"MISSING  {status.kind:4s}  {status.name}")
            else:
                missing_bytes += status.remote_bytes
                lines.append(
                    f"MISSING  {status.kind:4s}  {status.name}"
                    f"  ({_format_bytes(status.remote_bytes)})"
                )
        suffix = f" (+{unknown} of unknown size)" if unknown else ""
        lines.append(f"total missing bytes: {missing_bytes}{suffix}")
        lines.append("")

    lines.append("kind  needed  present  downloaded  failed")
    for kind in KINDS:
        part = [status for status in statuses if status.kind == kind]
        if not part:
            continue
        lines.append(
            f"{kind:4s}  {len(part):6d}  {sum(s.present for s in part):7d}"
            f"  {sum(s.downloaded for s in part):10d}  {sum(s.failed for s in part):6d}"
        )
    return "\n".join(lines)
