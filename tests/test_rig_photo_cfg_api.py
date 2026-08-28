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
            "enabled": False,
            "devices": {"camera": None, "mount": None, "focuser": None},
            "optics": {"focal_length_mm": rig_id * 100, "keep": rig_id},
            "photo": {"format": "RAW", "keep": rig_id},
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
        "sequence": {"common": {}},
        "rigs": rigs,
    }


@pytest.fixture
def photo_api(tmp_path, monkeypatch):
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


def test_photo_patch_persists_and_preserves_other_rigs(photo_api):
    client, config_path, original, reloads, emitted = photo_api
    response = client.post("/api/rigs/photo", json={"rigs": [{
        "rig_id": 1,
        "photo": {
            "anti_trailing_enabled": False,
            "iso_max": 1600,
            "motion_tolerance_px": 3,
        },
        "optics": {"focal_length_mm": 500},
    }]})

    assert response.status_code == 200
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["rigs"][0]["photo"]["anti_trailing_enabled"] is False
    assert saved["rigs"][0]["photo"]["iso_max"] == 1600
    assert saved["rigs"][0]["photo"]["motion_tolerance_px"] == 3
    assert saved["rigs"][0]["optics"]["focal_length_mm"] == 500
    assert saved["rigs"][0]["photo"]["format"] == "RAW"
    assert saved["rigs"][0]["optics"]["keep"] == 1
    assert saved["rigs"][1:] == original["rigs"][1:]
    assert reloads == [saved]
    assert [item[0] for item in emitted] == ["status_update"]


@pytest.mark.parametrize(
    "section,field,value",
    [
        ("optics", "focal_length_mm", 0),
        ("photo", "iso_max", 0),
        ("photo", "motion_tolerance_px", -1),
    ],
)
def test_invalid_positive_values_do_not_modify_file(
    photo_api, section, field, value
):
    client, config_path, _original, reloads, emitted = photo_api
    before = config_path.read_bytes()

    response = client.post(
        "/api/rigs/photo",
        json={"rigs": [{"rig_id": 1, section: {field: value}}]},
    )

    assert response.status_code == 400
    assert field in response.get_json()["error"]
    assert config_path.read_bytes() == before
    assert reloads == []
    assert emitted == []


def test_partial_atmos_patch_is_merged(photo_api):
    client, config_path, _original, _reloads, _emitted = photo_api

    response = client.post(
        "/api/rigs/photo",
        json={"rigs": [{"rig_id": 1, "photo": {"atmos_enabled": True}}]},
    )

    assert response.status_code == 200
    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["rigs"][0]["photo"]["atmos_enabled"] is True
    assert saved["rigs"][0]["photo"]["format"] == "RAW"
