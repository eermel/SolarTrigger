import sys
from types import ModuleType

import pytest

from backend.state_store import StateStore


pytest.importorskip("flask")
pytest.importorskip("flask_socketio")
sys.modules.setdefault("gphoto2", ModuleType("gphoto2"))

import flask_app.app as flask_module


class _FailOnAutomaticCameraReadWorker:
    def probe_info(self):
        raise AssertionError("GET /api/status must not probe a camera")

    def read_info(self):
        raise AssertionError("GET /api/status must not read camera information")


class _FailOnAutomaticCameraReadRuntime:
    def __init__(self):
        self.worker = _FailOnAutomaticCameraReadWorker()

    def reconcile(self, _config):
        raise AssertionError("GET /api/status must not reconcile camera workers")

    def get_for_rig(self, _rig_id):
        raise AssertionError("GET /api/status must not resolve a camera worker")


def test_repeated_status_uses_camera_cache_without_any_hardware_access(
    tmp_path, monkeypatch
):
    state_path = tmp_path / "state.json"
    state_store = StateStore(state_path)
    state_store.update_section(
        "camera",
        {
            "connected": False,
            "brand": "SONY",
            "model": "cached model",
            "battery": "cached battery",
            "time_sync": "2026-08-22T12:34:56Z",
        },
    )
    camera_before = state_store.snapshot("camera")
    runtime = _FailOnAutomaticCameraReadRuntime()
    runtime_requests = []
    direct_camera_calls = []
    direct_camera_init_calls = []

    def get_runtime(**kwargs):
        runtime_requests.append(kwargs)
        return runtime

    class TracingDirectCamera:
        def __init__(self, *args, **kwargs):
            direct_camera_calls.append((args, kwargs))

        def init(self):
            direct_camera_init_calls.append(True)

        def exit(self):
            return None

        def get_abilities(self):
            raise AssertionError("GET /api/status must not read camera properties")

        def get_config(self):
            raise AssertionError("GET /api/status must not read camera properties")

    monkeypatch.setattr(flask_module, "_state_store", state_store)
    monkeypatch.setattr(flask_module, "_state", state_store.data)
    monkeypatch.setattr(flask_module, "_state_lock", state_store.lock)
    monkeypatch.setattr(flask_module, "get_camera_worker_runtime", get_runtime)
    monkeypatch.setattr(flask_module.gp, "Camera", TracingDirectCamera, raising=False)
    flask_module.app.config.update(TESTING=True)

    client = flask_module.app.test_client()
    first = client.get("/api/status")
    second = client.get("/api/status")

    assert first.status_code == second.status_code == 200
    assert first.is_json and second.is_json
    assert first.get_json()["camera"] == camera_before
    assert second.get_json()["camera"] == camera_before
    assert runtime_requests == []
    assert direct_camera_calls == []
    assert direct_camera_init_calls == []
    assert state_store.snapshot("camera") == camera_before
    assert state_store.snapshot("camera_info") is None
    assert not state_path.exists()
