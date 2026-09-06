"""Offline tests for the GUI v2 backend: a synthetic mirror, no archive.

Everything the API answers is built here in a temporary directory -- a
twenty-row catalog, a two-step stack, one strip -- so the suite says
nothing about the archive and everything about the service that serves
it.  The stack and strip builders are the GUI v1 ones
(``test_gui_offline``), which is the point: the two front ends draw the
same objects, so they should be tested against the same fakes.
"""

from __future__ import annotations

import io
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.ipc as ipc
import pytest
import xarray as xr
from fastapi.testclient import TestClient

from jiram_catalog.api import catalog as api_catalog
from jiram_catalog.api.app import create_app
from jiram_catalog.gui import data as gui_data
from jiram_catalog.stacks import write_stack
from jiram_catalog.strips import write_strip
from test_gui_offline import synthetic_stack, synthetic_stats, synthetic_strip

FRAMES = 10
STRIP_ID = "04_L_synth_00"
STACK_ID = "synthetic/M_orbits4_sequence"

#: The columns and types the contract promises for ``frames.arrow``.
EXPECTED_TYPES: dict[str, object] = {
    "product_id": pa.string(),
    "half": pa.string(),
    "band": pa.string(),
    "orbit": pa.int16(),
    "seq_id": pa.string(),
    "start_time_ms": pa.int64(),
    "bore_lat": pa.float32(),
    "bore_lon_east": pa.float32(),
    "bore_emission": pa.float32(),
    "on_planet_frac": pa.float32(),
    "median_pixel_km": pa.float32(),
    "dayside_frac": pa.float32(),
    "min_lat": pa.float32(),
    "max_lat": pa.float32(),
    "lon_span_deg": pa.float32(),
    "pole_inside": pa.bool_(),
    "c1_lat": pa.float32(),
    "c1_lon": pa.float32(),
    "c2_lat": pa.float32(),
    "c2_lon": pa.float32(),
    "c3_lat": pa.float32(),
    "c3_lon": pa.float32(),
    "c4_lat": pa.float32(),
    "c4_lon": pa.float32(),
    "has_partner": pa.bool_(),
    "best_dt_s": pa.float32(),
}


# ---------------------------------------------------------------------------
# the synthetic mirror
# ---------------------------------------------------------------------------
def _frames_table() -> pd.DataFrame:
    """Ten index rows, one per product; the loader fills the other columns."""
    return pd.DataFrame(
        {
            "product_id": [f"JIR_IMG_RDR_{index:04d}" for index in range(FRAMES)],
            "orbit": np.arange(FRAMES, dtype="int64") + 1,
            "orbit_dir": np.arange(FRAMES, dtype="int64") + 1,
            "band": ["LM"] * FRAMES,
            "geom_band": ["LM"] * FRAMES,
            "parse_ok": [True] * FRAMES,
            "start_time": [datetime(2017, 1 + index, 1 + index, 3) for index in range(FRAMES)],
            "seq_id": [f"{index:02d}_seq" for index in range(FRAMES)],
            "sequence_number": np.arange(FRAMES, dtype="int64"),
        }
    )


def _geo_table() -> pd.DataFrame:
    """Twenty geometry rows: both band halves of each of the ten products."""
    rows = []
    for index in range(FRAMES):
        for half in ("L", "M"):
            rows.append(
                {
                    "product_id": f"JIR_IMG_RDR_{index:04d}",
                    "orbit_dir": index + 1,
                    "half": half,
                    "start_time": datetime(2017, 1 + index, 1 + index, 3),
                    "geo_ok": True,
                    "geo_error": "",
                    "on_planet_frac": float(np.linspace(0.05, 1.0, FRAMES)[index]),
                    "n_on_planet": 1000 * (index + 1),
                    "bore_lat": float(np.linspace(-85.0, 85.0, FRAMES)[index]),
                    "bore_lon_east": float(np.linspace(5.0, 355.0, FRAMES)[index]),
                    "bore_emission": float(np.linspace(5.0, 85.0, FRAMES)[index]),
                    "median_pixel_km": float(np.linspace(10.0, 450.0, FRAMES)[index]),
                    "dayside_frac": 0.0 if index % 3 == 0 else 0.7,
                    "min_lat": float(np.linspace(-88.0, 80.0, FRAMES)[index]),
                    "max_lat": float(np.linspace(-80.0, 88.0, FRAMES)[index]),
                    "lon_span_deg": 12.0 + index,
                    "pole_inside": bool(index in (0, FRAMES - 1)),
                    "mean_emission": float(np.linspace(5.0, 85.0, FRAMES)[index]),
                    **{
                        f"c{corner}_{name}": float(10 * corner + index)
                        for corner in (1, 2, 3, 4)
                        for name in ("lat", "lon")
                    },
                }
            )
    return pd.DataFrame(rows)


