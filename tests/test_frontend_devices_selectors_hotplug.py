import re
from pathlib import Path

import pytest


INDEX_HTML = (
    Path(__file__).resolve().parents[1] / "flask_app" / "templates" / "index.html"
).read_text(encoding="utf-8")


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


def test_devices_assigned_to_another_rig_are_labelled_and_disabled(
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
    assert "label += ` — assigned to RIG ${assignedRig}`" in renderer
    assert re.search(
        r"const\s+disabled\s*=\s*\(.*?"
        r"assignedRig\s*&&\s*assignedRig\s*!==\s*rigId.*?"
        r"\)",
        renderer,
        flags=re.DOTALL,
    )
    assert "${disabled ? ' disabled' : ''}" in renderer


def test_refresh_posts_once_then_rerenders_with_response(mocked_backend_responses):
    assert mocked_backend_responses["refreshed_inventory"]["camera"][0]["serial"] == "D850-0002"

    refresh = _function("refreshRigDevices", async_function=True)
    assert len(re.findall(r"fetch\('/api/rigs/devices/refresh'", refresh)) == 1
    assert re.search(
        r"fetch\('/api/rigs/devices/refresh',\s*\{method:\s*'POST'\}\)", refresh
    )
    assert re.search(
        r"const\s+inventory\s*=\s*await\s+response\.json\(\).*?"
        r"await\s+loadRigDevices\(inventory\)",
        refresh,
        flags=re.DOTALL,
    )
    loader = _function("loadRigDevices", async_function=True)
    assert "if (!inventoryOverride) requests.push(fetch('/api/rigs/devices/inventory'))" in loader
    assert "renderRigDevices(payload, inventory)" in loader


def test_devices_tab_does_not_refresh_or_poll():
    show_tab = _function("showTab")
    assert "loadRigDevices" not in show_tab
    assert "/api/rigs/devices/refresh" not in show_tab

    devices_logic = re.search(
        r"const\s+DEFAULT_RIGS\b(?P<body>.*?)function\s+updateControlsVisibility",
        INDEX_HTML,
        flags=re.DOTALL,
    )
    assert devices_logic, "Devices selector logic is missing"
    assert "setInterval" not in devices_logic.group("body")


def test_non_pilotable_camera_is_visible_but_disabled():
    renderer = _function("renderRigDevices")

    assert "choice.pilotable === false" in renderer
    assert " — not controllable" in renderer
    assert re.search(
        r"choice\.pilotable\s*===\s*false.*?' disabled'",
        renderer,
        flags=re.DOTALL,
    )
