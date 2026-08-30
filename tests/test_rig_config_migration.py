import json

import pytest

from backend.rig_config import load, migrate_legacy, save, validate
from backend.state_store import StateStore


CIRCUMSTANCES = {
    "_date": "2027-08-02",
    "_circumstances_location": {
        "latitude": 43.6,
        "longitude": 1.44,
        "altitude_m": 150,
    },
    "C1": "2027-08-02T08:00:00Z",
    "C2": "2027-08-02T09:00:00Z",
    "TMAX": "2027-08-02T09:01:00Z",
    "C3": "2027-08-02T09:02:00Z",
    "C4": "2027-08-02T10:00:00Z",
}

CAPTURE = {
    "schema_version": 2,
    "kind": "capture_execution",
    "name": "migration_test",
    "exposure_correction": {"atmospheric_attenuation_enabled": False},
    "phases": {
        "partial": {"enabled": True, "interval_s": 60},
        "diamond_ring": {"enabled": True, "interval_s": 3},
        "totality": {"enabled": True, "interval_s": 0},
    },
}


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _migration(tmp_path, *, camera=("sony", True), mount=("none", False),
               focuser=("none", False)):
    configs_dir = tmp_path / "configs"
    _write_json(configs_dir / "circumstances" / "eclipse.json", CIRCUMSTANCES)
    _write_json(configs_dir / "capture" / "capture.json", CAPTURE)

    store = StateStore(tmp_path / "state.json")
    store.set(
        "devices",
        {
            "camera": {"plugin": camera[0], "active": camera[1]},
            "gps": {"plugin": "none", "active": False},
            "mount": {"plugin": mount[0], "active": mount[1]},
            "focuser": {"plugin": focuser[0], "active": focuser[1]},
        },
    )
    store.set(
        "circumstances",
        {"loaded": True, "active_file": "eclipse.json", "meta": {}},
    )
    store.set(
        "capture",
        {"loaded": True, "active_file": "capture.json", "meta": {}},
    )
    store.set("camera_config_file", "capture.json")
    return migrate_legacy(store, configs_dir), store, configs_dir


def test_camera_only_creates_enabled_valid_rig(tmp_path):
    config, _, _ = _migration(tmp_path)

    rig = config["rigs"][0]
    assert rig["enabled"] is True
    assert rig["devices"]["camera"]["backend"] == "sony"
    assert rig["devices"]["mount"] is None
    assert rig["devices"]["focuser"] is None
    assert validate(config) is None


def test_active_camera_mount_and_focuser_are_migrated(tmp_path):
    config, _, _ = _migration(
        tmp_path, mount=("onstep", True), focuser=("zwo_eaf", True)
    )

    devices = config["rigs"][0]["devices"]
    assert devices["camera"]["backend"] == "sony"
    assert devices["mount"]["backend"] == "onstep"
    assert devices["focuser"]["backend"] == "zwo_eaf"
    assert validate(config) is None


def test_missing_camera_disables_rig(tmp_path):
    config, _, _ = _migration(tmp_path, camera=("none", False))

    assert config["rigs"][0]["enabled"] is False


def test_eclipse_circumstances_date_and_site_are_preserved(tmp_path):
    config, _, _ = _migration(tmp_path)

    assert config["eclipse"] == {
        "date": CIRCUMSTANCES["_date"],
        "reference_site": {"lat": 43.6, "lon": 1.44, "alt_m": 150},
        "circumstances": {
            key: CIRCUMSTANCES[key] for key in ("C1", "C2", "TMAX", "C3", "C4")
        },
    }


def test_sequence_common_occurs_once_and_contains_expected_phases(tmp_path):
    config, _, _ = _migration(tmp_path)

    assert list(config["sequence"]) == ["common"]
    assert set(config["sequence"]["common"]["phases"]) == {
        "partial",
        "diamond_ring",
        "totality",
    }
    assert all("sequence" not in rig for rig in config["rigs"])


