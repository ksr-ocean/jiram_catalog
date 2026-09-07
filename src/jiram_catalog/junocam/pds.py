"""JunoCam volume manifest: INDEX.LBL/INDEX.TAB parsing and product identity.

The JunoCam PDS3 volumes carry a per-volume ``INDEX/INDEX.TAB`` with one row
per archived product (label + image), described by ``INDEX/INDEX.LBL``.  The
manifest is built from those tables rather than by crawling the ``DATA/``
trees: volume index files replace tens of thousands of directory listings.

Three archive irregularities are handled here, all measured rather than
assumed (see ``docs/reports/junocam_archive_recon.md``):

* ``VOLUME_ID`` inside the table is not the volume directory name for the
  later volumes (``"JNOJNC_00028"`` for directory ``JNOJNC_0028``), so the
  ``volume`` column is always the directory name that was fetched.
* the rows are *not* reliably fixed-width even though ``INDEX.LBL`` gives
  ``START_BYTE``/``BYTES`` for every column (volume 4 writes ``"2"`` where
  volume 3 writes ``"2"`` padded to 19 characters), so rows are split as
  quoted CSV and the label supplies the column *names and order*; the byte
  positions are kept and used only as a fallback when the field count of a
  row disagrees with the column count.
* ``FILE_SPECIFICATION_NAME`` is a full path relative to the volume root on
  every volume except 2 and 3, where it degenerates to a bare file name.  For
  those the directory is recovered by listing the volume's ``DATA/`` tree
  once and caching the file-name -> path map.

Two tables are written, because the archive answers two different questions
and the difference is not cosmetic.  A product reprocessed after its first
release is re-issued in a later volume under the same stem and a bumped
``_Vxx`` -- 15 of orbit 4's 1021 EDR products come back as ``_V02`` or
``_V03`` in volumes 4, 12 and 13 -- and the superseded file stays online in
its original volume, so both versions exist and both are indexed:

* ``manifest_files.parquet``: one row per product *file* listed by any
  volume's ``INDEX.TAB``, every version of every product.  This is what the
  mirror expands, so a reprocessed label is fetched alongside the one it
  replaced and neither is lost.
* ``manifest.parquet``: one row per *product*, the row being its highest
  version, with ``n_versions`` and ``superseded_volumes`` recording what it
  replaced.  This is the product catalogue: counting it answers "how many
  images does orbit 4 have", which counting files does not.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import re
import ssl
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin
from urllib.request import Request, urlopen

import certifi
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from ..config import mirror_root

LOGGER = logging.getLogger(__name__)

#: PDS Imaging node root holding the JunoCam PDS3 volumes.
BASE_URL = "https://planetarydata.jpl.nasa.gov/img/data/juno/"
#: The volumes that exist; ``JNOSRU_0001`` on the same host is a different
#: instrument (Stellar Reference Unit) and is not JunoCam.
FIRST_VOLUME = 1
LAST_VOLUME = 35
#: Files cached verbatim per volume under ``<mirror>/junocam/manifest/<volume>/``.
VOLUME_FILES = ("INDEX/INDEX.LBL", "INDEX/INDEX.TAB", "ERRATA.TXT")

#: ``JNC<T>_<YYYYDDD>_<OO><F><NNNNN>_V<XX>``; ``T`` E=EDR, R=RDR, M=global map.
PRODUCT_ID_RE = re.compile(
    r"^JNC(?P<type>[A-Z])_(?P<year>\d{4})(?P<doy>\d{3})_"
    r"(?P<orbit>\d{2})(?P<filter_code>[A-Z])(?P<sequence>\d+)_V(?P<version>\d+)$"
)
LEVEL_BY_TYPE = {"E": "EDR", "R": "RDR", "M": "MAP"}
#: ``PROCESSING_LEVEL_ID`` is the only field that separates the three product
#: types cleanly: 2 = EDR (CODMAC edited), 3 = RDR (calibrated), 4 = the
#: L1B global map mosaics, which share the ``JNCR_`` prefix and the
#: ``STANDARD_DATA_PRODUCT_ID = "JUNOCAM-RDR"`` of a real RDR but are
#: reprojected mosaics under ``DATA/GLOBAL_MAPS/``, not framelet strips.
LEVEL_BY_PROCESSING_LEVEL = {"2": "EDR", "3": "RDR"}
#: Directory the global map products live in, whatever their level says.
MAP_DIRECTORY = "GLOBAL_MAPS"
#: Filter-combination code of the product id (EDR/RDR SIS Appendix B).
FILTER_CODE_NAMES = {
    "A": "ALL",
    "B": "BLUE",
    "C": "RGB",
    "G": "GREEN",
    "M": "METHANE",
    "R": "RED",
    "T": "RED+BLUE",
}

MANIFEST_COLUMNS = [
    "volume",
    "product_id",
    "product_stem",
    "level",
    "orbit",
    "orbit_from_id",
    "orbit_from_path",
    "orbit_disagree",
    "year",
    "doy_dir",
    "filter_code",
    "sequence",
    "version",
    "n_versions",
    "superseded_volumes",
    "start_time",
    "stop_time",
    "target_name",
    "file_spec",
    "url_lbl",
    "url_img",
    "label_md5",
    "index_row_json",
]
MANIFEST_SCHEMA = pa.schema(
    [
        ("volume", pa.string()),
        ("product_id", pa.string()),
        ("product_stem", pa.string()),
        ("level", pa.string()),
        ("orbit", pa.int16()),
        ("orbit_from_id", pa.int16()),
        ("orbit_from_path", pa.int16()),
        ("orbit_disagree", pa.bool_()),
        ("year", pa.int16()),
        ("doy_dir", pa.string()),
        ("filter_code", pa.string()),
        ("sequence", pa.int32()),
        ("version", pa.int16()),
        ("n_versions", pa.int16()),
        ("superseded_volumes", pa.string()),
        ("start_time", pa.timestamp("ns")),
        ("stop_time", pa.timestamp("ns")),
        ("target_name", pa.string()),
        ("file_spec", pa.string()),
        ("url_lbl", pa.string()),
        ("url_img", pa.string()),
        ("label_md5", pa.string()),
        ("index_row_json", pa.string()),
    ]
)


def ca_bundle() -> str:
    """Path of the CA bundle the PDS Imaging node validates against.

    The node's chain fails the system trust store on this cluster and passes
    with ``certifi``; every fetch here and every ``wget`` in
    :mod:`~jiram_catalog.junocam.mirror` is pointed at this file.
    """
    return certifi.where()


def _ssl_context() -> ssl.SSLContext:
    return ssl.create_default_context(cafile=ca_bundle())


def fetch_bytes(url: str, *, attempts: int = 4, timeout: float = 300.0) -> bytes:
    """Fetch one URL over TLS verified against :func:`ca_bundle`, with backoff."""
    request = Request(url, headers={"User-Agent": "Wget/1.19.5"})
    context = _ssl_context()
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            with urlopen(request, timeout=timeout, context=context) as response:
                return response.read()
        except (OSError, TimeoutError) as exc:
            last_error = exc
            LOGGER.warning("fetch failed (%d/%d) for %s: %s", attempt, attempts, url, exc)
            time.sleep(min(60.0, 5.0 * attempt))
    raise RuntimeError(f"fetch failed after {attempts} attempts: {url}") from last_error


def volume_name(number: int) -> str:
    """``3`` -> ``"JNOJNC_0003"`` (the directory name, not ``VOLUME_ID``)."""
    return f"JNOJNC_{int(number):04d}"


def volume_url(volume: str) -> str:
    return urljoin(BASE_URL, f"{volume}/")


def parse_product_id(product_id: str) -> dict[str, object] | None:
    """Split a JunoCam product id into its documented fields, or ``None``.

    ``None`` rather than an exception because the manifest must survive a
    malformed row in the archive: the caller counts and reports them.
    """
    match = PRODUCT_ID_RE.match(str(product_id).strip())
    if match is None:
        return None
    groups = match.groupdict()
    type_letter = groups["type"]
    return {
        "type": type_letter,
        "level": LEVEL_BY_TYPE.get(type_letter, "OTHER"),
        "year": int(groups["year"]),
        "doy": groups["doy"],
        "orbit": int(groups["orbit"]),
        "filter_code": groups["filter_code"],
        "filter_code_name": FILTER_CODE_NAMES.get(groups["filter_code"], "UNKNOWN"),
        "sequence": int(groups["sequence"]),
        "version": int(groups["version"]),
        "stem": product_id[: product_id.rindex("_V")],
    }


@dataclass(frozen=True)
class IndexColumn:
    """One ``OBJECT = COLUMN`` of an ``INDEX.LBL``."""

    name: str
    start_byte: int
    n_bytes: int
    data_type: str


_COLUMN_BLOCK_RE = re.compile(
    r"OBJECT\s*=\s*COLUMN(?P<body>.*?)END_OBJECT\s*=\s*COLUMN", re.S
)


def _keyword(body: str, keyword: str) -> str | None:
    match = re.search(rf"^\s*{keyword}\s*=\s*(.+)$", body, re.M)
    if match is None:
        return None
    return match.group(1).strip().strip('"').strip()


def parse_index_label(text: str) -> list[IndexColumn]:
    """Read the ordered column definitions out of an ``INDEX.LBL``.

    Names, order and byte positions all come from the label; nothing about
    the 16 columns is hard-coded, because the column set is not identical
    across the 35 volumes (widths and ``START_BYTE`` differ, and one column,
    ``FILE_SPECIFICATION_NAME``, changes meaning between volumes).
    """
    columns: list[IndexColumn] = []
    for match in _COLUMN_BLOCK_RE.finditer(text):
        body = match.group("body")
        name = _keyword(body, "NAME")
        start = _keyword(body, "START_BYTE")
        width = _keyword(body, "BYTES")
        if name is None or start is None or width is None:
            continue
        columns.append(
            IndexColumn(
                name=name,
                start_byte=int(start),
                n_bytes=int(width),
                data_type=_keyword(body, "DATA_TYPE") or "",
            )
        )
    if not columns:
        raise ValueError("INDEX.LBL declares no COLUMN objects")
    return columns


def _clean(value: str) -> str:
    return value.strip().strip('"').strip()


def parse_index_table(text: str, columns: Sequence[IndexColumn]) -> list[dict[str, str]]:
    """Split an ``INDEX.TAB`` into dictionaries keyed by the label's names.

    Rows are quoted CSV, which the archive honours everywhere; the declared
    byte positions are the fallback for a row whose field count disagrees
    (a row containing an unescaped comma inside a free-text description).
    """
    names = [column.name for column in columns]
    rows: list[dict[str, str]] = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if not line.strip():
            continue
        try:
            fields = next(csv.reader(io.StringIO(line)))
        except csv.Error:
            fields = []
        if len(fields) != len(names):
            fields = [
                line[column.start_byte - 1 : column.start_byte - 1 + column.n_bytes]
                for column in columns
            ]
        rows.append({name: _clean(field) for name, field in zip(names, fields)})
    return rows


class _HrefParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        href = dict(attrs).get("href")
        if href:
            self.hrefs.append(href)


def _listing_entries(url: str) -> tuple[list[str], list[str]]:
    """Return ``(subdirectories, file names)`` of an Apache index listing."""
    parser = _HrefParser()
    parser.feed(fetch_bytes(url).decode("utf-8", errors="replace"))
    directories: list[str] = []
    files: list[str] = []
    for href in parser.hrefs:
        if href.startswith(("?", "/", "http:", "https:", "mailto:")):
            continue
        if href in {"", ".."} or href.startswith(".."):
            continue
        if href.endswith("/"):
            directories.append(href.rstrip("/"))
        else:
            files.append(href)
    return sorted(set(directories)), sorted(set(files))


def _walk_volume_data(volume: str, *, max_depth: int = 4) -> dict[str, str]:
    """Map every ``*.LBL`` file name in a volume's ``DATA/`` tree to its path.

    Used only for volumes 2 and 3, whose ``FILE_SPECIFICATION_NAME`` is a bare
    file name.  Ten listings replace guessing the phase directory.
    """
    root = urljoin(volume_url(volume), "DATA/")
    mapping: dict[str, str] = {}
    stack: list[tuple[str, str, int]] = [(root, "DATA", 0)]
    while stack:
        url, relative, depth = stack.pop()
        directories, files = _listing_entries(url)
        for name in files:
            if name.upper().endswith(".LBL"):
                mapping[name] = f"{relative}/{name}"
        if depth < max_depth:
            for name in directories:
                stack.append((urljoin(url, f"{name}/"), f"{relative}/{name}", depth + 1))
    return mapping


def _volume_cache(root: Path, volume: str) -> Path:
    return root / "junocam" / "manifest" / volume


def _cached_fetch(root: Path, volume: str, relative: str, refresh: bool) -> str:
    destination = _volume_cache(root, volume) / relative.replace("/", "_")
    if refresh or not destination.is_file() or destination.stat().st_size == 0:
        destination.parent.mkdir(parents=True, exist_ok=True)
        payload = fetch_bytes(urljoin(volume_url(volume), relative))
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        temporary.write_bytes(payload)
        temporary.replace(destination)
    return destination.read_text(encoding="latin-1")


def _orbit_from_spec(file_spec: str) -> int | None:
    match = re.search(r"ORBIT_(\d+)", file_spec.upper())
    return int(match.group(1)) if match else None


def volume_records(
    root: Path, volume: str, *, refresh: bool = False
) -> tuple[list[dict[str, object]], dict[str, int]]:
    """Parse one volume's index into manifest records plus a small tally."""
    cached: dict[str, str] = {}
    for relative in VOLUME_FILES:
        try:
            cached[relative] = _cached_fetch(root, volume, relative, refresh)
        except RuntimeError:  # a missing ERRATA must not lose the whole volume
            LOGGER.warning("%s: could not fetch %s", volume, relative)
            cached[relative] = ""
    label_text = cached["INDEX/INDEX.LBL"]
    table_text = cached["INDEX/INDEX.TAB"]

    columns = parse_index_label(label_text)
    rows = parse_index_table(table_text, columns)
    tally = {
        "rows": len(rows),
        "bad_product_id": 0,
        "map_products": 0,
        "level_conflict": 0,
    }

    specs = [row.get("FILE_SPECIFICATION_NAME", "") for row in rows]
    if any(spec and "/" not in spec for spec in specs):
        cache = _volume_cache(root, volume) / "listing_map.json"
        if refresh or not cache.is_file():
            LOGGER.info("%s: file specs lack a path; listing DATA/ tree", volume)
            mapping = _walk_volume_data(volume)
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(mapping, indent=1), encoding="utf-8")
        listing_map = json.loads(cache.read_text(encoding="utf-8"))
    else:
        listing_map = {}

    base = volume_url(volume)
    records: list[dict[str, object]] = []
    for row in rows:
        product_id = row.get("PRODUCT_ID", "")
        parsed = parse_product_id(product_id)
        if parsed is None:
            tally["bad_product_id"] += 1
            continue
        file_spec = row.get("FILE_SPECIFICATION_NAME", "").replace("\\", "/").strip()
        if file_spec and "/" not in file_spec:
            file_spec = listing_map.get(file_spec, file_spec)
        level = LEVEL_BY_PROCESSING_LEVEL.get(
            row.get("PROCESSING_LEVEL_ID", "").strip(), str(parsed["level"])
        )
        if level not in ("EDR", "RDR") or MAP_DIRECTORY in file_spec.upper():
            tally["map_products"] += 1
            continue
        if level != parsed["level"]:
            tally["level_conflict"] += 1
            continue
        orbit_from_path = _orbit_from_spec(file_spec)
        orbit_from_id = int(parsed["orbit"])
        url_lbl = urljoin(base, file_spec)
        records.append(
            {
                "volume": volume,
                "product_id": product_id,
                "product_stem": parsed["stem"],
                "level": level,
                "orbit": orbit_from_id,
                "orbit_from_id": orbit_from_id,
                "orbit_from_path": orbit_from_path,
                "orbit_disagree": bool(
                    orbit_from_path is not None and orbit_from_path != orbit_from_id
                ),
                "year": int(parsed["year"]),
                "doy_dir": parsed["doy"],
                "filter_code": parsed["filter_code"],
                "sequence": int(parsed["sequence"]),
                "version": int(parsed["version"]),
                "n_versions": 1,
                "superseded_volumes": "",
                "start_time": row.get("START_TIME", ""),
                "stop_time": row.get("STOP_TIME", ""),
                "target_name": row.get("TARGET_NAME", ""),
                "file_spec": file_spec,
                "url_lbl": url_lbl,
                "url_img": re.sub(r"\.LBL$", ".IMG", url_lbl, flags=re.I),
                "label_md5": row.get("PRODUCT_LABEL_MD5CHECKSUM", ""),
                "index_row_json": json.dumps(row, sort_keys=True),
            }
        )
    return records, tally


