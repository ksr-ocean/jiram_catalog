"""Apache listing parsing and archive manifest construction."""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from html.parser import HTMLParser
import time
from pathlib import Path
from urllib.parse import urljoin
from urllib.request import Request, urlopen

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from .config import mirror_root

BASE_URL = (
    "https://atmos.nmsu.edu/PDS/data/PDS4/juno_jiram_bundle/data_calibrated/"
)
MANIFEST_COLUMNS = [
    "orbit",
    "basename",
    "ext",
    "family",
    "kind",
    "product_id",
    "url",
    "listed_size_bytes",
    "listed_mtime",
]
MANIFEST_SCHEMA = pa.schema(
    [
        ("orbit", pa.int16()),
        ("basename", pa.string()),
        ("ext", pa.string()),
        ("family", pa.string()),
        ("kind", pa.string()),
        ("product_id", pa.string()),
        ("url", pa.string()),
        ("listed_size_bytes", pa.int64()),
        ("listed_mtime", pa.string()),
    ]
)

LOGGER = logging.getLogger(__name__)


class _ListingParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[tuple[str | None, list[str]]] = []
        self.hrefs: list[str] = []
        self._in_row = False
        self._in_cell = False
        self._href: str | None = None
        self._cells: list[str] = []
        self._cell_text: list[str] = []

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        attributes = dict(attrs)
        if tag == "tr":
            self._in_row = True
            self._href = None
            self._cells = []
        elif tag in {"td", "th"} and self._in_row:
            self._in_cell = True
            self._cell_text = []
        elif tag == "a":
            href = attributes.get("href")
            if href is not None:
                self.hrefs.append(href)
                if self._in_row and self._href is None:
                    self._href = href

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._cell_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._in_cell:
            self._cells.append("".join(self._cell_text).strip())
            self._in_cell = False
        elif tag == "tr" and self._in_row:
            self.rows.append((self._href, self._cells))
            self._in_row = False


def family_for_basename(basename: str) -> str:
    """Classify an archive basename without confusing LOG and non-LOG files."""
    for prefix, family in (
        ("JIR_LOG_IMG_RDR_", "LOG_IMG"),
        ("JIR_LOG_SPE_RDR_", "LOG_SPE"),
        ("JIR_IMG_RDR_", "IMG"),
        ("JIR_SPE_RDR_", "SPE"),
    ):
        if basename.startswith(prefix):
            return family
    return "OTHER"


def _kind_for_extension(ext: str) -> str:
    upper = ext.upper()
    if upper in {"LBL", "XML"}:
        return "labels"
    if upper in {"IMG", "DAT", "TAB"}:
        return "data"
    return "other"


def _listed_size(value: str) -> int:
    value = value.strip()
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)\s*([KMGTP]?)", value, re.I)
    if match is None:
        return -1
    number = float(match.group(1))
    exponent = "KMGTP".find(match.group(2).upper()) + 1 if match.group(2) else 0
    return int(number * (1024**exponent))


def parse_listing(html: str, orbit: int, base_url: str) -> list[dict[str, object]]:
    """Parse one Apache mod_autoindex orbit listing into manifest records."""
    parser = _ListingParser()
    parser.feed(html)
    records: list[dict[str, object]] = []
    for href, cells in parser.rows:
        if href is None or href.startswith(("?", "/")) or href.endswith("/"):
            continue
        basename = href.rsplit("/", 1)[-1]
        if basename in {"", ".."}:
            continue
        if "." in basename:
            product_id, ext = basename.rsplit(".", 1)
        else:
            product_id, ext = basename, ""
        listed_mtime = cells[2].strip() if len(cells) > 2 else ""
        size_text = cells[3] if len(cells) > 3 else ""
        records.append(
            {
                "orbit": orbit,
                "basename": basename,
                "ext": ext,
                "family": family_for_basename(basename),
                "kind": _kind_for_extension(ext),
                "product_id": product_id,
                "url": urljoin(base_url.rstrip("/") + "/", href),
                "listed_size_bytes": _listed_size(size_text),
                "listed_mtime": listed_mtime,
            }
        )
    return records


def discover_orbits(html: str) -> list[int]:
    """Return the orbit directory numbers linked by the collection listing."""
    parser = _ListingParser()
    parser.feed(html)
    result = {
        int(match.group(1))
        for href in parser.hrefs
        if (match := re.fullmatch(r"orbit(\d{2})/", href)) is not None
    }
    return sorted(result)


