"""Regression evidence for review identity, fail-closed access and discovery."""
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import xarray as xr
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from jiram_catalog.api import catalog, coverage, data, strips
from jiram_catalog.junocam import pds, policy, quality


def nominal(identifier="JNCR_2017033_04C00099_V02", **changes):
    result = dict(product_id=identifier, orbit=4, level="RDR", start_time=pd.Timestamp("2017-02-02T12:00:00"),
                  metrics_ok=True, geo_ok=True, streak_index=.01, saturation_frac=0., zero_frac=.2, max_dn=5000.,
                  bloom_flag=False, quality_tier="A", quality_epoch="nominal", parse_ok=True,
                  img_present=True, filters="RED;GREEN;BLUE", bands="RED;GREEN;BLUE", n_bands=3,
                  path=f"sample/{identifier}.IMG", lines=384, samples=16, sample_bits=16,
                  sample_type="UNSIGNED_INTEGER", bore_lat=40., min_lat=20., max_lat=80.,
                  bore_lon_east=20., bore_emission=40., on_planet_frac=.5, median_pixel_km=10.,
                  dayside_frac=.8, lon_span_deg=10., pole_inside=False, fp_lon=np.array([0,10,20]), fp_lat=np.array([20,40,80]))
    result.update(changes)
    return result


@pytest.fixture
def mirror(tmp_path):
    path = tmp_path / "junocam/index"
    path.mkdir(parents=True)
    records = [nominal(identifier=f"JNCR_2017033_04C00099_V0{v}") for v in (1, 2)]
    records += [nominal(identifier="JNCR_2017033_04C00100_V01", max_dn=0., zero_frac=1.)]
    pd.DataFrame(records).to_parquet(path / "junocam_images.parquet")
    for row in records:
        pixels = tmp_path / "junocam" / row["path"]
        pixels.parent.mkdir(parents=True, exist_ok=True)
        np.arange(384 * 16, dtype=">u2").tofile(pixels)
    catalog.clear_caches()
    data._STACK_CACHE.clear()
    data._STRIP_CACHE.clear()
    return tmp_path


def test_documented_boundaries_and_unassessed_recovery():
    for pid, orbit, status in [("JNCR_2022001_47C00001_V03",47,"excluded"),
                               ("JNCR_2022001_47C00003_V01",47,"unassessed"),
                               ("JNCR_2022001_48C00214_V02",48,"excluded"),
                               ("JNCR_2022001_48C00215_V02",48,"unassessed"),
                               ("JNCR_2022001_73R00013_V01",73,"excluded"),
                               ("JNCR_2022001_74G00008_V01",74,"excluded"),
                               ("JNCR_2022001_75C00001_V01",75,"excluded"),
                               ("JNCR_2022001_57C00001_V01",57,"unassessed")]:
        assert policy.assess_observation(nominal(pid, orbit=orbit, quality_epoch="post_anneal"))["status"] == status
    clear = nominal("JNCR_2022001_48C00215_V02", orbit=48, unaffected_evidence="Per-image clearance", unaffected_evidence_source="source label")
    assert policy.assess_observation(clear)["status"] == "eligible"
    for missing in (np.nan, pd.NaT, pd.NA, None, True, False, "unknown"):
        incomplete = nominal("JNCR_2022001_57C00001_V01", orbit=57,
                             unaffected_evidence=missing, unaffected_evidence_source="label")
        assert policy.assess_observation(incomplete)["status"] == "unassessed"
        incomplete.update(unaffected_evidence="clearance", unaffected_evidence_source=missing)
        assert policy.assess_observation(incomplete)["status"] == "unassessed"
    # No quality flag or clearance can override a documented failure.
    clear["product_id"] = "JNCR_2022001_48C00214_V02"
    assert policy.assess_observation(clear)["status"] == "excluded"


def test_unknown_response_and_failed_metrics():
    assert all(np.isnan(x) for x in quality.throughput_factors(61).values())
    assert quality.quality_tier("post_anneal", .01, 0.) == "C"
    for changes in ({"bloom_flag": True}, {"max_dn": np.nan}, {"metrics_ok": False}):
        assert policy.assess_observation(nominal(**changes))["status"] != "eligible"


