"""Offline unit tests for the geometry engine (no SPICE kernels required)."""

from __future__ import annotations

import math
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest
import spiceypy

from jiram_catalog.geometry import (
    aberrate,
    ellipsoid_intercept,
    normalise_epoch,
    pixel_directions,
    planetocentric,
    surface_normal,
    vector_angle_deg,
)
from jiram_catalog import kernels as kernels_module

IFOV = 0.000237767
LINES = 128
SAMPLES = 432


# --------------------------------------------------------------------------
# pixel_directions
# --------------------------------------------------------------------------
def test_pixel_directions_shape_and_norms():
    dirs = pixel_directions(LINES, SAMPLES, IFOV)
    assert dirs.shape == (LINES, SAMPLES, 3)
    norms = np.linalg.norm(dirs, axis=-1)
    assert np.abs(norms - 1.0).max() < 1e-15


def test_pixel_directions_centre_is_the_boresight():
    dirs = pixel_directions(LINES, SAMPLES, IFOV)
    centre = dirs[LINES // 2 - 1 : LINES // 2 + 1, SAMPLES // 2 - 1 : SAMPLES // 2 + 1]
    mean = centre.reshape(-1, 3).mean(axis=0)
    mean = mean / np.linalg.norm(mean)
    assert np.abs(mean - np.array([0.0, 0.0, 1.0])).max() < 1e-12


def test_pixel_directions_corner_signs():
    dirs = pixel_directions(LINES, SAMPLES, IFOV)
    assert dirs[0, 0, 0] > 0 and dirs[0, 0, 1] > 0
    assert dirs[-1, -1, 0] < 0 and dirs[-1, -1, 1] < 0
    assert dirs[0, -1, 0] > 0 and dirs[0, -1, 1] < 0
    assert dirs[-1, 0, 0] < 0 and dirs[-1, 0, 1] > 0


def test_pixel_directions_cross_track_opening_angle():
    dirs = pixel_directions(LINES, SAMPLES, IFOV)
    x = (LINES / 2 - 0.5) * IFOV
    y = (SAMPLES / 2 - 0.5) * IFOV
    expected = 2.0 * math.atan(y / math.sqrt(1.0 + x * x))
    cosine = float(np.dot(dirs[0, 0], dirs[0, -1]))
    assert abs(math.acos(np.clip(cosine, -1.0, 1.0)) - expected) < 1e-9


# --------------------------------------------------------------------------
# ellipsoid intercept
# --------------------------------------------------------------------------
RADII = (3.0, 2.0, 1.0)


def test_ellipsoid_intercept_scalar_hits():
    spoint, distance, hit = ellipsoid_intercept((10.0, 0.0, 0.0), np.array([-1.0, 0.0, 0.0]), RADII)
    assert bool(hit)
    assert distance == pytest.approx(7.0, abs=1e-12)
    assert spoint == pytest.approx([3.0, 0.0, 0.0], abs=1e-12)

    spoint, distance, hit = ellipsoid_intercept((0.0, 0.0, 10.0), np.array([0.0, 0.0, -1.0]), RADII)
    assert bool(hit)
    assert distance == pytest.approx(9.0, abs=1e-12)
    assert spoint == pytest.approx([0.0, 0.0, 1.0], abs=1e-12)


def test_ellipsoid_intercept_miss_and_behind():
    _, distance, hit = ellipsoid_intercept((10.0, 0.0, 0.0), np.array([0.0, 1.0, 0.0]), RADII)
    assert not bool(hit) and math.isnan(float(distance))
    # the ellipsoid is behind the observer for a ray pointing away from it
    _, _, hit_back = ellipsoid_intercept((10.0, 0.0, 0.0), np.array([1.0, 0.0, 0.0]), RADII)
    assert not bool(hit_back)


def test_ellipsoid_intercept_batch_matches_scalar():
    observer = (10.0, 0.0, 4.0)
    rays = np.array(
        [
            [-1.0, 0.0, 0.0],
            [0.0, 1.0, 0.0],
            [-1.0, 0.0, -0.4],
            [1.0, 0.0, 0.0],
            [-0.9, 0.1, -0.45],
        ]
    )
    rays = rays / np.linalg.norm(rays, axis=-1, keepdims=True)
    spoint, distance, hit = ellipsoid_intercept(observer, rays, RADII)
    for index, ray in enumerate(rays):
        one_point, one_distance, one_hit = ellipsoid_intercept(observer, ray, RADII)
        assert bool(hit[index]) == bool(one_hit)
        if one_hit:
            assert spoint[index] == pytest.approx(one_point, abs=1e-12)
            assert distance[index] == pytest.approx(float(one_distance), abs=1e-12)
        else:
            assert np.isnan(spoint[index]).all() and math.isnan(float(distance[index]))
    assert hit.sum() > 0 and (~hit).sum() > 0


# --------------------------------------------------------------------------
# stellar aberration
# --------------------------------------------------------------------------
def _random_unit_vectors(count: int) -> np.ndarray:
    rng = np.random.default_rng(20260904)
    vectors = rng.normal(size=(count, 3))
    return vectors / np.linalg.norm(vectors, axis=-1, keepdims=True)


VELOCITY = np.array([10.0, -20.0, 5.0])


def test_aberrate_forward_matches_stelab():
    dirs = _random_unit_vectors(20)
    mine = aberrate(dirs, VELOCITY, inverse=False)
    for index, direction in enumerate(dirs):
        assert mine[index] == pytest.approx(spiceypy.stelab(direction, VELOCITY), abs=1e-12)


def test_aberrate_inverse_matches_stlabx():
    dirs = _random_unit_vectors(20)
    mine = aberrate(dirs, VELOCITY, inverse=True)
    for index, direction in enumerate(dirs):
        assert mine[index] == pytest.approx(spiceypy.stlabx(direction, VELOCITY), abs=1e-12)


def test_aberrate_round_trip():
    # stlabx reverses the rotation rather than inverting stelab exactly, so the
    # round trip closes only to order (v/c)**2.
    dirs = _random_unit_vectors(20)
    back = aberrate(aberrate(dirs, VELOCITY, inverse=False), VELOCITY, inverse=True)
    assert np.abs(back - dirs).max() < 1e-7


def test_aberrate_zero_velocity_is_identity():
    dirs = _random_unit_vectors(5)
    assert np.abs(aberrate(dirs, np.zeros(3), inverse=True) - dirs).max() == 0.0


# --------------------------------------------------------------------------
# planetocentric coordinates and illumination angles
# --------------------------------------------------------------------------
def test_planetocentric_cardinal_points():
    points = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, -1.0, 0.0], [0.0, 0.0, 2.0]])
    lat, lon = planetocentric(points)
    assert lat == pytest.approx([0.0, 0.0, 0.0, 90.0], abs=1e-12)
    assert lon == pytest.approx([0.0, 90.0, 270.0, 0.0], abs=1e-12)


