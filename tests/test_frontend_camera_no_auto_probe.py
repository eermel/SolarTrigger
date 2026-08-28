import re
from pathlib import Path


INDEX_HTML = (
    Path(__file__).resolve().parents[1] / "flask_app" / "templates" / "index.html"
).read_text(encoding="utf-8")


def test_camera_probe_endpoints_are_absent_from_frontend():
    assert "/api/camera/probe" not in INDEX_HTML
    assert not re.search(r"/api/rigs/[^\s'\"`]+/camera/probe", INDEX_HTML)


def test_camera_probe_is_not_scheduled_by_a_timer():
    timer_call = re.compile(
        r"set(?:Interval|Timeout)\s*\([^)]*(?:"
        r"/api/camera/probe|/api/rigs/[^\s'\"`]+/camera/probe"
        r")",
        re.DOTALL,
    )

    assert not timer_call.search(INDEX_HTML)
