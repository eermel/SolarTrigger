import re
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / "flask_app" / "templates" / "index.html").read_text(
    encoding="utf-8"
)
SOLARTRIGGER_JS = (
    ROOT / "flask_app" / "static" / "js" / "solartrigger.js"
).read_text(encoding="utf-8")
SOLARTRIGGER_CSS = (
    ROOT / "flask_app" / "static" / "css" / "solartrigger.css"
).read_text(encoding="utf-8")
INDEX += "\n" + SOLARTRIGGER_CSS


def _between(text, start, end):
    match = re.search(re.escape(start) + r"(?P<body>.*?)" + re.escape(end), text, re.DOTALL)
    assert match, f"missing region delimited by {start!r} and {end!r}"
    return match.group("body")


MOUNT_JS = _between(SOLARTRIGGER_JS, "// MOUNT UI START", "// MOUNT UI END")


class _MountDomParser(HTMLParser):
    _VOID_ELEMENTS = {
        "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
        "meta", "param", "source", "track", "wbr",
    }

    def __init__(self):
        super().__init__()
        self._open_ids = []
        self.mount_sections = 0
        self.mount_home_buttons = 0
        self.mount_home_buttons_inside_section = 0
        self.mount_slew_speed_sliders_inside_section = 0
        self.mount_slew_speed_labels_inside_section = 0
        self.mount_slew_pads_inside_section = 0
        self.mount_slew_directions_inside_pad = []

    def handle_starttag(self, tag, attrs):
        element_id = dict(attrs).get("id")
        if element_id == "mount-section":
            self.mount_sections += 1
        if element_id == "btn-mount-home":
            self.mount_home_buttons += 1
            if "mount-section" in self._open_ids:
                self.mount_home_buttons_inside_section += 1
        if element_id == "mount-slew-pad" and "mount-section" in self._open_ids:
            self.mount_slew_pads_inside_section += 1
        if (
            tag == "button"
            and "mount-slew-pad" in self._open_ids
            and "mount-slew-button" in dict(attrs).get("class", "").split()
        ):
            self.mount_slew_directions_inside_pad.append(
                dict(attrs).get("data-direction")
            )
        if (
            tag == "input"
            and element_id == "mount-slew-speed"
            and dict(attrs).get("type") == "range"
            and "mount-section" in self._open_ids
        ):
            self.mount_slew_speed_sliders_inside_section += 1
        if (
            tag == "label"
            and dict(attrs).get("for") == "mount-slew-speed"
            and "mount-section" in self._open_ids
        ):
            self.mount_slew_speed_labels_inside_section += 1
        if tag not in self._VOID_ELEMENTS:
            self._open_ids.append(element_id)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self._VOID_ELEMENTS:
            self._open_ids.pop()

    def handle_endtag(self, tag):
        self._open_ids.pop()


def test_mount_home_button_is_unique_and_inside_mount_section():
    parser = _MountDomParser()
    parser.feed(INDEX)

    assert parser.mount_sections == 1
    assert parser.mount_home_buttons == 1
    assert parser.mount_home_buttons_inside_section == 1


def test_mount_slew_speed_slider_and_label_are_inside_mount_section():
    parser = _MountDomParser()
    parser.feed(INDEX)

    assert parser.mount_slew_speed_sliders_inside_section == 1
    assert parser.mount_slew_speed_labels_inside_section == 1
    assert re.search(r"<label\b[^>]*>\s*Slew speed:", INDEX)


