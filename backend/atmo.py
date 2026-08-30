#!/usr/bin/env python3
"""
Pure-Python atmospheric extinction compensation and solar altitude interpolation.

This module is intentionally independent from any browser/JS/HTTP.
"""
from __future__ import annotations

import math
from datetime import datetime


ATMOS_ACTIVE_BELOW_DEG = 30.0


def atmospheric_compensation_active(h_deg: float) -> bool:
    """Return whether exposure compensation should be applied.

    The physical extinction model remains available at every altitude, but
    exposure compensation is intentionally ignored when the Sun is at or
    above 30 degrees.
    """
    try:
        altitude = float(h_deg)
    except (TypeError, ValueError) as exc:
        raise ValueError("h_deg doit etre numerique") from exc

    if not math.isfinite(altitude):
        raise ValueError("h_deg doit etre fini")

    return altitude < ATMOS_ACTIVE_BELOW_DEG



def facteur_atmospherique(h_deg: float, H_m: float) -> float:
    """Return unitless atmospheric compensation factor using Jubier's model.

    Inputs:
      - h_deg: geometric solar altitude in degrees
      - H_m: observer altitude in meters

    The returned value is normalized by F(90 deg, 0 m), exactly as in
    EclipseExposureCalculator.js.
    """
    try:
        h = float(h_deg)
        H = float(H_m)
    except (TypeError, ValueError):
        raise ValueError("h_deg et H_m doivent etre numeriques")

    def F(hd: float, Hm: float) -> float:
        if hd > 0.0:
            cosz = math.sin(math.radians(hd))
            air_mass = 1.0 / (
                cosz + 0.025 * math.exp(-11.0 * cosz)
            )
        else:
            air_mass = 40.0

        Aoz = 0.016

        Aray = 0.1451 * math.exp(
            -(Hm / 1000.0) / 7.996
        )

        Aaer = 0.120 * math.exp(
            -(Hm / 1000.0) / 1.5
        )

        extinction = (
            Aoz + Aray + Aaer
        ) * air_mass

        return 2.512 ** extinction

    reference = F(90.0, 0.0)

    return F(h, H) / reference

def _atmospheric_event_order(timeline: dict) -> tuple[str, ...]:
    """Return the atmospheric interpolation topology.

    Central eclipse:
        C1, C2, TMAX, C3, C4

    Partial eclipse:
        C1, TMAX, C4
    """

    if not isinstance(timeline, dict):
        raise ValueError("timeline atmospherique invalide")

    c2 = timeline.get("C2")
    c3 = timeline.get("C3")

    if (c2 is None) != (c3 is None):
        raise ValueError(
            "timeline atmospherique invalide: "
            "C2 et C3 doivent etre tous deux presents ou absents"
        )

    if c2 is None:
        order = ("C1", "TMAX", "C4")
    else:
        order = ("C1", "C2", "TMAX", "C3", "C4")

    for key in order:
        if not isinstance(timeline.get(key), datetime):
            raise ValueError(
                f"timeline atmospherique incomplete: {key} manquant"
            )

    return order


def validate_atmospheric_timeline(timeline: dict) -> None:
    """Validate physical chronology for a central or partial eclipse."""

    order = _atmospheric_event_order(timeline)
    values = [timeline[key] for key in order]

    if not all(a < b for a, b in zip(values, values[1:])):
        if order == ("C1", "TMAX", "C4"):
            expected = "C1 < TMAX < C4"
        else:
            expected = "C1 < C2 < TMAX < C3 < C4"

        raise ValueError(
            f"timeline atmospherique invalide: {expected} requis"
        )


def interpolate_altitude(t: datetime, timeline: dict, alts: dict) -> float:
    """Interpolate solar altitude for a central or partial eclipse.

    Central:
        C1 -> C2 -> TMAX -> C3 -> C4

    Partial:
        C1 -> TMAX -> C4
    """

    order = _atmospheric_event_order(timeline)

    heights: dict[str, float] = {}
    for key in order:
        altitude_key = f"{key}_alt_deg"
        raw = alts.get(altitude_key)
        if raw is None:
            raise ValueError(
                f"altitude atmospherique manquante: {altitude_key}"
            )
        try:
            value = float(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"altitude atmospherique invalide: {altitude_key}"
            ) from exc
        if not math.isfinite(value):
            raise ValueError(
                f"altitude atmospherique invalide: {altitude_key}"
            )
        heights[key] = value

    first = order[0]
    last = order[-1]

    if t <= timeline[first]:
        return heights[first]
    if t >= timeline[last]:
        return heights[last]

    for a, b in zip(order, order[1:]):
        t0 = timeline[a]
        t1 = timeline[b]
        if t0 <= t <= t1:
            dt = (t1 - t0).total_seconds()
            x = (t - t0).total_seconds() / dt
            return heights[a] + x * (heights[b] - heights[a])

    raise ValueError("timestamp hors timeline atmospherique")


__all__ = [
    "facteur_atmospherique",
    "interpolate_altitude",
    "validate_atmospheric_timeline",
]
