import sys
from datetime import datetime
from types import ModuleType, SimpleNamespace

import pytest

from backend.generic_worker import BusyDeviceError
from backend.rig_manager import RigManager


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
        self.calls = []

    def test_photo(self, speeds, *, photo_num_start, deadline):
        self.calls.append((speeds, photo_num_start, deadline))
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


@pytest.mark.parametrize(
    "payload",
    [{}, {"speed": ""}, {"speed": "   "}, {"speed": 0}, {"speeds": ["1/125"]}],
    ids=["missing", "empty", "whitespace", "non-string", "speeds-array"],
)
def test_test_photo_rejects_invalid_speed(monkeypatch, payload):
    client, runtime = _client(monkeypatch, _rig_config(), {})

    response = client.post("/api/rigs/1/camera/test_photo", json=payload)

    assert response.status_code == 400
    assert response.get_json()["code"] == "INVALID_TEST_PHOTO_SPEED"
    assert runtime.reconciled_config is None


def test_test_photo_does_not_reject_trigger_disabled_rig(monkeypatch):
    client, runtime = _client(monkeypatch, _rig_config(rig_2_enabled=False), {})

    response = client.post(
        "/api/rigs/2/camera/test_photo", json={"speed": "1/125"}
    )

    assert response.status_code == 409
    assert response.get_json() == {
        "error": "camera is not configured for rig 2",
        "code": "DEVICE_NOT_CONFIGURED",
        "rig_id": 2,
        "device_type": "camera",
    }
    assert runtime.reconciled_config is not None


def test_test_photo_returns_device_not_configured_without_worker(monkeypatch):
    client, runtime = _client(monkeypatch, _rig_config(), {})

    response = client.post(
        "/api/rigs/1/camera/test_photo", json={"speed": "1/125"}
    )

    assert response.status_code == 409
    assert response.get_json() == {
        "error": "camera is not configured for rig 1",
        "code": "DEVICE_NOT_CONFIGURED",
        "rig_id": 1,
        "device_type": "camera",
    }
    assert runtime.reconciled_config is not None


def test_test_photo_returns_camera_busy(monkeypatch):
    worker = FakeCameraWorker(error=BusyDeviceError("camera worker is busy"))
    client, _runtime = _client(monkeypatch, _rig_config(), {1: worker})

    response = client.post(
        "/api/rigs/1/camera/test_photo", json={"speed": "1/125"}
    )

    assert response.status_code == 409
    assert response.get_json() == {
        "error": "camera worker is busy",
        "code": "CAMERA_BUSY",
        "rig_id": 1,
    }


def test_test_photo_returns_camera_unavailable(monkeypatch):
    worker = FakeCameraWorker(error=RuntimeError("camera disconnected"))
    client, _runtime = _client(monkeypatch, _rig_config(), {1: worker})

    response = client.post(
        "/api/rigs/1/camera/test_photo", json={"speed": "1/125"}
    )

    assert response.status_code == 404
    assert response.get_json() == {
        "error": "camera unavailable",
        "code": "CAMERA_UNAVAILABLE",
        "rig_id": 1,
    }


def test_test_photo_returns_capture_result_and_timing(monkeypatch):
    result = SimpleNamespace(frames=1, planned=1, detail="single")
    events = []
    trace_log = type(
        "TraceLog",
        (),
        {"append": lambda self, entry: events.append((entry["kind"], entry))},
    )()
    monkeypatch.setattr(flask_module, "get_default_log", lambda: trace_log)
    worker = FakeCameraWorker(result=result)
    client, runtime = _client(monkeypatch, _rig_config(), {1: worker})
    monotonic_values = iter((10.0, 10.125))
    monkeypatch.setattr(flask_module.time, "monotonic", lambda: next(monotonic_values))

    response = client.post(
        "/api/rigs/1/camera/test_photo", json={"speed": "1/125"}
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload == {
        "status": "ok",
        "rig_id": 1,
        "speed": "1/125",
        "started_at": payload["started_at"],
        "duration_s": 0.125,
        "frames": 1,
        "planned": 1,
        "detail": "single",
    }
    assert datetime.fromisoformat(payload["started_at"]).tzinfo is not None
    assert payload["duration_s"] > 0
    assert worker.calls == [(["1/125"], 0, None)]
    assert runtime.reconciled_config is not None
    assert len(events) == 1
    kind, trace = events[0]
    assert kind == "camera.test_photo"
    assert trace["rig_id"] == 1
    assert trace["serial"] == "CAMERA-1"
    assert trace["duration_ms"] >= 0
    assert trace["status"] == "success"
    assert trace["frames"] == 1
    datetime.fromisoformat(trace["start_utc"])
    datetime.fromisoformat(trace["end_utc"])