def test_mount_slew_buttons_form_one_directional_cross_inside_mount_section():
    parser = _MountDomParser()
    parser.feed(INDEX)

    assert parser.mount_slew_pads_inside_section == 1
    assert parser.mount_slew_directions_inside_pad == [
        "north", "west", "east", "south",
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


def test_mount_slew_speed_uses_status_capabilities_and_current_value():
    assert re.search(r"slewSpeedCaps\s*=\s*data\s*&&\s*data\.slew_speed_caps", MOUNT_JS)
    assert re.search(r"slewSpeed\.value\s*=\s*data\.slew_speed", MOUNT_JS)
    assert re.search(
        r"findIndex\(\s*item\s*=>\s*item\.value\s*===\s*data\.slew_speed\s*\)",
        MOUNT_JS,
    )


def test_mount_slew_speed_change_posts_selected_speed_and_refreshes():
    assert re.search(
        r"slewSpeed\.addEventListener\(\s*['\"]change['\"].*?"
        r"postMount\(\s*mountUrl\(\s*['\"]speed['\"]\s*\).*?"
        r"JSON\.stringify\(\s*\{\s*speed:\s*selectedSlewSpeed\(\s*\)\s*\}\s*\)",
        MOUNT_JS,
        re.DOTALL,
    )
    assert re.search(
        r"async\s+function\s+postMount\([^)]*\).*?refreshMount\(\s*\)",
        MOUNT_JS,
        re.DOTALL,
    )


def test_mount_cancel_reuses_existing_focuser_danger_style():
    assert re.search(r"\.focuser-cancel\s*\{", INDEX)
    assert re.search(
        r"homeButton\.classList\.toggle\(\s*['\"]focuser-cancel['\"]\s*,\s*homing\s*\)",
        MOUNT_JS,
    )
    assert re.search(r"homing\s*=\s*data\s*&&\s*data\.homing\s*===\s*true", MOUNT_JS)
    assert re.search(r"homeButton\.textContent\s*=\s*homing\s*\?\s*['\"]STOP['\"]\s*:\s*['\"]HOME['\"]", MOUNT_JS)


def test_mount_home_display_follows_backend_homing_through_natural_end():
    display = _between(MOUNT_JS, "function displayMount(data)", "async function refreshMount()")
    assert re.search(r"homing\s*=\s*data\s*&&\s*data\.homing\s*===\s*true", display)
    assert re.search(
        r"homeButton\.textContent\s*=\s*homing\s*\?\s*['\"]STOP['\"]\s*:\s*['\"]HOME['\"]",
        display,
    )
    assert re.search(
        r"homeButton\.classList\.toggle\(\s*['\"]focuser-cancel['\"]\s*,\s*homing\s*\)",
        display,
    )


def test_mount_reload_and_socket_resync_preserve_backend_homing_display():
    refresh = _between(MOUNT_JS, "async function refreshMount()", "function scheduleMountRefresh(delay)")
    assert re.search(r"displayMount\(\s*data\s*\)", refresh)
    for event in ("connect", "status_update"):
        assert re.search(
            rf"socket\.on\(\s*['\"]{event}['\"]\s*,\s*refreshMount\s*\)",
            MOUNT_JS,
        )
    assert re.search(r"\n\s*refreshMount\(\);\s*\n\}\)\(\);", MOUNT_JS)


def test_each_mount_endpoint_is_referenced_once():
    assert re.search(
        r"`/api/rigs/\$\{rig\.rig_id\}/mount/\$\{path\}`", MOUNT_JS
    )
    assert "/api/mount/" not in MOUNT_JS


def test_mount_url_uses_the_selected_pilotable_rig():
    assert re.search(
        r"function\s+mountUrl\(path\)\s*\{\s*"
        r"const rig = selectedPilotableMountRig\(\);\s*"
        r"return rig \? `/api/rigs/\$\{rig\.rig_id\}/mount/\$\{path\}` : null;",
        MOUNT_JS,
    )
    selected_rig_id = 2
    path = "tracking/start"
    assert f"/api/rigs/{selected_rig_id}/mount/{path}" == (
        "/api/rigs/2/mount/tracking/start"
    )


def test_mount_actions_are_guarded_without_a_pilotable_selection():
    assert re.search(r"if \(!url\) \{.*?disableMountControls\(\);.*?return;", MOUNT_JS, re.DOTALL)
    assert re.search(r"async function postMount\([^)]*\) \{\s*if \(!url\) return;", MOUNT_JS)
    assert re.search(r"if \(!startUrl \|\| !stopUrl \|\| homing \|\| activeSlew\) return;", MOUNT_JS)
    for element_id in ("btn-mount-home", "mount-slew-speed", "mount-tracking-mode", "mount-tracking-switch"):
        assert re.search(rf'id="{element_id}"[^>]*\bdisabled\b', INDEX)
    assert len(re.findall(r'class="[^"]*mount-slew-button[^"]*"[^>]*\bdisabled\b', INDEX)) == 4


def test_mount_slew_pointer_events_keep_one_start_and_redundant_stop_safety():
    # A movement starts only from the directional button itself.
    assert len(re.findall(
        r"button\.addEventListener\(\s*['\"]pointerdown['\"]\s*,\s*startSlew\s*\)",
        MOUNT_JS,
    )) == 1

    # Button-local release paths remain present.
    for event in ("pointerup", "pointercancel", "lostpointercapture"):
        assert len(re.findall(
            rf"button\.addEventListener\(\s*['\"]{event}['\"]",
            MOUNT_JS,
        )) == 1

    # pointerup/pointercancel also have a window-level capture safety net.
    for event in ("pointerup", "pointercancel"):
        assert len(re.findall(
            rf"window\.addEventListener\(\s*['\"]{event}['\"]\s*,\s*"
            r"stopSlewBestEffort\s*,\s*true\s*\)",
            MOUNT_JS,
        )) == 1

    assert MOUNT_JS.count("fetch(startUrl") == 1
    assert "gesture_id: gestureId" in MOUNT_JS
    assert MOUNT_JS.count("fetch(slew.stopUrl") == 1
    assert "gesture_id: slew.gestureId" in MOUNT_JS

    # Multiple release events are harmless: the first one marks the gesture,
    # later ones return without scheduling another stop sequence.
    assert "if (!slew || slew.releaseRequested) return;" in MOUNT_JS
    assert "slew.releaseRequested = true;" in MOUNT_JS

    # One STOP is sent immediately and another after START settles, closing
    # the short-press START/STOP reordering race.
    assert re.search(
        r"slew\.releaseRequested\s*=\s*true\s*;.*?"
        r"sendSlewStop\(slew\)\s*;.*?"
        r"Promise\.resolve\(slew\.startPromise\)\.finally",
        MOUNT_JS,
        re.DOTALL,
    )
    assert re.search(
        r"function\s+finalizeReleasedSlew\(slew\).*?"
        r"sendSlewStop\(slew\)\.finally",
        MOUNT_JS,
        re.DOTALL,
    )

    for event in ("blur", "pagehide"):
        assert re.search(
            rf"window\.addEventListener\(\s*['\"]{event}['\"]\s*,\s*"
            r"stopSlewBestEffort\s*\)",
            MOUNT_JS,
        )
    assert re.search(
        r"document\.addEventListener\(\s*['\"]visibilitychange['\"].*?"
        r"document\.hidden.*?stopSlewBestEffort\(\)",
        MOUNT_JS,
        re.DOTALL,
    )


def test_controls_refresh_only_stops_slew_when_selected_rig_changes():
    handler = _between(
        MOUNT_JS,
        "document.addEventListener('controlsrigchange', () => {",
        "socket.on('connect'",
    )
    assert "activeSlew.rigId !== selectedRigId" in handler
    assert not re.search(
        r"controlsrigchange.*?\{\s*stopSlewBestEffort\(\)",
        handler,
        re.DOTALL,
    )


def test_mount_slew_buttons_are_disabled_while_homing():
    assert re.search(
        r"homing\s*=\s*data\s*&&\s*data\.homing\s*===\s*true\s*;"
        r".*?slewButtons\.forEach\(\s*button\s*=>\s*"
        r"\{\s*button\.disabled\s*=\s*homing\s*;\s*\}\s*\)",
        MOUNT_JS,
        re.DOTALL,
    )


def test_mount_slew_motion_has_no_timer_based_maintenance():
    slew_handlers = _between(MOUNT_JS, "function stopSlewBestEffort()", "homeButton.addEventListener")
    assert "setInterval" not in slew_handlers
    assert "setTimeout" not in slew_handlers


def test_mount_uses_timeout_refresh_and_socket_resynchronization():
    assert "setInterval" not in MOUNT_JS
    assert re.search(r"setTimeout\(\s*refreshMount\s*,\s*delay\s*\)", MOUNT_JS)
    assert re.search(
        r"socket\.on\(\s*['\"]connect['\"]\s*,\s*refreshMount\s*\)", MOUNT_JS
    )
    assert re.search(
        r"socket\.on\(\s*['\"]status_update['\"]\s*,\s*refreshMount\s*\)",
        MOUNT_JS,
    )
    assert re.search(r"\brefreshMount\(\s*\)\s*;", MOUNT_JS)


def test_mount_refresh_and_socket_resynchronization_do_not_start_or_stop_slew():
    refresh_source = _between(
        MOUNT_JS,
        "async function refreshMount()",
        "function scheduleMountRefresh(delay)",
    )
    assert not re.search(r"/api/mount/slew/(?:start|stop)", refresh_source)
    assert not re.search(r"\bpostMount\s*\(", refresh_source)

    for event in ("connect", "status_update"):
        listener = re.search(
            rf"socket\.on\(\s*['\"]{event}['\"]\s*,\s*"
            r"(?P<handler>\w+)\s*\)",
            MOUNT_JS,
        )
        assert listener
        assert listener.group("handler") == "refreshMount"


def test_mount_click_uses_last_server_status_and_refreshes_after_post():
    assert re.search(
        r"postMount\(\s*mountUrl\(\s*homing\s*\?\s*['\"]slew/stop['\"]\s*"
        r":\s*['\"]home['\"]\s*\)\s*\)",
        MOUNT_JS,
    )
    assert re.search(r"async\s+function\s+postMount\([^)]*\).*?refreshMount\(\s*\)", MOUNT_JS, re.DOTALL)
