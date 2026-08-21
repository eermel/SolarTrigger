"""Strict contact-time comparisons against the Jubier JavaScript oracle."""

from __future__ import annotations

import pytest

from backend.eclipse_engine.compute import compute_local_circumstances
from backend.eclipse_engine.loader import load_eclipse
from scripts.eclipse_calculator_jubier import JS_CALCULATE
from tests.test_diff_jubier_vs_python import (
    COORDINATES,
    EVENTS,
    _context,
    _time_delta_seconds,
    jubier_page,
)


TIME_TOLERANCE_SECONDS = 0.1
VALUE_TOLERANCE = 1e-6
ALTITUDE_TOLERANCE_DEGREES = 0.05
ECLIPSE_DATES = ("2026-08-12", "2027-08-02")


@pytest.mark.parametrize("date_iso", ECLIPSE_DATES)
@pytest.mark.parametrize("latitude,longitude,altitude", COORDINATES)
def test_python_engine_strict_contacts_match_jubier_javascript(
    jubier_page, date_iso, latitude, longitude, altitude
):
    dataset = load_eclipse(date_iso)
    python_result = compute_local_circumstances(
        dataset, latitude, longitude, altitude, tz_offset=0.0
    )
    js_result = jubier_page.evaluate(
        JS_CALCULATE,
        {
            "lat_dd": latitude,
            "lon_dd": longitude,
            "alt_m": altitude,
            "tz_offset": 0.0,
            "eclipse_val": dataset["jubier"]["val"],
        },
    )
    context = _context(date_iso, latitude, longitude, altitude)

    assert "error" not in js_result, f"{context}: JS oracle error: {js_result}"
    assert python_result["eclipse_type"] == js_result["eclipse_type"], (
        f"{context}: eclipse_type differs: Python={python_result['eclipse_type']!r}, "
        f"JS={js_result['eclipse_type']!r}"
    )

    for field in ("magnitude", "moon_sun_ratio"):
        difference = abs(python_result[field] - js_result[field])
        assert difference <= VALUE_TOLERANCE, (
            f"{context}: {field} differs: Python={python_result[field]!r}, "
            f"JS={js_result[field]!r}, delta={difference}, "
            f"tolerance={VALUE_TOLERANCE}"
        )

    for event in EVENTS:
        time_field = f"{event}_utc"
        python_time = python_result[time_field]
        js_time = js_result[time_field]
        assert (python_time is None) == (js_time is None), (
            f"{context}: {time_field} availability differs: "
            f"Python={python_time!r}, JS={js_time!r}"
        )
        if js_time is None:
            continue

        time_delta = _time_delta_seconds(python_time, js_time)
        assert time_delta <= TIME_TOLERANCE_SECONDS, (
            f"{context}: {time_field} differs: Python={python_time!r}, "
            f"JS={js_time!r}, delta={time_delta}s, "
            f"tolerance={TIME_TOLERANCE_SECONDS}s"
        )

        altitude_field = f"{event}_alt_deg"
        altitude_delta = abs(
            python_result[altitude_field] - js_result[altitude_field]
        )
        assert altitude_delta <= ALTITUDE_TOLERANCE_DEGREES, (
            f"{context}: {altitude_field} differs: "
            f"Python={python_result[altitude_field]!r}, "
            f"JS={js_result[altitude_field]!r}, delta={altitude_delta}deg, "
            f"tolerance={ALTITUDE_TOLERANCE_DEGREES}deg"
        )
