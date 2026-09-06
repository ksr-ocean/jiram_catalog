"""Gate for the GUI v2 backend (READ-ONLY). Needs the real mirror."""
from __future__ import annotations

import io
import os

import numpy as np
import pyarrow as pa
import pyarrow.ipc as ipc
import pytest

from jiram_catalog.config import mirror_root

REQUIRED = {"product_id", "half", "band", "orbit", "seq_id", "start_time_ms", "bore_lat", "bore_lon_east",
            "bore_emission", "on_planet_frac", "median_pixel_km", "dayside_frac", "min_lat", "max_lat",
            "lon_span_deg", "pole_inside", "c1_lat", "c1_lon", "c4_lat", "c4_lon", "has_partner", "best_dt_s"}


@pytest.fixture(scope="module")
def client():
    if os.environ.get("JIRAM_SKIP_GATES") == "1":
        pytest.skip("JIRAM_SKIP_GATES=1")
    from fastapi.testclient import TestClient

    from jiram_catalog.api.app import create_app

    with TestClient(create_app(mirror_root())) as c:
        yield c


def test_config_and_catalog(client):
    cfg = client.get("/api/config").json()
    assert cfg["counts"]["frames_on_planet"] >= 40_000 and cfg["counts"]["strips"] >= 200
    r = client.get("/api/catalog/frames.arrow")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/vnd.apache.arrow")
    table = ipc.open_stream(io.BytesIO(r.content)).read_all()
    assert table.num_rows >= 40_000
    assert REQUIRED <= set(table.column_names), REQUIRED - set(table.column_names)
    assert pa.types.is_int64(table.schema.field("start_time_ms").type)
    s = client.get("/api/catalog/summary", params={"half": "M", "lat_min": 60}).json()
    assert s["n"] > 1000 and len(s["by_lat_band"]) >= 1 and len(s["by_orbit"]) >= 10


def test_sequence_stack_meta_frame_and_movie_range(client):
    stacks = client.get("/api/stacks").json()
    st = next(s for s in stacks if s["id"] == "north_pole_paper/M_orbits4_sequence")
    assert st["n_time"] == 25 and st["has_movie"]
    meta = client.get(f"/api/stacks/{st['id']}/meta").json()
    assert len(meta["times"]) == 25 and "stretch" in meta and meta["graticule"]["type"] == "FeatureCollection"
    r = client.get(f"/api/stacks/{st['id']}/frame/3.png", params={"max_px": 800})
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    import imageio.v3 as iio

    img = iio.imread(io.BytesIO(r.content))
    assert img.ndim == 3 and img.shape[2] == 4 and max(img.shape[:2]) <= 800
    assert (img[..., 3] == 0).any() and (img[..., 3] == 255).any()
    assert int(r.headers["X-Rows"]) == img.shape[0] and int(r.headers["X-Cols"]) == img.shape[1]
    r = client.get(f"/api/stacks/{st['id']}/movie", headers={"Range": "bytes=0-1023"})
    assert r.status_code == 206 and len(r.content) == 1024 and r.headers["content-range"].startswith("bytes 0-1023/")


def test_strip_meta_and_stats(client):
    r = client.get("/api/strips.arrow")
    table = ipc.open_stream(io.BytesIO(r.content)).read_all()
    assert table.num_rows >= 200
    sid = table.column("strip_id")[0].as_py()
    meta = client.get(f"/api/strips/{sid}/meta").json()
    assert meta["shape"][0] > 0 and meta["graticule"]["type"] == "FeatureCollection"
    st = client.get(f"/api/strips/{sid}/stats").json()
    assert len(st["k"]) == len(st["E"]) > 5 and len(st["r_m"]) == len(st["S2"]) > 5
    assert np.isfinite(np.asarray(st["E"], dtype=float)).any()


def test_selection_round_trip(client):
    r = client.get("/api/catalog/frames.arrow")
    table = ipc.open_stream(io.BytesIO(r.content)).read_all()
    pids = table.column("product_id")[:5].to_pylist()
    rec = client.post("/api/selections", json={"name": "gate test", "product_ids": pids, "note": "delete me"}).json()
    assert rec["n_frames"] == 5 and rec["id"]
    got = client.get(f"/api/selections/{rec['id']}").json()
    assert set(got["product_ids"]) == set(pids)
    assert client.delete(f"/api/selections/{rec['id']}").json()["deleted"] == rec["id"]
    assert all(s["id"] != rec["id"] for s in client.get("/api/selections").json())
