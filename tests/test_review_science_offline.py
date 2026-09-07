"""Scientific API tests against known shifts, cadence, and independent passes."""
import json

import numpy as np
import pandas as pd
import pytest
import xarray as xr
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from jiram_catalog import science
from jiram_catalog.api import science as api
from jiram_catalog.export_goflow import export_stack


def stack():
    rng=np.random.default_rng(41)
    image=rng.normal(size=(3,2,16,16)).astype(np.float32)+5
    return xr.Dataset({
        "image":(("time","band","y","x"),image,{"units":"DN"}),
        "valid":(("time","y","x"),np.ones((3,16,16),bool)),
        "incidence":(("time","band","y","x"),np.full(image.shape,60.)),
    },coords={"time":pd.date_range("2017-02-02",periods=3,freq="300s"),
        "product_id":("time",["A_V01","B_V01","C_V01"]),
        "band":["RED","GREEN"],"x_km":("x",np.arange(16)*15.),
        "y_km":("y",np.arange(16)*15.)},
        attrs={"instrument":"JunoCam","band":"RED;GREEN","km_per_px":15.,
               "region":"test","projection":"test-grid","level":"frame"})


def client(tmp_path):
    app=FastAPI()
    app.state.mirror=tmp_path
    app.include_router(api.router)
    return TestClient(app)


def test_corrected_version_epochs_do_not_create_new_observations():
    original=stack()
    old=original.isel(time=[0]).assign_coords(product_id=("time",["A_V00"]),
        time=[pd.Timestamp("2017-02-02")-pd.Timedelta(milliseconds=12)])
    mixed=xr.concat([old,original],dim="time")
    result=science.stack_readiness(mixed,band="RED")
    assert result["ready"] and result["n_observations"]==3
    assert result["n_versions_removed"]==1
    assert result["gaps_s"]==[300.,300.]
    assert science.prepare_stack(mixed,band="RED").product_id.values.tolist()==["A_V01","B_V01","C_V01"]


def test_physical_export_units_mask_and_provenance(tmp_path):
    cube=stack()
    cube.incidence.values[:,:,0,0]=90.
    out=tmp_path/"quantitative"
    manifest=export_stack(cube,out,band="GREEN",norm="lambert")
    assert manifest["n_realizations"]==1
    with xr.open_dataset(out/"r00000"/"realization.nc") as exported:
        assert exported.image.dims==("frame","y_img","x_img")
        assert exported.attrs["band"]=="GREEN"
        assert exported.attrs["units_image"]=="DN"
        assert exported.attrs["norm"]=="lambert"
        assert not exported.valid.values[:,0,0].any()
        np.testing.assert_allclose(exported.image.values[:,1:,1:],cube.image.sel(band="GREEN").values[:,1:,1:]*2,rtol=1e-6)
        p=json.loads(exported.attrs["analysis_provenance"])
        assert p["sources"]==["A_V01","B_V01","C_V01"]
        assert p["units"]=="DN"
    spec=json.loads((out/"spec.json").read_text())
    assert spec["provenance"]["norm"]=="lambert"
    assert spec["units_image"]=="DN"


def test_failed_preflight_creates_no_output(tmp_path):
    cube=stack().isel(time=slice(0,2))
    output=tmp_path/"must-not-exist"
    with pytest.raises(ValueError,match="export-ready"):
        export_stack(cube,output,band="RED")
    assert not output.exists()
    with pytest.raises(ValueError,match="requires measured incidence"):
        science.prepare_stack(stack().drop_vars("incidence"),band="RED",norm="lambert")


def test_junocam_night_pixels_are_invalid_even_for_raw_analysis():
    cube=stack()
    cube.incidence.values[:,:,0,0]=90.
    result=science.prepare_stack(cube,band="RED")
    assert not result.valid.values[:,0,0].any()
    cube.attrs["instrument"]="JIRAM"
    result=science.prepare_stack(cube,band="RED")
    assert result.valid.values[:,0,0].all()


