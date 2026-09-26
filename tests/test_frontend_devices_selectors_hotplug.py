from tests.frontend_source import frontend_source
import re
from pathlib import Path

import pytest


INDEX_HTML = frontend_source()


def _function(name, *, async_function=False):
    prefix = "async function" if async_function else "function"
    match = re.search(
        rf"{prefix}\s+{re.escape(name)}\([^)]*\)\s*\{{(?P<body>.*?)\n\}}",
        INDEX_HTML,
        flags=re.DOTALL,
    )
    assert match, f"{name}() is missing"
    return match.group("body")


@pytest.fixture
def mocked_backend_responses():
    d850_a = {
        "backend": "gphoto2",
        "serial": "D850-0001",
        "model": "Nikon D850",
        "display_label": "Nikon D850 · D850-0001",
        "present": True,
    }
    d850_b = {
        "backend": "gphoto2",
        "serial": "D850-0002",
        "model": "Nikon D850",
        "display_label": "Nikon D850 · D850-0002",
        "present": True,
    }
    missing = {
        "backend": "gphoto2",
        "serial": "D850-ABSENT",
        "model": "Nikon D850",
        "display_label": "Nikon D850 · D850-ABSENT",
        "present": False,
    }
    return {
        "rigs": {
            "rigs": [
                {"rig_id": 1, "enabled": True, "devices": {"camera": d850_a}},
                {"rig_id": 2, "enabled": True, "devices": {"camera": d850_b}},
                {"rig_id": 3, "enabled": True, "devices": {"camera": missing}},
                {"rig_id": 4, "enabled": False, "devices": {"camera": None}},
            ]
        },
        "inventory": {"camera": [d850_a, d850_b], "mount": [], "focuser": []},
        "refreshed_inventory": {
            "camera": [d850_b, d850_a, missing],
            "mount": [],
            "focuser": [],
        },
    }


def test_two_identical_d850_are_separate_selectable_options(mocked_backend_responses):
    cameras = mocked_backend_responses["inventory"]["camera"]
    assert cameras[0]["model"] == cameras[1]["model"] == "Nikon D850"
    assert cameras[0]["serial"] != cameras[1]["serial"]

    identity = _function("rigDeviceIdentity")
    renderer = _function("renderRigDevices")
    assert "`serial:${device.serial}`" in identity
    assert re.search(r"choices\s*=\s*\[\.\.\.\(inventory\[category\]\s*\|\|\s*\[\]\)\]", renderer)
    assert "choices.forEach(choice =>" in renderer
    assert "encodedRigBinding(optionBinding)" in renderer
    assert "rigDeviceDisplayLabel(category, choice)" in renderer
    display_label = _function("rigDeviceDisplayLabel")
    assert "device.display_label" in display_label
    assert "device.model" in display_label
    assert "device.serial" in display_label


def test_absent_binding_is_kept_and_marked_not_detected(mocked_backend_responses):
    missing = mocked_backend_responses["rigs"]["rigs"][2]["devices"]["camera"]
    assert missing["present"] is False

    renderer = _function("renderRigDevices")
    assert re.search(
        r"if\s*\(current\s*&&\s*!choices\.some\(.*?\)\)\s*\{\s*choices\.push\(current\)",
        renderer,
        flags=re.DOTALL,
    )
    assert re.search(
        r"if\s*\(isCurrent\s*&&\s*current\.present\s*===\s*false\)\s*"
        r"label\s*\+=\s*['\"]\s*—\s*expected / not detected['\"]",
        renderer,
    )


def test_devices_assigned_to_another_rig_are_hidden(
    mocked_backend_responses,
):
    assigned_serials = {
        rig["devices"]["camera"]["serial"]
        for rig in mocked_backend_responses["rigs"]["rigs"][:2]
    }
    assert assigned_serials == {"D850-0001", "D850-0002"}

    renderer = _function("renderRigDevices")
    assert re.search(
        r"assignments\[`\$\{category\}:\$\{identity\}`\]\s*=\s*Number\(rig\.rig_id\)",
        renderer,
    )
    assert "if (assignedRig && assignedRig !== rigId) return;" in renderer
    assert "assigned to RIG" not in renderer

