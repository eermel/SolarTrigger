import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / "flask_app" / "templates" / "index.html").read_text()
FOCUSER_SERVICE = (ROOT / "plugins" / "focuser" / "zwo_plugin.py").read_text()


def _between(text, start, end):
    match = re.search(re.escape(start) + r"(?P<body>.*?)" + re.escape(end), text, re.DOTALL)
    assert match, f"missing region delimited by {start!r} and {end!r}"
    return match.group("body")


CAMERA_PANEL = _between(INDEX, '<div class="page" id="page-3"', '<!-- /page-3 CAMÉRA -->')
FOCUSER_HTML = _between(CAMERA_PANEL, '<!-- ── FOCUSEUR ── -->', '<!-- ── /FOCUSEUR ── -->')
FOCUSER_JS = _between(INDEX, "// FOCUSER UI START", "// FOCUSER UI END")
TABS = _between(INDEX, '<div id="tabs">', "<!-- PAGES -->")


def test_focuser_section_is_hidden_inside_camera_panel():
    camera_marker = CAMERA_PANEL.index('id="btn-cam-probe"')
    focuser_marker = CAMERA_PANEL.index('id="focuser-section"')

    assert focuser_marker > camera_marker
    assert re.search(
        r'<div\s+id=["\']focuser-section["\'][^>]*\bstyle=["\'][^"\']*display\s*:\s*none',
        FOCUSER_HTML,
        re.IGNORECASE,
    )


def test_visibility_is_driven_by_active_focuser_device():
    assert re.search(r"devices\s*&&\s*devices\.focuser", FOCUSER_JS)
    assert re.search(r"focuser\s*&&\s*focuser\.active\s*===\s*true", FOCUSER_JS)
    assert re.search(r"section\.style\.display\s*=\s*active\s*\?\s*['\"]['\"]\s*:\s*['\"]none['\"]", FOCUSER_JS)


def test_target_and_step_defaults_are_wired_to_backend_status():
    assert re.search(r'id=["\']focuser-target["\'][^>]*\bvalue=["\']12813["\']', FOCUSER_HTML)

    # The service owns the operational defaults; the UI replaces its initial
    # field values with these status fields as soon as it refreshes.
    assert re.search(r"^DEFAULT_COARSE\s*=\s*150\b", FOCUSER_SERVICE, re.MULTILINE)
    assert re.search(r"^DEFAULT_FINE\s*=\s*20\b", FOCUSER_SERVICE, re.MULTILINE)
    assert re.search(r"slowStep\.value\s*=\s*data\.step_fine", FOCUSER_JS)
    assert re.search(r"fastStep\.value\s*=\s*data\.step_coarse", FOCUSER_JS)


def test_press_and_page_lifecycle_safety_bindings_are_present():
    for event in ("pointerdown", "pointerup", "pointercancel", "pointerleave"):
        assert re.search(rf"addEventListener\(\s*['\"]{event}['\"]", FOCUSER_JS)

    assert re.search(r"window\.addEventListener\(\s*['\"]blur['\"]", FOCUSER_JS)
    assert re.search(r"document\.addEventListener\(\s*['\"]visibilitychange['\"]", FOCUSER_JS)
    assert re.search(r"window\.addEventListener\(\s*['\"](?:beforeunload|unload)['\"]", FOCUSER_JS)


def test_short_press_and_long_jog_use_distinct_single_request_paths():
    for endpoint in (
        "/api/focuser/step",
        "/api/focuser/jog/start",
        "/api/focuser/jog/stop",
    ):
        assert FOCUSER_JS.count(endpoint) == 1

    assert re.search(
        r"if\s*\(ended\.jogStarted\)\s*\{.*?jog/stop.*?\}\s*"
        r"else\s+if\s*\(singleStep\s*&&\s*active\)\s*\{.*?/api/focuser/step",
        FOCUSER_JS,
        re.DOTALL,
    )
    assert re.search(r"pointerup['\"].*?stopPress\(true\)", FOCUSER_JS, re.DOTALL)
    assert re.search(r"pointer(?:cancel|leave)['\"].*?stopPress\(false\)", FOCUSER_JS, re.DOTALL)


def test_movement_does_not_use_set_interval():
    assert "setInterval" not in FOCUSER_JS
    assert re.search(r"press\.timer\s*=\s*setTimeout", FOCUSER_JS)


def test_backend_refresh_and_socket_resynchronization_are_present():
    assert re.search(r"request\(\s*['\"]/api/focuser/status['\"]\s*\)", FOCUSER_JS)
    assert re.search(r"setTimeout\(\s*refreshFocuser\s*,", FOCUSER_JS)
    assert re.search(r"socket\.on\(\s*['\"]focuser_update['\"]", FOCUSER_JS)
    assert re.search(r"socket\.on\(\s*['\"]status_update['\"]", FOCUSER_JS)


def test_focuser_control_block_is_brand_neutral():
    assert not re.search(r"\bzwo\b", FOCUSER_JS, re.IGNORECASE)


def test_existing_top_level_navigation_is_unchanged():
    labels = re.findall(r"<span>\s*([^<]+?)\s*</span>", TABS)
    targets = [int(value) for value in re.findall(r"onclick=[\"']showTab\((\d+)\)[\"']", TABS)]

    assert labels == ["DEVICES", "SYNC GPS", "ÉCLIPSE", "CFG PHOTO", "CAMÉRA", "TRIGGER"]
    assert targets == [0, 1, 2, 3, 4, 5]
    assert "FOCUSEUR" not in TABS.upper()
