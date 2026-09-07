"""Offline tests for the JunoCam camera model, reader, and limb machinery.

No CK, no SPK, no network: the only kernel these tests load is the JunoCam
instrument kernel, which is pure text and carries the whole camera model, and
the only data they read are the two reconnaissance sample images under
``<mirror>/junocam_samples/``.  Everything that needs an ephemeris or an
attitude lives in the gate instead.

The camera model is checked the two ways the kernel itself makes checkable:
its ``distort``/``undistort`` pair must invert to the precision the fixed
five-iteration loop allows, and the band boresights the kernel publishes as
FOV keywords must come back at the pixel the kernel says it computed them for,
"(827.0, 64.0)" of each 128-line strip.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import spiceypy

from jiram_catalog.config import mirror_root
from jiram_catalog.junocam import limb
from jiram_catalog.junocam.camera import (
    BAND_IDS,
    CAMERA_FRAME,
    DARK_COLUMNS,
    FRAMELET_LINES,
    FRAMELET_SAMPLES,
    PHOTOACTIVE_COLUMNS,
    BandCamera,
    band_cameras,
    frame_epochs,
    ik_path,
    load_junocam_ik,
    photoactive_mask,
    timing_parameters,
)
from jiram_catalog.junocam.images import SQROOT_TABLE, decompand, read_image
from jiram_catalog.junocam.labels import parse_label

MIRROR = mirror_root()
SAMPLES = MIRROR / "junocam_samples"
#: The IK's own statement of where a band boresight lands, in its corner-origin
#: framelet coordinates; array indices are half a pixel less.
IK_BORESIGHT_PIXEL = (827.0, 64.0)


@pytest.fixture(scope="module")
def ik() -> Path:
    """The JunoCam IK, furnsh'd for the module and unloaded afterwards."""
    path = ik_path(MIRROR)
    if not path.is_file():
        pytest.skip(f"JunoCam instrument kernel not mirrored: {path}")
    load_junocam_ik(MIRROR)
    yield path
    spiceypy.unload(str(path))


def _require(path: Path) -> Path:
    if not path.is_file():
        pytest.skip(f"reconnaissance sample not available: {path}")
    return path


def _sample_row(label: Path, image: Path) -> dict:
    """An index row for a sample product, with an absolute image path."""
    row = parse_label(label)
    assert row["parse_ok"], row["parse_error"]
    row["path"] = str(image)
    return row


# --------------------------------------------------------------------------
# camera model
# --------------------------------------------------------------------------
def test_pixel_vector_round_trip_is_exact_to_a_millionth_of_a_pixel(ik: Path) -> None:
    generator = np.random.default_rng(20260906)
    for camera in band_cameras(sorted(BAND_IDS)):
        x = generator.uniform(0.0, FRAMELET_SAMPLES, 1000)
        y = generator.uniform(0.0, FRAMELET_LINES, 1000)
        back_x, back_y = camera.project(camera.pixel_to_vector(x, y))
        assert np.abs(back_x - x).max() < 1e-6, camera.band
        assert np.abs(back_y - y).max() < 1e-6, camera.band


def test_distort_and_undistort_invert_each_other(ik: Path) -> None:
    camera = band_cameras(["RED"])[0]
    generator = np.random.default_rng(7)
    x = generator.uniform(-820.0, 820.0, 500)
    y = generator.uniform(-450.0, 450.0, 500)
    round_x, round_y = camera.undistort(*camera.distort(x, y))
    assert np.abs(round_x - x).max() < 1e-6
    assert np.abs(round_y - y).max() < 1e-6


def test_band_boresights_land_at_the_kernels_own_pixel(ik: Path) -> None:
    """``INS-6150N_BORESIGHT`` projects to (827.0, 64.0) of its own strip.

    The kernel says its FOV vectors were "computed ... using the boresight
    computed for the pixel (827.0, 64.0)", which is the centre of the strip in
    its corner-origin convention.  "Near ``(cx, cy)``" is true of the *camera*
    boresight -- the frame's +Z, which projects to ``(cx, cy)`` exactly -- and
    of a band's ``cx`` alone; a band's ``cy`` is the focal-plane ordinate of
    the strip, 155 pixels away from its neighbour's, not the row a boresight
    lands on.  Both statements are asserted here.
    """
    for camera in band_cameras(sorted(BAND_IDS)):
        x, y = camera.project(camera.boresight())
        assert np.isclose(float(x), IK_BORESIGHT_PIXEL[0] - 0.5, atol=1e-5), camera.band
        assert np.isclose(float(y), IK_BORESIGHT_PIXEL[1] - 0.5, atol=1e-5), camera.band
        assert bool(camera.on_strip(x, y)), camera.band
        assert abs(float(x) - camera.cx) < 15.0, camera.band

        axis_x, axis_y = camera.project(np.array([0.0, 0.0, 1.0]))
        assert np.isclose(float(axis_x), camera.cx - 0.5, atol=1e-9)
        assert np.isclose(float(axis_y), camera.cy - 0.5, atol=1e-9)


