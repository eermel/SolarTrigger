import re
from pathlib import Path


INDEX_HTML = (
    Path(__file__).resolve().parents[1] / "flask_app" / "templates" / "index.html"
).read_text(encoding="utf-8")


def _application_clock_source() -> str:
    """Return the application clock code, excluding bundled third-party JavaScript."""
    start = INDEX_HTML.index("function _nowAdjusted()")
    end = INDEX_HTML.index("function updateGPS(", start)
    return INDEX_HTML[start:end]


def test_ui_clock_is_anchored_to_pi_epoch_and_monotonic_time():
    clock_source = _application_clock_source()

    assert re.search(
        r"_clockAnchorEpochMs\s*\+\s*"
        r"\(performance\.now\(\)\s*-\s*_clockAnchorPerfMs\)",
        clock_source,
    )
    assert re.search(
        r"function\s+updateTime\s*\(t\).*?"
        r"Number\.isFinite\(t\.epoch_ms\).*?"
        r"piMs\s*=\s*t\.epoch_ms.*?"
        r"_clockAnchorEpochMs\s*=\s*piMs.*?"
        r"_clockAnchorPerfMs\s*=\s*performance\.now\(\)",
        clock_source,
        re.DOTALL,
    )


def test_ui_clock_does_not_accumulate_or_use_browser_wall_clock():
    clock_source = _application_clock_source()

    assert not re.search(r"\bdisplayed\s*\+=\s*1000\b", INDEX_HTML)
    assert not re.search(r"\bDate\.now\s*\(\s*\)", clock_source)