def test_surface_normal_on_sphere_and_ellipsoid():
    assert surface_normal(np.array([2.0, 0.0, 0.0]), (2.0, 2.0, 2.0)) == pytest.approx(
        [1.0, 0.0, 0.0], abs=1e-15
    )
    assert surface_normal(np.array([0.0, 0.0, 1.0]), RADII) == pytest.approx(
        [0.0, 0.0, 1.0], abs=1e-15
    )


def test_emission_incidence_phase_on_a_unit_sphere():
    spoint = np.array([1.0, 0.0, 0.0])
    normal = surface_normal(spoint, (1.0, 1.0, 1.0))
    for observer, expected in (
        (np.array([2.0, 0.0, 0.0]), 0.0),
        (np.array([2.0, 1.0, 0.0]), 45.0),
        (np.array([1.0, 1.0, 0.0]), 90.0),
    ):
        assert vector_angle_deg(normal, observer - spoint) == pytest.approx(expected, abs=1e-12)

    sun = np.array([0.0, 10.0, 0.0])
    observer = np.array([2.0, 1.0, 0.0])
    expected_incidence = math.degrees(math.atan2(10.0, -1.0))
    assert vector_angle_deg(normal, sun - spoint) == pytest.approx(expected_incidence, abs=1e-12)
    expected_phase = math.degrees(
        math.acos(
            float(np.dot(observer - spoint, sun - spoint))
            / (np.linalg.norm(observer - spoint) * np.linalg.norm(sun - spoint))
        )
    )
    assert vector_angle_deg(observer - spoint, sun - spoint) == pytest.approx(
        expected_phase, abs=1e-12
    )


