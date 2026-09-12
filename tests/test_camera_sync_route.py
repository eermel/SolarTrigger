from datetime import datetime
import importlib.util
import sys
import types

import pytest

from backend.state_store import StateStore

sys.modules.setdefault("gphoto2", types.SimpleNamespace())


if importlib.util.find_spec("flask") is None:
    class _Response:
        def __init__(self, value):
            if isinstance(value, tuple):
                self._json, self.status_code = value
            else:
                self._json, self.status_code = value, 200

        def get_json(self):
            return self._json

    class _Request:
        def __init__(self):
            self.json = None

        def get_json(self, silent=False):
            return self.json

    _fake_request = _Request()

    class _TestClient:
        def __init__(self, routes):
            self.routes = routes

        def _call(self, path, method, json=None):
            _fake_request.json = json
            try:
                return _Response(self.routes[(path, method)]())
            finally:
                _fake_request.json = None

        def post(self, path, json=None, **kwargs):
            return self._call(path, "POST", json=json)

        def get(self, path, **kwargs):
            return self._call(path, "GET")

    class _Flask:
        def __init__(self, *args, **kwargs):
            self.config = {}
            self.routes = {}

        def route(self, path, methods=None, **kwargs):
            def register(function):
                for method in methods or ("GET",):
                    self.routes[(path, method)] = function
                return function

            return register

        def test_client(self):
            return _TestClient(self.routes)

    class _SocketIO:
        def __init__(self, *args, **kwargs):
            pass

        def emit(self, *args, **kwargs):
            pass

        def on(self, *args, **kwargs):
            return lambda function: function

    sys.modules["flask"] = types.SimpleNamespace(
        Flask=_Flask,
        jsonify=lambda value: value,
        request=_fake_request,
        send_from_directory=lambda *args, **kwargs: None,
    )
    sys.modules["flask_socketio"] = types.SimpleNamespace(
        SocketIO=_SocketIO,
        emit=lambda *args, **kwargs: None,
    )

from flask_app import app as flask_module


class FakeSyncWorker:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.references = []

    def sync_datetime(self, reference):
        self.references.append(reference)
        if self.error is not None:
            raise self.error
        return self.result


class FakeSyncRuntime:
    def __init__(self, worker):
        self.worker = worker
        self.reconciled = []
        self.requested_rig_ids = []

    def reconcile(self, config):
        self.reconciled.append(config)

    def get_for_rig(self, rig_id):
        self.requested_rig_ids.append(rig_id)
        return self.worker


def _install_runtime(monkeypatch, worker):
    runtime = FakeSyncRuntime(worker)
    config = {"schema_version": 2, "rigs": []}
    monkeypatch.setattr(
        flask_module,
        "get_camera_worker_runtime",
        lambda **_kwargs: runtime,
    )
    monkeypatch.setattr(flask_module, "load_rig_configuration", lambda: config)
    return runtime, config


@pytest.fixture
def camera_sync_client(tmp_path, monkeypatch):
    state_store = StateStore(tmp_path / "state.json")
    state_store.update_section(
        "devices", {"camera": {"plugin": "test", "active": True}}
    )
    monkeypatch.setattr(flask_module, "_state_store", state_store)
    flask_module.app.config.update(TESTING=True)
    return flask_module.app.test_client(), state_store


def test_camera_sync_uses_rig_worker_when_legacy_device_is_inactive(
    camera_sync_client, monkeypatch
):
    client, state_store = camera_sync_client
    state_store.update_section(
        "devices", {"camera": {"plugin": "none", "active": False}}
    )
    state_store.update_section(
        "gps", {"utc_offset_minutes": 120, "timezone_name": "Europe/Paris"}
    )
    result = {
        "status": "unsupported",
        "datetime_synced": False,
        "timezone_synced": False,
        "message": "Synchronization is not characterized",
    }
    worker = FakeSyncWorker(result=result)
    runtime, _config = _install_runtime(monkeypatch, worker)

    response = client.post("/api/camera/sync_time")

    assert response.status_code == 200
    assert response.get_json() == result
    assert len(worker.references) == 1
    assert runtime.requested_rig_ids == [1]


def test_camera_sync_requires_gps_offset_without_changing_state(
    camera_sync_client,
):
    client, state_store = camera_sync_client
    before = state_store.snapshot()

    response = client.post("/api/camera/sync_time")

    assert response.status_code == 409
    assert "GPS" in response.get_json()["error"]
    assert state_store.snapshot() == before


def test_camera_sync_rejects_running_trigger_without_changing_state(
    camera_sync_client,
):
    client, state_store = camera_sync_client
    state_store.update_trigger_rig(1, {"running": True})
    before = state_store.snapshot()

    response = client.post("/api/camera/sync_time")

    assert response.status_code == 409
    assert response.get_json()["code"] == "TRIGGER_RUNNING"
    assert state_store.snapshot() == before


def test_camera_sync_returns_404_when_worker_sync_fails_without_changing_state(
    camera_sync_client, monkeypatch
):
    client, state_store = camera_sync_client
    state_store.update_section("gps", {"utc_offset_minutes": 120})
    before = state_store.snapshot()

    worker = FakeSyncWorker(error=RuntimeError("gphoto2 init failed"))
    runtime, _config = _install_runtime(monkeypatch, worker)

    response = client.post("/api/camera/sync_time")

    assert response.status_code == 404
    assert "gphoto2 init failed" in response.get_json()["error"]
    assert state_store.snapshot() == before
    assert runtime.requested_rig_ids == [1]


def test_camera_sync_persists_unsupported_result_with_utc_timestamps(
    camera_sync_client, monkeypatch
):
    client, state_store = camera_sync_client
    state_store.update_section(
        "gps",
        {"utc_offset_minutes": 120, "timezone_name": "Europe/Paris"},
    )
    result = {
        "status": "unsupported",
        "datetime_synced": False,
        "timezone_synced": False,
        "datetime_applied": None,
        "timezone_name": "Europe/Paris",
        "utc_offset_minutes": 120,
        "message": "Synchronisation non supportée",
        "plugin": "base",
        "model": "Test Camera",
    }
    worker = FakeSyncWorker(result=result)
    runtime, config = _install_runtime(monkeypatch, worker)

    response = client.post("/api/camera/sync_time")

    assert response.status_code == 200
    assert response.get_json() == result
    assert len(worker.references) == 1
    reference = worker.references[0]
    assert (
        reference.datetime_local - reference.datetime_utc
    ).total_seconds() == pytest.approx(120 * 60)
    assert reference.timezone_name == "Europe/Paris"
    assert runtime.reconciled == [config]
    assert runtime.requested_rig_ids == [1]

    persisted = state_store.snapshot("camera")["time_sync"]
    assert {key: persisted[key] for key in result} == result
    assert set(persisted) == {*result, "attempted_at", "completed_at"}
    attempted_at = datetime.fromisoformat(persisted["attempted_at"])
    completed_at = datetime.fromisoformat(persisted["completed_at"])
    assert attempted_at.utcoffset().total_seconds() == 0
    assert completed_at.utcoffset().total_seconds() == 0
    assert attempted_at <= completed_at
