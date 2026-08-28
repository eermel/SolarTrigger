import json
import sys
from copy import deepcopy
from types import ModuleType

import pytest

from backend.rig_manager import RigManager


pytest.importorskip("flask")
pytest.importorskip("flask_socketio")
sys.modules.setdefault("gphoto2", ModuleType("gphoto2"))

import flask_app.app as flask_module
import backend.rig_runtime as rig_runtime


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


def _persisted_rig_config():
    config = _rig_config()
    config.update({
        "eclipse": {
            "date": "2026-08-12",
            "reference_site": {"lat": 44.0, "lon": 2.0, "alt_m": 120.0},
            "circumstances": {
                "C1": "16:00:00",
                "C2": "17:00:00",
                "TMAX": "17:01:00",
                "C3": "17:02:00",
                "C4": "18:00:00",
            },
        },
        "sequence": {"common": {"phases": {"partial": {"iso": 100}}}},
        "site_note": "preserve this top-level field",
    })
    for rig in config["rigs"]:
        rig["devices"].setdefault("camera", None)
        rig["devices"].setdefault("mount", None)
        rig["devices"].setdefault("focuser", None)
        rig["optics"] = {"focal_length_mm": rig["rig_id"] * 100}
        rig["photo"] = {"format": "RAW"}
    return config


@pytest.fixture
def persisted_rig_api(tmp_path, monkeypatch):
    config = _persisted_rig_config()
    config_path = tmp_path / "configs" / "rig" / "default.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")

    monkeypatch.setattr(flask_module, "TRIGGER_DIR", tmp_path)
    monkeypatch.setattr(rig_runtime, "TRIGGER_DIR", tmp_path)
    monkeypatch.setattr(rig_runtime, "_rig_manager", None)
    monkeypatch.setattr(flask_module, "get_cached_inventory", lambda: {
        "camera": [], "mount": [], "focuser": []
    })

    emitted = []
    monkeypatch.setattr(
        flask_module.socketio,
        "emit",
        lambda event, payload, **kwargs: emitted.append((event, payload, kwargs)),
    )
    reloads = []

    def reload_manager(candidate):
        reloads.append(deepcopy(candidate))
        return rig_runtime.reload_rig_manager(candidate)

    monkeypatch.setattr(flask_module, "reload_rig_manager", reload_manager)
    return flask_module.app.test_client(), config_path, config, emitted, reloads


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


def test_rig_devices_post_persists_single_rig_merge_and_reloads_manager(
    persisted_rig_api,
):
    client, config_path, original, emitted, reloads = persisted_rig_api
    original_mount = deepcopy(original["rigs"][0]["devices"]["mount"])

    response = client.post(
        "/api/rigs/devices",
        json={
            "rigs": [{
                "rig_id": 1,
                "name": "Updated wide field",
                "devices": {
                    "camera": {"backend": "gphoto2", "serial": "CAMERA-NEW"}
                },
            }]
        },
    )

    assert response.status_code == 200
    persisted = json.loads(config_path.read_text(encoding="utf-8"))
    assert persisted["rigs"][0]["name"] == "Updated wide field"
    assert persisted["rigs"][0]["devices"]["camera"]["serial"] == "CAMERA-NEW"
    assert persisted["rigs"][0]["devices"]["mount"] == original_mount
    assert persisted["rigs"][0]["optics"] == original["rigs"][0]["optics"]
    assert persisted["rigs"][0]["photo"] == original["rigs"][0]["photo"]
    assert persisted["rigs"][1:] == original["rigs"][1:]
    assert persisted["eclipse"] == original["eclipse"]
    assert persisted["sequence"] == original["sequence"]
    assert persisted["site_note"] == original["site_note"]
    assert reloads == [persisted]
    assert emitted[0][0] == "status_update"
    assert emitted[0][1]["rigs"][0]["name"] == "Updated wide field"
    assert emitted[0][2] == {"namespace": "/"}

    following_get = client.get("/api/rigs/devices")
    assert following_get.status_code == 200
    assert following_get.get_json()["rigs"][0]["name"] == "Updated wide field"


def test_rig_devices_post_null_unbinds_optional_device(persisted_rig_api):
    client, config_path, _original, _emitted, _reloads = persisted_rig_api

    response = client.post(
        "/api/rigs/devices",
        json={"rigs": [{"rig_id": 3, "devices": {"mount": None}}]},
    )

    assert response.status_code == 200
    persisted = json.loads(config_path.read_text(encoding="utf-8"))
    assert persisted["rigs"][2]["devices"]["mount"] is None
    assert persisted["rigs"][2]["devices"]["camera"]["serial"] == "SIM-3"


@pytest.mark.parametrize(
    "payload, expected_status",
    [
        ({"rigs": [{"rig_id": 3, "devices": {"camera": None}}]}, 400),
        ({"rigs": [{"rig_id": 1, "enabled": True, "devices": {"camera": None}}]}, 400),
        ({"rigs": [{"rig_id": 1, "enabled": True, "devices": {"camera": {"backend": "none"}}}]}, 400),
        ({"rigs": "not-an-array"}, 400),
        ({"rigs": [None]}, 400),
        ({"rigs": [{"rig_id": 5}]}, 400),
        ({"rigs": [{"rig_id": 1, "devices": []}]}, 400),
    ],
)
def test_rig_devices_post_invalid_payload_rolls_back(
    persisted_rig_api, payload, expected_status
):
    client, config_path, _original, emitted, reloads = persisted_rig_api
    before = config_path.read_bytes()

    response = client.post("/api/rigs/devices", json=payload)

    assert response.status_code == expected_status
    assert config_path.read_bytes() == before
    assert emitted == []
    assert reloads == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("present", True),
        ("transport_locator", "usb:001,002"),
        ("busnum", 1),
        ("devnum", 2),
    ],
)
def test_rig_devices_post_rejects_transient_fields_without_writing(
    persisted_rig_api, field, value
):
    client, config_path, _original, emitted, reloads = persisted_rig_api
    before = config_path.read_bytes()

    response = client.post(
        "/api/rigs/devices",
        json={
            "rigs": [{
                "rig_id": 1,
                "devices": {"camera": {"backend": "gphoto2", field: value}},
            }]
        },
    )

    assert response.status_code == 400
    assert config_path.read_bytes() == before
    assert emitted == []
    assert reloads == []


def test_rig_devices_post_duplicate_identity_returns_409_and_rolls_back(
    persisted_rig_api,
):
    client, config_path, _original, emitted, reloads = persisted_rig_api
    before = config_path.read_bytes()

    response = client.post(
        "/api/rigs/devices",
        json={
            "rigs": [{
                "rig_id": 2,
                "devices": {
                    "camera": {"backend": "gphoto2", "serial": "CAMERA-0001"}
                },
            }]
        },
    )

    assert response.status_code == 409
    assert "duplicate device identity" in response.get_json()["error"]
    assert config_path.read_bytes() == before
    assert emitted == []
    assert reloads == []