def test_band_boresights_come_from_getfov_in_the_camera_frame(ik: Path) -> None:
    for band, ins in BAND_IDS.items():
        shape, frame, bsight, _, bounds = spiceypy.getfov(ins, 32)
        assert frame == CAMERA_FRAME and shape == "RECTANGLE", band
        assert bounds.shape == (4, 3)
        assert np.allclose(np.asarray(bsight), band_cameras([band])[0].boresight(), atol=1e-9)


def test_only_the_distortion_centre_ordinate_separates_the_bands(ik: Path) -> None:
    cameras = band_cameras(["METHANE", "BLUE", "GREEN", "RED"])
    assert len({camera.cx for camera in cameras}) == 1
    assert len({camera.k1 for camera in cameras}) == 1
    assert len({round(camera.fl, 9) for camera in cameras}) == 1
    ordinates = [camera.cy for camera in cameras]
    assert ordinates == sorted(ordinates, reverse=True)  # top to bottom on the focal plane


# --------------------------------------------------------------------------
# photoactive mask
# --------------------------------------------------------------------------
def test_photoactive_mask_is_the_kernels_23_plus_1608_columns() -> None:
    mask = photoactive_mask()
    assert mask.shape == (FRAMELET_SAMPLES,) and mask.dtype == bool
    assert not mask[:DARK_COLUMNS].any()
    assert mask[DARK_COLUMNS : DARK_COLUMNS + PHOTOACTIVE_COLUMNS].all()
    assert not mask[DARK_COLUMNS + PHOTOACTIVE_COLUMNS :].any()
    assert int(mask.sum()) == PHOTOACTIVE_COLUMNS


def test_limb_columns_keep_clear_of_both_ends() -> None:
    columns = limb.limb_columns(photoactive_mask())
    assert columns.min() >= DARK_COLUMNS + limb.COLUMN_MARGIN
    assert columns.max() < DARK_COLUMNS + PHOTOACTIVE_COLUMNS - limb.COLUMN_MARGIN
    assert np.all(np.diff(columns) == limb.LIMB_COLUMN_STEP)


# --------------------------------------------------------------------------
# timing
# --------------------------------------------------------------------------
def test_frame_epochs_follow_the_instrument_kernels_rule(ik: Path) -> None:
    bias, delta = timing_parameters("RED")
    assert bias == pytest.approx(0.06188) and delta == pytest.approx(0.001)
    start_et = 539312934.0  # a synthetic label's START_TIME, already in ET
    epochs = frame_epochs(start_et, 5, 0.375, band="RED")
    expected = start_et + bias + np.arange(5) * (0.375 + delta)
    assert np.allclose(epochs, expected, atol=1e-12)
    assert np.allclose(np.diff(epochs), 0.376)


SYNTHETIC_LABEL = """PDS_VERSION_ID                = PDS3
RECORD_TYPE                   = FIXED_LENGTH
RECORD_BYTES                  = 3296
FILE_RECORDS                  = 768
^IMAGE                        = "SYNTH.IMG"
PRODUCT_ID                    = "JNCR_2017033_04C09999_V01"
TARGET_NAME                   = JUPITER
ORBIT_NUMBER                  = 4
START_TIME                    = 2017-02-02T13:05:34.387
STOP_TIME                     = 2017-02-02T13:05:36.643
INTERFRAME_DELAY              = 0.375 <s>
EXPOSURE_DURATION             = 9.600000 <ms>
JNO:TDI_STAGES_COUNT          = 3
SAMPLE_BIT_MODE_ID            = "SQROOT"
FILTER_NAME                   = ('BLUE', 'GREEN', 'RED')
OBJECT                        = IMAGE
  LINES                       = 768
  LINE_SAMPLES                = 1648
  SAMPLE_TYPE                 = UNSIGNED_INTEGER
  SAMPLE_BITS                 = 16
END_OBJECT                    = IMAGE
END
"""