def _trackability_table() -> pd.DataFrame:
    geo = _geo_table()
    return pd.DataFrame(
        {
            "product_id": geo["product_id"],
            "half": geo["half"],
            "has_partner": [index % 2 == 0 for index in range(len(geo))],
            "trackable_30": [index % 4 == 0 for index in range(len(geo))],
            "best_dt_s": np.linspace(5.0, 60.0, len(geo)),
        }
    )


def _strips_index(root: Path, dataset: xr.Dataset, relative: str) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "strip_id": STRIP_ID,
                "orbit": 4,
                "seq_id": "04_L_synth",
                "chunk_index": 0,
                "band": "L",
                "n_frames": 7,
                "time_start": pd.Timestamp("2017-02-02T04:00"),
                "time_end": pd.Timestamp("2017-02-02T04:05"),
                "time_mid": pd.Timestamp("2017-02-02T04:02"),
                "center_lat": 78.0,
                "center_lon_east": 30.0,
                "km_per_px": 30.0,
                "resolution_class": 30.0,
                "projection": "polar_ortho",
                "valid_frac": 0.9,
                "dayside_frac": 0.9,
                "median_emission": 45.0,
                "lat_min": 70.0,
                "lat_max": 86.0,
                "lon_min_east": 0.0,
                "lon_max_east": 60.0,
                "lon_span_deg": 60.0,
                "pole_inside": False,
                "created_utc": "2026-09-06T00:00:00Z",
                "software": "test",
                "path": relative,
                "rows": int(dataset.sizes["y"]),
                "cols": int(dataset.sizes["x"]),
            }
        ]
    )


@pytest.fixture(scope="module")
def mirror(tmp_path_factory) -> Path:
    """A mirror with an index, one region stack and one strip."""
    root = tmp_path_factory.mktemp("mirror")
    (root / "index").mkdir()
    _frames_table().to_parquet(root / "index" / "frames.parquet")
    _geo_table().to_parquet(root / "index" / "frames_geo.parquet")
    _trackability_table().to_parquet(root / "index" / "trackability_frames.parquet")

    stack = synthetic_stack(steps=2, size=32)
    write_stack(stack, root / "regions" / "synthetic" / "M_orbits4_sequence.nc")

    strip = synthetic_strip(STRIP_ID, size=32)
    relative = Path("strips") / "orbit04" / f"{STRIP_ID}.nc"
    write_strip(strip, root / relative)
    _strips_index(root, strip, str(relative)).to_parquet(root / "strips" / "strips.parquet")
    return root


@pytest.fixture(scope="module")
def client(mirror) -> TestClient:
    api_catalog.clear_caches()
    with TestClient(create_app(mirror)) as instance:
        yield instance


def _arrow(response) -> pa.Table:
    return ipc.open_stream(io.BytesIO(response.content)).read_all()


# ---------------------------------------------------------------------------
# config and health
# ---------------------------------------------------------------------------
def test_health(client):
    assert client.get("/api/health").json() == {"ok": True}


def test_config_counts_the_synthetic_mirror(client, mirror):
    payload = client.get("/api/config").json()
    assert payload["mirror"] == str(mirror)
    assert payload["counts"] == {
        "frames_on_planet": 20,
        "strips": 1,
        "stacks": 1,
        "selections": 0,
    }
    assert payload["has_trackability"] is True
    assert payload["version"]


# ---------------------------------------------------------------------------
# the catalog
# ---------------------------------------------------------------------------
def test_frames_arrow_has_the_contract_columns_and_types(client):
    response = client.get("/api/catalog/frames.arrow")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/vnd.apache.arrow")
    table = _arrow(response)
    assert table.num_rows == 20
    assert set(EXPECTED_TYPES) <= set(table.column_names)
    for name, expected in EXPECTED_TYPES.items():
        assert table.schema.field(name).type == expected, name
    assert set(table.column("half").to_pylist()) == {"L", "M"}
    assert table.column("start_time_ms")[0].as_py() > 1_400_000_000_000


