import sys
from datetime import datetime
from types import ModuleType

import pytest

from backend.generic_worker import BusyDeviceError
from backend.rig_manager import RigManager
from backend.state_store import StateStore


pytest.importorskip("flask")
pytest.importorskip("flask_socketio")
sys.modules.setdefault("gphoto2", ModuleType("gphoto2"))

import flask_app.app as flask_module


def _rig_config(*, rig_2_enabled=True):
    return {
        "schema_version": 2,
        "rigs": [
            {
                "rig_id": 1,
                "name": "Wide field",
                "enabled": True,
                "devices": {
                    "camera": {
                        "backend": "camera-backend-1",
                        "serial": "CAMERA-1",
                    }
                },
            },
            {
                "rig_id": 2,
                "name": "Telephoto",
                "enabled": rig_2_enabled,
                "devices": {
                    "camera": {
                        "backend": "camera-backend-2",
                        "serial": "CAMERA-2",
                    }
                },
            },
        ],
    }


class FakeCameraWorker:
    def __init__(self, result=None, error=None):
        self._result = result
        self._error = error
        self.sync_reference = None

    def probe_info(self):
        if self._error is not None:
            raise self._error
        return self._result

    def read_info(self):
        if self._error is not None:
            raise self._error
        return self._result

    def sync_datetime(self, reference):
        self.sync_reference = reference
        if self._error is not None:
            raise self._error
        return self._result


class FakeCameraWorkerRuntime:
    def __init__(self, workers):
        self.workers = workers
        self.reconciled_config = None

    def reconcile(self, config):
        self.reconciled_config = config

    def get_for_rig(self, rig_id):
        return self.workers.get(rig_id)


def _client(monkeypatch, config, workers):
    runtime = FakeCameraWorkerRuntime(workers)
    monkeypatch.setattr(
        flask_module, "get_rig_manager", lambda: RigManager.from_config(config)
    )
    monkeypatch.setattr(flask_module, "load_rig_configuration", lambda: config)
    monkeypatch.setattr(
        flask_module, "get_camera_worker_runtime", lambda **_kwargs: runtime
    )
    flask_module.app.config.update(TESTING=True)
    return flask_module.app.test_client(), runtime


def test_probe_is_scoped_to_requested_rig(monkeypatch):
    config = _rig_config()
    workers = {
        1: FakeCameraWorker({"model": "Sony ILCE-7M5", "battery": "81%"}),
        2: FakeCameraWorker({"model": "NIKON Z 8", "battery": "64%"}),
    }
    client, runtime = _client(monkeypatch, config, workers)

    rig_1 = client.post("/api/rigs/1/camera/probe")
    rig_2 = client.post("/api/rigs/2/camera/probe")

    assert rig_1.status_code == 200
    assert rig_1.get_json() == {
        "brand": "SONY",
        "model": "Sony ILCE-7M5",
        "battery": "81%",
    }
    assert rig_2.status_code == 200
    assert rig_2.get_json() == {
        "brand": "NIKON",
        "model": "NIKON Z 8",
        "battery": "64%",
    }
    assert runtime.reconciled_config is config


def test_probe_does_not_reject_trigger_disabled_rig(monkeypatch):
    client, runtime = _client(monkeypatch, _rig_config(rig_2_enabled=False), {})

    response = client.post("/api/rigs/2/camera/probe")

    assert response.status_code == 404
    assert response.get_json()["code"] == "CAMERA_UNAVAILABLE"
    assert runtime.reconciled_config is not None


def test_probe_rejects_invalid_rig_id(monkeypatch):
    client, runtime = _client(monkeypatch, _rig_config(), {})

    response = client.post("/api/rigs/5/camera/probe")

    assert response.status_code == 400
    assert "rig_id" in response.get_json()["error"]
    assert runtime.reconciled_config is None


@pytest.mark.parametrize(
    "worker",
    [None, FakeCameraWorker(error=RuntimeError("probe failed"))],
    ids=["worker-absent", "probe-raises"],
)
def test_probe_returns_camera_unavailable_contract(monkeypatch, worker):
    workers = {} if worker is None else {1: worker}
    client, _runtime = _client(monkeypatch, _rig_config(), workers)

    response = client.post("/api/rigs/1/camera/probe")

    assert response.status_code == 404
    payload = response.get_json()
    assert payload["code"] == "CAMERA_UNAVAILABLE"
    assert payload["rig_id"] == 1


