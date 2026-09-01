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
CONTROLS_PANEL = _between(INDEX, '<div class="page" id="controls-panel"', '<!-- ═══════════════ PAGE 4 : TRIGGER ═══════════════ -->')
FOCUSER_HTML = _between(CONTROLS_PANEL, '<!-- ── FOCUSEUR ── -->', '<!-- ── /FOCUSEUR ── -->')
FOCUSER_JS = _between(INDEX, "// FOCUSER UI START", "// FOCUSER UI END")
TABS = _between(INDEX, '<div id="tabs">', "<!-- PAGES -->")


def test_focuser_section_is_inside_controls_panel():
    assert 'id="focuser-section"' in CONTROLS_PANEL


def test_camera_panel_contains_no_focuser_ids():
    assert not re.search(r'id=["\'][^"\']*focuser[^"\']*["\']', CAMERA_PANEL, re.IGNORECASE)


def test_principal_focuser_ids_are_not_duplicated():
    for element_id in (
        "focuser-section",
        "focuser-plugin",
        "focuser-status",
        "focuser-position",
        "focuser-target",
        "focuser-step-slow",
        "focuser-step-fast",
        "focuser-speed-switch",
        "btn-focuser-go",
        "btn-focuser-home",
        "btn-focuser-minus",
        "btn-focuser-plus",
    ):
        assert len(re.findall(rf'id=["\']{re.escape(element_id)}["\']', INDEX)) == 1


def test_visibility_is_driven_by_active_focuser_device():
    assert "updateControlsVisibility(devices)" in FOCUSER_JS
    assert not re.search(r"section\.style\.display\s*=", FOCUSER_JS)


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
        "step",
        "jog/start",
        "jog/stop",
    ):
        assert len(re.findall(rf"['\"]{re.escape(endpoint)}['\"]", FOCUSER_JS)) == 1

    assert re.search(
        r"if\s*\(ended\.jogStarted\)\s*\{.*?focuserUrl\(\s*['\"]jog/stop['\"]\s*\).*?\}\s*"
        r"else\s+if\s*\(singleStep\s*&&\s*active\)\s*\{.*?post\(\s*['\"]step['\"]",
        FOCUSER_JS,
        re.DOTALL,
    )
    assert re.search(r"pointerup['\"].*?stopPress\(true\)", FOCUSER_JS, re.DOTALL)
    assert re.search(r"pointer(?:cancel|leave)['\"].*?stopPress\(false\)", FOCUSER_JS, re.DOTALL)


def test_movement_does_not_use_set_interval():
    assert "setInterval" not in FOCUSER_JS
    assert re.search(r"press\.timer\s*=\s*setTimeout", FOCUSER_JS)


def test_backend_refresh_and_socket_resynchronization_are_present():
    assert re.search(r"const\s+url\s*=\s*focuserUrl\(\s*['\"]status['\"]\s*\)", FOCUSER_JS)
    assert re.search(r"displayFocuser\(\s*await\s+request\(\s*url\s*\)\s*\)", FOCUSER_JS)
    assert re.search(r"setTimeout\(\s*refreshFocuser\s*,", FOCUSER_JS)
    assert re.search(r"socket\.on\(\s*['\"]focuser_update['\"]", FOCUSER_JS)
    assert re.search(r"socket\.on\(\s*['\"]status_update['\"]", FOCUSER_JS)


def test_focuser_control_block_is_brand_neutral():
    assert not re.search(r"\bzwo\b", FOCUSER_JS, re.IGNORECASE)


def test_top_level_navigation_includes_controls_without_a_focuser_tab():
    labels = re.findall(r"<span>\s*([^<]+?)\s*</span>", TABS)
    targets = [int(value) for value in re.findall(r"onclick=[\"']showTab\((\d+)\)[\"']", TABS)]

    assert labels == ["DEVICES", "SYNC GPS", "ECLIPSE", "PHOTO CFG", "CAMERA", "CONTROLS", "TRIGGER"]
    assert targets == [0, 1, 2, 3, 4, 5, 6]
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


def test_each_focuser_endpoint_is_referenced_once():
    assert not re.search(r"/api/focuser(?:/|['\"])", FOCUSER_JS)
    assert "`/api/rigs/${rig.rig_id}/focuser/${path}`" in FOCUSER_JS


def test_focuser_url_uses_selected_rig_and_is_guarded():
    url_function = re.search(
        r"function\s+focuserUrl\(\s*path\s*\)\s*\{(?P<body>.*?)\n\s*\}",
        FOCUSER_JS,
        re.DOTALL,
    )
    assert url_function
    assert "selectedControlsRig()" in url_function.group("body")
    assert "`/api/rigs/${rig.rig_id}/focuser/${path}`" in url_function.group("body")
    assert re.search(r":\s*null\s*;", url_function.group("body"))

    post_function = re.search(
        r"function\s+post\(\s*path\b[^)]*\)\s*\{(?P<body>.*?)\n\s*\}",
        FOCUSER_JS,
        re.DOTALL,
    )
    assert post_function
    assert re.search(r"const\s+url\s*=\s*focuserUrl\(\s*path\s*\)", post_function.group("body"))
    assert re.search(r"if\s*\(\s*!url\s*\)\s*return\b", post_function.group("body"))

    for path in ("mode", "home", "stop", "move_to", "set_step", "step", "jog/start"):
        assert re.search(rf"post\(\s*['\"]{re.escape(path)}['\"]", FOCUSER_JS)

    assert re.search(
        r"const\s+url\s*=\s*focuserUrl\(\s*['\"]jog/stop['\"]\s*\)\s*;\s*"
        r"if\s*\(\s*url\s*\)\s*fetch\(\s*url\b",
        FOCUSER_JS,
    )


def test_mode_switch_posts_backend_authoritative_slow_fast_mode():
    assert re.search(
        r"post\(\s*['\"]mode['\"]\s*,\s*"
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
    step_call = re.search(
        r"post\(\s*['\"]step['\"]\s*,\s*"
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
        r"post\(\s*['\"]jog/start['\"]\s*,\s*"
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
