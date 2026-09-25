from tests.frontend_source import frontend_source
from pathlib import Path


HTML = frontend_source()


def devices_panel():
    start = HTML.index('<div class="page active" id="devices-panel">')
    end = HTML.index('<!-- ═══════════════ PAGE 0', start)
    return HTML[start:end]


def test_device_discovery_waits_for_operator_refresh():
    init = HTML[HTML.rindex("// Init") :]
    assert "renderDevices({" in init
    assert "plugin: 'none'" in init
    assert "fetchDevices();" not in init
    assert "refreshRigDevices(true);" not in init


def test_devices_refresh_also_reload_gps_state():
    start = HTML.index("async function refreshRigDevices(silent = false)")
    end = HTML.index("// ── AUDIO", start)
    function = HTML[start:end]

    assert "/api/rigs/devices/refresh" in function
    assert "/api/devices/detect" in function
    assert "await fetchDevices();" not in function
    assert "await pollCameraCharacterization();" in function
    assert "await pollCameraValidation();" in function


def test_refresh_button_is_directly_below_gps_before_rigs():
    panel = devices_panel()

    gps = panel.index('id="devices-gps"')
    refresh = panel.index('id="devices-rescan"')
    rigs = panel.index('id="devices-rigs-row"')

    assert gps < refresh < rigs


def test_rig_activation_uses_switch_visual_style():
    panel = devices_panel()

    for rig_id in (2, 3, 4):
        assert f'id="rig-switch-{rig_id}"' in panel
        assert f'class="rig-switch"' in panel
        assert f'aria-label="RIG {rig_id} ON/OFF"' in panel

        assert f'<label for="rig-switch-{rig_id}">OFF</label>' not in panel
        assert f'<label for="rig-switch-{rig_id}">ON</label>' not in panel

def test_enabled_rig_has_green_border():
    assert ".rig-column.enabled" in HTML
    assert "border-color: var(--green)" in HTML


def test_rig_device_selectors_do_not_use_peripherique_label():
    start = HTML.index("function renderRigDevices(")
    end = HTML.index("async function loadRigDevices", start)
    renderer = HTML[start:end]

    assert ">Périphérique</label>" not in renderer
    assert "|| 'Périphérique'" not in renderer


def test_camera_selector_adds_serial_suffix():
    assert "function rigDeviceDisplayLabel(" in HTML
    assert "category === 'camera'" in HTML
    assert ".slice(-3)" in HTML


def test_rig_device_identity_uses_device_id_for_eaf_and_sdk_devices():
    start = HTML.index("function rigDeviceIdentity(device)")
    end = HTML.index("function persistedRigBinding", start)
    identity = HTML[start:end]

    assert "device.device_id" in identity
    assert "device.device_id.trim()" in identity
    assert "device_id:" in identity

    renderer_start = HTML.index("function renderRigDevices(")
    renderer_end = HTML.index("async function loadRigDevices", renderer_start)
    renderer = HTML[renderer_start:renderer_end]

    assert "const assignedRig = identity ? assignments" in renderer
    assert "(assignedRig && assignedRig !== rigId)" in renderer
    assert " disabled" in renderer


def test_external_altaz_virtual_mount_is_available():
    assert "External Alt-Az" in HTML
    assert "control: 'external'" in HTML
    assert "geometry: 'altaz'" in HTML


def test_external_altaz_does_not_show_non_pilotable_suffix():
    start = HTML.index("function renderRigDevices(")
    end = HTML.index("async function loadRigDevices", start)
    renderer = HTML[start:end]

    assert "choice.pilotable === false && !isExternalAltAz" in renderer


def test_external_altaz_is_selectable_without_mount_worker():
    start = HTML.index("function renderRigDevices(")
    end = HTML.index("async function loadRigDevices", start)
    renderer = HTML[start:end]

    assert "isExternalAltAz" in renderer
    assert "choice.pilotable === false && !isExternalAltAz" in renderer


def test_current_binding_and_inventory_binding_are_not_merged_only_by_identity():
    start = HTML.index("function renderRigDevices(")
    end = HTML.index("async function loadRigDevices", start)
    renderer = HTML[start:end]

    # Une même caméra peut garder son serial mais changer de backend/métadonnées.
    # L'entrée persistée et l'entrée inventaire doivent rester distinguables.
    assert (
        "encodedRigBinding(choice) === encodedRigBinding(current)"
        in renderer
    )


def test_all_rig_device_fields_are_rendered_immediately_at_startup():
    init = HTML[HTML.rindex("// Init") :]

    assert "renderRigDevices({" in init
    assert "rigs: DEFAULT_RIGS" in init
    assert "camera: []" in init
    assert "focuser: []" in init
    assert "mount: []" in init

    assert "refreshRigDevices(true);" not in init


def test_disabled_rig_remains_visible_and_configurable():
    assert ".rig-column:not(.enabled) .rig-body" not in HTML
    assert "pointer-events: none" not in HTML[
        HTML.index(".rig-column {") :
        HTML.index("/* ── CONTACTS TABLE", HTML.index(".rig-column {"))
    ]


def test_system_camera_status_is_event_driven_without_periodic_http_polling():
    assert "setInterval(pollCameraCharacterization" not in HTML
    assert "setInterval(pollCameraValidation" not in HTML
    assert "setInterval(refreshRecharacterizationCandidates" not in HTML
    assert "startCameraValidationPolling" not in HTML
    assert "socket.on('camera_characterization_status'" in HTML
    assert "socket.on('camera_validation_status'" in HTML


def test_operator_refresh_updates_characterization_and_validation_from_cache_once():
    start = HTML.index("async function refreshRigDevices(silent = false)")
    end = HTML.index("let cameraCharacterizationQuestion = null;", start)
    refresh = HTML[start:end]

    assert "fetch('/api/rigs/devices/refresh'" in refresh
    assert "await pollCameraCharacterization();" in refresh
    assert "await pollCameraValidation();" in refresh