def test_ncc_known_signed_shift_and_constant_scene():
    rng=np.random.default_rng(18)
    left=rng.normal(size=(64,64))
    right=np.zeros_like(left)
    right[4:,:-3]=left[:-4,3:]
    ma=np.ones_like(left,bool)
    mb=np.zeros_like(left,bool)
    mb[4:,:-3]=True
    found=api.register_images(left,right,ma,mb)
    assert found["status"]=="measured"
    assert (found["dy_px"],found["dx_px"])==(4.,-3.)
    assert found["correlation"]==pytest.approx(1.,abs=1e-10)
    blank=api.register_images(np.ones((16,16)),np.ones((16,16)),np.ones((16,16),bool),np.ones((16,16),bool))
    assert blank["status"]=="unassessed" and blank["correlation"] is None


def test_endpoint_comparison_distinguishes_grid_and_navigation(tmp_path,monkeypatch):
    cube=stack().sel(band="RED",drop=True)
    cube.attrs["band"]="RED"
    cube.image.values[1]=cube.image.values[0]
    monkeypatch.setattr(api,"_stack",lambda mirror,identifier:cube)
    c=client(tmp_path)
    body={"left":{"kind":"stack","id":"test","t":0},
          "right":{"kind":"stack","id":"test","t":1}}
    result=c.post("/api/science/compare",json=body)
    assert result.status_code==200,result.text
    value=result.json()
    assert value["compatible"] and value["dt_s"]==300.
    assert value["registration"]["dx_px"]==0.
    assert value["predicted_displacement_px"]==pytest.approx(.6)
    assert value["velocity_uncertainty_m_s"] is None
    body["navigation_error_px"]=1.
    assert c.post("/api/science/compare",json=body).json()["velocity_uncertainty_m_s"]==pytest.approx(np.sqrt(2)*50.)
    other=cube.assign_coords(x_km=cube.x_km+15.)
    monkeypatch.setattr(api,"_stack",lambda mirror,identifier:cube if identifier=="test" else other)
    body["right"]["id"]="offset"
    value=c.post("/api/science/compare",json=body).json()
    assert not value["compatible"]
    assert value["registration"]["correlation"] is None


def strip(pid,orbit=4,amplitude=1.):
    rng=np.random.default_rng(16)
    return xr.Dataset({"image":(("y","x"),amplitude*rng.normal(size=(16,16)),{"units":"DN"}),
        "valid":(("y","x"),np.ones((16,16),bool))},
        coords={"product_ids":("frame",[pid]),"x_km":("x",np.arange(16)*15.),"y_km":("y",np.arange(16)*15.)},
        attrs={"instrument":"JunoCam","band":"RED","km_per_px":15.,"resolution_class":15.,
               "orbit":orbit,"time_mid":"2017-02-02T00:00:00","projection":"same"})


def test_population_counts_observations_and_independent_passes(tmp_path,monkeypatch):
    samples={"old":strip("A_V01"),"new":strip("A_V02"),"samepass":strip("B_V01"),
             "nextpass":strip("C_V01",orbit=5,amplitude=2.)}
    monkeypatch.setattr(api,"_strip",lambda mirror,identifier:samples[identifier])
    c=client(tmp_path)
    value=c.post("/api/science/population",json={"strip_ids":["old","new","samepass"]})
    assert value.status_code==200,value.text
    group=value.json()["groups"][0]
    assert group["n_observations"]==2 and group["n_passes"]==1
    assert group["E_stderr"] is None
    assert value.json()["excluded"][0]["id"]=="old"
    single=np.asarray(group["E"])
    value=c.post("/api/science/population",json={"strip_ids":list(samples)}).json()
    group=value["groups"][0]
    assert group["n_passes"]==2 and group["n_observations"]==3
    # One second-pass strip has 4x the variance: passes receive equal weight,
    # even though the first pass has two independently identified strips.
    np.testing.assert_allclose(group["E"],single*2.5,rtol=1e-12)
    np.testing.assert_allclose(group["E_stderr"],single*1.5,rtol=1e-12)
    json.dumps(value,allow_nan=False)


def test_longitude_seam_overlap_and_fit_failure():
    assert api.bbox_overlap((-10,10,350,20),(-10,10,355,10))==pytest.approx(1.)
    assert api.bbox_overlap((-10,10,350,20),(-10,10,20,10))==0.
    assert science.fit_spectrum([1,2,3],[0,np.nan,-1])["slope"] is None


