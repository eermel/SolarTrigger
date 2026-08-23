import re
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / "flask_app" / "templates" / "index.html").read_text(
    encoding="utf-8"
)


def _between(text, start, end):
    match = re.search(
        re.escape(start) + r"(?P<body>.*?)" + re.escape(end),
        text,
        re.DOTALL,
    )
    assert match, f"missing region delimited by {start!r} and {end!r}"
    return match.group("body")


MOUNT_JS = _between(INDEX, "// MOUNT UI START", "// MOUNT UI END")
MOUNT_HTML = _between(INDEX, '<div id="mount-section">', "<!-- ═══════════════ PAGE 4")
SLEW_FUNCTIONS = _between(
    MOUNT_JS, "function stopSlewBestEffort()", "homeButton.addEventListener"
)


class _MountSlewParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []
        self.slew_buttons = []
        self.slider = None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if attributes.get("id"):
            self.ids.append(attributes["id"])
        if tag == "input" and attributes.get("id") == "mount-slew-speed":
            self.slider = attributes
        if tag == "button" and "mount-slew-button" in attributes.get(
            "class", ""
        ).split():
            self.slew_buttons.append(attributes)


def test_slew_slider_uses_capabilities_reflects_status_and_posts_selection():
    parser = _MountSlewParser()
    parser.feed(MOUNT_HTML)

    assert parser.slider is not None
    assert parser.slider["type"] == "range"
    assert re.search(r"const\s+slewSpeedCaps\s*=\s*data\s*&&\s*data\.slew_speed_caps", MOUNT_JS)
    assert re.search(r"slewSpeed\.(?:min|max|step)\s*=\s*slewSpeedCaps\.", MOUNT_JS)
    assert re.search(r"slewSpeed\.value\s*=\s*data\.slew_speed", MOUNT_JS)
    assert re.search(
        r"findIndex\(\s*item\s*=>\s*item\.value\s*===\s*data\.slew_speed\s*\)",
        MOUNT_JS,
    )
    assert re.search(
        r"slewSpeed\.addEventListener\(\s*['\"]change['\"].*?"
        r"postMount\(\s*['\"]/api/mount/speed['\"].*?"
        r"body:\s*JSON\.stringify\(\s*\{\s*speed:\s*selectedSlewSpeed\(\)\s*\}\s*\)",
        MOUNT_JS,
        re.DOTALL,
    )


def test_direction_buttons_are_unique_and_laid_out_as_a_cross():
    parser = _MountSlewParser()
    parser.feed(INDEX)

    assert len(parser.ids) == len(set(parser.ids))
    assert [button.get("data-direction") for button in parser.slew_buttons] == [
        "north",
        "west",
        "east",
        "south",
    ]
    for direction, column, row in (
        ("north", 2, 1),
        ("west", 1, 2),
        ("east", 3, 2),
        ("south", 2, 3),
    ):
        assert re.search(
            rf'\.mount-slew-button\[data-direction=["\']{direction}["\']\]\s*\{{'
            rf'(?=[^}}]*grid-column:\s*{column}\s*;)'
            rf'(?=[^}}]*grid-row:\s*{row}\s*;)',
            INDEX,
        )


def test_hold_starts_once_and_all_pointer_end_paths_stop():
    assert re.search(
        r"button\.addEventListener\(\s*['\"]pointerdown['\"]\s*,\s*startSlew\s*\)",
        MOUNT_JS,
    )
    for event in ("pointerup", "pointercancel", "lostpointercapture"):
        assert re.search(
            rf"button\.addEventListener\(\s*['\"]{event}['\"]\s*,\s*"
            r"stopSlewBestEffort\s*\)",
            MOUNT_JS,
        )
    assert MOUNT_JS.count("fetch('/api/mount/slew/start'") == 1
    assert re.search(
        r"fetch\(\s*homeButton\.dataset\.slewStopUrl\s*,\s*"
        r"\{\s*method:\s*['\"]POST['\"]\s*\}\s*\)",
        MOUNT_JS,
    )


def test_slew_has_no_click_command_or_hold_repetition_timer():
    button_handlers = _between(
        MOUNT_JS, "slewButtons.forEach(button => {", "window.addEventListener"
    )
    assert not re.search(
        r"addEventListener\(\s*['\"]click['\"]",
        button_handlers,
    )
    assert "setInterval" not in SLEW_FUNCTIONS
    assert "setTimeout" not in SLEW_FUNCTIONS
    assert len(re.findall(r"/api/mount/slew/start", SLEW_FUNCTIONS)) == 1


