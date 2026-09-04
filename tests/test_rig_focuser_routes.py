import sys
from types import ModuleType

import pytest

from backend.rig_manager import RigManager


pytest.importorskip("flask")
pytest.importorskip("flask_socketio")
sys.modules.setdefault("gphoto2", ModuleType("gphoto2"))

import flask_app.app as flask_module


def _rig_config():
    return {
        "schema_version": 2,
        "rigs": [
            {
                "rig_id": rig_id,
                "name": f"Rig {rig_id}",
                "enabled": True,
                "devices": {
                    "camera": {"backend": f"camera-{rig_id}"},
                    "focuser": {
                        "backend": "indi",
                        "serial": f"focuser-{rig_id}",
                    },
                },
            }
            for rig_id in (1, 2)
        ],
    }


class FakeFocuserWorker:
    def __init__(self, rig_id):
        self.rig_id = rig_id
        self.calls = []

    def _call(self, method, *args):
        self.calls.append((method, args))
        return {"status": "ok", "worker_rig_id": self.rig_id}

    def stop(self):
        return self._call("stop")

    def stop_jog(self):
        return self._call("stop_jog")


class FakeFocuserWorkerRuntime:
    def __init__(self, workers):
        self.workers = workers
        self.reconciled_config = None

    def reconcile(self, config):
        self.reconciled_config = config

    def get_for_rig(self, rig_id):
        return self.workers.get(rig_id)


def _client(monkeypatch, workers):
    config = _rig_config()
    runtime = FakeFocuserWorkerRuntime(workers)
    emitted = []
    monkeypatch.setattr(
        flask_module, "get_rig_manager", lambda: RigManager.from_config(config)
    )
    monkeypatch.setattr(flask_module, "load_rig_configuration", lambda: config)
    monkeypatch.setattr(
        flask_module, "get_focuser_worker_runtime", lambda **_kwargs: runtime
    )
    monkeypatch.setattr(
        flask_module.socketio,
        "emit",
        lambda event, payload, **kwargs: emitted.append((event, payload, kwargs)),
    )
    monkeypatch.setitem(flask_module._state["trigger"], "running", False)
    flask_module.app.config.update(TESTING=True)
    return flask_module.app.test_client(), runtime, emitted


def _assert_single_rig_1_update(emitted):
    assert emitted == [
        (
            "focuser_update",
            {
                "status": "ok",
                "worker_rig_id": 1,
                "rig_id": 1,
                "device_type": "focuser",
            },
            {"namespace": "/"},
        )
    ]


def test_stop_rig1_focuser_does_not_affect_rig2(monkeypatch):
    rig_1_worker = FakeFocuserWorker(1)
    rig_2_worker = FakeFocuserWorker(2)
    client, runtime, emitted = _client(
        monkeypatch, {1: rig_1_worker, 2: rig_2_worker}
    )

    response = client.post("/api/rigs/1/focuser/stop")

    assert response.status_code == 200
    assert rig_1_worker.calls == [("stop", ())]
    assert rig_2_worker.calls == []
    assert runtime.reconciled_config is not None
    _assert_single_rig_1_update(emitted)


def test_jog_stop_rig1_focuser_does_not_affect_rig2(monkeypatch):
    rig_1_worker = FakeFocuserWorker(1)
    rig_2_worker = FakeFocuserWorker(2)
    client, runtime, emitted = _client(
        monkeypatch, {1: rig_1_worker, 2: rig_2_worker}
    )

    response = client.post("/api/rigs/1/focuser/jog/stop")

    assert response.status_code == 200
    assert rig_1_worker.calls == [("stop_jog", ())]
    assert rig_2_worker.calls == []
    assert runtime.reconciled_config is not None
    _assert_single_rig_1_update(emitted)
