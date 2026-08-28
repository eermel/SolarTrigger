import math
from datetime import datetime, timezone

import pytest

from backend.solar_position import (
    greenwich_sidereal_deg_utc,
    local_hour_angle_deg,
    solar_apparent_ra_dec_deg_utc,
)


@pytest.mark.parametrize(
    "instant",
    [
        datetime(2026, 8, 12, 17, 46),
        datetime(2026, 8, 12, 17, 46, tzinfo=timezone.utc),
    ],
)
def test_apparent_ra_dec_are_finite_for_naive_and_aware_utc(
    instant: datetime,
) -> None:
    alpha_deg, delta_deg = solar_apparent_ra_dec_deg_utc(instant)

    assert math.isfinite(alpha_deg)
    assert math.isfinite(delta_deg)


@pytest.mark.parametrize(
    ("alpha_deg", "gst_deg", "longitude_east_deg", "expected"),
    [
        (0.0, 180.0, 0.0, -180.0),
        (0.0, 179.999, 0.0, 179.999),
        (0.0, 540.0, 0.0, -180.0),
        (180.0, 0.0, -0.001, 179.999),
    ],
)
def test_local_hour_angle_is_normalized_to_half_open_interval(
    alpha_deg: float,
    gst_deg: float,
    longitude_east_deg: float,
    expected: float,
) -> None:
    result = local_hour_angle_deg(alpha_deg, gst_deg, longitude_east_deg)

    assert -180.0 <= result < 180.0
    assert result == pytest.approx(expected)


def test_east_positive_longitude_increases_local_hour_angle() -> None:
    alpha_deg = 200.0
    gst_deg = 150.0
    longitude_east_deg = 10.0
    longitude_delta_deg = 15.0

    base = local_hour_angle_deg(alpha_deg, gst_deg, longitude_east_deg)
    farther_east = local_hour_angle_deg(
        alpha_deg, gst_deg, longitude_east_deg + longitude_delta_deg
    )

    assert farther_east == pytest.approx(base + longitude_delta_deg)


def test_greenwich_sidereal_angle_is_normalized() -> None:
    result = greenwich_sidereal_deg_utc(
        datetime(2026, 8, 12, 17, 46, tzinfo=timezone.utc)
    )

    assert 0.0 <= result < 360.0
