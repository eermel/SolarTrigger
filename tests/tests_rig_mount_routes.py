import sys
from types import ModuleType

import pytest

from backend.rig_manager import RigManager
from plugins.mount.indi_client import IndiClientError


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
                    "mount": {
                        "backend": "indi",
                        "serial": f"mount-{rig_id}",
                    },
                },
            }
            for rig_id in (1, 2)
        ],
    }


class FakeMountWorker:
    def __init__(self, rig_id, error=None):
        self.rig_id = rig_id
        self.error = error
        self.calls = []

    def _call(self, method, *args):
        self.calls.append((method, args))
        if self.error is not None:
            raise self.error
        return {"status": "ok", "worker_rig_id": self.rig_id}

    def status(self):
        return self._call("status")

    def set_tracking_mode(self, mode):
        return self._call("set_tracking_mode", mode)

    def start_tracking(self):
        return self._call("start_tracking")

    def stop_tracking(self):
        return self._call("stop_tracking")

    def set_speed(self, speed):
        return self._call("set_speed", speed)

    def start_slew(self, direction):
        return self._call("start_slew", direction)

    def home_start(self):
        return self._call("home_start")

    def stop(self):
        return self._call("stop")


class FakeMountWorkerRuntime:
    def __init__(self, workers):
        self.workers = workers
        self.reconciled_config = None

    def reconcile(self, config):
        self.reconciled_config = config

    def get_for_rig(self, rig_id):
        return self.workers.get(rig_id)


def _client(monkeypatch, workers):
    config = _rig_config()
    runtime = FakeMountWorkerRuntime(workers)
    emitted = []
    monkeypatch.setattr(
        flask_module, "get_rig_manager", lambda: RigManager.from_config(config)
    )
    monkeypatch.setattr(flask_module, "load_rig_configuration", lambda: config)
    monkeypatch.setattr(
        flask_module, "get_mount_worker_runtime", lambda **_kwargs: runtime
    )
    monkeypatch.setattr(
        flask_module.socketio,
        "emit",
        lambda event, payload, **kwargs: emitted.append((event, payload, kwargs)),
    )
    monkeypatch.setitem(flask_module._state["trigger"], "running", False)
    flask_module.app.config.update(TESTING=True)
    return flask_module.app.test_client(), runtime, emitted


@pytest.mark.parametrize(
    ("path", "json", "expected_call"),
    [
        ("status", None, ("status", ())),
        ("tracking/mode", {"mode": "solar"}, ("set_tracking_mode", ("solar",))),
        ("tracking/start", None, ("start_tracking", ())),
        ("tracking/stop", None, ("stop_tracking", ())),
        ("speed", {"speed": 2}, ("set_speed", (2,))),
        ("slew/start", {"direction": "east"}, ("start_slew", ("east",))),
        ("home", None, ("home_start", ())),
        ("slew/stop", None, ("stop", ())),
    ],
)
def test_mount_routes_dispatch_to_requested_rig(
    monkeypatch, path, json, expected_call
):
    rig_1_worker = FakeMountWorker(1)
    rig_2_worker = FakeMountWorker(2)
    client, runtime, emitted = _client(
        monkeypatch, {1: rig_1_worker, 2: rig_2_worker}
    )

    if path == "status":
        response = client.get(f"/api/rigs/2/mount/{path}")
    else:
        response = client.post(f"/api/rigs/2/mount/{path}", json=json)

    assert response.status_code == 200
    assert response.get_json()["worker_rig_id"] == 2
    assert rig_1_worker.calls == []
    assert rig_2_worker.calls == [expected_call]
    assert runtime.reconciled_config is not None
    assert emitted == [
        (
            "mount_update",
            {
                "status": "ok",
                "worker_rig_id": 2,
                "rig_id": 2,
                "device_type": "mount",
            },
            {"namespace": "/"},
        )
    ]


def test_stop_rig1_mount_does_not_affect_rig2(monkeypatch):
    rig_1_worker = FakeMountWorker(1)
    rig_2_worker = FakeMountWorker(2)
    client, _runtime, emitted = _client(
        monkeypatch, {1: rig_1_worker, 2: rig_2_worker}
    )

    response = client.post("/api/rigs/1/mount/slew/stop")

    assert response.status_code == 200
    assert rig_1_worker.calls == [("stop", ())]
    assert rig_2_worker.calls == []
    assert emitted == [
        (
            "mount_update",
            {
                "status": "ok",
                "worker_rig_id": 1,
                "rig_id": 1,
                "device_type": "mount",
            },
            {"namespace": "/"},
        )
    ]


def test_absent_mount_worker_returns_device_not_configured(monkeypatch):
    client, runtime, emitted = _client(monkeypatch, {})

    response = client.get("/api/rigs/1/mount/status")

    assert response.status_code == 409
    assert response.get_json() == {
        "error": "mount is not configured for rig 1",
        "code": "DEVICE_NOT_CONFIGURED",
        "rig_id": 1,
        "device_type": "mount",
    }
    assert runtime.reconciled_config is not None
    assert emitted == []


def test_handled_worker_failure_emits_mount_error_envelope(monkeypatch):
    error = IndiClientError("INDI_UNAVAILABLE", "INDI is unavailable")
    client, _runtime, emitted = _client(
        monkeypatch, {1: FakeMountWorker(1, error=error)}
    )

    response = client.get("/api/rigs/1/mount/status")

    assert response.status_code == 400
    assert response.get_json() == {
        "error": "INDI is unavailable",
        "code": "INDI_UNAVAILABLE",
    }
    assert emitted == [
        (
            "mount_update",
            {
                "status": "error",
                "rig_id": 1,
                "device_type": "mount",
                "error": "INDI is unavailable",
                "code": "INDI_UNAVAILABLE",
            },
            {"namespace": "/"},
        )
    ]