def test_failed_start_clears_the_only_active_slew_state_and_sends_stop():
    assert re.search(r"let\s+activeSlew\s*=\s*null", MOUNT_JS)
    assert re.search(
        r"function\s+stopSlewBestEffort\(\)\s*\{.*?activeSlew\s*=\s*null\s*;.*?"
        r"fetch\(\s*homeButton\.dataset\.slewStopUrl",
        MOUNT_JS,
        re.DOTALL,
    )
    assert re.search(
        r"fetch\(\s*['\"]/api/mount/slew/start['\"].*?"
        r"\.catch\(\s*\(\)\s*=>\s*stopSlewBestEffort\(\)\s*\)",
        MOUNT_JS,
        re.DOTALL,
    )
    assert not re.search(r"classList\.(?:add|toggle)\([^)]*(?:slew|active)", SLEW_FUNCTIONS)


def test_homing_disables_every_direction_and_preserves_home_cancel():
    assert re.search(
        r"homing\s*=\s*data\s*&&\s*data\.homing\s*===\s*true\s*;.*?"
        r"slewButtons\.forEach\(\s*button\s*=>\s*\{\s*button\.disabled\s*=\s*homing",
        MOUNT_JS,
        re.DOTALL,
    )
    assert re.search(
        r"homeButton\.textContent\s*=\s*homing\s*\?\s*['\"]Cancel['\"]\s*:\s*['\"]Home['\"]",
        MOUNT_JS,
    )
    assert re.search(
        r"postMount\(\s*homing\s*\?\s*['\"]/api/mount/slew/stop['\"]\s*"
        r":\s*['\"]/api/mount/home['\"]\s*\)",
        MOUNT_JS,
    )


def test_tracking_mode_and_off_on_switch_share_the_focuser_switch_layout():
    assert re.search(
        r'<div[^>]*class=["\'][^"\']*\bfocuser-mode-switch\b[^"\']*["\'][^>]*>'
        r'<select[^>]*id=["\']mount-tracking-mode["\'][^>]*></select>'
        r'<label[^>]*>\s*OFF\s*</label>'
        r'<input(?=[^>]*id=["\']mount-tracking-switch["\'])'
        r'(?=[^>]*role=["\']switch["\'])[^>]*>'
        r'<label[^>]*>\s*ON\s*</label>'
        r'</div>',
        MOUNT_HTML,
    )
    assert 'id="btn-mount-tracking"' not in MOUNT_HTML


def test_tracking_switch_reflects_status_and_preserves_tracking_commands():
    assert re.search(r"trackingMode\.value\s*=\s*data\s*&&\s*data\.tracking_mode", MOUNT_JS)
    assert re.search(
        r"trackingSwitch\.checked\s*=\s*trackingEnabled",
        MOUNT_JS,
    )
    for endpoint in (
        "/api/mount/tracking/mode",
        "/api/mount/tracking/start",
        "/api/mount/tracking/stop",
    ):
        assert MOUNT_JS.count(endpoint) == 1


def test_tracking_switch_on_state_uses_the_shared_green_style():
    checked = re.search(
        r'\.focuser-mode-switch\s+input\[role="switch"\]:checked\s*\{(?P<body>.*?)\}',
        INDEX,
        re.DOTALL,
    )
    checked_thumb = re.search(
        r'\.focuser-mode-switch\s+input\[role="switch"\]:checked::after\s*'
        r'\{(?P<body>.*?)\}',
        INDEX,
        re.DOTALL,
    )
    assert checked and re.search(r"border-color:\s*var\(--green\)", checked.group("body"))
    assert checked_thumb and re.search(
        r"background:\s*var\(--green\)", checked_thumb.group("body")
    )


def test_refresh_and_socket_resync_cannot_issue_a_slew_command():
    refresh = _between(
        MOUNT_JS, "async function refreshMount()", "function scheduleMountRefresh(delay)"
    )
    assert not re.search(r"/api/mount/slew/(?:start|stop)", refresh)
    assert not re.search(r"\b(?:startSlew|stopSlewBestEffort)\s*\(", refresh)
    for event in ("connect", "status_update"):
        assert re.search(
            rf"socket\.on\(\s*['\"]{event}['\"]\s*,\s*refreshMount\s*\)",
            MOUNT_JS,
        )
    assert re.search(r"\n\s*refreshMount\(\);\s*\n\}\)\(\);", MOUNT_JS)


def test_direction_buttons_prevent_touch_selection_and_dragging():
    parser = _MountSlewParser()
    parser.feed(MOUNT_HTML)

    assert all(button.get("draggable") == "false" for button in parser.slew_buttons)
    css = re.search(r"\.mount-slew-button\s*\{(?P<body>.*?)\}", INDEX, re.DOTALL)
    assert css
    for declaration in (
        r"user-select:\s*none",
        r"-webkit-user-select:\s*none",
        r"touch-action:\s*none",
        r"-webkit-user-drag:\s*none",
    ):
        assert re.search(declaration, css.group("body"))
    assert re.search(
        r"button\.addEventListener\(\s*['\"]dragstart['\"].*?preventDefault\(\)",
        MOUNT_JS,
    )