def test_frames_arrow_is_cached_and_conditional(client):
    first = client.get("/api/catalog/frames.arrow")
    etag = first.headers["etag"]
    again = client.get("/api/catalog/frames.arrow", headers={"If-None-Match": etag})
    assert again.status_code == 304 and not again.content
    assert client.get("/api/catalog/frames.arrow").content == first.content


def test_frame_detail_carries_every_column_and_the_halves(client):
    payload = client.get("/api/catalog/frame/JIR_IMG_RDR_0003").json()
    assert payload["product_id"] == "JIR_IMG_RDR_0003"
    assert payload["halves"] == ["L", "M"]
    assert payload["lon_span_deg"] == 15.0
    # A column the synthetic labels never fill comes back as null, not NaN.
    assert payload["center_lat"] is None
    assert client.get("/api/catalog/frame/nope").status_code == 404


def _n(client, **params) -> int:
    return client.get("/api/catalog/summary", params=params).json()["n"]


def test_summary_bins_three_ways(client):
    payload = client.get("/api/catalog/summary").json()
    assert payload["n"] == 20
    assert sum(item["n"] for item in payload["by_lat_band"]) == 20
    assert [item["orbit"] for item in payload["by_orbit"]] == list(range(1, 11))
    assert payload["by_month"][0]["month"] == "2017-01"
    assert sum(item["n"] for item in payload["by_month"]) == 20


def test_summary_each_filter_once(client):
    assert _n(client, orbit_min=3) == 16
    assert _n(client, orbit_max=2) == 4
    assert _n(client, orbit_min=3, orbit_max=5) == 6
    assert _n(client, time_min="2017-05-01") == 12
    assert _n(client, time_max="2017-02-28") == 4
    assert _n(client, half="M") == 10
    # Thresholds: the synthetic table has no missing values, so each is exact.
    assert _n(client, pixel_max_km=200.0) == 8
    assert _n(client, emission_max=50.0) == 12
    assert _n(client, on_planet_min=0.5) == 10
    assert _n(client, dayside_only=True) == 12
    assert _n(client, lat_min=0.0) == 10
    assert _n(client, lat_max=0.0) == 10
    assert _n(client, revisit_only=True) == 5


def test_latitude_filter_excludes_a_missing_boresight(client, mirror):
    """A frame whose boresight misses the planet has no latitude to test."""
    table = api_catalog.catalog_frame(mirror).copy()
    table.loc[0, "bore_lat"] = np.nan
    assert len(api_catalog.apply_filters(table, lat_min=-90.0)) == 19
    assert len(api_catalog.apply_filters(table, lat_max=90.0)) == 19
    # The other thresholds still keep it: only the latitude window excludes.
    assert len(api_catalog.apply_filters(table, emission_max=90.0)) == 20


def test_summary_threshold_keeps_a_row_whose_value_is_missing(client, mirror):
    """The GUI v1 rule the contract names: unknown is not a failure."""
    table = api_catalog.catalog_frame(mirror).copy()
    table.loc[0, "bore_emission"] = np.nan
    kept = api_catalog.apply_filters(table, emission_max=0.0)
    assert kept["product_id"].tolist() == [table.loc[0, "product_id"]]
    # ...except for on_planet_min, where the contract says missing excludes.
    table.loc[0, "on_planet_frac"] = np.nan
    assert api_catalog.apply_filters(table, on_planet_min=0.0).shape[0] == 19


# ---------------------------------------------------------------------------
# selections
# ---------------------------------------------------------------------------
def test_selection_round_trip(client):
    ids = ["JIR_IMG_RDR_0001", "JIR_IMG_RDR_0002", "JIR_IMG_RDR_0003"]
    record = client.post(
        "/api/selections", json={"name": "North pole PJ4!", "product_ids": ids, "note": "hi"}
    ).json()
    assert record["id"].startswith("north-pole-pj4-")
    assert record["n_frames"] == 3 and record["n_orbits"] == 3
    assert record["lat_min"] < record["lat_max"] and record["note"] == "hi"

    listed = client.get("/api/selections").json()
    assert [item["id"] for item in listed] == [record["id"]]
    assert "product_ids" not in listed[0]

    fetched = client.get(f"/api/selections/{record['id']}").json()
    assert fetched["product_ids"] == ids and fetched["halves"] == []

    assert client.delete(f"/api/selections/{record['id']}").json() == {"deleted": record["id"]}
    assert client.get("/api/selections").json() == []
    assert client.get(f"/api/selections/{record['id']}").status_code == 404


