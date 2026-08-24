import re
from datetime import datetime, timezone
from pathlib import Path


INDEX_HTML = (
    Path(__file__).resolve().parents[1] / "flask_app" / "templates" / "index.html"
).read_text(encoding="utf-8")


def _clock_source() -> str:
    start = INDEX_HTML.index("function _nowAdjusted()")
    end = INDEX_HTML.index("function updateGPS(", start)
    return INDEX_HTML[start:end]


def _simulate_client(payload, anchor_perf_ms, current_perf_ms, browser_timezone):
    """Minimal model of updateTime() followed by _tickClock()."""
    del browser_timezone  # Browser timezone is deliberately not a clock input.
    elapsed_ms = current_perf_ms - anchor_perf_ms
    utc_ms = payload["backend_utc_epoch_ms"] + elapsed_ms
    local_ms = payload["backend_local_epoch_ms"] + elapsed_ms

    def display(epoch_ms):
        value = datetime.fromtimestamp(epoch_ms / 1000, timezone.utc)
        return value.strftime("%Y-%m-%d %H:%M:%S")

    return {
        "utc": display(utc_ms),
        "local": display(local_ms),
    }


def test_two_clients_derive_identical_utc_and_local_from_backend_anchors():
    clock_source = _clock_source()
    assert re.search(
        r"_clockAnchorUtcMs\s*=\s*piMs.*?"
        r"_clockAnchorLocalMs\s*=\s*piLocalMs.*?"
        r"_clockAnchorPerfMs\s*=\s*performance\.now\(\)",
        clock_source,
        re.DOTALL,
    )
    assert "_clockAnchorUtcMs + (performance.now() - _clockAnchorPerfMs)" in clock_source
    # Local time must be derived from the Pi UTC anchor plus the
    # backend/configured timezone offset. A Unix epoch has no timezone,
    # so a separate "local epoch" must not be advanced as if it differed
    # from the UTC epoch.
    assert "_getTimezoneOffset()" in clock_source
    assert "_nowAdjustedUtcMs() + offsetH * 3600000" in clock_source

    payload = {
        "backend_utc_epoch_ms": 1_816_675_200_000,
        "backend_local_epoch_ms": 1_816_682_400_000,
    }
    client_a = _simulate_client(payload, 10_000, 14_250, "Pacific/Honolulu")
    client_b = _simulate_client(payload, 80_000, 84_250, "Pacific/Kiritimati")

    assert client_a == client_b
    assert client_a == {
        "utc": "2027-07-27 08:00:04",
        "local": "2027-07-27 10:00:04",
    }


def test_application_clock_has_no_browser_timezone_derivation():
    clock_source = _clock_source()

    assert not re.search(r"\.\s*toLocaleTimeString\s*\(", clock_source)
    assert not re.search(r"\.\s*getTimezoneOffset\s*\(", clock_source)
    assert not re.search(r"\bIntl\s*\.\s*DateTimeFormat\s*\(", clock_source)