def test_the_timing_rule_on_a_synthetic_label(ik: Path, tmp_path: Path) -> None:
    """From a label's own keywords to the epoch of every framelet.

    Two frames of three bands: the milestone-1 parser turns ``LINES`` and
    ``FILTER_NAME`` into two frames, and the timing rule then gives one epoch
    per frame -- shared by all three of its strips, because a frame is a
    simultaneous readout.
    """
    label = tmp_path / "JNCR_2017033_04C09999_V01.LBL"
    label.write_text(SYNTHETIC_LABEL)
    row = parse_label(label)
    assert row["parse_ok"], row["parse_error"]
    assert row["n_bands"] == 3 and row["n_framelets"] == 2 and row["framelets_exact"]
    assert row["interframe_delay_s"] == pytest.approx(0.375)

    bias, delta = timing_parameters("BLUE")
    start_et = 539312934.5687  # what str2et makes of the label's START_TIME
    epochs = frame_epochs(start_et, int(row["n_framelets"]), float(row["interframe_delay_s"]))
    assert epochs.shape == (2,)
    assert epochs[0] == pytest.approx(start_et + bias)
    assert epochs[1] - epochs[0] == pytest.approx(float(row["interframe_delay_s"]) + delta)
    # The label's own STOP_TIME is one interframe delay past the last frame's
    # start, which is what makes the frame count and the rule consistent.
    assert float(row["duration_s"]) == pytest.approx(2.256, abs=1e-3)


def test_frame_epochs_shift_rigidly_with_the_refined_offset(ik: Path) -> None:
    base = frame_epochs(0.0, 8, 0.375)
    shifted = frame_epochs(0.0, 8, 0.375, dt=-0.0123)
    assert np.allclose(shifted - base, -0.0123)


def test_every_band_publishes_the_same_timing_constants(ik: Path) -> None:
    values = {band: timing_parameters(band) for band in BAND_IDS}
    assert len(set(values.values())) == 1, values


# --------------------------------------------------------------------------
# limb detection
# --------------------------------------------------------------------------
def _synthetic_strip(edge: float, columns: int = 64, space: float = 40.0, planet: float = 1200.0):
    """One framelet whose every column steps from sky to planet at ``edge``.

    A pixel straddling the edge gets the area-weighted mean of the two levels,
    which is what a real detector reports and what makes the half-way crossing
    of the smoothed profile land exactly on the edge.
    """
    rows = np.arange(FRAMELET_LINES, dtype=np.float64)
    fraction = np.clip(rows - edge + 0.5, 0.0, 1.0)
    profile = space + (planet - space) * fraction
    strip = np.repeat(profile[:, None], columns, axis=1)
    generator = np.random.default_rng(11)
    strip = strip + generator.normal(0.0, 1.0, strip.shape)
    inside = rows[:, None] >= np.ceil(edge)
    return strip[None], np.repeat(inside, columns, axis=1)[None]


def test_observed_limb_lands_on_a_synthetic_edge_within_a_fifth_of_a_pixel() -> None:
    columns = 64
    for edge in (40.0, 55.5, 63.25, 80.75):
        strip, inside = _synthetic_strip(edge, columns)
        index = np.arange(columns)
        observed = limb.detect_observed_rows(
            strip,
            inside,
            np.zeros(columns, dtype=np.intp),
            index,
            np.zeros(columns, dtype=np.intp),
            np.full(columns, edge + 1.5),  # a deliberately wrong prediction
        )
        assert np.isfinite(observed).all()
        assert np.abs(observed - edge).max() < 0.2, edge


def test_a_falling_edge_is_detected_from_the_other_side() -> None:
    columns = 32
    edge = 70.5
    strip, inside = _synthetic_strip(edge, columns)
    strip = strip[:, ::-1, :]
    inside = inside[:, ::-1, :]
    flipped = FRAMELET_LINES - 1 - edge
    observed = limb.detect_observed_rows(
        strip,
        inside,
        np.zeros(columns, dtype=np.intp),
        np.arange(columns),
        np.ones(columns, dtype=np.intp),
        np.full(columns, flipped - 1.0),
    )
    assert np.isfinite(observed).all()
    assert np.abs(observed - flipped).max() < 0.2


def test_a_contrastless_framelet_yields_no_limb_points() -> None:
    columns = 32
    strip, inside = _synthetic_strip(50.0, columns, space=500.0, planet=500.0)
    observed = limb.detect_observed_rows(
        strip,
        inside,
        np.zeros(columns, dtype=np.intp),
        np.arange(columns),
        np.zeros(columns, dtype=np.intp),
        np.full(columns, 50.0),
    )
    assert not np.isfinite(observed).any()


