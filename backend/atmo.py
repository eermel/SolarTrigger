#!/usr/bin/env python3
"""
Pure-Python atmospheric extinction compensation and solar altitude interpolation.

This module is intentionally independent from any browser/JS/HTTP.
"""
from __future__ import annotations

import math
from datetime import datetime


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

def interpolate_altitude(t: datetime, timeline: dict, alts: dict) -> float:
    """Piecewise-linear interpolation of solar altitude over the eclipse.

    - timeline: dict with datetime for keys C1,C2,TMAX,C3,C4
    - alts: dict with floating degrees for keys C1_alt_deg, C2_alt_deg,
            TMAX_alt_deg, C3_alt_deg, C4_alt_deg
    """
    required_t = ("C1", "C2", "TMAX", "C3", "C4")
    required_a = ("C1_alt_deg", "C2_alt_deg", "TMAX_alt_deg", "C3_alt_deg", "C4_alt_deg")
    if not all(k in timeline and isinstance(timeline[k], datetime) for k in required_t):
        raise ValueError("timeline incomplet pour interpolation")
    if not all(k in alts for k in required_a):
        raise ValueError("altitudes manquantes pour interpolation")

    segments = [
        ("C1", "C2"),
        ("C2", "TMAX"),
        ("TMAX", "C3"),
        ("C3", "C4"),
    ]
    for a, b in segments:
        t0, t1 = timeline[a], timeline[b]
        if t0 is None or t1 is None:
            continue
        if t0 <= t <= t1:
            h0 = float(alts[a + "_alt_deg"]) if not a.endswith("_alt_deg") else float(alts[a])
            h1 = float(alts[b + "_alt_deg"]) if not b.endswith("_alt_deg") else float(alts[b])
            dt = (t1 - t0).total_seconds()
            if dt <= 0:
                return float(h0)
            x = (t - t0).total_seconds() / dt
            return h0 + x * (h1 - h0)
    # Outside segments: clamp to nearest bound
    if t < timeline["C1"]:
        return float(alts["C1_alt_deg"])  # before C1
    return float(alts["C4_alt_deg"])  # after C4


__all__ = ["facteur_atmospherique", "interpolate_altitude"]
