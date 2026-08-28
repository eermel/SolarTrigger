"""Pure solar-position helpers."""

from __future__ import annotations

import math
from datetime import datetime, timezone


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


__all__ = ["solar_declination_deg_utc"]
