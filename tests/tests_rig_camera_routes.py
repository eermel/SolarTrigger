import sys
from types import ModuleType

import pytest

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
                "devices": {"camera": {"backend": "camera-backend-1"}},
            },
            {
                "rig_id": 2,
                "name": "Telephoto",
                "enabled": rig_2_enabled,
                "devices": {"camera": {"backend": "camera-backend-2"}},
            },
        ],
    }


class FakeCameraWorker:
    def __init__(self, result=None, error=None):
        self._result = result
        self._error = error

    def probe_info(self):
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


def test_probe_rejects_disabled_rig(monkeypatch):
    client, runtime = _client(monkeypatch, _rig_config(rig_2_enabled=False), {})

    response = client.post("/api/rigs/2/camera/probe")

    assert response.status_code == 409
    assert "disabled" in response.get_json()["error"]
    assert runtime.reconciled_config is None


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
