import json
import sys
from datetime import datetime, timedelta, timezone
from types import ModuleType

import pytest

from backend import devices as device_helpers
from backend.state_store import StateStore


pytest.importorskip("flask")
pytest.importorskip("flask_socketio")
sys.modules.setdefault("gphoto2", ModuleType("gphoto2"))

import flask_app.app as flask_module


CATEGORIES = ("camera", "gps", "focuser", "mount")
T0 = datetime(2026, 8, 19, 10, 0, tzinfo=timezone.utc)


class _ControlledDatetime(datetime):
    current = T0

    @classmethod
    def now(cls, tz=None):
        if tz is None:
            return cls.current.replace(tzinfo=None)
        return cls.current.astimezone(tz)


def _selections(updated_at="2026-08-22T10:00:00+00:00"):
    devices = {
        name: {"plugin": f"{name}-plugin", "active": True}
        for name in CATEGORIES
    }
    devices["updated_at"] = updated_at
    return devices


def _detection(suggested="detected-plugin"):
    return {
        name: {
            "detected": True,
            "detected_info": [name],
            "detected_model": None,
            "suggested_plugin": suggested,
        }
        for name in CATEGORIES
    }


@pytest.fixture
def devices_api(tmp_path, monkeypatch):
    state_file = tmp_path / "state.json"
    state_store = StateStore(state_file)
    monkeypatch.setattr(flask_module, "_state_store", state_store)
    monkeypatch.setattr(flask_module, "_state", state_store.data)
    monkeypatch.setattr(flask_module, "_state_lock", state_store.lock)
    with flask_module._device_detection_lock:
        flask_module._device_detection_cache.clear()
    return flask_module.app.test_client(), state_store, state_file


def test_devices_get_merges_detection_without_persisting(devices_api, monkeypatch):
    client, state_store, state_file = devices_api
    persisted = _selections()
    state_store.update_section("devices", persisted, persist=True)
    disk_before = state_file.read_text(encoding="utf-8")
    with flask_module._device_detection_lock:
        flask_module._device_detection_cache.update(_detection())
    monkeypatch.setattr(flask_module, "ttl_expired", lambda _value: False)
    monkeypatch.setattr(
        flask_module, "detect_all", lambda _timeouts: pytest.fail("unexpected scan")
    )

    response = client.get("/api/devices")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["camera"]["plugin"] == "camera-plugin"
    assert payload["camera"]["suggested_plugin"] == "detected-plugin"
    assert payload["updated_at"] == persisted["updated_at"]
    assert state_file.read_text(encoding="utf-8") == disk_before
    assert state_store.snapshot("devices") == persisted


def test_devices_get_detects_missing_selection_without_renewing_ttl(
    devices_api, monkeypatch
):
    client, state_store, state_file = devices_api
    persisted = _selections()
    persisted["gps"] = {"plugin": "none", "active": False}
    state_store.update_section("devices", persisted, persist=True)
    disk_before = state_file.read_text(encoding="utf-8")
    calls = []
    monkeypatch.setattr(flask_module, "ttl_expired", lambda _value: False)
    monkeypatch.setattr(
        flask_module,
        "detect_all",
        lambda timeouts: calls.append(timeouts) or _detection("gpsd"),
    )

    response = client.get("/api/devices")

    assert response.status_code == 200
    assert response.get_json()["gps"]["suggested_plugin"] == "gpsd"
    assert calls == [flask_module._DEVICE_DETECTION_TIMEOUTS]
    assert state_store.snapshot("devices")["updated_at"] == persisted["updated_at"]
    assert state_file.read_text(encoding="utf-8") == disk_before