def coerce_manifest(df: pd.DataFrame) -> pd.DataFrame:
    """Apply the stable manifest column order and physical types."""
    result = df.reindex(columns=MANIFEST_COLUMNS).copy()
    for column, dtype in (
        ("orbit", "Int16"),
        ("orbit_from_id", "Int16"),
        ("orbit_from_path", "Int16"),
        ("year", "Int16"),
        ("sequence", "Int32"),
        ("version", "Int16"),
        ("n_versions", "Int16"),
    ):
        result[column] = pd.to_numeric(result[column], errors="coerce").astype(dtype)
    for column in ("start_time", "stop_time"):
        parsed = pd.to_datetime(result[column], errors="coerce", format="mixed", utc=True)
        result[column] = parsed.dt.tz_localize(None)
    result["orbit_disagree"] = result["orbit_disagree"].fillna(False).astype("bool")
    for column in set(MANIFEST_COLUMNS) - {
        "orbit",
        "orbit_from_id",
        "orbit_from_path",
        "year",
        "sequence",
        "version",
        "n_versions",
        "start_time",
        "stop_time",
        "orbit_disagree",
    }:
        result[column] = result[column].fillna("").astype("str")
    return result


def annotate_versions(df: pd.DataFrame) -> pd.DataFrame:
    """Set ``n_versions`` to how many releases each product stem has."""
    if df.empty:
        return df
    counts = df.groupby(["level", "product_stem"], sort=False)["version"].transform("size")
    return df.assign(n_versions=counts.astype("Int16"))