def fetch_listing(url: str, *, attempts: int = 5, timeout: float = 300.0) -> str:
    """Fetch a listing with the user agent permitted by the archive robots file.

    Large orbit listings (orbit50 is ~36 MB) can exceed a short socket timeout,
    so use a long timeout and retry with backoff on transient errors.
    """
    request = Request(url, headers={"User-Agent": "Wget/1.19.5"})
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            with urlopen(request, timeout=timeout) as response:
                return response.read().decode("utf-8", errors="replace")
        except (OSError, TimeoutError) as exc:  # includes URLError, socket timeouts
            last_error = exc
            logging.getLogger(__name__).warning(
                "listing fetch failed (%d/%d) for %s: %s", attempt, attempts, url, exc
            )
            time.sleep(min(60.0, 5.0 * attempt))
    raise RuntimeError(f"listing fetch failed after {attempts} attempts: {url}") from last_error


def _empty_manifest() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "orbit": pd.Series(dtype="int16"),
            "basename": pd.Series(dtype="str"),
            "ext": pd.Series(dtype="str"),
            "family": pd.Series(dtype="str"),
            "kind": pd.Series(dtype="str"),
            "product_id": pd.Series(dtype="str"),
            "url": pd.Series(dtype="str"),
            "listed_size_bytes": pd.Series(dtype="int64"),
            "listed_mtime": pd.Series(dtype="str"),
        }
    )


def coerce_manifest(df: pd.DataFrame) -> pd.DataFrame:
    """Apply the stable manifest column order and nullable physical types."""
    if df.empty:
        return _empty_manifest()
    result = df.reindex(columns=MANIFEST_COLUMNS).copy()
    result["orbit"] = pd.to_numeric(result["orbit"], errors="raise").astype("int16")
    result["listed_size_bytes"] = pd.to_numeric(
        result["listed_size_bytes"], errors="raise"
    ).astype("int64")
    for column in set(MANIFEST_COLUMNS) - {"orbit", "listed_size_bytes"}:
        result[column] = result[column].astype("str")
    return result


def build_manifest(
    mirror: str | Path | None = None,
    orbits: Iterable[int] | None = None,
    refresh: bool = False,
) -> pd.DataFrame:
    """Fetch/cache selected listings and merge them into manifest.parquet."""
    root = mirror_root(mirror)
    manifest_dir = root / "manifest"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = manifest_dir / "manifest.parquet"

    LOGGER.info("fetching collection listing")
    collection_html = fetch_listing(BASE_URL)
    available = discover_orbits(collection_html)
    if orbits is None:
        selected = available
    else:
        selected = sorted(set(int(orbit) for orbit in orbits))
        missing = sorted(set(selected) - set(available))
        if missing:
            raise ValueError(
                "orbit directories not present in archive: "
                + ", ".join(str(value) for value in missing)
            )

    new_records: list[dict[str, object]] = []
    for orbit in selected:
        cache = manifest_dir / f"listing_orbit{orbit:02d}.html"
        orbit_url = urljoin(BASE_URL, f"orbit{orbit:02d}/")
        if refresh or not cache.exists():
            LOGGER.info("fetching orbit%02d listing", orbit)
            listing = fetch_listing(orbit_url)
            cache.write_text(listing, encoding="utf-8")
        else:
            LOGGER.info("using cached orbit%02d listing", orbit)
            listing = cache.read_text(encoding="utf-8")
        new_records.extend(parse_listing(listing, orbit, orbit_url))

    new_df = coerce_manifest(pd.DataFrame.from_records(new_records))
    if manifest_path.exists():
        old_df = coerce_manifest(pd.read_parquet(manifest_path))
        old_df = old_df.loc[~old_df["orbit"].isin(selected)]
        combined = pd.concat([old_df, new_df], ignore_index=True)
    else:
        combined = new_df
    combined = coerce_manifest(combined)
    combined = combined.sort_values(
        ["orbit", "basename"], kind="stable", ignore_index=True
    )
    temporary = manifest_path.with_suffix(".parquet.tmp")
    arrays = [
        pa.array(combined[column], type=MANIFEST_SCHEMA.field(column).type)
        for column in MANIFEST_COLUMNS
    ]
    pq.write_table(pa.Table.from_arrays(arrays, schema=MANIFEST_SCHEMA), temporary)
    temporary.replace(manifest_path)
    LOGGER.info("wrote %d manifest rows to %s", len(combined), manifest_path)
    return combined


def manifest_count_table(df: pd.DataFrame, orbits: Iterable[int]) -> str:
    """Format the required per-orbit product and file counts."""
    lines = ["orbit  IMG products  SPE products  files"]
    for orbit in sorted(set(int(value) for value in orbits)):
        part = df.loc[df["orbit"] == orbit]
        img = part.loc[part["family"] == "IMG", "product_id"].nunique()
        spe = part.loc[part["family"] == "SPE", "product_id"].nunique()
        lines.append(f"{orbit:5d}  {img:12d}  {spe:12d}  {len(part):5d}")
    return "\n".join(lines)