# --------------------------------------------------------------------------
# kernel manifest
# --------------------------------------------------------------------------
def test_needed_kernels_keeps_only_ck_and_reconstructed_spk(monkeypatch):
    monkeypatch.setattr(kernels_module, "latest_sclk_name", lambda: "JNO_SCLKSCET.00211.tsc")
    frames = pd.DataFrame(
        {
            "spice_kernels": [
                "de436s.bsp;spk_rec_A.bsp;juno_sc_rec_B.bc",
                "spk_rec_A.bsp;juno_sc_rec_C.bc;junk.tf",
            ],
            "parse_ok": [True, True],
        }
    )
    assert kernels_module.needed_kernels(frames) == [
        ("lsk", "naif0012.tls"),
        ("pck", "pck00010.tpc"),
        ("fk", "juno_v12.tf"),
        ("ik", "juno_jiram_v02.ti"),
        ("sclk", "JNO_SCLKSCET.00211.tsc"),
        ("spk", "de442s.bsp"),
        ("spk", "jup380s.bsp"),
        ("spk", "jup388s.bsp"),
        ("spk", "spk_rec_A.bsp"),
        ("ck", "juno_sc_rec_B.bc"),
        ("ck", "juno_sc_rec_C.bc"),
    ]


def test_needed_kernels_ignores_unparsed_rows(monkeypatch):
    monkeypatch.setattr(kernels_module, "latest_sclk_name", lambda: "JNO_SCLKSCET.00211.tsc")
    frames = pd.DataFrame(
        {"spice_kernels": ["juno_sc_rec_D.bc"], "parse_ok": [False]}
    )
    dynamic = [entry for entry in kernels_module.needed_kernels(frames) if entry[0] == "ck"]
    assert dynamic == []


def test_kernel_urls_rename_reconstructed_spk_on_the_pds_archive():
    ops, pds = kernels_module.kernel_urls("spk", "spk_rec_170106_170228_170307.bsp")
    assert ops.endswith("/JUNO/kernels/spk/spk_rec_170106_170228_170307.bsp")
    assert pds.endswith("/data/spk/juno_rec_170106_170228_170307.bsp")
    ops, pds = kernels_module.kernel_urls("ck", "juno_sc_rec_170129_170204_v01.bc")
    assert ops.endswith("/ck/juno_sc_rec_170129_170204_v01.bc")
    assert pds.endswith("/ck/juno_sc_rec_170129_170204_v01.bc")


# --------------------------------------------------------------------------
# epoch normalisation
# --------------------------------------------------------------------------
EXPECTED_EPOCH = "2017-02-02T11:40:03.870000"


def test_epoch_accepts_the_four_input_types():
    text = "2017-02-02T11:40:03.870"
    values = [
        text,
        datetime(2017, 2, 2, 11, 40, 3, 870000),
        np.datetime64("2017-02-02T11:40:03.870"),
        pd.Timestamp("2017-02-02T11:40:03.870"),
    ]
    assert [normalise_epoch(value) for value in values] == [EXPECTED_EPOCH] * 4


def test_epoch_accepts_trailing_z_space_separator_and_pads_fractions():
    assert normalise_epoch("2017-02-02T11:40:03.870Z") == EXPECTED_EPOCH
    assert normalise_epoch("2017-02-02 11:40:03.870000") == EXPECTED_EPOCH
    assert normalise_epoch("2017-02-02T11:40:03.87") == "2017-02-02T11:40:03.870000"
    assert normalise_epoch("2017-02-02T11:40:03") == "2017-02-02T11:40:03.000000"


def test_epoch_rejects_sub_microsecond_and_malformed_input():
    with pytest.raises(ValueError):
        normalise_epoch("2017-02-02T11:40:03.8700001")
    with pytest.raises(ValueError):
        normalise_epoch("2017-02-02T11:40:03.870+01:00")
    with pytest.raises(ValueError):
        normalise_epoch("not a time")
    with pytest.raises(ValueError):
        normalise_epoch(pd.Timestamp("2017-02-02T11:40:03.8700001"))
    with pytest.raises(TypeError):
        normalise_epoch(12345.0)


def test_epoch_converts_aware_timestamps_to_utc():
    aware = datetime(2017, 2, 2, 12, 40, 3, 870000, tzinfo=timezone.utc)
    assert normalise_epoch(aware) == "2017-02-02T12:40:03.870000"
