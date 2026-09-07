"""Job and cached-media boundaries for the accepted scientific workflow."""
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import xarray as xr
from fastapi.testclient import TestClient

from jiram_catalog.api import data, stacks
from jiram_catalog.api.app import create_app


@pytest.fixture
def setup(tmp_path, monkeypatch):
    path = tmp_path / "regions" / "test" / "cube.nc"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"source identity for mocked loader")
    image = np.arange(3*2*8*8, dtype=np.float32).reshape(3,2,8,8)+1
    ds = xr.Dataset({"image": (("time","band","y","x"), image),
                     "valid": (("time","y","x"), np.ones((3,8,8), bool))},
        coords={"time": pd.date_range("2017-02-02",periods=3,freq="300s"),
                "band": ["RED","GREEN"], "product_id": ("time",["A_V01","B_V01","C_V01"])},
        attrs={"instrument":"JunoCam","km_per_px":15.,"level":"frame","region":"test"})
    ds.image.attrs["units"]="DN"
    monkeypatch.setattr(stacks,"resolve",lambda root,identifier:path)
    monkeypatch.setattr(data,"open_stack",lambda p:ds)
    with TestClient(create_app(tmp_path)) as client:
        yield client, ds, path


def test_preflight_refuses_ambiguous_or_irregular_export_before_job(setup):
    client,ds,_=setup
    assert client.post("/api/stacks/test/cube/export",json={}).status_code==400
    ds.coords["time"]=pd.to_datetime(["2017-02-02 00:00","2017-02-02 00:05","2017-02-02 00:15"])
    response=client.post("/api/stacks/test/cube/export",json={"band":"GREEN"})
    assert response.status_code==400 and not response.json()["detail"]["readiness"]["ready"]
    assert client.get("/api/jobs").json()==[]


def test_legacy_junocam_movie_cannot_bypass_filtered_cube(setup):
    client,ds,path=setup
    path.with_suffix(".mp4").write_bytes(b"old movie with all versions")
    assert client.get("/api/stacks/test/cube/movie",params={"band":"GREEN"}).status_code==404
    assert client.post("/api/stacks/test/cube/movie",json={}).status_code==400
    target=stacks.research_movie_path(client.app.state.mirror,path,ds,"GREEN","none")
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_bytes(b"eligible band-specific movie")
    assert client.get("/api/stacks/test/cube/movie",params={"band":"GREEN"}).content==target.read_bytes()
    assert client.get("/api/stacks/test/cube/movie",params={"band":"RED"}).status_code==404
    # A changed policy view yields a different signature, even if the source
    # NetCDF remains untouched on disk.
    ds.coords["product_id"]=("time",["D_V01","B_V01","C_V01"])
    assert client.get("/api/stacks/test/cube/movie",params={"band":"GREEN"}).status_code==404


def test_export_job_carries_physical_band_and_stays_in_mirror(setup,tmp_path):
    client,_,_=setup
    outside=tmp_path.parent / "prohibited-export"
    assert client.post("/api/stacks/test/cube/export",json={"band":"GREEN","out_dir":str(outside)}).status_code==400
    response=client.post("/api/stacks/test/cube/export",json={"band":"GREEN","norm":"none"})
    assert response.status_code==200
    job=client.app.state.jobs.wait(response.json()["job_id"],timeout=30)
    assert job["status"]=="done",job
    output=Path(job["result"]["out_dir"])
    assert output.is_relative_to(data.export_dir(client.app.state.mirror))
    assert job["result"]["n_realizations"]==1
    import json
    manifest=json.loads((output/"spec.json").read_text())
    assert manifest["band"]=="GREEN" and manifest["units_image"]=="DN"


def test_thermal_flattening_keeps_nightside_emission():
    from jiram_catalog.science import normalise_frame
    ds=xr.Dataset({"image":(("y","x"),np.arange(64,dtype=float).reshape(8,8)+10),
                   "incidence":(("y","x"),np.full((8,8),120.)),
                   "valid":(("y","x"),np.ones((8,8),bool))},attrs={"instrument":"JIRAM"})
    image,mask,_=normalise_frame(ds,"flat:2")
    assert mask.all() and np.isfinite(image).all()


