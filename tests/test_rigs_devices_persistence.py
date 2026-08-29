import json
import sys
from copy import deepcopy
from types import ModuleType

import pytest


pytest.importorskip("flask")
pytest.importorskip("flask_socketio")
sys.modules.setdefault("gphoto2", ModuleType("gphoto2"))

import backend.rig_runtime as rig_runtime
import flask_app.app as flask_module


def _configuration():
    rigs = []
    for rig_id in range(1, 5):
        rigs.append({
            "rig_id": rig_id,
            "name": f"RIG {rig_id}",
            "enabled": rig_id == 1,
            "devices": {
                "camera": {
                    "backend": "gphoto2",
                    "serial": f"CAM-{rig_id}",
                },
                "mount": {"backend": "indi", "serial": f"MOUNT-{rig_id}"},
                "focuser": {"backend": "indi", "serial": f"FOCUSER-{rig_id}"},
            },
            "optics": {"focal_length_mm": rig_id * 100},
            "photo": {"format": "RAW", "iso": rig_id * 100},
            "rig_extension": {"keep": rig_id},
        })
    return {
        "schema_version": 2,
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
        "rigs": rigs,
        "top_level_extension": {"keep": True},
    }


@pytest.fixture
def persistence_api(tmp_path, monkeypatch):
    original = _configuration()
    config_path = tmp_path / "configs" / "rig" / "default.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(json.dumps(original, indent=2) + "\n", encoding="utf-8")

    monkeypatch.setattr(flask_module, "TRIGGER_DIR", tmp_path)
    monkeypatch.setattr(rig_runtime, "TRIGGER_DIR", tmp_path)
    monkeypatch.setattr(rig_runtime, "_rig_manager", None)

    reloads = []

    def record_reload(config):
        reloads.append(deepcopy(config))
        return rig_runtime.reload_rig_manager(config)

    monkeypatch.setattr(flask_module, "reload_rig_manager", record_reload)
    emitted = []
    monkeypatch.setattr(
        flask_module.socketio,
        "emit",
        lambda event, payload, **kwargs: emitted.append((event, payload, kwargs)),
    )
    return flask_module.app.test_client(), config_path, original, reloads, emitted


def test_single_rig_patch_merges_and_preserves_unrelated_content(persistence_api):
    client, config_path, original, reloads, emitted = persistence_api

    response = client.post(
        "/api/rigs/devices",
        json={
            "rigs": [{
                "rig_id": 1,
                "name": "Patched RIG",
                "devices": {
                    "camera": {"backend": "gphoto2", "serial": "CAM-NEW"}
                },
            }]
        },
    )

    assert response.status_code == 200
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["rigs"][0]["name"] == "Patched RIG"
    assert saved["rigs"][0]["devices"]["camera"]["serial"] == "CAM-NEW"
    assert (
        saved["rigs"][0]["devices"]["mount"]
        == original["rigs"][0]["devices"]["mount"]
    )
    assert (
        saved["rigs"][0]["devices"]["focuser"]
        == original["rigs"][0]["devices"]["focuser"]
    )
    assert saved["rigs"][0]["optics"] == original["rigs"][0]["optics"]
    assert saved["rigs"][0]["photo"] == original["rigs"][0]["photo"]
    assert (
        saved["rigs"][0]["rig_extension"]
        == original["rigs"][0]["rig_extension"]
    )
    assert saved["rigs"][1:] == original["rigs"][1:]
    assert saved["eclipse"] == original["eclipse"]
    assert saved["sequence"] == original["sequence"]
    assert saved["top_level_extension"] == original["top_level_extension"]
    assert reloads == [saved]
    assert len(emitted) == 1
    assert emitted[0][0] == "status_update"
    assert emitted[0][1]["rigs"][0]["name"] == "Patched RIG"
    assert emitted[0][2] == {"namespace": "/"}


def test_mount_and_focuser_null_unbind_without_changing_camera(persistence_api):
    client, config_path, original, reloads, emitted = persistence_api

    response = client.post(
        "/api/rigs/devices",
        json={"rigs": [{"rig_id": 1, "devices": {"mount": None, "focuser": None}}]},
    )

    assert response.status_code == 200
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["rigs"][0]["devices"]["mount"] is None
    assert saved["rigs"][0]["devices"]["focuser"] is None
    assert (
        saved["rigs"][0]["devices"]["camera"]
        == original["rigs"][0]["devices"]["camera"]
    )
    assert reloads == [saved]
    assert [event for event, _payload, _kwargs in emitted] == ["status_update"]


def test_enabling_rig_without_camera_is_valid_before_trigger(persistence_api):
    client, config_path, _original, reloads, emitted = persistence_api

    response = client.post(
        "/api/rigs/devices",
        json={"rigs": [{"rig_id": 2, "enabled": True, "devices": {"camera": None}}]},
    )

    assert response.status_code == 200
    saved = __import__("json").loads(config_path.read_text(encoding="utf-8"))
    assert saved["rigs"][1]["enabled"] is True
    assert saved["rigs"][1]["devices"]["camera"] is None
    assert reloads == [saved]
    assert [event for event, _payload, _kwargs in emitted] == ["status_update"]


def test_duplicate_device_identity_returns_409_without_side_effects(persistence_api):
    client, config_path, _original, reloads, emitted = persistence_api
    before = config_path.read_bytes()

    response = client.post(
        "/api/rigs/devices",
        json={
            "rigs": [{
                "rig_id": 2,
                "devices": {"camera": {"backend": "gphoto2", "serial": "CAM-1"}},
            }]
        },
    )

    assert response.status_code == 409
    assert "duplicate device identity" in response.get_json()["error"]
    assert config_path.read_bytes() == before
    assert reloads == []
    assert emitted == []


@pytest.mark.parametrize(
    "field,value",
    [
        ("present", True),
        ("transport_locator", "usb:001,002"),
        ("busnum", 1),
        ("devnum", 2),
    ],
)
def test_runtime_inventory_fields_are_never_persisted(persistence_api, field, value):
    client, config_path, _original, reloads, emitted = persistence_api
    before = config_path.read_bytes()

    response = client.post(
        "/api/rigs/devices",
        json={
            "rigs": [{
                "rig_id": 1,
                "devices": {
                    "camera": {
                        "backend": "gphoto2",
                        "serial": "CAM-NEW",
                        field: value,
                    }
                },
            }]
        },
    )

    assert response.status_code == 400
    assert config_path.read_bytes() == before
    assert reloads == []
    assert emitted == []


def test_focuser_device_id_is_persisted_and_reloaded(persistence_api):
    client, config_path, *_ = persistence_api

    response = client.post(
        "/api/rigs/devices",
        json={
            "rigs": [
                {
                    "rig_id": 1,
                    "devices": {
                        "focuser": {
                            "backend": "zwo_eaf",
                            "manufacturer": "ZWO",
                            "model": "EAF",
                            "serial": None,
                            "device_id": "zwo_eaf:0",
                            "fallback_physical_path": None,
                        }
                    },
                }
            ]
        },
    )

    assert response.status_code == 200

    import json

    with config_path.open("r", encoding="utf-8") as stream:
        saved = json.load(stream)

    focuser = saved["rigs"][0]["devices"]["focuser"]

    assert focuser["backend"] == "zwo_eaf"
    assert focuser["device_id"] == "zwo_eaf:0"
    assert focuser["serial"] is None
