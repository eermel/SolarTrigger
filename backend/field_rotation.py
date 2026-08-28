"""Pure calculation of the field-rotation rate for an alt-azimuth mount."""

from __future__ import annotations

import math


SIDEREAL_DAY_SECONDS = 86164.0905
SINGULARITY_TOLERANCE = 1e-12


class FieldRotationSingularityError(ArithmeticError):
    """Raised when the parallactic angle is undefined."""


def field_rotation_rate_deg_s(
    latitude_deg: float,
    solar_declination_deg: float,
    hour_angle_deg: float,
) -> float:
    """Return the instantaneous field-rotation rate in degrees per second.

    The parallactic-angle convention follows Jean Meeus, *Astronomical
    Algorithms*, chapter 14, equation for the parallactic angle. Latitude is
    positive north, declination is positive north, and hour angle is positive
    westward and normalized to ``[-180, 180)`` degrees. A
    :class:`FieldRotationSingularityError` is raised where the parallactic
    angle is undefined.
    """
    inputs = (
        ("latitude_deg", latitude_deg),
        ("solar_declination_deg", solar_declination_deg),
        ("hour_angle_deg", hour_angle_deg),
    )
    converted: dict[str, float] = {}
    for name, value in inputs:
        if isinstance(value, bool):
            raise TypeError(f"{name} must be floatable and not bool")
        try:
            converted[name] = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise TypeError(f"{name} must be floatable and not bool") from exc

    for name, value in converted.items():
        if not math.isfinite(value):
            raise ValueError(f"{name} must be finite")

    latitude = converted["latitude_deg"]
    declination = converted["solar_declination_deg"]
    if not -90.0 < latitude < 90.0:
        raise ValueError("latitude_deg must be strictly between -90 and 90")
    if not -90.0 <= declination <= 90.0:
        raise ValueError("solar_declination_deg must be between -90 and 90")

    hour_angle = (converted["hour_angle_deg"] + 180.0) % 360.0 - 180.0
    phi = math.radians(latitude)
    delta = math.radians(declination)
    hour_angle_rad = math.radians(hour_angle)

    sin_hour_angle = math.sin(hour_angle_rad)
    x = (
        math.tan(phi) * math.cos(delta)
        - math.sin(delta) * math.cos(hour_angle_rad)
    )
    y = sin_hour_angle
    denominator = x * x + y * y
    if math.hypot(x, y) <= SINGULARITY_TOLERANCE:
        raise FieldRotationSingularityError(
            "parallactic angle is undefined at this geometry"
        )

    dq_dh = (
        x * math.cos(hour_angle_rad)
        - y * math.sin(delta) * sin_hour_angle
    ) / denominator
    omega_sidereal = 2.0 * math.pi / SIDEREAL_DAY_SECONDS
    return float(dq_dh * omega_sidereal * 180.0 / math.pi)


__all__ = ["field_rotation_rate_deg_s", "FieldRotationSingularityError"]