def test_devices_get_detects_expired_selection_without_renewing_ttl(
    devices_api, monkeypatch
):
    client, state_store, state_file = devices_api
    persisted = _selections(T0.isoformat())
    state_store.update_section("devices", persisted, persist=True)
    disk_before = state_file.read_text(encoding="utf-8")
    monkeypatch.setattr(
        flask_module,
        "ttl_expired",
        lambda updated_at: device_helpers.ttl_expired(
            updated_at, T0 + timedelta(hours=72, seconds=1)
        ),
    )
    monkeypatch.setattr(flask_module, "detect_all", lambda _timeouts: _detection())

    response = client.get("/api/devices")

    assert response.status_code == 200
    assert state_store.snapshot("devices")["updated_at"] == T0.isoformat()
    assert state_file.read_text(encoding="utf-8") == disk_before


def test_devices_post_updates_only_provided_categories(devices_api, monkeypatch):
    client, state_store, state_file = devices_api
    persisted = _selections("2026-08-20T10:00:00+00:00")
    state_store.update_section("devices", persisted, persist=True)
    _ControlledDatetime.current = datetime(2026, 8, 22, 15, 30, tzinfo=timezone.utc)
    monkeypatch.setattr(flask_module, "datetime", _ControlledDatetime)

    response = client.post(
        "/api/devices",
        json={"camera": "none", "gps": {"plugin": "gpsd", "active": False}},
    )

    assert response.status_code == 200
    saved = state_store.snapshot("devices")
    assert saved["camera"] == {"plugin": "none", "active": False}
    assert saved["gps"] == {"plugin": "gpsd", "active": True}
    assert saved["focuser"] == persisted["focuser"]
    assert saved["mount"] == persisted["mount"]
    assert saved["updated_at"] == _ControlledDatetime.current.isoformat()
    assert json.loads(state_file.read_text(encoding="utf-8"))["devices"] == saved


def test_devices_detect_is_ephemeral_and_does_not_renew_ttl(
    devices_api, monkeypatch
):
    client, state_store, state_file = devices_api
    persisted = _selections()
    state_store.update_section("devices", persisted, persist=True)
    disk_before = state_file.read_text(encoding="utf-8")
    monkeypatch.setattr(flask_module, "detect_all", lambda _timeouts: _detection())

    response = client.post("/api/devices/detect")

    assert response.status_code == 200
    assert response.get_json()["mount"]["suggested_plugin"] == "detected-plugin"
    assert state_store.snapshot("devices") == persisted
    assert state_file.read_text(encoding="utf-8") == disk_before


def test_status_includes_only_persisted_devices_without_detection(
    devices_api, monkeypatch
):
    client, state_store, state_file = devices_api
    persisted = _selections()
    state_store.update_section("devices", persisted, persist=True)
    disk_before = state_file.read_text(encoding="utf-8")
    monkeypatch.setattr(flask_module, "_get_camera_status", lambda: {})
    monkeypatch.setattr(flask_module, "_load_eclipse_json", lambda: None)

    response = client.get("/api/status")

    assert response.status_code == 200
    assert response.get_json()["devices"] == persisted
    assert state_store.snapshot("devices") == persisted
    assert state_file.read_text(encoding="utf-8") == disk_before


def test_status_includes_ephemeral_devices_only_after_detect(
    devices_api, monkeypatch
):
    client, state_store, state_file = devices_api
    persisted = _selections()
    detected = _detection()
    state_store.update_section("devices", persisted, persist=True)
    disk_before = state_file.read_text(encoding="utf-8")
    monkeypatch.setattr(flask_module, "detect_all", lambda _timeouts: detected)
    monkeypatch.setattr(flask_module, "_get_camera_status", lambda: {})
    monkeypatch.setattr(flask_module, "_load_eclipse_json", lambda: None)

    detect_response = client.post("/api/devices/detect")
    response = client.get("/api/status")

    assert detect_response.status_code == 200
    assert response.status_code == 200
    expected = {
        name: {**persisted[name], **detected[name]}
        for name in CATEGORIES
    }
    expected["updated_at"] = persisted["updated_at"]
    assert response.get_json()["devices"] == expected
    assert state_store.snapshot("devices") == persisted
    assert state_store.snapshot("devices")["updated_at"] == persisted["updated_at"]
    assert state_file.read_text(encoding="utf-8") == disk_before