def deduplicate(df: pd.DataFrame) -> pd.DataFrame:
    """Keep the highest version of each product, recording what it replaced.

    A reprocessed product is re-released in a later volume under the same
    stem and a bumped ``_Vxx`` (five orbit-4 products of volume 3 return as
    ``_V02`` in volume 4).  Counting both copies would double-count products;
    the archive's own convention is that the highest version supersedes.
    """
    if df.empty:
        return df
    ordered = df.sort_values(
        ["level", "product_stem", "version", "volume"], kind="stable"
    )
    groups = ordered.groupby(["level", "product_stem"], sort=False)
    counts = groups["version"].transform("size")
    others = groups["volume"].transform(lambda values: ";".join(values.iloc[:-1]))
    ordered = ordered.assign(n_versions=counts.astype("Int16"), superseded_volumes=others)
    return ordered.drop_duplicates(["level", "product_stem"], keep="last")


def _write_manifest(df: pd.DataFrame, path: Path) -> None:
    arrays = [
        pa.array(df[column], type=MANIFEST_SCHEMA.field(column).type, from_pandas=True)
        for column in MANIFEST_COLUMNS
    ]
    temporary = path.with_suffix(".parquet.tmp")
    pq.write_table(pa.Table.from_arrays(arrays, schema=MANIFEST_SCHEMA), temporary)
    temporary.replace(path)