def test_raw_details_versions_and_excluded_pixels(mirror, monkeypatch):
    app = FastAPI(); app.state.mirror = mirror; app.include_router(catalog.router)
    client = TestClient(app)
    good = "JNCR_2017033_04C00099_V01"
    bad = "JNCR_2017033_04C00100_V01"
    detail = client.get(f"/api/catalog/frame/{good}").json()
    assert detail["instrument"] == "JunoCam"
    assert detail["versions"] == [good[:-1] + "2", good]
    assert detail["preview_allowed"]
    assert detail["quality_assessment"]["status"] == "eligible"
    assert client.get(f"/api/catalog/frame/{good}/thumbnail.png?band=RED").content.startswith(b"\x89PNG")
    monkeypatch.setattr(np, "memmap", lambda *a, **k: pytest.fail("withheld pixels must not open"))
    assert client.get(f"/api/catalog/frame/{bad}").json()["preview_allowed"] is False
    assert client.get(f"/api/catalog/frame/{bad}/thumbnail.png?band=RED").status_code == 403
    assert client.get(f"/api/catalog/frame/{good}/thumbnail.png?band=METHANE").status_code == 400


def test_catalog_time_identity_and_latitude_coverage(mirror):
    table = data.junocam_catalog(mirror)
    assert len(table) == 1
    assert table.start_time_ms.iloc[0] == int(pd.Timestamp("2017-02-02T12:00:00").timestamp() * 1000)
    assert table.observation_id.iloc[0] == "JNCR_2017033_04C00099"
    assert table.trackability_status.tolist() == ["unassessed"]
    assert len(catalog.apply_filters(table, lat_min=70., latitude_mode="coverage")) == 1
    assert len(catalog.apply_filters(table, lat_min=70., latitude_mode="boresight")) == 0


def test_existing_stack_and_strip_withheld_without_modification(mirror, monkeypatch):
    ids = ["JNCR_2017033_04C00099_V01", "JNCR_2017033_04C00099_V02", "JNCR_2017033_04C00100_V01"]
    ds = xr.Dataset({"image": (("time","y","x"), np.ones((3,2,2)))},
                    coords={"time": pd.date_range("2017-02-02", periods=3, freq="ms"), "product_id": ("time", ids)},
                    attrs={"instrument":"JunoCam"})
    monkeypatch.setattr(data, "read_stack", lambda _: ds)
    path = mirror / "regions/test/junocam.nc"
    viewed = data.open_stack(path)
    assert viewed.sizes["time"] == 1 and viewed.product_id.item() == ids[1]
    assert ds.sizes["time"] == 3
    bad = ds.isel(time=2, drop=True).assign_coords(product_ids=("frame", [ids[2]]))
    monkeypatch.setattr(data, "read_strip", lambda *a: bad)
    app = FastAPI(); app.state.mirror = mirror; app.include_router(strips.router)
    client = TestClient(app)
    assert client.get("/api/strips/bad/meta").status_code == 403
    with pytest.raises(HTTPException) as error:
        data.strip_stats(mirror,"bad")
    assert error.value.status_code == 403


def test_jiram_detail_and_half_thumbnail(mirror, monkeypatch):
    pid = "JIR_IMG_RDR_2017033T120000_V02"
    image = mirror / "frame.IMG"
    np.concatenate([np.arange(128 * 432).reshape(128,432), np.ones((128,432))]).astype("<f4").tofile(image)
    rows = pd.DataFrame([dict(product_id=pid, half=b, orbit_dir=4, path="frame.IMG") for b in ("L","M")])
    monkeypatch.setattr(catalog, "full_frame", lambda _: rows)
    app = FastAPI(); app.state.mirror = mirror; app.include_router(catalog.router)
    client = TestClient(app)
    detail = client.get(f"/api/catalog/frame/{pid}").json()
    assert detail["halves"] == ["L","M"] and detail["source_url"].endswith(pid+".IMG")
    assert client.get(f"/api/catalog/frame/{pid}/thumbnail.png?band=L").content != client.get(f"/api/catalog/frame/{pid}/thumbnail.png?band=M").content


