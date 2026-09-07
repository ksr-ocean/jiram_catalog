"""Offline tests for the JunoCam acquisition, index, and quality layer.

Nothing here touches the network.  The only files read from outside the
repository are the two sample labels saved during the archive reconnaissance
under ``<mirror>/junocam_samples/``; the tests that need them skip when the
mirror is not present, so the suite still passes on a bare checkout.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from jiram_catalog.config import mirror_root
from jiram_catalog.junocam import mirror as junocam_mirror
from jiram_catalog.junocam import pds, quality
from jiram_catalog.junocam.cli import parse_doy, parse_kinds, parse_orbits, parse_volumes
from jiram_catalog.junocam.labels import framelet_counts, parse_label

SAMPLES = mirror_root() / "junocam_samples"
SAMPLE_2017 = SAMPLES / "JNCE_2017033_04C00105_V01.LBL"
SAMPLE_2024 = SAMPLES / "JNCE_2024034_58C00012_V01.LBL"


def _require(path: Path) -> Path:
    if not path.is_file():
        pytest.skip(f"reconnaissance sample not available: {path}")
    return path


# --------------------------------------------------------------------------
# product identity
# --------------------------------------------------------------------------


def test_parse_product_id_fields() -> None:
    parsed = pds.parse_product_id("JNCE_2017033_04C00105_V01")
    assert parsed is not None
    assert parsed["type"] == "E" and parsed["level"] == "EDR"
    assert parsed["year"] == 2017 and parsed["doy"] == "033"
    assert parsed["orbit"] == 4
    assert parsed["filter_code"] == "C" and parsed["filter_code_name"] == "RGB"
    assert parsed["sequence"] == 105 and parsed["version"] == 1
    assert parsed["stem"] == "JNCE_2017033_04C00105"


def test_parse_product_id_rdr_and_cruise() -> None:
    rdr = pds.parse_product_id("JNCR_2024034_58M00012_V03")
    assert rdr is not None and rdr["level"] == "RDR" and rdr["version"] == 3
    assert rdr["orbit"] == 58 and rdr["filter_code_name"] == "METHANE"
    cruise = pds.parse_product_id("JNCE_2011238_00A00002_V01")
    assert cruise is not None and cruise["orbit"] == 0 and cruise["filter_code"] == "A"


@pytest.mark.parametrize(
    "product_id",
    ["", "JNCE_2017033_04C00105", "NOTAPRODUCT", "JNCE_201703_04C00105_V01"],
)
def test_parse_product_id_rejects_malformed(product_id: str) -> None:
    assert pds.parse_product_id(product_id) is None


def test_volume_name_and_url() -> None:
    assert pds.volume_name(3) == "JNOJNC_0003"
    assert pds.volume_url("JNOJNC_0003").endswith("/juno/JNOJNC_0003/")


# --------------------------------------------------------------------------
# INDEX.LBL / INDEX.TAB
# --------------------------------------------------------------------------

SYNTHETIC_INDEX_LABEL = """
PDS_VERSION_ID        = PDS3
RECORD_TYPE             = FIXED_LENGTH
RECORD_BYTES            = 60
FILE_RECORDS            = 2
  ^INDEX_TABLE            = ("INDEX.TAB")
  OBJECT                  = INDEX_TABLE
    NAME                    = "INDEX_TABLE"
    ROWS                    = 2
    COLUMNS                 = 3
    ROW_BYTES               = 60

    OBJECT                  = COLUMN
      NAME                    = "PRODUCT_ID"
      DATA_TYPE               = CHARACTER
      START_BYTE              = 2
      BYTES                   = 25
      DESCRIPTION             = "The unchanging product ID."
    END_OBJECT              = COLUMN

    OBJECT                  = COLUMN
      NAME                    = "PROCESSING_LEVEL_ID"
      DATA_TYPE               = CHARACTER
      START_BYTE              = 30
      BYTES                   = 1
    END_OBJECT              = COLUMN

    OBJECT                  = COLUMN
      NAME                    = "FILE_SPECIFICATION_NAME"
      DATA_TYPE               = CHARACTER
      START_BYTE              = 34
      BYTES                   = 25
    END_OBJECT              = COLUMN
  END_OBJECT              = INDEX_TABLE