def manifest_path(mirror: str | Path | None = None) -> Path:
    return mirror_root(mirror) / "junocam" / "manifest" / "manifest.parquet"


def manifest_files_path(mirror: str | Path | None = None) -> Path:
    """The every-version companion of :func:`manifest_path`."""
    return mirror_root(mirror) / "junocam" / "manifest" / "manifest_files.parquet"


def load_manifest(mirror: str | Path | None = None) -> pd.DataFrame:
    path = manifest_path(mirror)
    if not path.exists():
        raise FileNotFoundError(
            f"JunoCam manifest not found: {path} (run `junocam manifest` first)"
        )
    return coerce_manifest(pd.read_parquet(path))


def load_manifest_files(mirror: str | Path | None = None) -> pd.DataFrame:
    """Every product file of every volume, all versions.

    Falls back to the deduplicated product manifest when the file table is
    missing, so a mirror built before this table existed still works.
    """
    path = manifest_files_path(mirror)
    if not path.exists():
        return load_manifest(mirror)
    return coerce_manifest(pd.read_parquet(path))


def discover_volumes(mirror: str | Path | None = None) -> list[int]:
    """Discover current volume directories; log the source of offline fallback."""
    root = mirror_root(mirror)
    cache = root / "junocam/manifest/volume_directories.json"
    try:
        parser = _HrefParser()
        parser.feed(fetch_bytes(BASE_URL, attempts=1, timeout=20).decode("utf-8", errors="replace"))
        numbers = sorted({int(match.group(1)) for href in parser.hrefs
                          if (match := re.search(r"(?:^|/)JNOJNC_(\d{4})/$", href))})
        if not numbers:
            raise ValueError("No JunoCam volume directories in root listing")
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(numbers), encoding="utf-8")
        LOGGER.info("Discovered %d JunoCam volume directories from %s", len(numbers), BASE_URL)
        return numbers
    except (OSError, RuntimeError, ValueError) as exc:
        if cache.exists():
            try:
                numbers = sorted({int(v) for v in json.loads(cache.read_text()) if int(v) > 0})
                if numbers:
                    LOGGER.warning("Volume discovery unavailable (%s); using cached directory snapshot %s", exc, cache)
                    return numbers
            except (OSError, ValueError, TypeError):
                pass
        LOGGER.warning("Volume discovery unavailable (%s); using historical offline fallback 1-%d, completeness unverified", exc, LAST_VOLUME)
        return list(range(FIRST_VOLUME, LAST_VOLUME + 1))


