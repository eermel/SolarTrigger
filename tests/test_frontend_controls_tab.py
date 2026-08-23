from html.parser import HTMLParser
from pathlib import Path
import re

import pytest


ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / "flask_app" / "templates" / "index.html").read_text(encoding="utf-8")


class _NavigationParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.container = None
        self._container_depth = 0
        self.tabs = []
        self.pages = []
        self._tab = None

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        element_id = attributes.get("id")
        classes = attributes.get("class", "").split()

        if tag == "div" and element_id in {"tabs", "pages"} and self.container is None:
            self.container = element_id
            self._container_depth = 1
        elif tag == "div" and self.container is not None:
            self._container_depth += 1

        if self.container == "tabs" and tag == "button" and "tab" in classes:
            self._tab = {
                "id": element_id,
                "onclick": attributes.get("onclick"),
                "text": [],
            }
            self.tabs.append(self._tab)
        elif self.container == "pages" and tag == "div" and "page" in classes:
            self.pages.append(element_id)

    def handle_endtag(self, tag):
        if tag == "button":
            self._tab = None
        elif tag == "div" and self.container is not None:
            self._container_depth -= 1
            if self._container_depth == 0:
                self.container = None

    def handle_data(self, data):
        if self._tab is not None:
            self._tab["text"].append(data)


def test_controls_tab_and_panel_are_in_the_seven_item_navigation_order():
    parser = _NavigationParser()
    parser.feed(INDEX)

    labels = [" ".join(tab["text"]).strip() for tab in parser.tabs]
    assert labels == [
        "DEVICES",
        "SYNC GPS",
        "ÉCLIPSE",
        "CFG PHOTO",
        "CAMÉRA",
        "CONTROLS",
        "TRIGGER",
    ]
    assert parser.pages == [
        "devices-panel",
        "page-0",
        "page-1",
        "page-2",
        "page-3",
        "controls-panel",
        "page-4",
    ]

    controls, trigger = parser.tabs[5:]
    assert controls["id"] == "controls-tab"
    assert controls["onclick"] == "showTab(5)"
    assert trigger["onclick"] == "showTab(6)"


def test_trigger_initialization_uses_trigger_tab_index():
    trigger_initialization = re.compile(
        r"if\s*\(\s*n\s*===\s*6\s*\)\s*\{\s*"
        r"loadTriggerConfigList\(\);\s*loadEclipseFileList\(\);\s*\}"
    )

    assert trigger_initialization.search(INDEX)


def test_mount_section_is_unique_and_inside_controls_panel():
    controls_start = INDEX.index('<div class="page" id="controls-panel"')
    trigger_start = INDEX.index('<!-- ═══════════════ PAGE 4 : TRIGGER ═══════════════ -->')
    controls_panel = INDEX[controls_start:trigger_start]

    assert 'id="mount-section"' in controls_panel
    assert len(re.findall(r'id=["\']mount-section["\']', INDEX)) == 1


def _controls_visibility_source():
    match = re.search(
        r"function\s+updateControlsVisibility\(devices\)\s*\{(?P<body>.*?)\n\}",
        INDEX,
        re.DOTALL,
    )
    assert match, "updateControlsVisibility(devices) is missing"
    return match.group("body")


def _function_source(name, *, asynchronous=False):
    prefix = r"async\s+" if asynchronous else ""
    match = re.search(
        rf"{prefix}function\s+{re.escape(name)}\([^)]*\)\s*"
        r"\{(?P<body>.*?)(?=\n\}\n(?:\n|//))",
        INDEX,
        re.DOTALL,
    )
    assert match, f"{name}() is missing"
    return match.group("body")


@pytest.mark.parametrize(
    ("devices", "controls_hidden", "focuser_hidden", "mount_hidden"),
    (
        ({"focuser": {"active": False}, "mount": {"active": False}}, True, True, True),
        ({"focuser": {"active": True}, "mount": {"active": False}}, False, False, True),
        ({"focuser": {"active": False}, "mount": {"active": True}}, False, True, False),
        ({"focuser": {"active": True}, "mount": {"active": True}}, False, False, False),
    ),
)
def test_controls_visibility_for_all_device_states(
    devices, controls_hidden, focuser_hidden, mount_hidden
):
    source = _controls_visibility_source()
    focuser_active = devices.get("focuser", {}).get("active") is True
    mount_active = devices.get("mount", {}).get("active") is True

    assert (not (focuser_active or mount_active)) is controls_hidden
    assert (not focuser_active) is focuser_hidden
    assert (not mount_active) is mount_hidden
    assert re.search(r"controlsTab\.hidden\s*=\s*!controlsActive", source)
    assert re.search(r"controlsPanel\.hidden\s*=\s*!controlsActive", source)
    assert re.search(r"getElementById\(['\"]focuser-section['\"]\)\.hidden\s*=\s*!focuserActive", source)
    assert re.search(r"getElementById\(['\"]mount-section['\"]\)\.hidden\s*=\s*!mountActive", source)


def test_missing_devices_are_inactive_and_hidden():
    source = _controls_visibility_source()

    assert re.search(r"devices\s*&&\s*devices\.focuser\s*&&\s*devices\.focuser\.active\s*===\s*true", source)
    assert re.search(r"devices\s*&&\s*devices\.mount\s*&&\s*devices\.mount\.active\s*===\s*true", source)


def test_active_controls_falls_back_to_devices_when_controls_become_hidden():
    source = _controls_visibility_source()

    assert re.search(r"controlsWasSelected\s*=\s*controlsTab\.classList\.contains\(['\"]active['\"]\)", source)
    assert re.search(r"if\s*\(controlsWasSelected\s*&&\s*!controlsActive\)\s*showTab\(0\)", source)


def test_backend_refresh_restores_device_rendering_and_controls_visibility():
    source = _function_source("fetchDevices", asynchronous=True)

    assert re.search(r"fetch\(\s*['\"]/api/devices['\"]\s*\)", source)
    assert re.search(
        r"const\s+devices\s*=\s*await\s+response\.json\(\).*?"
        r"renderDevices\(devices\).*?updateControlsVisibility\(devices\)",
        source,
        re.DOTALL,
    )
    assert re.search(r"if\s*\(n\s*===\s*0\)\s*fetchDevices\(\)", INDEX)
    assert re.search(r"// Init\s*fetchDevices\(\);", INDEX)


@pytest.mark.parametrize("function_name", ("selectDevice", "rescanDevices"))
def test_device_updates_recalculate_controls_visibility(function_name):
    source = _function_source(function_name, asynchronous=True)

    assert re.search(
        r"const\s+devices\s*=\s*await\s+response\.json\(\).*?"
        r"renderDevices\(devices\).*?updateControlsVisibility\(devices\)",
        source,
        re.DOTALL,
    )


def test_socket_device_updates_recalculate_controls_visibility():
    assert re.search(
        r"socket\.on\(\s*['\"]state_update['\"].*?"
        r"if\s*\(d\.devices\)\s*updateControlsVisibility\(d\.devices\)",
        INDEX,
        re.DOTALL,
    )
    assert re.search(
        r"socket\.on\(\s*['\"]status_update['\"].*?"
        r"if\s*\(data\.devices\)\s*\{.*?"
        r"updateControlsVisibility\(devices\).*?applyDevices\(devices\)",
        INDEX,
        re.DOTALL,
    )
