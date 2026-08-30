import json
import sys
from copy import deepcopy
from types import ModuleType

import pytest


pytest.importorskip("flask")
pytest.importorskip("flask_socketio")
sys.modules.setdefault("gphoto2", ModuleType("gphoto2"))

import backend.rig_runtime as rig_runtime
from backend.rig_config import migrate_legacy
from backend.state_store import StateStore
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


def _normalized_rig(rig, *, atmos_enabled=False):
    result = deepcopy(rig)
    result.setdefault("optics", {}).setdefault("focal_length_mm", None)
    photo = result.setdefault("photo", {})
    photo.setdefault("atmos_enabled", atmos_enabled)
    photo.setdefault("anti_trailing_enabled", False)
    photo.setdefault("motion_tolerance_px", 1.0)
    photo.setdefault("iso_compensation_enabled", True)
    photo.setdefault("iso_max", 6400)
    return result


def test_legacy_migration_initializes_photo_flags(tmp_path):
    configs_dir = tmp_path / "configs"
    circumstances_dir = configs_dir / "circumstances"
    capture_dir = configs_dir / "capture"
    circumstances_dir.mkdir(parents=True)
    capture_dir.mkdir(parents=True)
    circumstances_dir.joinpath("eclipse.json").write_text(json.dumps({
        "_date": "2026-08-12",
        "_circumstances_location": {
            "latitude": 44.0,
            "longitude": 2.0,
            "altitude_m": 120.0,
        },
        "C1": "16:00:00",
        "C2": "17:00:00",
        "TMAX": "17:01:00",
        "C3": "17:02:00",
        "C4": "18:00:00",
    }), encoding="utf-8")
    capture_dir.joinpath("capture.json").write_text(json.dumps({
        "exposure_correction": {
            "atmospheric_attenuation_enabled": True,
        },
    }), encoding="utf-8")

    state_store = StateStore(tmp_path / "state.json")
    state_store.set("circumstances", {"active_file": "eclipse.json"})
    state_store.set("camera_config_file", "capture.json")

    migrated = migrate_legacy(state_store, configs_dir)

    assert migrated["rigs"][0]["photo"] == {
        "atmos_enabled": True,
        "anti_trailing_enabled": False,
        "motion_tolerance_px": 1.0,
        "iso_compensation_enabled": True,
        "iso_max": 6400,
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
    assert saved["rigs"][1:] == [
        _normalized_rig(rig)
        for rig in original["rigs"][1:]
    ]
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


def test_photo_get_returns_four_persisted_rig_configs(photo_api):
    client, _config_path, original, _reloads, _emitted = photo_api

    response = client.get("/api/rigs/photo")

    assert response.status_code == 200
    rigs = response.get_json()["rigs"]
    assert [rig["rig_id"] for rig in rigs] == [1, 2, 3, 4]
    expected_1 = _normalized_rig(original["rigs"][0])
    expected_4 = _normalized_rig(original["rigs"][3])

    assert rigs[0]["optics"] == expected_1["optics"]
    assert rigs[0]["photo"] == expected_1["photo"]
    assert rigs[3]["optics"] == expected_4["optics"]
    assert rigs[3]["photo"] == expected_4["photo"]