def test_duplicate_selection_names_do_not_collide(client):
    body = {"name": "same name", "product_ids": ["JIR_IMG_RDR_0000"]}
    first = client.post("/api/selections", json=body).json()
    second = client.post("/api/selections", json=body).json()
    assert first["id"] != second["id"] and second["id"].startswith(first["id"])
    for record in (first, second):
        client.delete(f"/api/selections/{record['id']}")


# ---------------------------------------------------------------------------
# stacks
# ---------------------------------------------------------------------------
def test_stacks_listing(client):
    entries = client.get("/api/stacks").json()
    assert len(entries) == 1
    entry = entries[0]
    assert entry["id"] == STACK_ID and entry["region"] == "synthetic"
    assert entry["n_time"] == 2 and entry["shape"] == [32, 32]
    assert entry["band"] == "M" and entry["level"] == "sequence"
    assert entry["has_movie"] is False and entry["size_bytes"] > 0


def test_stack_meta_has_times_stretch_and_graticule(client):
    meta = client.get(f"/api/stacks/{STACK_ID}/meta").json()
    assert len(meta["times"]) == 2 and meta["times"][0].startswith("2017-02-02T08:10")
    assert meta["stretch"]["p99"] > meta["stretch"]["p1"]
    assert meta["graticule"]["type"] == "FeatureCollection"
    assert meta["graticule"]["features"], "the synthetic grid should contour"
    assert meta["graticule"]["features"][0]["geometry"]["type"] == "LineString"
    assert meta["per_time"][1]["i"] == 1 and meta["per_time"][1]["seq_id"] == "04_M_seq"
    assert meta["shape"] == [32, 32] and meta["attrs"]["region"] == "synthetic"
    assert client.get("/api/stacks/nope/nothing/meta").status_code == 404


def test_stack_frame_png_decodes_at_the_served_size(client):
    import imageio.v3 as iio

    response = client.get(f"/api/stacks/{STACK_ID}/frame/0.png", params={"max_px": 16})
    assert response.status_code == 200 and response.headers["content-type"] == "image/png"
    image = iio.imread(io.BytesIO(response.content))
    assert image.shape == (16, 16, 4)
    assert int(response.headers["X-Rows"]) == 16 and int(response.headers["X-Cols"]) == 16
    assert response.headers["X-Stride"] == "2"
    assert response.headers["Cache-Control"] == "max-age=3600"
    assert len(response.headers["X-Bounds"].split(",")) == 4
    # The synthetic stack marks its first two rows invalid.
    assert (image[..., 3] == 0).any() and (image[..., 3] == 255).any()
    assert client.get(f"/api/stacks/{STACK_ID}/frame/9.png").status_code == 404


def test_stack_emission_png(client):
    import imageio.v3 as iio

    response = client.get(f"/api/stacks/{STACK_ID}/frame/1/emission.png", params={"max_px": 32})
    image = iio.imread(io.BytesIO(response.content))
    assert image.shape == (32, 32, 4)
    # 60 degrees on a 0-90 scale.
    assert int(image[8, 8, 0]) == int(round(60.0 / 90.0 * 255.0))


def test_movie_is_absent_then_answers_a_range(client, mirror):
    assert client.get(f"/api/stacks/{STACK_ID}/movie").status_code == 404
    path = mirror / "regions" / "synthetic" / "M_orbits4_sequence.mp4"
    path.write_bytes(bytes(range(64)))
    try:
        whole = client.get(f"/api/stacks/{STACK_ID}/movie")
        assert whole.status_code == 200 and whole.headers["accept-ranges"] == "bytes"
        part = client.get(f"/api/stacks/{STACK_ID}/movie", headers={"Range": "bytes=0-9"})
        assert part.status_code == 206 and part.content == bytes(range(10))
        assert part.headers["content-range"] == "bytes 0-9/64"
        assert client.get("/api/stacks").json()[0]["has_movie"] is True
    finally:
        path.unlink()


