"""Differential integration test for the Python and Jubier JS engines.

By default every eclipse in the dataset registry is checked.  For a quicker
targeted run, set ``ECLIPSE_DIFF_DATES`` to comma-separated ISO dates, e.g.::

    ECLIPSE_DIFF_DATES=2026-08-12,2027-08-02 pytest -q \
        tests/test_diff_jubier_vs_python.py

The test is skipped, with an actionable reason, when Playwright or a Chromium
executable is unavailable.
"""

from __future__ import annotations

import functools
import http.server
import os
import threading
from pathlib import Path

import pytest

from backend.eclipse_engine.compute import compute_local_circumstances
from backend.eclipse_engine.loader import list_supported_eclipses, load_eclipse
from scripts.eclipse_calculator_jubier import JS_CALCULATE


playwright = pytest.importorskip(
    "playwright.sync_api",
    reason="differential JS test requires Playwright and Chromium",
)

TIME_TOLERANCE_SECONDS = 0.5
VALUE_TOLERANCE = 1e-6
ALTITUDE_TOLERANCE_DEGREES = 0.05
EVENTS = ("C1", "C2", "TMAX", "C3", "C4")
COORDINATES = (
    pytest.param(25.2854, 32.5907, 76.0, id="luxor"),
    pytest.param(40.4168, -3.7038, 667.0, id="madrid"),
    pytest.param(-33.8688, 151.2093, 58.0, id="sydney"),
)
JUBIER_DIR = Path(__file__).resolve().parents[1] / "jubier_files"


def _selected_eclipses() -> list[str]:
    supported = list_supported_eclipses()
    requested = os.environ.get("ECLIPSE_DIFF_DATES")
    if not requested:
        return supported

    selected = [date.strip() for date in requested.split(",") if date.strip()]
    unknown = sorted(set(selected) - set(supported))
    if unknown:
        raise pytest.UsageError(
            "ECLIPSE_DIFF_DATES contains unsupported dates: " + ", ".join(unknown)
        )
    if not selected:
        raise pytest.UsageError("ECLIPSE_DIFF_DATES must contain at least one date")
    return selected


class _QuietRequestHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, _format, *args):
        pass


@pytest.fixture(scope="session")
def jubier_page():
    handler = functools.partial(_QuietRequestHandler, directory=str(JUBIER_DIR))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    browser = None
    try:
        with playwright.sync_playwright() as pw:
            try:
                browser = pw.chromium.launch(
                    headless=True,
                    args=["--no-sandbox", "--disable-dev-shm-usage"],
                )
            except playwright.Error as exc:
                pytest.skip(f"differential JS test requires launchable Chromium: {exc}")

            page = browser.new_page()
            page.goto(
                f"http://127.0.0.1:{server.server_port}/index.html",
                wait_until="networkidle",
            )
            page.wait_for_function("typeof getall !== 'undefined'")
            yield page
    finally:
        if browser is not None:
            browser.close()
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)


def _hms_seconds(value: str) -> float:
    hours, minutes, seconds = value.split(":")
    return int(hours) * 3600.0 + int(minutes) * 60.0 + float(seconds)


def _time_delta_seconds(left: str, right: str) -> float:
    difference = abs(_hms_seconds(left) - _hms_seconds(right))
    return min(difference, 86_400.0 - difference)


def _context(date_iso: str, latitude: float, longitude: float, altitude: float) -> str:
    return (
        f"eclipse={date_iso}, observer=(lat={latitude}, lon={longitude}, "
        f"alt={altitude}m)"
    )


@pytest.mark.parametrize("date_iso", _selected_eclipses())
@pytest.mark.parametrize("latitude,longitude,altitude", COORDINATES)
def test_python_engine_matches_jubier_javascript(
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
