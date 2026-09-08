"""Lead-owned synthetic preservation oracle; no external mirror required."""
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import xarray as xr

from jiram_catalog.junocam import strips as jc
from jiram_catalog.strips import INDEX_COLUMNS, _coerce_index, strips_index_path


def row(identifier, orbit=5, instrument="JunoCam", band="RGB"):
    result = dict.fromkeys(INDEX_COLUMNS)
    result.update(strip_id=identifier, instrument=instrument, orbit=orbit,
                  chunk_index=0, n_frames=1, rows=2, cols=2, band=band,
                  bands="RED,GREEN,BLUE" if band == "RGB" else band,
                  pole_inside=False, path=f"strips/junocam/orbit{orbit:02d}/{identifier}.nc",
                  time_start="2017-03-27T00:00:00", time_mid="2017-03-27T00:00:00",
                  time_end="2017-03-27T00:00:01")
    return result


def seed(root, rows):
    path = strips_index_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    _coerce_index(pd.DataFrame(rows, columns=INDEX_COLUMNS)).to_parquet(path, index=False)
    for item in rows:
        product = root / item["path"]
        product.parent.mkdir(parents=True, exist_ok=True)
        product.write_bytes(item["strip_id"].encode())
    return path


def test_partial_upsert_preserves_omitted_products_and_files(tmp_path):
    old = [row("rebuild"), row("methane", band="METHANE"), row("failed"),
           row("jiram", instrument="JIRAM", band="M"), row("pj4", orbit=4)]
    path = seed(tmp_path, old)
    orphan = tmp_path / "strips/junocam/orbit05/unindexed.nc"
    orphan.write_bytes(b"keep")
    expected = {tmp_path / r["path"]: (tmp_path / r["path"]).read_bytes() for r in old}
    replacement = {**row("rebuild"), "cols": 9}
    jc.update_index(tmp_path, [replacement, row("new")], [5])
    actual = pd.read_parquet(path).set_index("strip_id")
    assert set(actual.index) == {"rebuild", "methane", "failed", "jiram", "pj4", "new"}
    assert actual.index.is_unique and actual.loc["rebuild", "cols"] == 9
    for product, content in expected.items():
        assert product.read_bytes() == content
    assert orphan.read_bytes() == b"keep"


def test_empty_update_is_byte_preserving(tmp_path):
    path = seed(tmp_path, [row("keep")])
    before = path.read_bytes()
    jc.update_index(tmp_path, [], [5])
    assert path.read_bytes() == before
    assert (tmp_path / row("keep")["path"]).read_bytes() == b"keep"


@pytest.mark.parametrize("bad", [[row("bad", orbit=6)],
                                 [row("bad", instrument="JIRAM")],
                                 [row("duplicate"), row("duplicate")]])
def test_invalid_upsert_does_not_write(tmp_path, bad):
    path = seed(tmp_path, [row("keep")])
    before = path.read_bytes()
    with pytest.raises(ValueError):
        jc.update_index(tmp_path, bad, [5])
    assert path.read_bytes() == before


def worker_setup(tmp_path, monkeypatch, *, existing_bands=None):
    bands = ("RED", "GREEN", "BLUE")
    dataset = xr.Dataset({"image": (("band", "y", "x"), np.ones((3, 2, 2)))},
                         coords={"band": list(bands)})
    closed = []
    dataset.set_close(lambda: closed.append(True))
    monkeypatch.setattr(jc, "_worker_kernels", lambda *a: None)
    monkeypatch.setattr(jc, "build_strip", lambda *a, **k: dataset)
    path = jc.junocam_strip_path(tmp_path, 5, "example")
    if existing_bands is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        xr.Dataset(coords={"band": list(existing_bands)}).to_netcdf(path, engine="netcdf4")
    task = jc._Task(str(tmp_path), 5, bands, False, {"product_id": "example"})
    return task, path, dataset, closed


def test_metadata_failure_precedes_write_and_closes_dataset(tmp_path, monkeypatch):
    task, path, dataset, closed = worker_setup(tmp_path, monkeypatch,
                                               existing_bands=("RED", "GREEN", "BLUE"))
    before = path.read_bytes()
    def fail(*a):
        raise ValueError("invalid metadata")
    monkeypatch.setattr(jc, "index_row", fail)
    monkeypatch.setattr(jc, "write_strip", lambda ds, p: p.write_bytes(b"overwritten"))
    result = jc._strip_task(task)
    assert not result["ok"] and result["row"] is None
    assert "invalid metadata" in result["error"]
    assert path.read_bytes() == before and closed == [True]


def test_band_collision_preserves_existing_file(tmp_path, monkeypatch):
    task, path, dataset, closed = worker_setup(tmp_path, monkeypatch, existing_bands=("RED",))
    before = path.read_bytes()
    monkeypatch.setattr(jc, "index_row", lambda *a: row("example"))
    monkeypatch.setattr(jc, "write_strip", lambda ds, p: p.write_bytes(b"overwritten"))
    result = jc._strip_task(task)
    assert not result["ok"] and "band" in result["error"].lower()
    assert path.read_bytes() == before


def test_write_failure_returns_no_row_and_closes(tmp_path, monkeypatch):
    task, path, dataset, closed = worker_setup(tmp_path, monkeypatch)
    monkeypatch.setattr(jc, "index_row", lambda *a: row("example"))
    def fail(*a):
        raise OSError("storage unavailable")
    monkeypatch.setattr(jc, "write_strip", fail)
    result = jc._strip_task(task)
    assert not result["ok"] and result["row"] is None and closed == [True]


def test_identical_band_set_can_replace(tmp_path, monkeypatch):
    task, path, dataset, closed = worker_setup(tmp_path, monkeypatch,
                                               existing_bands=("BLUE", "RED", "GREEN"))
    monkeypatch.setattr(jc, "index_row", lambda *a: row("example"))
    monkeypatch.setattr(jc, "write_strip", lambda ds, p: p.write_bytes(b"replacement"))
    result = jc._strip_task(task)
    assert result["ok"] and result["row"]["strip_id"] == "example"
    assert path.read_bytes() == b"replacement" and closed == [True]
