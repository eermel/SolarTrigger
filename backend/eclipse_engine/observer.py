"""Prepare the observer constants used by the Jubier eclipse equations."""

from __future__ import annotations

import math


_DEGREES_TO_RADIANS = math.pi / 180.0
_EARTH_FLATTENING_FACTOR = 0.996647189335
_EARTH_EQUATORIAL_RADIUS_M = 6378137.0


def prepare_observer_constants(
    val: int,
    latitude_deg: float,
    longitude_deg: float,
    altitude_m: float,
    tz_offset: float,
) -> tuple[float, float, float, float, float, float, int]:
    """Return Jubier's ``obsvconst[0..6]`` for an observer.

    Input longitude and UTC offset use the usual east-positive convention. The
    Jubier equations store both with the opposite sign.
    """

    latitude_rad = latitude_deg * _DEGREES_TO_RADIANS
    longitude_rad = -longitude_deg * _DEGREES_TO_RADIANS
    altitude = float(altitude_m)
    timezone = -float(tz_offset)

    tmp = math.atan(
        _EARTH_FLATTENING_FACTOR * math.tan(latitude_rad)
    )
    geocentric_sine = (
        _EARTH_FLATTENING_FACTOR * math.sin(tmp)
    ) + (
        altitude * math.sin(latitude_rad) / _EARTH_EQUATORIAL_RADIUS_M
    )
    geocentric_cosine = math.cos(tmp) + (
        altitude * math.cos(latitude_rad) / _EARTH_EQUATORIAL_RADIUS_M
    )
    elements_index = 28 * (val + 65)

    return (
        latitude_rad,
        longitude_rad,
        altitude,
        timezone,
        geocentric_sine,
        geocentric_cosine,
        elements_index,
    )