def test_edge_rows_interpolate_the_discriminant_zero_crossing() -> None:
    """Exact for a locally linear discriminant, which is what tangency gives.

    The real discriminant crosses zero linearly in the row at the limb (it is
    proportional to the squared difference of the tangent radius and the impact
    parameter, and the impact parameter is linear in the row over a pixel), so
    a straight interpolation between the last miss and the first hit is exact.
    A frankly quadratic profile is the pessimistic case and still lands inside
    a hundredth of a pixel.
    """
    rows = np.arange(FRAMELET_LINES, dtype=np.float64)
    top_true, bottom_true = 30.4, 90.8
    ramp = np.minimum(rows - top_true, bottom_true - rows)
    top, bottom = limb.edge_rows((ramp[:, None] * np.ones((1, 5)))[None])
    assert np.allclose(top[0], top_true, atol=1e-12)
    assert np.allclose(bottom[0], bottom_true, atol=1e-12)

    parabola = (rows - top_true) * (bottom_true - rows)
    top, bottom = limb.edge_rows((parabola[:, None] * np.ones((1, 5)))[None])
    assert np.allclose(top[0], top_true, atol=0.01)
    assert np.allclose(bottom[0], bottom_true, atol=0.01)


def test_edge_rows_report_nothing_for_an_all_sky_or_all_planet_column() -> None:
    disc = np.stack(
        [
            np.full((FRAMELET_LINES, 3), -1.0),
            np.full((FRAMELET_LINES, 3), +1.0),
        ]
    )
    top, bottom = limb.edge_rows(disc)
    assert not np.isfinite(top).any() and not np.isfinite(bottom).any()


# --------------------------------------------------------------------------
# the search
# --------------------------------------------------------------------------
def test_golden_section_finds_the_minimum_of_a_v() -> None:
    best, value = limb.golden_section(lambda x: abs(x - 0.0371) + 0.25, -0.25, 0.25, 5e-4)
    assert abs(best - 0.0371) < 5e-4
    assert value == pytest.approx(0.25, abs=5e-4)


def test_refine_dt_recovers_a_known_offset_on_a_synthetic_problem() -> None:
    """The real problem's shape: rows move at about -306 px per second."""
    generator = np.random.default_rng(3)
    n_points = 400
    base = generator.uniform(20.0, 100.0, n_points)
    rate = -305.8
    truth = 0.0123
    observed = base + rate * truth + generator.normal(0.0, 0.15, n_points)

    def predict(dt: float) -> np.ndarray:
        return base + rate * dt

    fit = limb.refine_dt(predict, lambda dt: observed, passes=1)
    assert fit.refined
    assert abs(fit.dt_refined_s - truth) < 1e-3
    assert fit.n_limb_points == n_points
    assert fit.limb_residual_px_after < 0.2
    assert fit.limb_residual_px_after < fit.limb_residual_px_before


def test_refine_dt_refuses_an_image_with_too_little_limb() -> None:
    base = np.arange(50, dtype=np.float64)
    fit = limb.refine_dt(lambda dt: base, lambda dt: base + 3.0)
    assert not fit.refined
    assert np.isnan(fit.dt_refined_s)
    assert "limb points" in fit.reason


def test_residual_statistic_penalises_throwing_points_away() -> None:
    observed = np.zeros(100)
    kept = np.full(100, np.nan)
    kept[:10] = 0.0
    honest, count = limb.residual_statistic(observed, np.zeros(100), 100)
    cheating, few = limb.residual_statistic(observed, kept, 100)
    assert honest == pytest.approx(0.0) and count == 100
    assert cheating > 100.0 and few == 10


# --------------------------------------------------------------------------
# the reader
# --------------------------------------------------------------------------
def test_sqroot_table_matches_the_sis_anchors() -> None:
    assert SQROOT_TABLE.shape == (256,)
    assert (np.diff(SQROOT_TABLE) > 0).all()
    assert SQROOT_TABLE[0] == 0
    assert SQROOT_TABLE[24] == 25
    assert SQROOT_TABLE[90] == 255
    assert SQROOT_TABLE[255] == 2879
    assert (SQROOT_TABLE[:24] == np.arange(24)).all()
    assert decompand(np.array([[0, 24], [90, 255]])).tolist() == [[0, 25], [255, 2879]]


