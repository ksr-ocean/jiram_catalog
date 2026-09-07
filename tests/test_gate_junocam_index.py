"""Gate for JunoCam acquisition, index, and quality table (READ-ONLY)."""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from jiram_catalog.config import mirror_root


def _root() -> Path:
    if os.environ.get("JIRAM_SKIP_GATES") == "1":
        pytest.skip("JIRAM_SKIP_GATES=1")
    return mirror_root() / "junocam"


def test_manifest_covers_volumes_and_orbit4_counts():
    m = pd.read_parquet(_root() / "manifest" / "manifest.parquet")
    vols = set(m["volume"].astype(str))
    assert {f"JNOJNC_{i:04d}" for i in (1, 3, 28, 35)} <= vols, sorted(vols)[:5]
    o4 = m[(m["orbit"] == 4) & (m["level"] == "EDR")]
    assert len(o4) == 1021, len(o4)
    assert (m["level"].isin(["EDR", "RDR"])).all()


def test_index_has_sample_products_with_quoted_values():
    ix = pd.read_parquet(_root() / "index" / "junocam_images.parquet").set_index("product_id")
    a = ix.loc["JNCE_2017033_04C00105_V01"] if "JNCE_2017033_04C00105_V01" in ix.index else ix.loc["JNCR_2017033_04C00105_V01"]
    assert str(pd.Timestamp(a["start_time"])).startswith("2017-02-02 13:05:34.387")
    assert a["sclk_start"] == "539312928:137" and int(a["orbit"]) == 4
    assert abs(float(a["interframe_delay_s"]) - 0.375) < 1e-9
    assert abs(float(a["exposure_ms"]) - 9.6) < 1e-6 and int(a["tdi_stages"]) == 3
    assert a["filters"] == "BLUE;GREEN;RED" and int(a["n_bands"]) == 3
    assert int(a["lines"]) == 15360 and int(a["n_framelets"]) == 40 and int(a["samples"]) == 1648
    b = ix[ix.index.str.contains("2024034_58C00012")].iloc[0]
    assert abs(float(b["exposure_ms"]) - 6.4) < 1e-6 and int(b["tdi_stages"]) == 2 and int(b["lines"]) == 3840
    assert ix["parse_ok"].all()


def test_orbit4_perijove_day_rdr_images_mirrored_and_quality_rows():
    root = _root()
    ix = pd.read_parquet(root / "index" / "junocam_images.parquet")
    day = ix[(ix["orbit"] == 4) & (ix["level"] == "RDR") & ix["product_id"].str.contains("_2017033_")]
    assert len(day) >= 90, len(day)
    assert day["img_present"].all()
    for _, r in day.head(5).iterrows():
        p = root / r["path"] if not str(r["path"]).startswith("/") else Path(r["path"])
        assert p.exists() and p.stat().st_size == int(r["img_size_bytes"]), p
    q = pd.read_parquet(root / "index" / "junocam_quality.parquet").set_index("product_id")
    missing = [p for p in day["product_id"] if p not in q.index]
    assert not missing, missing[:3]
    rows = q.loc[day["product_id"].tolist()]
    assert (rows["quality_epoch"] == "nominal").all()
    for c in ["streak_index", "noise_mad", "saturation_frac", "zero_frac"]:
        assert np.isfinite(rows[c].to_numpy(dtype=float)).all(), c
    assert set(rows["quality_tier"]) <= {"A", "B", "C"}
    assert (rows["throughput_factor_red"] == 1.0).all()


def test_2024_epoch_is_post_anneal():
    ix = pd.read_parquet(_root() / "index" / "junocam_images.parquet")
    b = ix[ix["product_id"].str.contains("2024034_58C00012")]
    assert len(b) >= 1
    from jiram_catalog.junocam.quality import epoch_for_orbit

    assert epoch_for_orbit(int(b.iloc[0]["orbit"])) == "post_anneal"
    assert epoch_for_orbit(4) == "nominal"
    assert epoch_for_orbit(56) == "ccd_damage"
