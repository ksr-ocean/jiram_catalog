from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from jiram_catalog.index import build_index, load_frames


ROOT = Path(__file__).parents[1]
SAMPLE = ROOT / "docs" / "samples" / "JIR_IMG_RDR_2017033T114006_V02.LBL"


def _shifted_label(text: str, seconds: float, sequence_number: int) -> tuple[str, str]:
    start = datetime.fromisoformat("2017-02-02T11:40:03.870") + timedelta(seconds=seconds)
    stop = start + timedelta(seconds=0.012)
    timestamp = start.strftime("%Y%jT%H%M%S")
    product = f"JIR_IMG_RDR_{timestamp}_V02"
    text = re.sub(r"(?m)^PRODUCT_ID\s*=.*$", f"PRODUCT_ID = {product}", text)
    text = re.sub(r"(?m)^START_TIME\s*=.*$", f"START_TIME = {start.isoformat(timespec='milliseconds')}", text)
    text = re.sub(r"(?m)^STOP_TIME\s*=.*$", f"STOP_TIME = {stop.isoformat(timespec='milliseconds')}", text)
    text = re.sub(r"(?m)^SEQUENCE_NUMBER\s*=.*$", f"SEQUENCE_NUMBER = {sequence_number}", text)
    text = text.replace("JIR_IMG_RDR_2017033T114006_V02.IMG", product + ".IMG")
    return product, text


def test_build_and_load_three_frame_index(tmp_path: Path) -> None:
    orbit_dir = tmp_path / "pds4" / "juno_jiram_bundle" / "data_calibrated" / "orbit04"
    orbit_dir.mkdir(parents=True)
    original = SAMPLE.read_text()
    (orbit_dir / SAMPLE.name).write_text(original)
    for seconds, sequence_number in ((30.5, 3), (90.0, 1)):
        product, text = _shifted_label(original, seconds, sequence_number)
        (orbit_dir / f"{product}.LBL").write_text(text)

    frames = build_index(tmp_path, [4], jobs=1)
    assert len(frames) == 3
    by_time = frames.sort_values("start_time").reset_index(drop=True)
    assert by_time.loc[0, "seq_id"] == by_time.loc[1, "seq_id"]
    assert by_time.loc[0, "seq_n"] == 2
    assert by_time.loc[1, "seq_n"] == 2
    assert by_time.loc[2, "seq_id"] != by_time.loc[0, "seq_id"]
    assert by_time.loc[2, "seq_n"] == 1
    assert by_time.loc[1, "seq_gap_s"] == pytest.approx(30.5, abs=1e-3)
    assert not by_time["img_present"].any()

    loaded = load_frames(tmp_path)
    assert loaded["product_id"].tolist() == frames["product_id"].tolist()