def test_reader_shapes_the_2017_sample_as_frames_bands_rows_samples() -> None:
    label = _require(SAMPLES / "JNCE_2017033_04C00105_V01.LBL")
    image = _require(SAMPLES / "JNCE_2017033_04C00105_V01.IMG")
    row = _sample_row(label, image)
    assert row["filters"] == "BLUE;GREEN;RED"
    data = read_image(row, MIRROR)
    assert data.shape == (40, 3, FRAMELET_LINES, FRAMELET_SAMPLES)
    assert data.dtype == np.float32
    assert int(row["lines"]) == 40 * 3 * FRAMELET_LINES
    # Decompanded, so the 8-bit ceiling is gone and the 12-bit one applies.
    assert data.max() > 255.0 and data.max() <= 2879.0


def test_reader_shapes_the_2024_sample_and_keeps_the_filter_order() -> None:
    label = _require(SAMPLES / "JNCE_2024034_58C00012_V01.LBL")
    image = _require(SAMPLES / "JNCE_2024034_58C00012_V01.IMG")
    row = _sample_row(label, image)
    assert row["filters"] == "BLUE;GREEN;RED"
    data = read_image(row, MIRROR)
    assert data.shape == (10, 3, FRAMELET_LINES, FRAMELET_SAMPLES)
    assert data.dtype == np.float32


def test_raw_edr_counts_are_companded_until_the_table_is_applied() -> None:
    label = _require(SAMPLES / "JNCE_2017033_04C00105_V01.LBL")
    image = _require(SAMPLES / "JNCE_2017033_04C00105_V01.IMG")
    row = _sample_row(label, image)
    raw = read_image(row, MIRROR, decompand_edr=False)
    assert raw.max() <= 255.0
    linear = read_image(row, MIRROR)
    assert np.array_equal(linear, SQROOT_TABLE[raw.astype(np.intp)].astype(np.float32))


def test_decompanded_edr_is_monotonic_with_the_rdr_band_by_band() -> None:
    """The RDR scales each band separately, so monotonicity is a per-band claim.

    Ground processing inverts exactly this ``SQROOT`` table and then multiplies
    by a per-band radiometric factor -- about 3.9 for blue, 1.8 for green, 1.4
    for red on this product, blue being the least sensitive channel.  Pooling
    the bands would therefore break monotonicity for a perfectly correct
    decompanding, which is why the test does not.
    """
    edr_label = _require(SAMPLES / "JNCE_2017033_04C00105_V01.LBL")
    edr_image = _require(SAMPLES / "JNCE_2017033_04C00105_V01.IMG")
    rdr_label = _require(SAMPLES / "JNCR_2017033_04C00105_V01.LBL")
    rdr_image = (
        MIRROR
        / "junocam"
        / "JNOJNC_0003"
        / "DATA"
        / "RDR"
        / "JUPITER"
        / "ORBIT_04"
        / "JNCR_2017033_04C00105_V01.IMG"
    )
    _require(rdr_image)

    edr = read_image(_sample_row(edr_label, edr_image), MIRROR)
    rdr = read_image(_sample_row(rdr_label, rdr_image), MIRROR)
    assert edr.shape == rdr.shape

    for band in range(edr.shape[1]):
        left = edr[:, band].ravel()[::311].astype(np.float64)
        right = rdr[:, band].ravel()[::311].astype(np.float64)
        assert np.corrcoef(left, right)[0, 1] > 0.99
        edges = np.unique(np.quantile(left, np.linspace(0.0, 1.0, 17)))
        which = np.clip(np.digitize(left, edges[1:-1]), 0, edges.size - 2)
        medians = np.array(
            [np.median(right[which == index]) for index in range(edges.size - 1)]
        )
        assert (np.diff(medians) >= 0).all(), band


def test_reader_rejects_a_row_whose_line_count_does_not_divide(tmp_path: Path) -> None:
    path = tmp_path / "fake.IMG"
    path.write_bytes(b"\x00" * (127 * 1648 * 2))
    row = {
        "path": str(path),
        "filters": "RED",
        "n_framelets": 1,
        "lines": 127,
        "samples": FRAMELET_SAMPLES,
        "sample_bits": 16,
    }
    with pytest.raises(ValueError, match="not n_frames"):
        read_image(row, MIRROR)


def test_band_camera_rejects_an_unknown_band(ik: Path) -> None:
    with pytest.raises(ValueError, match="unknown JunoCam band"):
        BandCamera.from_kernel("INFRARED")
