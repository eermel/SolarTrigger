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
        "rigs": [{
            "rig_id": 2,
            "name": "Rig 2",
            "enabled": True,
            "devices": {
                "camera": {"backend": "camera-2"},
                "focuser": {"backend": "indi", "serial": "focuser-2"},
                "mount": {"backend": "indi", "serial": "mount-2"},
            },
        }],
    }


class FakeWorker:
    def stop(self):
        return {"status": "ok"}

    def stop_jog(self):
        return {"status": "ok"}

    def stop_tracking(self):
        return {"status": "ok"}


class FakeRuntime:
    def __init__(self, worker):
        self.worker = worker

    def reconcile(self, _config):
        pass

    def get_for_rig(self, _rig_id):
        return self.worker


def _client(monkeypatch, worker):
    config = _rig_config()
    runtime = FakeRuntime(worker)
    events = []
    monkeypatch.setattr(
        flask_module, "get_rig_manager", lambda: RigManager.from_config(config)
    )
    monkeypatch.setattr(flask_module, "load_rig_configuration", lambda: config)
    monkeypatch.setattr(
        flask_module, "get_focuser_worker_runtime", lambda **_kwargs: runtime
    )
    monkeypatch.setattr(
        flask_module, "get_mount_worker_runtime", lambda **_kwargs: runtime
    )
    monkeypatch.setattr(
        flask_module.rig_trace,
        "trace_event",
        lambda kind, payload: events.append((kind, payload)),
    )
    monkeypatch.setattr(flask_module.socketio, "emit", lambda *_args, **_kwargs: None)
    flask_module._state_store.reset_boot_sensitive()
    flask_module.app.config.update(TESTING=True)
    return flask_module.app.test_client(), events


@pytest.mark.parametrize(
    ("path", "kind", "device_type"),
    [
        ("focuser/stop", "focuser.stop", "focuser"),
        ("focuser/jog/stop", "focuser.stop", "focuser"),
        ("mount/slew/stop", "mount.stop", "mount"),
        ("mount/tracking/stop", "mount.stop", "mount"),
    ],
)
def test_rig_stop_route_traces_success(
    monkeypatch, path, kind, device_type
):
    client, events = _client(monkeypatch, FakeWorker())

    response = client.post(f"/api/rigs/2/{path}")

    assert response.status_code == 200
    assert len(events) == 1
    event_kind, payload = events[0]
    assert event_kind == kind
    assert payload["rig_id"] == 2
    assert payload["device_type"] == device_type
    assert payload["action"] == "stop"
    assert payload["status"] == "success"
    assert payload["duration_ms"] >= 0
    assert payload["start_utc"] <= payload["end_utc"]


def test_legacy_focuser_stop_route_traces_rig_one(monkeypatch):
    client, events = _client(monkeypatch, FakeWorker())
    monkeypatch.setattr(flask_module, "_focuser_service", FakeWorker())
    monkeypatch.setattr(
        flask_module, "require_device_active", lambda _device_type: None
    )

    response = client.post("/api/focuser/stop")

    assert response.status_code == 200
    assert len(events) == 1
    event_kind, payload = events[0]
    assert event_kind == "focuser.stop"
    assert payload["rig_id"] == 1
    assert payload["device_type"] == "focuser"
    assert payload["action"] == "stop"
    assert payload["status"] == "success"
    assert payload["duration_ms"] >= 0
    assert payload["start_utc"] <= payload["end_utc"]


@pytest.mark.parametrize(
    ("path", "kind", "device_type"),
    [
        ("focuser/stop", "focuser.stop", "focuser"),
        ("focuser/jog/stop", "focuser.stop", "focuser"),
        ("mount/slew/stop", "mount.stop", "mount"),
        ("mount/tracking/stop", "mount.stop", "mount"),
    ],
)
def test_rig_stop_route_traces_device_error(
    monkeypatch, path, kind, device_type
):
    client, events = _client(monkeypatch, None)

    response = client.post(f"/api/rigs/2/{path}")

    assert response.status_code == 409
    assert len(events) == 1
    event_kind, payload = events[0]
    assert event_kind == kind
    assert payload["rig_id"] == 2
    assert payload["device_type"] == device_type
    assert payload["action"] == "stop"
    assert payload["status"] == "error"
    assert payload["code"] == "DEVICE_NOT_CONFIGURED"
    assert payload["message"] == f"{device_type} is not configured for rig 2"
    assert payload["duration_ms"] >= 0
