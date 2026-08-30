"""Pure calculation of the solar trailing limit for a fixed mount."""

from __future__ import annotations

import math


ARCSECONDS_PER_RADIAN = 206265.0
SOLAR_RATE_ARCSECONDS_PER_SECOND = 15.0411
MICROMETERS_PER_MILLIMETER = 1000.0
MIN_ABS_DECLINATION_COSINE = 1e-15


def max_exposure_time_fixed_mount(
    pixel_pitch_um: float,
    focal_length_mm: float,
    tolerance_pixels: float,
    solar_declination_deg: float,
) -> float:
    """Return the maximum exposure time in seconds for a fixed solar mount."""
    values = (
        ("pixel_pitch_um", pixel_pitch_um),
        ("focal_length_mm", focal_length_mm),
        ("tolerance_pixels", tolerance_pixels),
        ("solar_declination_deg", solar_declination_deg),
    )

    converted: dict[str, float] = {}
    for name, value in values:
        try:
            converted[name] = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"{name} must be numeric") from exc

    for name, value in converted.items():
        if not math.isfinite(value):
            raise ValueError(f"{name} must be finite")

    for name in ("pixel_pitch_um", "focal_length_mm", "tolerance_pixels"):
        if converted[name] <= 0.0:
            raise ValueError(f"{name} must be greater than zero")

    declination_cosine = math.cos(
        math.radians(converted["solar_declination_deg"])
    )
    if abs(declination_cosine) < MIN_ABS_DECLINATION_COSINE:
        raise ValueError("solar_declination_deg cosine must be non-zero")

    return float(
        (
            ARCSECONDS_PER_RADIAN
            * (
                converted["pixel_pitch_um"]
                / MICROMETERS_PER_MILLIMETER
            )
            * converted["tolerance_pixels"]
        )
        / (
            converted["focal_length_mm"]
            * SOLAR_RATE_ARCSECONDS_PER_SECOND
            * declination_cosine
        )
    )


__all__ = ["max_exposure_time_fixed_mount"]
