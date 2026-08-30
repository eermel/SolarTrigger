import math

import pytest

from backend.solar_trailing import max_exposure_time_fixed_mount


def test_baseline_numeric_zero_declination():
    expected_seconds = {
        0.5: 0.06857,
        1.0: 0.13713,
        2.0: 0.27427,
    }

    for tolerance_pixels, expected in expected_seconds.items():
        result = max_exposure_time_fixed_mount(
            pixel_pitch_um=5.0,
            focal_length_mm=500.0,
            tolerance_pixels=tolerance_pixels,
            solar_declination_deg=0.0,
        )
        assert result == pytest.approx(expected, abs=1e-2)


def test_proportional_tolerance_half_and_double():
    baseline = max_exposure_time_fixed_mount(5.0, 500.0, 1.0, 0.0)
    half_tolerance = max_exposure_time_fixed_mount(5.0, 500.0, 0.5, 0.0)
    double_tolerance = max_exposure_time_fixed_mount(5.0, 500.0, 2.0, 0.0)

    assert half_tolerance == pytest.approx(baseline / 2.0, rel=1e-6)
    assert double_tolerance == pytest.approx(baseline * 2.0, rel=1e-6)


def test_focal_scaling():
    at_500_mm = max_exposure_time_fixed_mount(5.0, 500.0, 1.0, 0.0)
    at_1000_mm = max_exposure_time_fixed_mount(5.0, 1000.0, 1.0, 0.0)

    assert at_1000_mm == pytest.approx(at_500_mm / 2.0, rel=1e-6)


def test_declination_scaling():
    at_equator = max_exposure_time_fixed_mount(5.0, 500.0, 1.0, 0.0)
    at_solar_solstice = max_exposure_time_fixed_mount(5.0, 500.0, 1.0, 23.44)

    expected_scale = 1.0 / math.cos(math.radians(23.44))
    assert at_solar_solstice / at_equator == pytest.approx(
        expected_scale, rel=1e-6
    )


@pytest.mark.parametrize(
    ("pixel_pitch_um", "focal_length_mm", "tolerance_pixels", "declination_deg"),
    [
        (0.0, 500.0, 1.0, 0.0),
        (-5.0, 500.0, 1.0, 0.0),
        (5.0, 0.0, 1.0, 0.0),
        (5.0, -500.0, 1.0, 0.0),
        (5.0, 500.0, 0.0, 0.0),
        (5.0, 500.0, -1.0, 0.0),
        (math.nan, 500.0, 1.0, 0.0),
        (math.inf, 500.0, 1.0, 0.0),
        (5.0, math.nan, 1.0, 0.0),
        (5.0, math.inf, 1.0, 0.0),
        (5.0, 500.0, math.nan, 0.0),
        (5.0, 500.0, math.inf, 0.0),
        (5.0, 500.0, 1.0, math.nan),
        (5.0, 500.0, 1.0, math.inf),
        (5.0, 500.0, 1.0, -90.0),
        (5.0, 500.0, 1.0, 90.0),
    ],
)
def test_invalid_inputs_raise_value_error(
    pixel_pitch_um, focal_length_mm, tolerance_pixels, declination_deg
):
    with pytest.raises(ValueError):
        max_exposure_time_fixed_mount(
            pixel_pitch_um,
            focal_length_mm,
            tolerance_pixels,
            declination_deg,
        )



def test_egypt_2027_real_camera_pixel_scales():
    # Solar declination corresponding to the 2027-08-02 TMAX test case.
    declination_deg = 17.75807377

    d850 = max_exposure_time_fixed_mount(
        pixel_pitch_um=4.3484,
        focal_length_mm=430.0,
        tolerance_pixels=1.0,
        solar_declination_deg=declination_deg,
    )

    a7v = max_exposure_time_fixed_mount(
        pixel_pitch_um=5.1227,
        focal_length_mm=430.0,
        tolerance_pixels=1.0,
        solar_declination_deg=declination_deg,
    )

    assert d850 == pytest.approx(0.145616, rel=1e-4)
    assert a7v == pytest.approx(0.171545, rel=1e-4)

    # Larger pixels tolerate a slightly longer exposure.
    assert a7v > d850
