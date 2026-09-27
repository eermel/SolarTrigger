import sys
from types import ModuleType, SimpleNamespace

import pytest

from backend.state_store import StateStore


pytest.importorskip("flask")
pytest.importorskip("flask_socketio")
sys.modules.setdefault("gphoto2", ModuleType("gphoto2"))

import flask_app.app as flask_module


class _BatteryWidget:
    def get_value(self):
        return "85%"


class _CameraConfig:
    def get_child_by_name(self, name):
        if name != "batterylevel":
            raise KeyError(name)
        return _BatteryWidget()


class _TracingCamera:
    def __init__(self):
        self.init_calls = 0
        self.exit_calls = 0

    def init(self):
        self.init_calls += 1

    def exit(self):
        self.exit_calls += 1

    def get_abilities(self):
        return SimpleNamespace(model="Sony ILCE-7M5 (PC Control)")

    def get_config(self):
        return _CameraConfig()


@pytest.fixture
def camera_reads_api(tmp_path, monkeypatch):
    state_store = StateStore(tmp_path / "state.json")
    monkeypatch.setattr(flask_module, "_state_store", state_store)
    monkeypatch.setattr(flask_module, "_state", state_store.data)
    monkeypatch.setattr(flask_module, "_state_lock", state_store.lock)
    monkeypatch.setattr(flask_module, "_append_log", lambda *args, **kwargs: None)

    camera = _TracingCamera()
    monkeypatch.setattr(flask_module.gp, "Camera", lambda: camera, raising=False)
    flask_module.app.config.update(TESTING=True)
    return flask_module.app.test_client(), camera, state_store, monkeypatch


def test_status_returns_cached_camera_without_auto_probe(camera_reads_api):
    client, camera, state_store, _monkeypatch = camera_reads_api
    cached = state_store.snapshot("camera")

    response = client.get("/api/status")

    assert response.status_code == 200
    assert response.get_json()["camera"] == cached
    assert state_store.snapshot("camera") == cached
    assert camera.init_calls == 0
    assert camera.exit_calls == 0


def test_camera_probe_is_manual_and_uses_rig_owner(camera_reads_api):
    client, camera, state_store, monkeypatch = camera_reads_api
    calls = []

    class Worker:
        def probe_info(self):
            calls.append("probe")
            return {
                "model": "Sony ILCE-7M5 (PC Control)",
                "battery": "85%",
                "plugin": "test",
            }

    class Runtime:
        def reconcile(self, config):
            calls.append(("reconcile", config))

        def get_for_rig(self, rig_id):
            calls.append(("get_for_rig", rig_id))
            return Worker() if rig_id == 1 else None

    config = {"rigs": []}
    monkeypatch.setattr(
        flask_module,
        "load_rig_configuration",
        lambda: config,
    )
    monkeypatch.setattr(
        flask_module,
        "get_camera_worker_runtime",
        lambda **_kwargs: Runtime(),
    )
    monkeypatch.setattr(
        flask_module.gp,
        "Camera",
        lambda: pytest.fail(
            "legacy /api/camera/probe must not open gphoto2 directly"
        ),
        raising=False,
    )

    response = client.post("/api/camera/probe")

    assert response.status_code == 200
    assert response.get_json() == {
        "brand": "SONY",
        "model": "Sony ILCE-7M5 (PC Control)",
        "battery": "85%",
    }
    assert state_store.snapshot("camera")["connected"] is False
    assert calls == [
        ("reconcile", config),
        ("get_for_rig", 1),
        "probe",
    ]
    assert camera.init_calls == 0
    assert camera.exit_calls == 0
