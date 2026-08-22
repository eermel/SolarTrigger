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


def test_slow_fast_switch_is_present_with_constant_labels():
    assert re.search(
        r'<div[^>]*class=["\'][^"\']*\bfocuser-mode-switch\b[^"\']*["\'][^>]*>'
        r'<input[^>]*id=["\']focuser-step-fast["\'][^>]*>'
        r'<label[^>]*>\s*Slow\s*</label>'
        r'<input(?=[^>]*id=["\']focuser-speed-switch["\'])(?=[^>]*role=["\']switch["\'])[^>]*>'
        r'<label[^>]*>\s*Fast\s*</label>'
        r'</div>',
        FOCUSER_HTML,
    )


def test_focuser_cancel_style_uses_red_background_white_text_and_red_border():
    assert re.search(
        r"\.focuser-cancel\s*\{"
        r"(?=[^}]*\bbackground\s*:\s*var\(--red\)\s*;)"
        r"(?=[^}]*\bcolor\s*:\s*white\s*;)"
        r"(?=[^}]*\bborder-color\s*:\s*var\(--red\)\s*;)"
        r"[^}]*\}",
        INDEX,
        re.DOTALL,
    )


def test_focuser_stop_endpoint_is_referenced_once():
    assert FOCUSER_JS.count("/api/focuser/stop") == 1


def test_mode_switch_posts_backend_authoritative_slow_fast_mode():
    assert FOCUSER_JS.count("/api/focuser/mode") == 1
    assert re.search(
        r"post\(\s*['\"]/api/focuser/mode['\"]\s*,\s*"
        r"\{\s*mode\s*:\s*speedSwitch\.checked\s*\?\s*['\"]fast['\"]"
        r"\s*:\s*['\"]slow['\"]\s*\}",
        FOCUSER_JS,
    )
    assert re.search(
        r"data\.mode\s*===\s*['\"]slow['\"]\s*\|\|\s*"
        r"data\.mode\s*===\s*['\"]fast['\"]",
        FOCUSER_JS,
    )
    assert re.search(
        r"speedSwitch\.checked\s*=\s*data\.mode\s*===\s*['\"]fast['\"]",
        FOCUSER_JS,
    )


def test_step_and_jog_requests_are_direction_only():
    assert FOCUSER_JS.count("/api/focuser/step") == 1
    assert FOCUSER_JS.count("/api/focuser/jog/start") == 1
    assert FOCUSER_JS.count("/api/focuser/jog/stop") == 1

    step_call = re.search(
        r"post\(\s*['\"]/api/focuser/step['\"]\s*,\s*"
        r"(?P<body>\{.*?\})\s*\)",
        FOCUSER_JS,
        re.DOTALL,
    )
    assert step_call
    assert "direction" in step_call.group("body")
    assert "increase" in step_call.group("body")
    assert "decrease" in step_call.group("body")
    assert "delta" not in step_call.group("body")
    assert "mode" not in step_call.group("body")

    jog_call = re.search(
        r"post\(\s*['\"]/api/focuser/jog/start['\"]\s*,\s*"
        r"(?P<body>\{.*?\})\s*\)",
        FOCUSER_JS,
        re.DOTALL,
    )
    assert jog_call
    assert "direction" in jog_call.group("body")
    assert "increase" in jog_call.group("body")
    assert "decrease" in jog_call.group("body")
    assert "delta" not in jog_call.group("body")
    assert "mode" not in jog_call.group("body")


def test_go_and_home_are_adjacent_in_target_position_row():
    assert re.search(
        r'id=["\']focuser-target["\']'
        r'.*?'
        r'<button[^>]*id=["\']btn-focuser-go["\'][^>]*>\s*Go\s*</button>'
        r'\s*'
        r'<button[^>]*id=["\']btn-focuser-home["\'][^>]*>\s*Home\s*</button>',
        FOCUSER_HTML,
        re.DOTALL,
    )

def test_socket_updates_refresh_from_backend_status():
    assert re.search(
        r"socket\.on\(\s*['\"]focuser_update['\"]\s*,\s*refreshFocuser\s*\)",
        FOCUSER_JS,
    )
    assert re.search(
        r"socket\.on\(\s*['\"]status_update['\"].*?"
        r"if\s*\(\s*data\.focuser\s*\)\s*refreshFocuser\(\s*\)",
        FOCUSER_JS,
        re.DOTALL,
    )