def build_manifest(
    mirror: str | Path | None = None,
    volumes: Iterable[int] | None = None,
    refresh: bool = False,
) -> pd.DataFrame:
    """Fetch/cache the selected volumes' index files and write the manifest."""
    root = mirror_root(mirror)
    selected = (
        discover_volumes(root)
        if volumes is None
        else sorted(set(int(value) for value in volumes))
    )
    bad = [value for value in selected if value < FIRST_VOLUME]
    if bad:
        raise ValueError(f"volume numbers must be positive: {bad}")

    records: list[dict[str, object]] = []
    tallies: dict[str, dict[str, int]] = {}
    for number in selected:
        volume = volume_name(number)
        LOGGER.info("reading index of %s", volume)
        volume_rows, tally = volume_records(root, volume, refresh=refresh)
        records.extend(volume_rows)
        tallies[volume] = tally
    new = coerce_manifest(pd.DataFrame.from_records(records))

    path = manifest_path(root)
    files_path = manifest_files_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    names = {volume_name(number) for number in selected}
    if files_path.exists():
        old = coerce_manifest(pd.read_parquet(files_path))
        old = old.loc[~old["volume"].isin(names)]
        every = pd.concat([old, new], ignore_index=True)
    else:
        every = new
    every = annotate_versions(coerce_manifest(every))
    every = every.sort_values(
        ["orbit", "level", "product_id"], kind="stable", ignore_index=True
    )
    _write_manifest(every, files_path)
    combined = deduplicate(every)
    combined = combined.sort_values(
        ["orbit", "level", "product_id"], kind="stable", ignore_index=True
    )
    _write_manifest(combined, path)
    dropped = sum(
        tally["bad_product_id"] + tally["map_products"] + tally["level_conflict"]
        for tally in tallies.values()
    )
    LOGGER.info(
        "wrote %d product rows to %s and %d file rows to %s "
        "(%d non-EDR/RDR or unparsable index rows dropped)",
        len(combined),
        path,
        len(every),
        files_path,
        dropped,
    )
    return combined


def manifest_summary(df: pd.DataFrame, orbits: Iterable[int] = ()) -> str:
    """Counts by level and volume coverage, plus per-orbit totals if asked."""
    lines = [
        f"manifest rows: {len(df)}",
        f"volumes: {df['volume'].nunique()} "
        f"({df['volume'].min()} .. {df['volume'].max()})",
    ]
    for level, count in sorted(df["level"].value_counts().items()):
        lines.append(f"  {level}: {count}")
    superseded = int((df["n_versions"].fillna(1) > 1).sum())
    lines.append(f"products superseding an earlier version: {superseded}")
    lines.append(f"orbit id/path disagreements: {int(df['orbit_disagree'].sum())}")
    wanted = sorted(set(int(value) for value in orbits))
    if wanted:
        lines.append("orbit  EDR  RDR")
        for orbit in wanted:
            part = df.loc[df["orbit"] == orbit]
            lines.append(
                f"{orbit:5d}  {int((part['level'] == 'EDR').sum()):3d}  "
                f"{int((part['level'] == 'RDR').sum()):3d}"
            )
    return "\n".join(lines)
