from __future__ import annotations

from pathlib import Path

import pytest

from jiram_catalog.labels import parse_label


ROOT = Path(__file__).parents[1]
SAMPLE = ROOT / "docs" / "samples" / "JIR_IMG_RDR_2017033T114006_V02.LBL"


def test_parse_sample_label() -> None:
    row = parse_label(SAMPLE)
    exact = {
        "product_id": "JIR_IMG_RDR_2017033T114006_V02",
        "orbit": 4,
        "band": "M",
        "geom_band": "M",
        "sclk_start": "1/539307798:00786",
        "sequence_number": 2,
        "sequence_samples": 13,
        "instrument_mode_id": "SCI_I2_S1",
        "lines": 128,
        "samples": 432,
        "sample_bits": 32,
        "img_size_bytes": 221184,
        "md5_checksum": "739d4daf79cc6e205ecc06b44fd56564",
        "target_pixels": 55296,
        "target_presence_flag": "000000000010000",
        "img_file_name": "JIR_IMG_RDR_2017033T114006_V02.IMG",
    }
    for key, value in exact.items():
        assert row[key] == value
    assert row["start_time"].isoformat(timespec="milliseconds") == "2017-02-02T11:40:03.870"
    assert row["stop_time"].isoformat(timespec="milliseconds") == "2017-02-02T11:40:03.882"
    assert row["exposure_s"] == pytest.approx(0.012, abs=1e-6)
    expected_floats = {
        "sub_sc_lat": 73.1781,
        "sub_sc_lon": 309.7766,
        "sc_altitude_km": 115377.9107,
        "center_lat": 83.1502,
        "center_lon": 305.7333,
        "min_lat": 79.8064,
        "max_lat": 84.7971,
        "westernmost_lon": 262.5403,
        "easternmost_lon": 352.9865,
        "emission_angle": 16.5375,
        "incidence_angle": 94.6048,
        "phase_angle": 98.1083,
        "slant_distance_km": 117265.7597,
        "h_pixel_scale_m": 27690.6986,
        "north_azimuth": 66.3493,
        "line_first_pixel": 2,
        "sample_first_pixel": 139,
        "q_w": 0.27929,
        "q_x": -0.65052,
    }
    for key, value in expected_floats.items():
        assert row[key] == pytest.approx(value)
    assert "juno_sc_rec_170129_170204_v01.bc" in row["spice_kernels"]
    assert row["parse_ok"] is True


def test_geometry_falls_back_to_l_band(tmp_path: Path) -> None:
    text = SAMPLE.read_text()
    text = text.replace("L_BAND_PARAMETERS", "TEMP_BAND_PARAMETERS")
    text = text.replace("M_BAND_PARAMETERS", "L_BAND_PARAMETERS")
    text = text.replace("TEMP_BAND_PARAMETERS", "M_BAND_PARAMETERS")
    label = tmp_path / SAMPLE.name
    label.write_text(text)
    row = parse_label(label)
    assert row["parse_ok"] is True
    assert row["geom_band"] == "L"
    assert row["center_lat"] == pytest.approx(83.1502)


def test_truncated_label_is_retained_as_parse_failure(tmp_path: Path) -> None:
    label = tmp_path / "JIR_IMG_RDR_2099001T000000_V01.LBL"
    label.write_text(SAMPLE.read_text().rsplit("END", maxsplit=1)[0])
    row = parse_label(label)
    assert row["product_id"] == "JIR_IMG_RDR_2099001T000000_V01"
    assert row["parse_ok"] is False
    assert row["parse_error"]