def test_migrated_config_contains_no_usb_bus_or_device_fields(tmp_path):
    config, _, _ = _migration(
        tmp_path, mount=("onstep", True), focuser=("zwo_eaf", True)
    )

    def assert_no_usb_fields(value):
        if isinstance(value, dict):
            assert "usb:bus" not in value
            assert "usb:device" not in value
            for child in value.values():
                assert_no_usb_fields(child)
        elif isinstance(value, list):
            for child in value:
                assert_no_usb_fields(child)

    assert_no_usb_fields(config)


def test_migration_does_not_invent_device_identity(tmp_path):
    config, _, _ = _migration(
        tmp_path, mount=("onstep", True), focuser=("zwo_eaf", True)
    )

    for device in config["rigs"][0]["devices"].values():
        if device is not None:
            for field in ("serial", "manufacturer", "model"):
                assert device.get(field) is None


def test_migration_is_idempotent(tmp_path):
    first, store, configs_dir = _migration(tmp_path)

    assert migrate_legacy(store, configs_dir) == first


def test_migrated_object_is_valid_schema_v2(tmp_path):
    config, _, _ = _migration(
        tmp_path, mount=("onstep", True), focuser=("zwo_eaf", True)
    )

    assert config["schema_version"] == 2
    assert validate(config) is None


def _minimal_config():
    return {
        "schema_version": 2,
        "eclipse": {
            "date": "2027-08-02",
            "reference_site": {
                "lat": 43.6,
                "lon": 1.44,
                "alt_m": 150,
            },
            "circumstances": {
                "C1": "2027-08-02T08:00:00Z",
                "C2": "2027-08-02T09:00:00Z",
                "TMAX": "2027-08-02T09:01:00Z",
                "C3": "2027-08-02T09:02:00Z",
                "C4": "2027-08-02T10:00:00Z",
            },
        },
        "sequence": {"common": {}},
        "rigs": [
            {
                "rig_id": 1,
                "enabled": True,
                "name": "Primary rig",
                "devices": {
                    "camera": {},
                    "mount": {},
                    "focuser": {},
                },
                "optics": {},
                "photo": {},
            }
        ],
    }


def test_minimal_v2_config_validates_and_round_trips(tmp_path):
    config = _minimal_config()

    assert validate(config) is None

    path = tmp_path / "rig-config.json"
    save(path, config)

    loaded = load(path)

    assert loaded["rigs"][0]["optics"] == {
        "focal_length_mm": None,
    }
    assert loaded["rigs"][0]["photo"] == {
        "atmos_enabled": False,
        "anti_trailing_enabled": False,
        "mechanical_vibration_enabled": False,
        "motion_tolerance_px": 1.0,
        "iso_compensation_enabled": True,
        "iso_max": 6400,
    }


def test_load_fills_missing_defaults_without_overwriting_existing_values(tmp_path):
    config = _minimal_config()
    config["sequence"]["common"]["exposure_correction"] = {
        "atmospheric_attenuation_enabled": True,
    }
    config["rigs"][0]["optics"] = {
        "focal_length_mm": 430,
        "extension": "preserved",
    }
    config["rigs"][0]["photo"] = {
        "anti_trailing_enabled": True,
        "iso_max": 1600,
        "extension": "preserved",
    }

    path = tmp_path / "rig-config.json"
    save(path, config)

    loaded = load(path)
    rig = loaded["rigs"][0]

    assert rig["optics"] == {
        "focal_length_mm": 430,
        "extension": "preserved",
    }
    assert rig["photo"] == {
        "anti_trailing_enabled": True,
        "iso_max": 1600,
        "extension": "preserved",
        "atmos_enabled": True,
        "mechanical_vibration_enabled": False,
        "motion_tolerance_px": 1.0,
        "iso_compensation_enabled": True,
    }


def test_validate_accepts_null_camera_config_before_trigger_execution():
    config = _minimal_config()
    config["rigs"][0]["devices"]["camera"] = None

    assert validate(config) is None
