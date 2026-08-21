"""Compute local eclipse circumstances from one 28-value Jubier element set.

This is a direct port of the Besselian calculations in
``jubier_files/SolarEclipseTimerSVG_VML.js``.  Times are returned in UTC and
local civil time; geometric altitudes are the source arrays' index 32.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from .observer import prepare_observer_constants


_D2R = math.pi / 180.0
_R2D = 180.0 / math.pi
_LAMBDA_K1_K2 = 1.00076024401
_ELEMENT_KEYS = (
    "julian_day", "t0", "tmin", "tmax", "dUTC", "dT",
    "x0", "x1", "x2", "x3", "y0", "y1", "y2", "y3",
    "d0", "d1", "d2", "m0", "m1", "m2",
    "l10", "l11", "l12", "l20", "l21", "l22", "tan_f1", "tan_f2",
)
_TYPE_NAMES = {0: "Aucune", 1: "Partielle", 2: "Annulaire", 3: "Totale"}
_EVENTS = (("C1", -2), ("C2", -1), ("TMAX", 0), ("C3", 1), ("C4", 2))


def _element_slice(dataset: Mapping[str, Any]) -> list[float]:
    source = dataset.get("elements", dataset)
    if not isinstance(source, Mapping) or set(source) != set(_ELEMENT_KEYS):
        raise ValueError("elements must be the exact 28-value dataset element slice")
    values = []
    for key in _ELEMENT_KEYS:
        value = source[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"element {key!r} must be numeric")
        values.append(float(value))
    return values


def _format_hours(hours: float) -> str:
    # JavaScript Math.round is floor(x + .5) for the non-negative normalized value.
    total_ms = math.floor((hours % 24.0) * 3_600_000.0 + 0.5) % 86_400_000
    hour, remainder = divmod(total_ms, 3_600_000)
    minute, remainder = divmod(remainder, 60_000)
    return f"{hour:02d}:{minute:02d}:{remainder / 1000.0:06.3f}"


class _Calculator:
    def __init__(self, elements: list[float], observer: tuple[float, ...]):
        self.e = elements
        self.o = observer
        self.f1 = math.atan(elements[26])
        self.f2 = math.atan(elements[27])
        self.mid = [0.0] * 47

    def time_dependent(self, c: list[float]) -> None:
        e, t = self.e, c[1]
        c[2] = ((e[9] * t + e[8]) * t + e[7]) * t + e[6]
        c[10] = (3.0 * e[9] * t + 2.0 * e[8]) * t + e[7]
        c[3] = ((e[13] * t + e[12]) * t + e[11]) * t + e[10]
        c[11] = (3.0 * e[13] * t + 2.0 * e[12]) * t + e[11]
        c[4] = ((e[16] * t + e[15]) * t + e[14]) * _D2R
        c[5], c[6] = math.sin(c[4]), math.cos(c[4])
        c[12] = (2.0 * e[16] * t + e[15]) * _D2R
        mu = (e[19] * t + e[18]) * t + e[17]
        if mu >= 360.0:
            mu -= 360.0
        c[7] = mu * _D2R
        c[13] = (2.0 * e[19] * t + e[18]) * _D2R
        c[8] = (e[22] * t + e[21]) * t + e[20]
        if c[0] in (-2, 0, 2):
            c[14] = 2.0 * e[22] * t + e[21]
        c[9] = (e[25] * t + e[24]) * t + e[23]
        if c[0] in (-1, 0, 1):
            c[15] = 2.0 * e[25] * t + e[24]

    def time_location_dependent(self, c: list[float]) -> None:
        self.time_dependent(c)
        e, o = self.e, self.o
        c[16] = c[7] - o[1] - e[5] / 13713.440924999626077
        c[17], c[18] = math.sin(c[16]), math.cos(c[16])
        c[19] = o[5] * c[17]
        c[20] = o[4] * c[6] - o[5] * c[18] * c[5]
        c[21] = o[4] * c[5] + o[5] * c[18] * c[6]
        c[22] = c[13] * o[5] * c[18]
        c[23] = c[13] * c[19] * c[5] - c[21] * c[12]
        c[24], c[25] = c[2] - c[19], c[3] - c[20]
        c[26], c[27] = c[10] - c[22], c[11] - c[23]
        if c[0] in (-2, 0, 2):
            c[28] = c[8] - c[21] * e[26]
        if c[0] in (-1, 0, 1):
            c[29] = c[9] - c[21] * e[27]
        c[30] = c[26] * c[26] + c[27] * c[27]

    def maximum(self) -> list[float]:
        c = self.mid
        c[0] = c[1] = 0.0
        self.time_location_dependent(c)
        correction = 1.0
        iterations = 0
        while abs(correction) > 0.000001 and iterations < 50:
            correction = (c[24] * c[26] + c[25] * c[27]) / c[30]
            c[1] -= correction
            self.time_location_dependent(c)
            iterations += 1
        return c

    def contact(self, event_type: int, initial_t: float, internal: bool) -> list[float]:
        c = [0.0] * 47
        c[0], c[1] = event_type, initial_t
        self.time_location_dependent(c)
        correction = 1.0
        iterations = 0
        while abs(correction) > 0.000001 and iterations < 50:
            n = math.sqrt(c[30])
            radius = c[29] if internal else c[28]
            cross = (c[26] * c[25] - c[24] * c[27]) / (n * radius)
            root_term = math.sqrt(1.0 - cross * cross) * radius / n if abs(cross) <= 1.0 else 0.0
            sign = -1.0 if event_type < 0 else 1.0
            if internal and self.mid[29] < 0.0:
                sign = -sign
            correction = (c[24] * c[26] + c[25] * c[27]) / c[30] - sign * root_term
            c[1] -= correction
            self.time_location_dependent(c)
            iterations += 1
        return c

    def observational(self, c: list[float]) -> None:
        sinlat, coslat = math.sin(self.o[0]), math.cos(self.o[0])
        contact_type = -1.0 if self.mid[39] == 3 and c[0] in (-1, 1) else 1.0
        c[31] = math.atan2(contact_type * c[24], contact_type * c[25])
        c[32] = math.asin(c[5] * sinlat + c[6] * coslat * c[18])
        c[33] = math.asin(coslat * c[17] / math.cos(c[32]))
        if c[20] < 0.0:
            c[33] = math.pi - c[33]
        c[34] = c[31] - c[33]
        c[35] = math.atan2(-c[17] * c[6], c[5] * coslat - c[18] * sinlat * c[6])
        c[40] = 0.0 if c[32] > -0.00524 else 1.0

        u, v, zeta = c[24], c[25], c[21]
        zs = (c[8] * math.cos(self.f1) - c[9] * math.cos(self.f2)) / (math.sin(self.f1) - math.sin(self.f2)) - zeta
        zm = (c[8] * math.cos(self.f1) + _LAMBDA_K1_K2 * c[9] * math.cos(self.f2)) / (math.sin(self.f1) + _LAMBDA_K1_K2 * math.sin(self.f2)) - zeta
        sun_distance = math.sqrt(u * u + v * v + zs * zs)
        moon_distance = math.sqrt(u * u + v * v + zm * zm)
        sdec = math.asin((v * c[6] + zs * c[5]) / sun_distance)
        mdec = math.asin((v * c[6] + zm * c[5]) / moon_distance)
        sha = c[7] + math.atan(u / (v * c[5] - zs * c[6])) - self.o[1] - self.e[5] / 13713.440924999626077
        mha = c[7] + math.atan(u / (v * c[5] - zm * c[6])) - self.o[1] - self.e[5] / 13713.440924999626077
        c[45] = math.asin(math.sin(sdec) * sinlat + math.cos(sdec) * math.cos(sha) * coslat)
        c[46] = math.atan2(-math.cos(sdec) * math.sin(sha), math.sin(sdec) * coslat - math.cos(sdec) * math.cos(sha) * sinlat)
        c[41] = math.asin(math.sin(mdec) * sinlat + math.cos(mdec) * math.cos(mha) * coslat)
        c[42] = math.atan2(-math.cos(mdec) * math.sin(mha), math.sin(mdec) * coslat - math.cos(mdec) * math.cos(mha) * sinlat)
        radius_term = c[8] * math.cos(self.f1) * math.sin(self.f2) - c[9] * math.sin(self.f1) * math.cos(self.f2)
        c[43] = math.asin((radius_term / (math.sin(self.f1) - math.sin(self.f2))) / sun_distance) * _R2D
        c[44] = math.asin((radius_term / (math.sin(self.f1) / _LAMBDA_K1_K2 + math.sin(self.f2))) / moon_distance) * _R2D

    def all_events(self) -> tuple[int, dict[str, list[float] | None]]:
        mid = self.maximum()
        self.observational(mid)
        mid[36] = math.sqrt(mid[24] * mid[24] + mid[25] * mid[25])
        mid[37] = (mid[28] - mid[36]) / (mid[28] + mid[29])
        mid[38] = (mid[28] - mid[29]) / (mid[28] + mid[29])
        events: dict[str, list[float] | None] = {"C1": None, "C2": None, "TMAX": mid, "C3": None, "C4": None}
        if mid[37] <= 0.0:
            mid[39] = 0.0
            return 0, events

        n = math.sqrt(mid[30])
        cross = (mid[26] * mid[25] - mid[24] * mid[27]) / (n * mid[28])
        span = math.sqrt(1.0 - cross * cross) * mid[28] / n
        events["C1"] = self.contact(-2, mid[1] - span, False)
        events["C4"] = self.contact(2, mid[1] + span, False)
        if mid[36] < mid[29] or mid[36] < -mid[29]:
            cross = (mid[26] * mid[25] - mid[24] * mid[27]) / (n * mid[29])
            span = math.sqrt(1.0 - cross * cross) * mid[29] / n
            c2_t, c3_t = ((mid[1] + span, mid[1] - span) if mid[29] < 0.0 else (mid[1] - span, mid[1] + span))
            events["C2"] = self.contact(-1, c2_t, True)
            events["C3"] = self.contact(1, c3_t, True)
            mid[39] = 3.0 if mid[29] < 0.0 else 2.0
        else:
            mid[39] = 1.0
        for name in ("C1", "C2", "C3", "C4"):
            if events[name] is not None:
                self.observational(events[name])
        return int(mid[39]), events


def compute_local_circumstances(
    dataset: Mapping[str, Any],
    latitude_deg: float,
    longitude_deg: float,
    altitude_m: float = 0.0,
    tz_offset: float = 0.0,
) -> dict[str, Any]:
    """Return Jubier-equivalent local circumstances for one observer.

    ``dataset`` may be a complete dataset returned by :func:`load_eclipse` or
    its exact ``elements`` mapping.  ``tz_offset`` is east-positive hours.
    """

    elements = _element_slice(dataset)
    val = dataset.get("jubier", {}).get("val", -65) if isinstance(dataset.get("jubier"), Mapping) else -65
    observer = prepare_observer_constants(int(val), latitude_deg, longitude_deg, altitude_m, tz_offset)
    calculator = _Calculator(elements, observer)
    eclipse_type, events = calculator.all_events()
    mid = events["TMAX"]
    assert mid is not None

    present = eclipse_type >= 1
    central = eclipse_type >= 2
    duration = abs(events["C3"][1] - events["C2"][1]) * 3600.0 if central else 0.0  # type: ignore[index]
    result: dict[str, Any] = {
        "eclipse_type": _TYPE_NAMES[eclipse_type],
        "magnitude": math.floor(mid[37] * 100000.0 + 0.5) / 100000.0,
        "moon_sun_ratio": math.floor(mid[38] * 100000.0 + 0.5) / 100000.0,
        "duration_str": f"{math.floor(duration / 60.0)}m {math.floor(duration % 60.0 + 0.5)}s",
        "duration_sec": math.floor(duration + 0.5),
        "sun_alt_tmax": f"{mid[45] * _R2D:.1f}\N{DEGREE SIGN}",
    }
    for name, _event_type in _EVENTS:
        circumstances = events[name]
        available = present and (name not in ("C2", "C3") or central)
        result[f"{name}_utc"] = _format_hours(circumstances[1] + elements[1] - elements[4] / 3600.0) if available and circumstances else None
        result[f"{name}_local"] = _format_hours(circumstances[1] + elements[1] - elements[4] / 3600.0 + tz_offset) if available and circumstances else None
        result[f"{name}_alt_deg"] = circumstances[32] * _R2D if available and circumstances else None
    return result


# Short public alias for callers that already operate in eclipse-engine context.
compute_circumstances = compute_local_circumstances


__all__ = ["compute_circumstances", "compute_local_circumstances"]