def test_coverage_archive_metadata_counts_observations(mirror):
    app = FastAPI(); app.state.mirror = mirror; app.include_router(coverage.router)
    client = TestClient(app)
    archive = client.get("/api/archive").json()
    assert archive["total"] == 2
    assert {r["status"] for r in archive["items"]} == {"eligible", "excluded"}
    payload = client.get("/api/coverage").json()
    row = next(r for r in payload["rows"] if r["instrument"] == "JunoCam")
    assert row["eligible"] == 1 and row["excluded"] == 1 and row["labels_indexed"] == 2
    assert "snapshot" in row["pixels_local_basis"]
    assert client.get("/api/archive?limit=201").status_code == 422
    assert client.get("/api/references").json()["items"][1]["quantitative_ready"] is False


def test_volume_discovery_extends_past_historical_ceiling(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(pds, "fetch_bytes", lambda *a, **k: b'<a href="JNOJNC_0036/">new</a><a href="JNOSRU_0001/">other</a>')
    assert pds.discover_volumes(tmp_path) == [36]
    def offline(*args, **kwargs):
        raise RuntimeError("offline")
    monkeypatch.setattr(pds, "fetch_bytes", offline)
    assert pds.discover_volumes(tmp_path) == [36]
    assert "cached directory snapshot" in caplog.text


def test_preferred_version_does_not_fall_back_to_older_clean_pixels(mirror):
    path = mirror / "junocam/index/junocam_images.parquet"
    table = pd.read_parquet(path)
    latest = table.product_id == "JNCR_2017033_04C00099_V02"
    table.loc[latest, "max_dn"] = 0.
    table.loc[latest, "zero_frac"] = 1.
    table.to_parquet(path)
    assert policy.eligible_images(mirror).empty
    assert policy.eligible_images(mirror, preferred=False).product_id.tolist() == ["JNCR_2017033_04C00099_V01"]
    # A known newer archive release also prevents falling back to local data.
    table.loc[latest, "max_dn"] = 5000.
    table.loc[latest, "zero_frac"] = .2
    table.to_parquet(path)
    archived = mirror / "junocam/manifest/manifest_files.parquet"
    archived.parent.mkdir(parents=True)
    pd.DataFrame({"product_id": ["JNCR_2017033_04C00099_V03"]}).to_parquet(archived)
    assert policy.eligible_images(mirror).empty


def test_partial_evidence_columns_do_not_clear_other_observations(mirror):
    path = mirror / "junocam/index/junocam_images.parquet"
    table = pd.read_parquet(path)
    table = pd.concat([table, pd.DataFrame([
        nominal("JNCR_2023364_57C00100_V01", orbit=57,
                unaffected_evidence="Image-specific unaffected assessment",
                unaffected_evidence_source="product assessment document"),
        nominal("JNCR_2023364_57C00101_V01", orbit=57),
    ])], ignore_index=True)
    table.to_parquet(path)
    assessed = policy.observation_table(mirror).set_index("product_id")
    assert assessed.loc["JNCR_2023364_57C00100_V01", "quality_status"] == "eligible"
    assert assessed.loc["JNCR_2023364_57C00101_V01", "quality_status"] == "unassessed"
    app = FastAPI(); app.state.mirror = mirror; app.include_router(catalog.router)
    client = TestClient(app)
    assert client.get("/api/catalog/frame/JNCR_2023364_57C00101_V01/thumbnail.png?band=RED").status_code == 403


def test_batch_and_concurrent_assessment_avoid_metadata_storm(mirror, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    config = quality.load_quality_config()
    calls = []
    def counted_config():
        calls.append("config")
        return config
    monkeypatch.setattr(policy, "load_quality_config", counted_config)
    assert len(policy.assess_rows(nominal() for _ in range(1000))) == 1000
    assert calls == ["config"]
    calls.clear()
    policy._CACHE.clear()
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: policy.observation_table(mirror), range(6)))
    assert all(len(result) == 3 for result in results)
    assert calls == ["config"]