# ---------------------------------------------------------------------------
# strips
# ---------------------------------------------------------------------------
def test_strips_arrow_renames_datetimes(client):
    table = _arrow(client.get("/api/strips.arrow"))
    assert table.num_rows == 1
    assert "time_start_ms" in table.column_names and "time_start" not in table.column_names
    assert pa.types.is_int64(table.schema.field("time_mid_ms").type)
    assert table.column("strip_id")[0].as_py() == STRIP_ID


def test_strip_meta_and_image(client):
    meta = client.get(f"/api/strips/{STRIP_ID}/meta").json()
    assert meta["shape"] == [32, 32]
    assert meta["graticule"]["type"] == "FeatureCollection" and meta["graticule"]["features"]
    assert meta["local_time_contours"]["features"], "the clock should contour"
    assert meta["stretch"]["p99"] > meta["stretch"]["p1"]
    assert meta["attrs"]["strip_id"] == STRIP_ID

    import imageio.v3 as iio

    response = client.get(f"/api/strips/{STRIP_ID}/image.png", params={"max_px": 32})
    image = iio.imread(io.BytesIO(response.content))
    assert image.shape == (32, 32, 4) and (image[..., 3] == 0).any()
    assert client.get("/api/strips/nope/meta").status_code == 404


def test_strip_stats_uses_the_contract_names(client, monkeypatch):
    monkeypatch.setattr(gui_data, "strip_stats", lambda mirror, strip: synthetic_stats())
    payload = client.get(f"/api/strips/{STRIP_ID}/stats").json()
    assert len(payload["k"]) == len(payload["E"]) == 40
    assert len(payload["r_m"]) == len(payload["S2"]) == len(payload["S3"]) == 20
    assert len(payload["k_x"]) == len(payload["P_x"]) == 40
    assert payload["E"][0] is None, "a non-finite value must be null, not NaN"
    assert np.isfinite(np.asarray(payload["E"], dtype=float)).any()
    assert payload["attrs"]["band"] == "L"


# ---------------------------------------------------------------------------
# jobs
# ---------------------------------------------------------------------------
def test_a_job_runs_to_done_and_is_listed(client):
    manager = client.app.state.jobs
    assert {"movie", "stack_build", "goflow_export"} <= set(manager.kinds())

    def noop(progress, *, label: str) -> dict[str, str]:
        progress(0.5, "half way")
        return {"label": label}

    manager.register("noop", noop)
    submitted = manager.submit("noop", {"label": "hello"})
    assert submitted["status"] in ("queued", "running", "done")
    finished = manager.wait(submitted["id"], timeout=30.0)
    assert finished["status"] == "done" and finished["progress"] == 1.0

    record = client.get(f"/api/jobs/{submitted['id']}").json()
    assert record["result"] == {"label": "hello"} and record["finished"]
    assert any(item["id"] == submitted["id"] for item in client.get("/api/jobs").json())
    assert (Path(manager.cache_dir) / f"{submitted['id']}.json").exists()
    assert client.get("/api/jobs/nope").status_code == 404


def test_a_failing_job_becomes_a_record(client):
    manager = client.app.state.jobs

    def boom(progress) -> None:
        raise ValueError("no frame survives the cuts")

    manager.register("boom", boom)
    record = manager.wait(manager.submit("boom", {})["id"], timeout=30.0)
    assert record["status"] == "failed" and "no frame survives" in record["message"]


# ---------------------------------------------------------------------------
# the static bundle
# ---------------------------------------------------------------------------
def test_static_falls_back_to_a_hint_then_to_index_html(client, tmp_path, monkeypatch):
    from jiram_catalog.api import app as api_app

    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setattr(api_app, "dist_dir", lambda: empty)
    missing = client.get("/")
    assert missing.status_code == 503 and "npm run build" in missing.text

    built = tmp_path / "dist"
    (built / "assets").mkdir(parents=True)
    (built / "index.html").write_text("<!doctype html><title>jiram</title>")
    (built / "assets" / "app.js").write_text("console.log(1)")
    monkeypatch.setattr(api_app, "dist_dir", lambda: built)
    assert "jiram" in client.get("/").text
    assert "jiram" in client.get("/strips/04_L_synth_00").text, "client routes fall back"
    assert client.get("/assets/app.js").text == "console.log(1)"
    assert client.get("/api/nothing").status_code == 404
