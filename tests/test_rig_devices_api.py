import sys
from types import ModuleType

import pytest

from backend.rig_manager import RigManager


pytest.importorskip("flask")
pytest.importorskip("flask_socketio")
sys.modules.setdefault("gphoto2", ModuleType("gphoto2"))

import flask_app.app as flask_module


def _rig_config():
    return {
        "schema_version": 2,
        "rigs": [
            {
                "rig_id": 1,
                "name": "Wide field",
                "enabled": False,
                "devices": {
                    "camera": {
                        "category": "camera",
                        "backend": "gphoto2",
                        "model": "Sony Alpha",
                        "serial": "CAMERA-0001",
                    }
                },
            },
            {
                "rig_id": 2,
                "name": "Telephoto",
                "enabled": False,
                "devices": {
                    "camera": {
                        "category": "camera",
                        "backend": "gphoto2",
                        "model": "Sony Alpha",
                        "serial": "CAMERA-ABSENT",
                    }
                },
            },
            {
                "rig_id": 3,
                "name": "Tracked",
                "enabled": True,
                "devices": {
                    "camera": {"backend": "simulated", "serial": "SIM-3"},
                    "mount": {
                        "category": "mount",
                        "backend": "indi",
                        "model": "EQ Mount",
                        "fallback_physical_path": "usb-port:1-2.3",
                    },
                },
            },
            {
                "rig_id": 4,
                "name": "Manual focus",
                "enabled": False,
                "devices": {
                    "focuser": {
                        "category": "focuser",
                        "backend": "indi",
                        "model": "EAF",
                    }
                },
            },
        ],
    }


def test_rig_devices_get_enriches_persisted_bindings_from_cached_inventory(
    monkeypatch,
):
    manager = RigManager.from_config(_rig_config())
    inventory = {
        "camera": [
            {
                "category": "camera",
                "backend": "gphoto2",
                "model": "Sony Alpha",
                "serial": "CAMERA-0001",
            }
        ],
        "mount": [
            {
                "category": "mount",
                "backend": "indi",
                "model": "EQ Mount",
                "fallback_physical_path": "usb-port:1-2.3",
            }
        ],
        "focuser": [
            {
                "category": "focuser",
                "backend": "indi",
                "model": "EAF",
                "serial": "FOCUSER-1",
            }
        ],
    }
    monkeypatch.setattr(flask_module, "get_rig_manager", lambda: manager)
    monkeypatch.setattr(flask_module, "get_cached_inventory", lambda: inventory)
    monkeypatch.setattr(
        flask_module,
        "refresh_inventory",
        lambda: pytest.fail("GET must not probe hardware"),
    )

    response = flask_module.app.test_client().get("/api/rigs/devices")

    assert response.status_code == 200
    payload = response.get_json()
    assert len(payload["rigs"]) == 4
    assert [
        (rig["rig_id"], rig["name"], rig["enabled"])
        for rig in payload["rigs"]
    ] == [
        (1, "Wide field", False),
        (2, "Telephoto", False),
        (3, "Tracked", True),
        (4, "Manual focus", False),
    ]
    assert all(
        set(rig["devices"]) == {"camera", "mount", "focuser"}
        for rig in payload["rigs"]
    )

    rigs = {rig["rig_id"]: rig for rig in payload["rigs"]}
    assert rigs[1]["devices"]["camera"]["present"] is True
    assert rigs[2]["devices"]["camera"]["present"] is False
    assert rigs[3]["devices"]["mount"]["present"] is True
    assert rigs[4]["devices"]["focuser"]["present"] is False
    assert rigs[1]["devices"]["mount"] is None

    configured_bindings = [
        binding
        for rig in payload["rigs"]
        for binding in rig["devices"].values()
        if binding is not None
    ]
    assert all(binding["display_label"] for binding in configured_bindings)
    assert all(
        entry["display_label"]
        for entries in payload["inventory"].values()
        for entry in entries
    )
    assert payload["identity_warnings"] == manager.identity_warnings
    assert payload["identity_warnings"] == [
        "RIG 3 mount: using fallback physical path as identity; "
        "prefer a stable serial"
    ]
