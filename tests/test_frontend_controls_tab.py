from tests.frontend_source import frontend_source
from html.parser import HTMLParser
from pathlib import Path
import re

import pytest


ROOT = Path(__file__).resolve().parents[1]
INDEX = frontend_source()


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


def test_controls_tab_and_panel_are_in_the_eight_item_navigation_order():
    parser = _NavigationParser()
    parser.feed(INDEX)

    labels = [" ".join(tab["text"]).strip() for tab in parser.tabs]
    assert labels == [
        "DEVICES",
        "SYNC GPS",
        "ECLIPSE",
        "PHOTO SETUP",
        "EXPO. OPT.",
        "SEQUENCER",
        "CAMERA",
        "CONTROLS",
        "TRIGGER",
    ]
    assert parser.pages == [
        "devices-panel",
        "page-0",
        "page-1",
        "page-2",
        "page-exposure-opt",
        "sequencer-panel",
        "page-3",
        "controls-panel",
        "page-4",
    ]

    controls = parser.tabs[7]
    trigger = parser.tabs[8]
    assert controls["id"] == "controls-tab"
    assert controls["onclick"] == "showTab(7)"
    assert trigger["onclick"] == "showTab(8)"


def test_trigger_initialization_uses_trigger_tab_index():
    trigger_initialization = re.compile(
        r"if\s*\(\s*n\s*===\s*8\s*\)\s*\{\s*"
        r"loadTriggerConfigList\(\);\s*loadEclipseFileList\(\);\s*\}"
    )

    assert trigger_initialization.search(INDEX)


def test_eclipse_calculation_has_no_dst_control_or_payload_field():
    source = _function_source("calculateEclipse", asynchronous=True)

    assert 'id="inp-dst"' not in INDEX
    assert not re.search(r"\bdst\b", source, re.IGNORECASE)
    assert re.search(
        r"body\s*:\s*JSON\.stringify\(\s*"
        r"\{\s*lat\s*,\s*lon\s*,\s*alt\s*,\s*tz\s*,\s*eclipse\s*:\s*ecl\s*\}\s*"
        r"\)",
        source,
    )


def test_mount_section_is_unique_and_inside_controls_panel():
    controls_start = INDEX.index('<div class="page" id="controls-panel"')
    trigger_start = INDEX.index('<!-- ═══════════════ PAGE 4 : TRIGGER ═══════════════ -->')
    controls_panel = INDEX[controls_start:trigger_start]

    assert 'id="mount-section"' in controls_panel
    assert len(re.findall(r'id=["\']mount-section["\']', INDEX)) == 1


def test_controls_panel_has_four_exclusive_rig_buttons_and_target_label():
    controls_start = INDEX.index('<div class="page" id="controls-panel"')
    trigger_start = INDEX.index('<!-- ═══════════════ PAGE 4 : TRIGGER ═══════════════ -->')
    controls_panel = INDEX[controls_start:trigger_start]

    assert len(re.findall(r'data-controls-rig-id="[1-4]"', controls_panel)) == 4
    for rig_id in range(1, 5):
        assert f'id="controls-rig-{rig_id}"' in controls_panel
        assert f'onclick="selectControlsRig({rig_id})"' in controls_panel
    assert 'id="controls-target-label"' in controls_panel
    assert re.search(r"let\s+selectedRigId\s*=\s*1\s*;", INDEX)


def test_controls_rig_rendering_only_exposes_operational_rigs():
    source = _function_source("renderControlsRigSelection")

    assert re.search(
        r"const\s+available\s*=\s*rigIsOperationallyActive\(rig\)",
        source,
    )
    assert re.search(r"button\.hidden\s*=\s*!available", source)
    assert re.search(r"button\.disabled\s*=\s*!available", source)
    assert re.search(
        r"button\.classList\.toggle\(['\"]active['\"],\s*"
        r"available\s*&&\s*selectedRigId\s*===\s*defaultRig\.rig_id\)",
        source,
    )


def test_controls_rig_selection_rejects_inactive_rigs_without_network_calls():
    source = _function_source("selectControlsRig")

    assert re.search(
        r"if\s*\(!rigIsOperationallyActive\(rig\)\)\s*return",
        source,
    )
    assert re.search(r"selectedRigId\s*=\s*numericRigId", source)
    assert "fetch(" not in source
    assert not re.search(r"/api/", source)


def test_controls_target_label_uses_cached_rig_mount_display_label():
    source = _function_source("renderControlsRigSelection")

    assert "No RIG selected" in source
    assert "No controllable mount" in source
    assert "mount.display_label" in source
    assert "rigDevicesState.rigs" in source
    assert "fetch(" not in source


