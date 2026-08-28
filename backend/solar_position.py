"""Pure solar-position helpers."""

from __future__ import annotations

import math
from datetime import datetime, timezone
from numbers import Real


def _utc_datetime(when_utc: datetime) -> datetime:
    if not isinstance(when_utc, datetime):
        raise TypeError("when_utc must be a datetime")

    if when_utc.tzinfo is None:
        return when_utc.replace(tzinfo=timezone.utc)
    return when_utc.astimezone(timezone.utc)


def _julian_day_utc(when_utc: datetime) -> float:
    utc = _utc_datetime(when_utc)
    return 2440587.5 + utc.timestamp() / 86400.0


def _finite_number(value: float, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{name} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be a finite number")
    return number


def solar_apparent_ra_dec_deg_utc(when_utc: datetime) -> tuple[float, float]:
    """Return the Sun's apparent right ascension and declination in degrees."""
    julian_centuries = (_julian_day_utc(when_utc) - 2451545.0) / 36525.0

    mean_longitude = (
        280.46646
        + julian_centuries * (36000.76983 + 0.0003032 * julian_centuries)
    ) % 360.0
    mean_anomaly = math.radians(
        (
            357.52911
            + julian_centuries
            * (35999.05029 - 0.0001537 * julian_centuries)
        )
        % 360.0
    )
    equation_of_center = (
        (1.914602 - julian_centuries * (0.004817 + 0.000014 * julian_centuries))
        * math.sin(mean_anomaly)
        + (0.019993 - 0.000101 * julian_centuries)
        * math.sin(2.0 * mean_anomaly)
        + 0.000289 * math.sin(3.0 * mean_anomaly)
    )
    true_longitude = mean_longitude + equation_of_center
    ascending_node = 125.04 - 1934.136 * julian_centuries
    apparent_longitude = math.radians(
        true_longitude - 0.00569 - 0.00478 * math.sin(math.radians(ascending_node))
    )

    mean_obliquity = (
        23.0
        + (
            26.0
            + (
                21.448
                - julian_centuries
                * (
                    46.815
                    + julian_centuries * (0.00059 - 0.001813 * julian_centuries)
                )
            )
            / 60.0
        )
        / 60.0
    )
    true_obliquity = math.radians(
        mean_obliquity + 0.00256 * math.cos(math.radians(ascending_node))
    )

    alpha_deg = math.degrees(
        math.atan2(
            math.cos(true_obliquity) * math.sin(apparent_longitude),
            math.cos(apparent_longitude),
        )
    ) % 360.0
    delta_deg = math.degrees(
        math.asin(math.sin(true_obliquity) * math.sin(apparent_longitude))
    )
    return float(alpha_deg), float(delta_deg)


def greenwich_sidereal_deg_utc(when_utc: datetime) -> float:
    """Return Greenwich mean sidereal angle in degrees in ``[0, 360)``."""
    julian_day = _julian_day_utc(when_utc)
    julian_centuries = (julian_day - 2451545.0) / 36525.0
    angle = (
        280.46061837
        + 360.98564736629 * (julian_day - 2451545.0)
        + 0.000387933 * julian_centuries**2
        - julian_centuries**3 / 38710000.0
    )
    return float(angle % 360.0)


def local_hour_angle_deg(
    alpha_deg: float, gst_deg: float, longitude_east_deg: float
) -> float:
    """Return the east-positive local solar hour angle in ``[-180, 180)``."""
    alpha = _finite_number(alpha_deg, "alpha_deg")
    gst = _finite_number(gst_deg, "gst_deg")
    longitude_east = _finite_number(longitude_east_deg, "longitude_east_deg")
    return float((gst + longitude_east - alpha + 180.0) % 360.0 - 180.0)


def solar_declination_deg_utc(when_utc: datetime) -> float:
    """Return the Sun's geometric declination in degrees at a UTC instant.

    Naive datetimes are interpreted as UTC. Aware datetimes are converted to
    UTC before applying NOAA's fractional-year approximation.
    """
    if not isinstance(when_utc, datetime):
        raise TypeError("when_utc must be a datetime")

    if when_utc.tzinfo is None:
        utc = when_utc.replace(tzinfo=timezone.utc)
    else:
        utc = when_utc.astimezone(timezone.utc)

    fractional_hour = (
        utc.hour
        + utc.minute / 60.0
        + utc.second / 3600.0
        + utc.microsecond / 3_600_000_000.0
    )
    fractional_year = (2.0 * math.pi / 365.0) * (
        utc.timetuple().tm_yday - 1 + (fractional_hour - 12.0) / 24.0
    )

    declination_radians = (
        0.006918
        - 0.399912 * math.cos(fractional_year)
        + 0.070257 * math.sin(fractional_year)
        - 0.006758 * math.cos(2.0 * fractional_year)
        + 0.000907 * math.sin(2.0 * fractional_year)
        - 0.002697 * math.cos(3.0 * fractional_year)
        + 0.00148 * math.sin(3.0 * fractional_year)
    )
    return float(math.degrees(declination_radians))


__all__ = [
    "greenwich_sidereal_deg_utc",
    "local_hour_angle_deg",
    "solar_apparent_ra_dec_deg_utc",
    "solar_declination_deg_utc",
]