def test_read_info_updates_runtime_cache_without_persistence(monkeypatch, tmp_path):
    expected = {"model": "Sony ILCE-7M5", "battery": "81%"}
    events = []
    trace_log = type(
        "TraceLog",
        (),
        {"append": lambda self, entry: events.append((entry["kind"], entry))},
    )()
    monkeypatch.setattr(flask_module, "get_default_log", lambda: trace_log)
    client, runtime = _client(
        monkeypatch, _rig_config(), {1: FakeCameraWorker(expected)}
    )
    state_path = tmp_path / "state.json"
    state_store = StateStore(state_path)
    monkeypatch.setattr(flask_module, "_state_store", state_store)

    response = client.post("/api/rigs/1/camera/read_info")

    assert response.status_code == 200
    assert response.get_json() == expected
    assert runtime.reconciled_config is not None
    cached = state_store.snapshot("camera_info")["1"]
    assert cached["data"] == expected
    datetime.fromisoformat(cached["last_read"])
    assert not state_path.exists()
    assert len(events) == 1
    kind, trace = events[0]
    assert kind == "camera.read_info"
    assert trace["rig_id"] == 1
    assert trace["serial"] == "CAMERA-1"
    assert trace["duration_ms"] >= 0
    assert trace["status"] == "success"
    datetime.fromisoformat(trace["start_utc"])
    datetime.fromisoformat(trace["end_utc"])


def test_read_info_does_not_reject_trigger_disabled_rig(monkeypatch):
    client, runtime = _client(monkeypatch, _rig_config(rig_2_enabled=False), {})

    response = client.post("/api/rigs/2/camera/read_info")

    assert response.status_code == 409
    assert response.get_json() == {
        "error": "camera is not configured for rig 2",
        "code": "DEVICE_NOT_CONFIGURED",
        "rig_id": 2,
        "device_type": "camera",
    }
    assert runtime.reconciled_config is not None


def test_read_info_rejects_invalid_rig_id(monkeypatch):
    client, runtime = _client(monkeypatch, _rig_config(), {})

    response = client.post("/api/rigs/5/camera/read_info")

    assert response.status_code == 400
    assert "rig_id" in response.get_json()["error"]
    assert runtime.reconciled_config is None


def test_read_info_returns_device_not_configured_without_worker(monkeypatch):
    client, runtime = _client(monkeypatch, _rig_config(), {})

    response = client.post("/api/rigs/1/camera/read_info")

    assert response.status_code == 409
    assert response.get_json() == {
        "error": "camera is not configured for rig 1",
        "code": "DEVICE_NOT_CONFIGURED",
        "rig_id": 1,
        "device_type": "camera",
    }
    assert runtime.reconciled_config is not None


def test_read_info_returns_camera_busy(monkeypatch):
    worker = FakeCameraWorker(error=BusyDeviceError("camera worker is busy"))
    client, runtime = _client(monkeypatch, _rig_config(), {1: worker})

    response = client.post("/api/rigs/1/camera/read_info")

    assert response.status_code == 409
    assert response.get_json() == {
        "error": "camera worker is busy",
        "code": "CAMERA_BUSY",
        "rig_id": 1,
    }
    assert runtime.reconciled_config is not None


def test_sync_time_is_scoped_to_requested_rig_without_persistence(monkeypatch):
    expected = {"status": "ok", "camera_datetime": "2026-08-28T12:34:56"}
    worker = FakeCameraWorker(expected)
    client, runtime = _client(monkeypatch, _rig_config(), {1: worker})
    monkeypatch.setitem(
        flask_module._state["gps"], "utc_offset_minutes", 120
    )
    monkeypatch.setitem(
        flask_module._state["gps"], "timezone_name", "Europe/Paris"
    )
    original_time_sync = flask_module._state["camera"].get("time_sync")

    response = client.post("/api/rigs/1/camera/sync_time")

    assert response.status_code == 200
    assert response.get_json() == expected
    assert flask_module._state["camera"].get("time_sync") == original_time_sync
    assert runtime.reconciled_config is not None
    assert worker.sync_reference.utc_offset_minutes == 120
    assert worker.sync_reference.timezone_name == "Europe/Paris"
    assert (
        worker.sync_reference.datetime_local
        - worker.sync_reference.datetime_utc
    ).total_seconds() == 7200


def test_sync_time_only_enforces_rig_enabled(monkeypatch):
    worker = FakeCameraWorker({"status": "ok"})
    client, _runtime = _client(monkeypatch, _rig_config(), {1: worker})
    monkeypatch.setitem(flask_module._state["gps"], "utc_offset_minutes", 0)
    flask_module._state_store.update_trigger_rig(2, {"running": True})
    monkeypatch.setitem(flask_module._state["camera"], "active", False)

    response = client.post("/api/rigs/1/camera/sync_time")

    assert response.status_code == 200


def test_sync_time_returns_camera_unavailable_contract(monkeypatch):
    worker = FakeCameraWorker(error=RuntimeError("sync failed"))
    client, _runtime = _client(monkeypatch, _rig_config(), {1: worker})
    monkeypatch.setitem(flask_module._state["gps"], "utc_offset_minutes", 0)

    response = client.post("/api/rigs/1/camera/sync_time")

    assert response.status_code == 404
    assert response.get_json() == {
        "error": "camera unavailable",
        "code": "CAMERA_UNAVAILABLE",
        "rig_id": 1,
    }