def test_readiness_endpoint_returns_reasons_not_server_error(tmp_path,monkeypatch):
    monkeypatch.setattr(api,"_stack",lambda mirror,identifier:stack())
    c=client(tmp_path)
    response=c.get("/api/science/stacks/region/cube.nc/readiness")
    assert response.status_code==200
    assert not response.json()["ready"]
    assert "band" in response.json()["reasons"][0]
    assert c.get("/api/science/stacks/region/cube.nc/readiness?band=RED").json()["ready"]


def test_cross_instrument_matches_use_seam_bounds_and_collect_bands(tmp_path,monkeypatch):
    from jiram_catalog.api import catalog
    rows=[]
    for pid,instrument,band,lo,hi in [("J_V01","JunoCam","RED;GREEN",350,10),
                                     ("M_V01","JIRAM","M",355,5),
                                     ("M_V01","JIRAM","L",355,5)]:
        rows.append(dict(product_id=pid,instrument=instrument,band=band,
            start_time=pd.Timestamp("2017-02-02"),min_lat=-10.,max_lat=10.,
            c1_lon=lo,c2_lon=hi,c3_lon=hi,c4_lon=lo,median_pixel_km=15.))
    monkeypatch.setattr(catalog,"catalog_frame",lambda root:pd.DataFrame(rows))
    response=client(tmp_path).post("/api/science/matches",json={"product_id":"J_V01"})
    assert response.status_code==200,response.text
    items=response.json()["items"]
    assert len(items)==1 and items[0]["bands"]==["L","M"]
    assert items[0]["overlap_fraction"]==1.
    assert "approximate" in items[0]["overlap_method"]


def test_vectors_require_explicit_basis_time_and_stack_association(tmp_path,monkeypatch):
    from jiram_catalog.api import stacks
    cube=stack()
    monkeypatch.setattr(api,"_stack",lambda root,identifier:cube)
    monkeypatch.setattr(stacks,"resolve",lambda root,identifier:tmp_path/"cube.nc")
    c=client(tmp_path)
    endpoint="/api/science/stacks/cube/vectors?t=0"
    assert c.get(endpoint).json()["status"]=="unassessed"
    field=xr.Dataset({"u":("point",[3.,4.],{"units":"m s-1"}),
                      "v":("point",[5.,6.],{"units":"m s-1"})},
        coords={"x_km":("point",[15.,30.],{"units":"km"}),
                "y_km":("point",[45.,60.],{"units":"km"})},
        attrs={"vector_basis":"map_xy","source_stack":"cube","projection":"test-grid",
               "time_start":"2017-02-02T00:00:00"})
    path=tmp_path/"test_tracking.nc"
    field.to_netcdf(path,engine="netcdf4")
    result=c.get(endpoint).json()
    assert result["status"]=="available"
    assert result["features"][0]==dict(x_km=15.,y_km=45.,u=3.,v=5.)
    field.attrs["vector_basis"]="east_north"
    field.to_netcdf(path,engine="netcdf4",mode="w")
    result=c.get(endpoint).json()
    assert result["status"]=="unassessed" and not result["features"]


def test_population_continues_after_policy_exclusions(tmp_path,monkeypatch):
    def load(root,identifier):
        if identifier=="withheld":
            raise HTTPException(403,"instrument anomaly excludes this observation")
        return strip("A_V01")
    monkeypatch.setattr(api,"_strip",load)
    response=client(tmp_path).post("/api/science/population",json={"strip_ids":["allowed","withheld"]})
    assert response.status_code==200,response.text
    result=response.json()
    assert result["groups"][0]["n_observations"]==1
    assert result["excluded"]==[{"id":"withheld","reason":"instrument anomaly excludes this observation"}]
    assert result["recipe"]["current_exclusions"]==result["excluded"]


def test_thermal_radiance_rejects_reflected_light_transform():
    cube=stack()
    cube.attrs["instrument"]="JIRAM"
    with pytest.raises(ValueError,match="thermal radiance"):
        science.prepare_stack(cube,band="RED",norm="lambert")