def test_failed_movie_encode_preserves_complete_file(tmp_path,monkeypatch):
    from jiram_catalog import movie
    target=tmp_path/"complete.mp4"
    target.write_bytes(b"complete prior movie")
    def failed(stack,path,**kwargs):
        Path(path).write_bytes(b"partial new movie")
        raise RuntimeError("encoder failed")
    monkeypatch.setattr(movie,"_write_movie",failed)
    with pytest.raises(RuntimeError):
        movie.write_movie(xr.Dataset(),target)
    assert target.read_bytes()==b"complete prior movie"
    assert list(tmp_path.iterdir())==[target]


def test_concurrent_netcdf_routes_are_serialized_but_health_remains_live(setup,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    import time
    from jiram_catalog.api import coverage
    client,ds,path=setup
    active=0; maximum=0
    tally=threading.Lock()
    entered=threading.Event()
    release=threading.Event()
    def opened(source):
        nonlocal active,maximum
        with tally:
            active+=1;maximum=max(maximum,active)
        entered.set()
        release.wait(5)
        time.sleep(.02)
        with tally:active-=1
        return ds
    monkeypatch.setattr(data,"open_stack",opened)
    monkeypatch.setattr(coverage, "coverage_payload", lambda root: {"n": int(opened(path).sizes["time"])})
    with ThreadPoolExecutor(max_workers=3) as pool:
        first=pool.submit(client.get,"/api/stacks/test/cube/movie?band=GREEN")
        assert entered.wait(3)
        second=pool.submit(client.get,"/api/stacks/test/cube/movie?band=RED")
        third=pool.submit(client.get,"/api/coverage")
        assert client.get("/api/health").json()=={"ok":True}
        assert client.get("/api/config").status_code==200
        release.set()
        assert first.result().status_code==404 and second.result().status_code==404
        assert third.result().json()=={"n":3}
    assert maximum==1


def test_metadata_stretch_bounds_reads_and_preserves_solar_correction(monkeypatch):
    values = np.arange(2 * 64 * 64, dtype=float).reshape(2, 64, 64) + 1
    ds = xr.Dataset({
        "image": (("time", "y", "x"), values),
        "valid": (("time", "y", "x"), np.ones_like(values, dtype=bool)),
        "incidence": (("time", "y", "x"), np.full_like(values, 60.)),
        "emission": (("time", "y", "x"), np.zeros_like(values)),
    })
    getter = xr.DataArray.values.fget
    def bounded_read(array):
        assert array.size <= 256, "display metadata materialised a full native plane"
        assert array.name != "emission", "Lambert correction does not need emission"
        return getter(array)
    monkeypatch.setattr(xr.DataArray, "values", property(bounded_read))
    limits = data.normalised_stretch(ds, key="bounded-solar-read", norms=("none", "lambert"),
                                     max_steps=2, max_samples=512)
    expected = np.percentile(values[:, ::4, ::4], [1., 99.])
    np.testing.assert_allclose(limits["none"], expected)
    np.testing.assert_allclose(limits["lambert"], expected * 2.)


def test_local_time_contours_bound_reads_preserve_midnight_and_refresh(tmp_path, monkeypatch):
    from jiram_catalog.api.strips import local_time_geojson
    axis = np.linspace(0., 8., 801)
    ds = xr.Dataset({"local_time_h": (("y", "x"), np.broadcast_to((22. + axis / 2.) % 24., (801, 801)))},
                    coords={"x_km": ("x", axis), "y_km": ("y", axis)})
    source = tmp_path / "clock.nc"
    source.write_bytes(b"first")
    ds.encoding["source"] = str(source)
    getter = xr.DataArray.values.fget
    reads = []
    def bounded_read(array):
        assert array.size <= 400 * 400
        reads.append(array.name)
        return getter(array)
    monkeypatch.setattr(xr.DataArray, "values", property(bounded_read))
    first = local_time_geojson(ds, "clock-test")
    assert first["features"]
    midpoints = []
    for feature in first["features"]:
        xs = np.asarray(feature["geometry"]["coordinates"])[:, 0]
        # The 22h boundary is at x=0 and midnight at x=4. No extra seam.
        target = 0. if abs(xs.mean()) < .05 else 4.
        np.testing.assert_allclose(xs, target, atol=.05)
        midpoints.append(float(xs.mean()))
    assert any(abs(value - 4.) < .05 for value in midpoints)
    count = len(reads)
    assert local_time_geojson(ds, "clock-test") == first and len(reads) == count
    source.write_bytes(b"changed source signature")
    local_time_geojson(ds, "clock-test")
    assert len(reads) > count