def test_strip_table_single_flight_and_snapshot_invalidation(mirror, monkeypatch):
    """Simultaneous startup reads share work; source/quality edits never go stale."""
    import os
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    source = mirror / "strips/strips.parquet"
    source.parent.mkdir(parents=True)
    ids = ["JNCR_2017033_04C00099_V01", "JNCR_2017033_04C00099_V02", "JNCR_2017033_04C00100_V01"]
    rows = [dict(strip_id=pid, seq_id=pid, orbit=4, band="RED", resolution_class=10., instrument="JunoCam") for pid in ids]
    rows.append(dict(strip_id="JIRAM_ONE", seq_id="JIRAM_SEQ", orbit=4, band="M", resolution_class=10., instrument="JIRAM"))
    pd.DataFrame(rows).to_parquet(source)
    data._STRIPS_CACHE.clear()
    original = data.load_index
    calls = []
    def counted(root):
        calls.append("read")
        return original(root)
    monkeypatch.setattr(data, "load_index", counted)
    barrier = Barrier(6)
    def request(_):
        barrier.wait(timeout=10)
        return data.strips_table(mirror)
    with ThreadPoolExecutor(max_workers=6) as pool:
        tables = list(pool.map(request, range(6)))
    assert calls == ["read"]
    expected = ["JIRAM_ONE", ids[1]]
    assert all(table.strip_id.tolist() == expected for table in tables)
    # Returned scalar cells and columns are isolated from the cached snapshot.
    tables[0].loc[0, "strip_id"] = "CALLER_EDIT"
    tables[0]["new_column"] = 42
    assert data.strips_table(mirror).strip_id.tolist() == expected
    assert "new_column" not in data.strips_table(mirror)
    assert len(calls) == 1
    # A timestamp-only change invalidates the cache even with identical bytes.
    stat = source.stat()
    os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns + 2_000_000_000))
    data.strips_table(mirror)
    assert len(calls) == 2
    # A size-only change also invalidates it, even if mtime is restored.
    stat = source.stat()
    rows.append(dict(strip_id="JIRAM_TWO", seq_id="JIRAM_SEQ_2", orbit=4, band="L", resolution_class=10., instrument="JIRAM"))
    pd.DataFrame(rows).to_parquet(source)
    assert source.stat().st_size != stat.st_size
    os.utime(source, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert len(data.strips_table(mirror)) == 3
    assert len(calls) == 3
    # Changed image quality removes both versions without touching strip files.
    image_index = mirror / "junocam/index/junocam_images.parquet"
    images = pd.read_parquet(image_index)
    images["max_dn"] = 0.
    images["zero_frac"] = 1.
    images.to_parquet(image_index)
    assert data.strips_table(mirror).strip_id.tolist() == ["JIRAM_ONE", "JIRAM_TWO"]
    assert len(calls) == 4
    # The application's cache-reset hook clears this view too.
    catalog.clear_caches()
    data.strips_table(mirror)
    assert len(calls) == 5


def test_strip_table_invalidates_on_exclusion_configuration(mirror, monkeypatch):
    import yaml
    from copy import deepcopy
    source = mirror / "strips/strips.parquet"
    source.parent.mkdir(parents=True)
    pid = "JNCR_2017033_04C00099_V02"
    pd.DataFrame([dict(strip_id=pid, seq_id=pid, orbit=4, band="RED", instrument="JunoCam")]).to_parquet(source)
    config = deepcopy(quality.load_quality_config())
    config_file = mirror / "quality.yaml"
    config_file.write_text(yaml.safe_dump(config))
    monkeypatch.setenv(quality.CONFIG_ENV, str(config_file))
    assert len(data.strips_table(mirror)) == 1
    config["access_policy"]["exclusions"].append(dict(orbit_min=4, orbit_max=4,
        reason="Synthetic policy revision", source="offline regression"))
    config_file.write_text(yaml.safe_dump(config))
    assert data.strips_table(mirror).empty