def test_refresh_posts_once_then_rerenders_with_response(mocked_backend_responses):
    assert mocked_backend_responses["refreshed_inventory"]["camera"][0]["serial"] == "D850-0002"

    refresh = _function("refreshRigDevices", async_function=True)
    assert len(re.findall(r"fetch\('/api/rigs/devices/refresh'", refresh)) == 1
    assert re.search(
        r"fetch\('/api/rigs/devices/refresh',\s*\{method:\s*'POST'\}\)", refresh
    )
    assert len(re.findall(r"fetch\('/api/devices/detect'", refresh)) == 1
    assert re.search(
        r"const\s+inventory\s*=\s*await\s+inventoryResponse\.json\(\).*?"
        r"await\s+loadRigDevices\(inventory\)",
        refresh,
        flags=re.DOTALL,
    )
    assert re.search(
        r"const\s+devices\s*=\s*await\s+devicesResponse\.json\(\).*?"
        r"renderDevices\(devices\).*?"
        r"updateControlsVisibility\(devices\)",
        refresh,
        flags=re.DOTALL,
    )
    loader = _function("loadRigDevices", async_function=True)
    assert "if (!inventoryOverride) requests.push(fetch('/api/rigs/devices/inventory'))" in loader
    assert "renderRigDevices(payload, inventory)" in loader


def test_devices_poll_usb_presence_every_second_and_refresh_only_on_change():
    assert "const DEVICE_AUTO_REFRESH_INTERVAL_MS = 1000;" in INDEX_HTML
    assert "let deviceAutoRefreshInFlight = false;" in INDEX_HTML
    assert "let deviceUsbPresenceSignature = null;" in INDEX_HTML
    assert "let deviceUsbPresencePollInFlight = false;" in INDEX_HTML
    assert "let deviceAutoRefreshTimer = null;" in INDEX_HTML

    refresh = _function("refreshRigDevices", async_function=True)
    assert "if (deviceAutoRefreshInFlight) return;" in refresh
    assert "deviceAutoRefreshInFlight = true;" in refresh
    assert "deviceAutoRefreshInFlight = false;" in refresh

    poll = _function("pollDeviceUsbPresence", async_function=True)
    assert "/api/rigs/devices/usb-presence" in poll
    assert "deviceUsbPresencePollInFlight || deviceAutoRefreshInFlight" in poll
    assert "if (signature === deviceUsbPresenceSignature) return;" in poll
    assert "await refreshRigDevices(true, false);" in poll

    auto_refresh = _function("startDeviceAutoRefresh")
    assert "if (deviceAutoRefreshTimer !== null) return;" in auto_refresh
    assert "pollDeviceUsbPresence();" in auto_refresh
    assert "deviceAutoRefreshTimer = setInterval" in auto_refresh
    assert "DEVICE_AUTO_REFRESH_INTERVAL_MS" in auto_refresh
    assert "setInterval" in auto_refresh
    assert "refreshRigDevices(true, false)" not in auto_refresh

    assert "loadRigDevices();\nstartDeviceAutoRefresh();" in INDEX_HTML



def test_rig_render_does_not_emit_controls_change_or_restart_status_polling():
    controls = _function("renderControlsRigSelection")
    assert "controlsrigchange" not in controls

    selector = _function("selectControlsRig")
    assert "renderControlsRigSelection();" in selector
    assert "document.dispatchEvent(new CustomEvent('controlsrigchange'));" in selector

    assert re.search(
        r"document\.addEventListener\('controlsrigchange',\s*\(\)\s*=>\s*\{"
        r".*?clearTimeout\(pollTimer\);"
        r".*?refreshFocuser\(\);",
        INDEX_HTML,
        flags=re.DOTALL,
    )




def test_idle_ui_does_not_poll_mount_focuser_or_global_status():
    # Idle hardware state is pushed over Socket.IO.  HTTP status polling is
    # reserved for active motion and explicit user actions.
    assert "schedulePoll(data.moving === true ? 400 : 1500)" not in INDEX_HTML
    assert "scheduleMountRefresh(homing ? 400 : 1500)" not in INDEX_HTML
    assert "socket.on('status_update', refreshMount)" not in INDEX_HTML
    assert "setInterval(loadCameraStatus, 10000)" not in INDEX_HTML
    assert "setInterval(loadMaintenanceStatus,2000)" not in INDEX_HTML

    assert "if (absoluteMotion || jogPolling) schedulePoll(250);" in INDEX_HTML
    assert "let jogPolling = false;" in INDEX_HTML
    assert "jogPolling = true;" in INDEX_HTML
    assert "jogPolling = false;" in INDEX_HTML
    assert "data.moving === true && absoluteMotion" not in INDEX_HTML
    assert "if (homing) scheduleMountRefresh(400);" in INDEX_HTML
    assert "socket.on('focuser_update', refreshFocuser);" in INDEX_HTML
    assert "socket.on('connect', refreshMount);" in INDEX_HTML
    assert "setTimeout(loadMaintenanceStatus, 250);" in INDEX_HTML


def test_non_pilotable_camera_is_visible_but_disabled():
    renderer = _function("renderRigDevices")

    assert "choice.pilotable === false" in renderer
    assert " — not controllable" in renderer
    assert re.search(
        r"choice\.pilotable\s*===\s*false.*?' disabled'",
        renderer,
        flags=re.DOTALL,
    )
