"""Pure Nikon shutter-grid planner."""

from __future__ import annotations

import math


NIKON_SPEEDS = [
    ("30", 30), ("25", 25), ("20", 20), ("15", 15), ("13", 13), ("10", 10),
    ("8", 8), ("6", 6), ("5", 5), ("4", 4), ("3", 3), ("2.5", 2.5), ("2", 2),
    ("1.6", 1.6), ("1.3", 1.3), ("1", 1), ("0.8", 0.8), ("0.6", 0.6),
    ("0.5", 0.5), ("0.4", 0.4), ("1/3", 1/3), ("1/4", 1/4), ("1/5", 1/5),
    ("1/6", 1/6), ("1/8", 1/8), ("1/10", 1/10), ("1/13", 1/13),
    ("1/15", 1/15), ("1/20", 1/20), ("1/25", 1/25), ("1/30", 1/30),
    ("1/40", 1/40), ("1/50", 1/50), ("1/60", 1/60), ("1/80", 1/80),
    ("1/100", 1/100), ("1/125", 1/125), ("1/160", 1/160),
    ("1/200", 1/200), ("1/250", 1/250), ("1/320", 1/320),
    ("1/400", 1/400), ("1/500", 1/500), ("1/640", 1/640),
    ("1/800", 1/800), ("1/1000", 1/1000), ("1/1250", 1/1250),
    ("1/1600", 1/1600), ("1/2000", 1/2000), ("1/2500", 1/2500),
    ("1/3200", 1/3200), ("1/4000", 1/4000), ("1/5000", 1/5000),
    ("1/6400", 1/6400), ("1/8000", 1/8000),
]


def _ev(seconds):
    return math.log2(seconds)


def _parse(speed):
    value = str(speed).strip()
    if "/" in value:
        numerator, denominator = value.split("/", 1)
        return float(numerator) / float(denominator)
    return float(value)


def _speeds_between(v_max, v_min, step_il):
    """Return the exact Nikon photo-by-photo shutter sequence."""

    vmax_s, vmin_s = _parse(v_max), _parse(v_min)
    if vmax_s > vmin_s:
        vmax_s, vmin_s = vmin_s, vmax_s

    ev_fast, ev_slow = _ev(vmax_s), _ev(vmin_s)
    n = round((ev_slow - ev_fast) / step_il) + 1
    if n < 1:
        n = 1

    out = []
    previous = None

    for index in range(n):
        target = ev_fast + index * step_il
        selected = min(
            NIKON_SPEEDS,
            key=lambda item: abs(_ev(item[1]) - target),
        )
        if selected[0] != previous:
            out.append(selected[0])
        previous = selected[0]

    return out


__all__ = [
    "NIKON_SPEEDS",
    "_ev",
    "_parse",
    "_speeds_between",
]