END
"""


def test_parse_index_label_reads_names_and_positions() -> None:
    columns = pds.parse_index_label(SYNTHETIC_INDEX_LABEL)
    assert [column.name for column in columns] == [
        "PRODUCT_ID",
        "PROCESSING_LEVEL_ID",
        "FILE_SPECIFICATION_NAME",
    ]
    assert [column.start_byte for column in columns] == [2, 30, 34]
    assert [column.n_bytes for column in columns] == [25, 1, 25]
    assert columns[0].data_type == "CHARACTER"


def test_parse_index_label_rejects_a_label_without_columns() -> None:
    with pytest.raises(ValueError):
        pds.parse_index_label("PDS_VERSION_ID = PDS3\nEND\n")


def test_parse_index_table_splits_quoted_csv() -> None:
    columns = pds.parse_index_label(SYNTHETIC_INDEX_LABEL)
    table = (
        '"JNCE_2017033_04C00105_V01","2","DATA/EDR/JUPITER/A.LBL"\r\n'
        '"JNCR_2017033_04C00105_V01","3","DATA/RDR/JUPITER/A.LBL"\r\n'
    )
    rows = pds.parse_index_table(table, columns)
    assert len(rows) == 2
    assert rows[0]["PRODUCT_ID"] == "JNCE_2017033_04C00105_V01"
    assert rows[0]["PROCESSING_LEVEL_ID"] == "2"
    assert rows[1]["FILE_SPECIFICATION_NAME"] == "DATA/RDR/JUPITER/A.LBL"


def test_parse_index_table_falls_back_to_byte_positions() -> None:
    """A row whose field count disagrees is cut at the declared positions."""
    columns = pds.parse_index_label(SYNTHETIC_INDEX_LABEL)
    padded = (
        '"JNCE_2017033_04C00105_V01" "2" "DATA/EDR/JUPITER/A.LBL"'
    )  # spaces, not commas: one CSV field
    rows = pds.parse_index_table(padded, columns)
    assert rows[0]["PRODUCT_ID"] == "JNCE_2017033_04C00105_V01"
    assert rows[0]["PROCESSING_LEVEL_ID"] == "2"


def test_deduplicate_keeps_the_highest_version() -> None:
    frame = pds.coerce_manifest(
        pd.DataFrame.from_records(
            [
                {
                    "volume": "JNOJNC_0003",
                    "product_id": "JNCE_2017033_04C00097_V01",
                    "product_stem": "JNCE_2017033_04C00097",
                    "level": "EDR",
                    "version": 1,
                },
                {
                    "volume": "JNOJNC_0004",
                    "product_id": "JNCE_2017033_04C00097_V02",
                    "product_stem": "JNCE_2017033_04C00097",
                    "level": "EDR",
                    "version": 2,
                },
                {
                    "volume": "JNOJNC_0003",
                    "product_id": "JNCR_2017033_04C00097_V01",
                    "product_stem": "JNCR_2017033_04C00097",
                    "level": "RDR",
                    "version": 1,
                },
            ]
        )
    )
    kept = pds.deduplicate(frame)
    assert len(kept) == 2
    edr = kept.loc[kept["level"] == "EDR"].iloc[0]
    assert edr["product_id"] == "JNCE_2017033_04C00097_V02"
    assert int(edr["n_versions"]) == 2
    assert edr["superseded_volumes"] == "JNOJNC_0003"


# --------------------------------------------------------------------------
# labels
# --------------------------------------------------------------------------


def test_sample_2017_label_matches_the_archive_values() -> None:
    row = parse_label(_require(SAMPLE_2017))
    assert row["parse_ok"] and row["parse_error"] == ""
    assert row["product_id"] == "JNCE_2017033_04C00105_V01"
    assert row["level"] == "EDR" and int(row["orbit"]) == 4
    assert row["start_time"].isoformat(timespec="milliseconds") == "2017-02-02T13:05:34.387"
    assert row["image_time"] == row["start_time"]
    assert row["stop_time"].isoformat(timespec="milliseconds") == "2017-02-02T13:05:49.387"
    assert row["sclk_start"] == "539312928:137"
    assert row["interframe_delay_s"] == pytest.approx(0.375, abs=1e-12)
    assert row["exposure_ms"] == pytest.approx(9.6, abs=1e-9)
    assert int(row["tdi_stages"]) == 3
    assert row["filters"] == "BLUE;GREEN;RED" and int(row["n_bands"]) == 3
    assert int(row["lines"]) == 15360 and int(row["samples"]) == 1648
    assert int(row["n_framelet_rows"]) == 120 and int(row["n_framelets"]) == 40
    assert row["framelets_exact"] is True
    assert int(row["sample_bits"]) == 8 and row["sample_bit_mode_id"] == "SQROOT"
    assert row["companded"] is True
    assert int(row["record_bytes"]) == 1648 and int(row["file_records"]) == 15360
    assert int(row["img_size_bytes"]) == 1648 * 15360
    assert row["md5"] == "5479106c82f54750b362cd50cf167c34"
    assert row["target"] == "JUPITER"
    assert row["doy_dir"] == "033" and row["filter_code"] == "C"


def test_sample_2024_label_differs_where_the_recon_says_it_does() -> None:
    row = parse_label(_require(SAMPLE_2024))
    assert row["parse_ok"]
    assert row["product_id"] == "JNCE_2024034_58C00012_V01"
    assert int(row["orbit"]) == 58
    assert row["exposure_ms"] == pytest.approx(6.4, abs=1e-9)
    assert int(row["tdi_stages"]) == 2
    assert int(row["lines"]) == 3840 and int(row["n_framelets"]) == 10
    assert row["interframe_delay_s"] == pytest.approx(0.375, abs=1e-12)
    assert row["focal_plane_temperature_k"] == pytest.approx(259.2, abs=1e-9)


def test_label_json_carries_every_keyword() -> None:
    import json

    row = parse_label(_require(SAMPLE_2017))
    document = json.loads(row["label_json"])
    assert document["ORBIT_NUMBER"] == 4
    assert document["INTERFRAME_DELAY"] == "0.375 <s>"
    assert document["FILTER_NAME"] == ["BLUE", "GREEN", "RED"]
    assert document["IMAGE"]["SAMPLE_BITS"] == 8
    assert "RATIONALE_DESC" in document


def test_a_truncated_label_stays_a_row(tmp_path: Path) -> None:
    text = _require(SAMPLE_2017).read_text()
    broken = tmp_path / "JNCE_2017033_04C00105_V01.LBL"
    broken.write_text(text.replace("\nEND\n", "\n"))
    row = parse_label(broken)
    assert row["parse_ok"] is False
    assert "truncated" in row["parse_error"]
    assert row["product_id"] == "JNCE_2017033_04C00105_V01"
    assert row["level"] == "EDR"


def test_relative_paths_are_taken_from_the_mirror_root(tmp_path: Path) -> None:
    directory = tmp_path / "JNOJNC_0003" / "DATA" / "EDR" / "JUPITER" / "ORBIT_04"
    directory.mkdir(parents=True)
    label = directory / SAMPLE_2017.name
    label.write_text(_require(SAMPLE_2017).read_text())
    spec = "DATA/EDR/JUPITER/ORBIT_04/JNCE_2017033_04C00105_V01.LBL"
    row = parse_label(label, tmp_path, "JNOJNC_0003", spec)
    assert row["label_path"] == f"JNOJNC_0003/{spec}"
    assert row["path"] == f"JNOJNC_0003/{spec[:-4]}.IMG"
    assert row["img_present"] is False
    assert int(row["orbit_from_path"]) == 4


@pytest.mark.parametrize(
    ("lines", "n_bands", "expected"),
    [
        (15360, 3, (120, 40, True)),
        (3840, 3, (30, 10, True)),
        (1024, 1, (8, 8, True)),
        (1280, 4, (10, 2, False)),  # 10 framelet rows do not fill 4-band frames
        (100, 3, (0, 0, False)),  # fewer lines than one framelet
        (0, 3, (None, None, False)),
    ],
)
def test_framelet_counts(lines: int, n_bands: int, expected: tuple) -> None:
    assert framelet_counts(lines, n_bands) == expected


def test_framelet_counts_without_filters() -> None:
    assert framelet_counts(15360, 0) == (120, None, False)


# --------------------------------------------------------------------------
# mirror bookkeeping
# --------------------------------------------------------------------------


def test_is_complete_label(tmp_path: Path) -> None:
    good = tmp_path / "a.LBL"
    good.write_text("PDS_VERSION_ID = PDS3\nEND\n")
    bad = tmp_path / "b.LBL"
    bad.write_text("PDS_VERSION_ID = PDS3\n")
    assert junocam_mirror.is_complete(good)
    assert not junocam_mirror.is_complete(bad)
    assert not junocam_mirror.is_complete(tmp_path / "missing.LBL")


def test_is_complete_image_uses_the_sibling_label(tmp_path: Path) -> None:
    label = tmp_path / "a.LBL"
    label.write_text("RECORD_BYTES = 10\nFILE_RECORDS = 4\nEND\n")
    image = tmp_path / "a.IMG"
    image.write_bytes(b"x" * 39)
    assert not junocam_mirror.is_complete(image)
    image.write_bytes(b"x" * 40)
    assert junocam_mirror.is_complete(image)
    assert junocam_mirror.declared_image_size(label) == 40


def test_select_products_restricts_data_only() -> None:
    manifest = pd.DataFrame(
        {
            "orbit": [4, 4, 4, 58],
            "level": ["EDR", "RDR", "RDR", "RDR"],
            "doy_dir": ["033", "033", "034", "034"],
        }
    )
    labels, data = junocam_mirror.select_products(manifest, [4], "RDR", ["033"])
    assert len(labels) == 3  # every product of orbit 4, both levels
    assert len(data) == 1 and data.iloc[0]["doy_dir"] == "033"
    labels, data = junocam_mirror.select_products(manifest, [4], "both", None)
    assert len(labels) == 3 and len(data) == 3


def test_select_products_rejects_an_unknown_level() -> None:
    with pytest.raises(ValueError):
        junocam_mirror.select_products(pd.DataFrame({"orbit": [], "level": []}), None, "L1B")


# --------------------------------------------------------------------------
# command-line argument parsing
# --------------------------------------------------------------------------


def test_argument_parsing() -> None:
    assert parse_orbits("4,58") == [4, 58]
    assert parse_orbits("1-3,7") == [1, 2, 3, 7]
    assert parse_orbits("all") is None
    assert parse_orbits("0") == [0]
    assert parse_volumes("1-35") == list(range(1, 36))
    assert parse_kinds("labels,data") == ["labels", "data"]
    assert parse_doy("33,034") == ["033", "034"]
    assert parse_doy(None) is None
    for bad in ("", "x", "5-1", "100"):
        with pytest.raises(ValueError):
            parse_orbits(bad)
    with pytest.raises(ValueError):
        parse_volumes("36")
    with pytest.raises(ValueError):
        parse_kinds("labels,images")
    with pytest.raises(ValueError):
        parse_doy("3333")


# --------------------------------------------------------------------------
# documented epochs and throughput (the YAML)
# --------------------------------------------------------------------------


def test_epoch_lookup_matches_the_errata_boundaries() -> None:
    assert quality.epoch_for_orbit(0) == "nominal"
    assert quality.epoch_for_orbit(4) == "nominal"
    assert quality.epoch_for_orbit(46) == "nominal"
    assert quality.epoch_for_orbit(47) == "regulator_damage"
    assert quality.epoch_for_orbit(55) == "regulator_damage"
    assert quality.epoch_for_orbit(56) == "ccd_damage"
    assert quality.epoch_for_orbit(57) == "post_anneal"
    assert quality.epoch_for_orbit(58) == "post_anneal"
    assert quality.epoch_for_orbit(None) == quality.UNKNOWN_EPOCH
    assert quality.epoch_for_orbit(1000) == quality.UNKNOWN_EPOCH


def test_epoch_intervals_are_contiguous_and_disjoint() -> None:
    epochs = quality.load_quality_config()["epochs"]
    bounds = [(int(e["orbit_min"]), int(e["orbit_max"])) for e in epochs]
    assert bounds == sorted(bounds)
    for (_, previous_max), (next_min, _) in zip(bounds, bounds[1:]):
        assert next_min == previous_max + 1
    for entry in epochs:
        assert entry["note"].strip() and entry["source"].strip()


def test_throughput_is_unknown_outside_the_documented_fit() -> None:
    # The accepted quality policy must not turn absent calibration evidence
    # into a measured unity response.
    assert all(np.isnan(v) for v in quality.throughput_factors(4).values())
    assert all(np.isnan(v) for v in quality.throughput_factors(61).values())
    at_60 = quality.throughput_factors(60)
    # The ERRATA's own linear fits: Red -0.00391/1.01, Green -0.00787/1.05,
    # Blue -0.0108/1.07, i.e. roughly the quoted 23/47/64 per cent loss.
    assert at_60["red"] == pytest.approx(1.01 - 0.00391 * 60, abs=1e-9)
    assert at_60["green"] == pytest.approx(1.05 - 0.00787 * 60, abs=1e-9)
    assert at_60["blue"] == pytest.approx(1.07 - 0.0108 * 60, abs=1e-9)
    assert at_60["blue"] < at_60["green"] < at_60["red"] < 1.0


def test_throughput_factors_are_clipped_to_unity() -> None:
    for orbit in range(47, 61):
        for value in quality.throughput_factors(orbit).values():
            assert 0.0 <= value <= 1.0


# --------------------------------------------------------------------------
# measured metrics on synthetic framelets
# --------------------------------------------------------------------------


def _striped(shape: tuple[int, int] = (128, 512)) -> np.ndarray:
    """Constant lines whose levels differ: the pure striping signature."""
    rows = np.linspace(100.0, 900.0, shape[0])
    return np.repeat(rows[:, None], shape[1], axis=1)


def _flat_noisy(shape: tuple[int, int] = (128, 512), seed: int = 0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.normal(500.0, 20.0, size=shape)


def test_streak_index_separates_striped_from_flat() -> None:
    assert quality.streak_index(_striped()) > 0.9
    assert quality.streak_index(_flat_noisy()) < 0.1


def test_streak_index_of_a_uniform_framelet_is_zero() -> None:
    assert quality.streak_index(np.full((128, 512), 7.0)) == 0.0


def test_streak_index_of_a_striped_framelet_with_noise() -> None:
    framelet = _striped() + _flat_noisy(seed=1) * 0.02
    assert quality.streak_index(framelet) > 0.9


def test_saturation_and_zero_fractions() -> None:
    framelet = np.zeros((128, 512), dtype=np.uint16)
    framelet[:, :] = 1000
    framelet[0, :] = 65535  # one line of 128 saturated
    framelet[1:3, :] = 0  # two lines of 128 at zero
    assert quality.saturation_fraction(framelet, 65535) == pytest.approx(1 / 128)
    assert quality.zero_fraction(framelet) == pytest.approx(2 / 128)
    assert quality.saturation_fraction(framelet, 70000) == 0.0


def test_noise_mad_of_a_constant_framelet_is_zero() -> None:
    assert quality.noise_mad(np.full((32, 64), 12.0)) == 0.0


def test_noise_mad_grows_with_noise() -> None:
    quiet = quality.noise_mad(_flat_noisy(shape=(64, 256), seed=2))
    loud = quality.noise_mad(_flat_noisy(shape=(64, 256), seed=2) * 4.0)
    assert 0.0 < quiet < loud


def test_noise_mad_nonzero_ignores_the_off_planet_zeros() -> None:
    """A frame more than a fifth off-planet: the plain metric reads 0."""
    framelet = np.zeros((64, 256))
    framelet[:, 128:] = _flat_noisy(shape=(64, 128), seed=3)
    assert quality.noise_mad(framelet) == 0.0
    assert quality.noise_mad(framelet, exclude_zero=True) > 0.0
    assert math.isnan(quality.noise_mad(np.zeros((16, 16)), exclude_zero=True))


def test_bloom_flag_fires_only_on_runaway_lines() -> None:
    calm = np.full((128, 256), 100.0)
    assert quality.bloom_flag(calm) is False
    bloomed = calm.copy()
    bloomed[:20, :] = 500.0  # 20 of 128 lines = 16% above 3x the median
    assert quality.bloom_flag(bloomed) is True
    marginal = calm.copy()
    marginal[:5, :] = 500.0  # 5 of 128 lines = 3.9%, below the 5% threshold
    assert quality.bloom_flag(marginal) is False
    assert quality.bloom_flag(np.zeros((8, 8))) is False


def test_framelets_view_and_dtype() -> None:
    image = np.arange(3 * 128 * 4, dtype=np.uint16).reshape(3 * 128, 4)
    stack = quality.framelets(image)
    assert stack.shape == (3, 128, 4)
    assert np.array_equal(stack[1], image[128:256])
    with pytest.raises(ValueError):
        quality.framelets(np.zeros((10, 4)))
    assert quality.image_dtype(8, "UNSIGNED_INTEGER") == np.dtype(np.uint8)
    assert quality.image_dtype(16, "UNSIGNED_INTEGER") == np.dtype(">u2")
    assert quality.image_dtype(16, "LSB_UNSIGNED_INTEGER") == np.dtype("<u2")


def test_measure_image_round_trip(tmp_path: Path) -> None:
    """A synthetic three-band strip: bands are assigned by framelet order."""
    lines, samples, bands = 128 * 6, 32, 3
    image = np.zeros((lines, samples), dtype=">u2")
    for position in range(6):
        image[position * 128 : (position + 1) * 128] = 100 * (position % bands + 1)
    image[:128, :] = 65535  # the first BLUE framelet is saturated
    path = tmp_path / "strip.IMG"
    image.tofile(path)
    measured = quality.measure_image(
        path, lines, samples, 16, "UNSIGNED_INTEGER", ["BLUE", "GREEN", "RED"]
    )
    assert measured["max_dn"] == 65535.0
    assert measured["saturation_frac_blue"] == pytest.approx(0.5)  # 1 of 2 blue
    assert measured["saturation_frac_red"] == 0.0
    assert measured["zero_frac"] == 0.0
    assert measured["bloom_flag"] is False
    assert all(math.isfinite(measured[name]) for name in quality.METRIC_NAMES)


def test_read_image_rejects_a_short_file(tmp_path: Path) -> None:
    path = tmp_path / "short.IMG"
    np.zeros(10, dtype=">u2").tofile(path)
    with pytest.raises(ValueError):
        quality.read_image(path, 128, 1648, 16, "UNSIGNED_INTEGER")


# --------------------------------------------------------------------------
# the tier rule
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("epoch", "streak", "saturation", "expected"),
    [
        ("nominal", 0.01, 0.0, "A"),
        ("post_anneal", 0.299, 0.019, "C"),
        ("nominal", 0.31, 0.0, "B"),
        ("nominal", 0.01, 0.05, "B"),
        ("post_anneal", float("nan"), 0.0, "C"),
        ("nominal", None, None, "B"),
        ("regulator_damage", 0.01, 0.0, "C"),
        ("ccd_damage", 0.01, 0.0, "C"),
        ("unknown", 0.01, 0.0, "C"),
    ],
)
def test_quality_tier_rule(
    epoch: str, streak: float | None, saturation: float | None, expected: str
) -> None:
    assert quality.quality_tier(epoch, streak, saturation) == expected


def test_quality_tier_uses_the_module_constants() -> None:
    below = quality.STREAK_INDEX_MAX_A - 1e-9
    above = quality.STREAK_INDEX_MAX_A
    assert quality.quality_tier("nominal", below, 0.0) == "A"
    assert quality.quality_tier("nominal", above, 0.0) == "B"
    assert quality.quality_tier("nominal", 0.0, quality.SATURATION_FRAC_MAX_A) == "B"


def test_quality_row_flags_a_missing_image(tmp_path: Path) -> None:
    row = {
        "product_id": "JNCR_2017033_04C00105_V01",
        "level": "RDR",
        "volume": "JNOJNC_0003",
        "orbit": 4,
        "doy_dir": "033",
        "path": "JNOJNC_0003/DATA/RDR/JUPITER/ORBIT_04/JNCR_2017033_04C00105_V01.IMG",
        "start_time": pd.Timestamp("2017-02-02T13:05:34.387"),
        "filters": "BLUE;GREEN;RED",
        "n_bands": 3,
        "n_framelets": 40,
        "n_framelet_rows": 120,
        "sample_bits": 16,
        "sample_type": "UNSIGNED_INTEGER",
        "lines": 15360,
        "samples": 1648,
    }
    record = quality.quality_row(row, tmp_path)
    assert record["quality_epoch"] == "nominal"
    assert np.isnan(record["throughput_factor_red"])
    assert record["metrics_ok"] is False and record["metrics_error"]
    assert record["quality_tier"] == "B"  # healthy epoch, unmeasurable metrics


def test_subparser_registration_matches_the_harness_contract() -> None:
    """The lead wires ``junocam`` by calling ``add_subparser`` and nothing else."""
    import argparse

    from jiram_catalog.junocam import cli as junocam_cli

    parser = argparse.ArgumentParser()
    junocam_cli.add_subparser(parser.add_subparsers(dest="command", required=True))
    args = parser.parse_args(
        ["junocam", "mirror", "--orbits", "4", "--kinds", "labels", "--doy", "33"]
    )
    assert args.command == "junocam" and args.junocam_command == "mirror"
    assert args.func is junocam_cli.run
    assert args.level == "RDR" and args.jobs == 3

    stand_alone = junocam_cli.make_parser().parse_args(["manifest", "--volumes", "1-35"])
    assert stand_alone.junocam_command == "manifest" and stand_alone.volumes == "1-35"