@pytest.mark.parametrize(
    ("mount", "expected_label", "mount_hidden"),
    (
        (
            {"backend": "indi", "display_label": "EQMod — USB 2-1"},
            "RIG 2 — Monture : EQMod — USB 2-1",
            False,
        ),
        (None, "RIG 2 — Aucune monture pilotable", True),
    ),
)
def test_cached_mount_binding_drives_rendered_label_and_visibility(
    mount, expected_label, mount_hidden
):
    rig_devices_state = {
        "rigs": [
            {"rig_id": 2, "enabled": True, "devices": {"mount": mount}},
        ]
    }
    selected_rig = rig_devices_state["rigs"][0]
    selected_mount = selected_rig["devices"]["mount"]
    pilotable = bool(
        selected_mount
        and selected_mount.get("backend") not in {None, "", "none", "external"}
    )
    rendered_label = (
        f"RIG 2 — Monture : {selected_mount['display_label']}"
        if pilotable
        else "RIG 2 — Aucune monture pilotable"
    )

    label_source = _function_source("renderControlsRigSelection")
    visibility_source = _function_source("renderSelectedMountAvailability")
    assert rendered_label == expected_label
    assert (not pilotable) is mount_hidden
    assert "mount.display_label" in label_source
    assert "selectedPilotableMountRig()" in visibility_source
    assert re.search(r"mountSection\.hidden\s*=\s*!mountAvailable", visibility_source)
    assert re.search(r"control\.disabled\s*=\s*!mountAvailable", visibility_source)
    assert "fetch(" not in label_source
    assert "fetch(" not in visibility_source


def test_mount_controls_are_reenabled_when_cached_mount_becomes_pilotable():
    source = _function_source("renderSelectedMountAvailability")

    assert re.search(
        r"querySelectorAll\(['\"]button, input, select['\"]\).*?"
        r"control\.disabled\s*=\s*!mountAvailable",
        source,
        re.DOTALL,
    )


def test_disabled_selected_rig_falls_back_to_active_rig_without_commands():
    selection_source = _function_source("renderControlsRigSelection")
    update_source = _function_source("updateRigs")
    visibility_source = _controls_visibility_source()

    assert (
        "selectedRigId = firstOperationalRigId();"
        in selection_source
    )
    assert (
        "selectedRig = selectedControlsRig();"
        in selection_source
    )
    assert "renderSelectedMountAvailability()" in selection_source
    assert "renderSelectedFocuserAvailability()" in selection_source
    assert "renderControlsRigSelection()" in update_source
    assert "renderControlsRigSelection()" in visibility_source

    # Passive selection fallback must never command hardware or call the API.
    for source in (selection_source, update_source, visibility_source):
        assert "fetch(" not in source
        assert not re.search(
            r"/api/|postMount\(|mountUrl\(",
            source,
        )


def test_passive_rig_updates_refresh_cached_mount_label_without_fetch():
    load_source = _function_source("loadRigDevices", asynchronous=True)
    render_source = _function_source("renderRigDevices")
    update_source = _function_source("updateRigs")

    assert "renderRigDevices(payload, inventory)" in load_source
    assert "updateRigs(rigs)" in render_source
    assert "renderControlsRigSelection()" in update_source
    assert re.search(
        r"socket\.on\(\s*['\"]state_update['\"].*?"
        r"if\s*\(d\.rigs\)\s*updateRigs\(d\.rigs\)",
        INDEX,
        re.DOTALL,
    )
    assert "fetch(" not in update_source


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


def test_controls_visibility_combines_global_and_per_rig_devices():
    source = _controls_visibility_source()

    assert "globalDevicesState = devices" in source
    assert "const currentDevices = globalDevicesState" in source

    assert re.search(
        r"currentDevices\s*&&\s*currentDevices\.focuser\s*&&\s*"
        r"currentDevices\.focuser\.active\s*===\s*true",
        source,
    )
    assert re.search(
        r"currentDevices\s*&&\s*currentDevices\.mount\s*&&\s*"
        r"currentDevices\.mount\.active\s*===\s*true",
        source,
    )

    assert "rigDevicesState.rigs.some" in source
    assert "pilotableMount" in source
    assert "pilotableFocuser" in source
    assert (
        "const controlsActive = "
        "focuserActive || mountActive || rigControlsActive"
    ) in source

    assert re.search(r"controlsTab\.hidden\s*=\s*!controlsActive", source)
    assert re.search(r"controlsPanel\.hidden\s*=\s*!controlsActive", source)

    # La section affichée dépend du RIG sélectionné, pas de l'ancien
    # état global focuser/mount.
    assert "renderControlsRigSelection()" in source
    assert "focuser-section').hidden = !focuserActive" not in source

def test_missing_global_devices_do_not_erase_per_rig_controls_state():
    source = _controls_visibility_source()

    assert "if (devices && typeof devices === 'object')" in source
    assert "globalDevicesState = devices" in source
    assert "const currentDevices = globalDevicesState" in source
    assert "rigControlsActive" in source
    assert (
        "focuserActive || mountActive || rigControlsActive"
        in source
    )

def test_active_controls_falls_back_to_devices_when_controls_become_hidden():
    source = _controls_visibility_source()

    assert re.search(r"controlsWasSelected\s*=\s*controlsTab\.classList\.contains\(['\"]active['\"]\)", source)
    assert re.search(r"if\s*\(controlsWasSelected\s*&&\s*!controlsActive\)\s*showTab\(0\)", source)


def test_backend_refresh_restores_device_rendering_and_controls_visibility():
    source = _function_source("fetchDevices", asynchronous=True)
    show_tab_source = _function_source("showTab")

    assert re.search(r"fetch\(\s*['\"]/api/devices['\"]\s*\)", source)
    assert re.search(
        r"const\s+devices\s*=\s*await\s+response\.json\(\).*?"
        r"renderDevices\(devices\).*?updateControlsVisibility\(devices\)",
        source,
        re.DOTALL,
    )
    assert "fetchDevices()" not in show_tab_source
    assert not re.search(r"// Init\s*fetchDevices\(\);", INDEX)


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
