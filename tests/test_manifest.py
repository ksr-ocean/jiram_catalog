from __future__ import annotations

from pathlib import Path

from jiram_catalog.pds import parse_listing


FIXTURE = Path(__file__).parent / "fixtures" / "listing_orbit77_trimmed.html"
BASE = "https://atmos.nmsu.edu/PDS/data/PDS4/juno_jiram_bundle/data_calibrated/orbit77/"


def test_parse_trimmed_apache_listing() -> None:
    records = parse_listing(FIXTURE.read_text(), 77, BASE)
    assert len(records) == 48
    assert {record["family"] for record in records} == {"IMG", "SPE", "LOG_IMG", "LOG_SPE"}
    assert {record["kind"] for record in records} == {"labels", "data"}
    image = next(record for record in records if record["basename"].endswith(".IMG"))
    assert image["product_id"] == "JIR_IMG_RDR_2025290T154243_V01"
    assert image["ext"] == "IMG"
    assert image["listed_size_bytes"] == 432 * 1024
    assert image["listed_mtime"] == "2026-04-29 15:16"
    assert image["url"] == BASE + image["basename"]
    log_label = next(record for record in records if record["family"] == "LOG_IMG")
    assert log_label["kind"] == "labels"
    assert all(not str(record["basename"]).startswith(("?", "/")) for record in records)
    assert all("Parent Directory" not in str(record["basename"]) for record in records)
