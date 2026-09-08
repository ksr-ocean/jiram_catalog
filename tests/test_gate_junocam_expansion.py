"""Lead-owned integration oracle for the September 8 multi-pass expansion."""
import hashlib
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd
import pytest


def context():
    if os.environ.get("JIRAM_SKIP_GATES") == "1":
        pytest.skip("JIRAM_SKIP_GATES=1")
    from jiram_catalog.config import mirror_root
    root = mirror_root()
    oracle = pd.read_csv(Path(__file__).parents[1] / "docs/reports/junocam_expansion_selection_2026-09-08.csv")
    return root, oracle.loc[oracle.selected].copy(), root / "junocam/expansion_2026-09-08"


def test_acquired_products():
    root, selected, work = context()
    assert len(selected) == 194 and selected.image_bytes.sum() == 7854710784
    assert set(selected.orbit) == {5, 6, 8, 12, 18, 24, 30, 34}
    for row in selected.itertuples():
        label = root / "junocam" / row.volume / row.file_spec
        native = label.with_suffix(".IMG")
        assert native.stat().st_size == row.image_bytes, row.product_id
        for path, expected in [(label, row.label_md5), (native, row.image_md5)]:
            with path.open("rb") as stream:
                assert hashlib.file_digest(stream, "md5").hexdigest() == expected, str(path)
    images = pd.read_parquet(root / "junocam/index/junocam_images.parquet")
    new = images[images.product_id.isin(selected.product_id)]
    assert len(new) == 194 and new.parse_ok.all() and new.img_present.all()
    for name in ["junocam_images.parquet", "junocam_quality.parquet"]:
        before = pd.read_parquet(work / "baseline/junocam/index" / name)
        after = pd.read_parquet(root / "junocam/index" / name)
        after = after[after.product_id.isin(before.product_id)]
        pd.testing.assert_frame_equal(before.sort_values("product_id").reset_index(drop=True),
                                      after.sort_values("product_id").reset_index(drop=True))


def test_new_passes_eligible_and_mapped():
    import xarray as xr
    from jiram_catalog.junocam.policy import observation_table, eligible_images
    from jiram_catalog.strips import load_strips
    root, selected, work = context()
    eligible = eligible_images(root)
    new = eligible[eligible.product_id.isin(selected.product_id)]
    assert len(set(new.orbit)) >= 3 and len(new) >= 20
    assert set(new.product_id) <= set(selected.product_id)
    all_observations = observation_table(root)
    assert not all_observations[all_observations.orbit >= 47].product_id.isin(eligible.product_id).any()
    strips = load_strips(root, instrument="JunoCam")
    added = strips[strips.strip_id.isin(new.product_id)]
    assert len(set(added.orbit)) >= 3 and len(added) >= 6
    for orbit, rows in added.groupby("orbit"):
        with xr.open_dataset(root / rows.iloc[0].path) as dataset:
            assert list(dataset.band.values) == ["RED", "GREEN", "BLUE"]
            for band in dataset.band.values:
                assert np.isfinite(dataset.image.sel(band=band).values).any()
            assert dataset.valid.values.any()
    for record in json.loads((work / "baseline/existing_products.json").read_text()):
        stat = (root / record["path"]).stat()
        assert stat.st_size == record["bytes"] and stat.st_mtime_ns == record["mtime_ns"], record["path"]


def test_api_exposes_multiple_passes_and_physical_bands():
    import io
    import pyarrow.ipc as ipc
    from fastapi.testclient import TestClient
    from jiram_catalog.api.app import create_app
    root, selected, work = context()
    with TestClient(create_app(root)) as client:
        response = client.get("/api/catalog/frames.arrow")
        assert response.status_code == 200
        table = ipc.open_stream(io.BytesIO(response.content)).read_all().to_pandas()
        new = table[(table.instrument == "JunoCam") & table.orbit.isin(selected.orbit)]
        assert new.orbit.nunique() >= 3 and len(new) >= 20
        response = client.get("/api/strips.arrow")
        strips = ipc.open_stream(io.BytesIO(response.content)).read_all().to_pandas()
        added = strips[(strips.instrument == "JunoCam") & strips.orbit.isin(selected.orbit)]
        assert added.orbit.nunique() >= 3
