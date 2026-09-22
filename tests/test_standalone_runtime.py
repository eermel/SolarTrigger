from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest

from backend.camera_worker_runtime import CameraWorkerRuntime
from backend.runtime_daemon import RuntimeController, RuntimeUnixServer
from backend.runtime_rpc import (
    RemoteCameraWorkerRuntime,
    RemoteTriggerService,
    RuntimeClient,
    _from_wire,
    _to_wire,
)
from backend.state_store import StateStore


class _StaticController:
    def __init__(self):
        self.trigger = {
            "running": True,
            "phase": "totality",
            "rigs": {
                "1": {
                    "running": True,
                    "phase": "totality",
                    "mode": "real",
                    "speed": 1.0,
                },
                "2": {
                    "running": False,
                    "phase": "idle",
                    "mode": None,
                    "speed": None,
                },
                "3": {
                    "running": False,
                    "phase": "idle",
                    "mode": None,
                    "speed": None,
                },
                "4": {
                    "running": False,
                    "phase": "idle",
                    "mode": None,
                    "speed": None,
                },
            },
        }

    def dispatch(self, operation, payload):
        if operation == "trigger.status":
            return {"pid": 4242, "trigger": self.trigger}
        if operation == "trigger.is_active_or_starting":
            rig = self.trigger["rigs"].get(str(payload["rig_id"]), {})
            return bool(rig.get("running"))
        if operation == "trigger.any_active_or_starting":
            return True
        if operation == "ping":
            return {"status": "ok", "pid": 4242}
        raise ValueError(operation)


@pytest.fixture
def runtime_server(tmp_path):
    socket_path = tmp_path / "runtime.sock"
    server = RuntimeUnixServer(str(socket_path), _StaticController())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield socket_path
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_runtime_wire_codec_preserves_datetime_and_namespace():
    value = SimpleNamespace(
        datetime_utc=datetime(2026, 9, 22, 19, 0, tzinfo=timezone.utc),
        nested={"items": [1, 2, 3]},
    )

    decoded = _from_wire(_to_wire(value))

    assert isinstance(decoded, SimpleNamespace)
    assert decoded.datetime_utc == value.datetime_utc
    assert decoded.nested == {"items": [1, 2, 3]}


def test_portal_restart_reattaches_to_live_trigger(runtime_server, tmp_path):
    first_state = StateStore(tmp_path / "portal-1.json")
    first = RemoteTriggerService(
        first_state,
        socket_path=str(runtime_server),
    )
    assert first.sync_state() is True
    assert first_state.snapshot("trigger")["rigs"]["1"]["phase"] == "totality"

    # Model a fresh Gunicorn process: its local StateStore starts idle, while
    # the autonomous runtime keeps the active sequence.
    second_state = StateStore(tmp_path / "portal-2.json")
    assert second_state.snapshot("trigger")["rigs"]["1"]["running"] is False

    second = RemoteTriggerService(
        second_state,
        socket_path=str(runtime_server),
    )
    assert second.sync_state() is True

    restored = second_state.snapshot("trigger")
    assert restored["running"] is True
    assert restored["rigs"]["1"] == {
        "running": True,
        "phase": "totality",
        "mode": "real",
        "speed": 1.0,
    }
    assert second.any_active_or_starting() is True
    assert second.is_active_or_starting(1) is True


def test_runtime_client_ping(runtime_server):
    client = RuntimeClient(str(runtime_server))
    assert client.call("ping") == {"status": "ok", "pid": 4242}


def test_camera_runtime_getter_uses_rpc_facade_in_portal_mode(monkeypatch):
    import backend.runtime_rpc as rpc
    import backend.camera_worker_runtime as workers

    monkeypatch.setenv("SOLARTRIGGER_RUNTIME_CLIENT", "1")
    monkeypatch.setenv(
        "SOLARTRIGGER_RUNTIME_SOCKET",
        "/tmp/solartrigger-test-runtime.sock",
    )
    monkeypatch.setattr(rpc, "_remote_camera_runtime", None)

    runtime = workers.get_camera_worker_runtime()

    assert isinstance(runtime, RemoteCameraWorkerRuntime)
    assert runtime._client.socket_path == "/tmp/solartrigger-test-runtime.sock"


def test_install_script_defines_separate_runtime_and_portal_services():
    root = Path(__file__).resolve().parents[1]
    script = (root / "install" / "install_solareclipse.sh").read_text(
        encoding="utf-8"
    )

    assert "solartrigger-runtime.service" in script
    assert "ExecStart=$VENV_DIR/bin/python -m backend.runtime_daemon" in script
    assert 'Environment="SOLARTRIGGER_RUNTIME_CLIENT=1"' in script
    assert 'Environment="SOLARTRIGGER_RUNTIME_SOCKET=/run/solartrigger/runtime.sock"' in script
    assert "Requires=solartrigger-runtime.service" in script


def test_offline_update_restarts_runtime_before_portal():
    root = Path(__file__).resolve().parents[1]
    script = (root / "install" / "solartrigger-release-update").read_text(
        encoding="utf-8"
    )

    runtime_restart = script.index('systemctl restart "$RUNTIME_SERVICE"')
    portal_restart = script.index('systemctl restart "$PORTAL_SERVICE"')
    assert runtime_restart < portal_restart


class _CameraWorkerStub:
    def __init__(self, *, rig_id, clock, log_fn):
        self.rig_id = rig_id
        self.started = False
        self.stopped = False
        self.config = None

    def configure_camera(self, config):
        self.config = config

    def start(self):
        self.started = True

    def stop(self, timeout=None):
        self.stopped = True


class _IpcServerStub:
    def __init__(self, runtime, *, clock, log_fn):
        self.socket_path = "/tmp/test-camera-ipc.sock"
        self.sessions = set()

    def start(self):
        return None

    def stop(self, timeout=None):
        return None

    def activate_session(self, session_id, rig_ids=None):
        self.sessions.add(session_id)

    def revoke_session(self, session_id):
        self.sessions.remove(session_id)


def _camera_config():
    return {
        "rigs": [
            {
                "rig_id": 1,
                "enabled": True,
                "devices": {
                    "camera": {
                        "backend": "profile",
                        "manufacturer": "Test",
                        "model": "Camera",
                    }
                },
            }
        ]
    }


def test_idle_camera_workers_can_be_released_for_characterization():
    runtime = CameraWorkerRuntime(
        worker_factory=_CameraWorkerStub,
        ipc_server_factory=_IpcServerStub,
    )
    runtime.reconcile(_camera_config())
    worker = runtime.get_for_rig(1)

    assert worker is not None
    assert worker.started is True

    runtime.release_idle_workers()

    assert worker.stopped is True
    assert runtime.get_for_rig(1) is None
    assert runtime.active_camera_rig_ids() == ()


def test_camera_workers_cannot_be_released_during_trigger_lease():
    runtime = CameraWorkerRuntime(
        worker_factory=_CameraWorkerStub,
        ipc_server_factory=_IpcServerStub,
    )
    runtime.reconcile(_camera_config())
    session = runtime.open_ipc_session([1])

    with pytest.raises(RuntimeError, match="trigger IPC session"):
        runtime.release_idle_workers()

    assert runtime.get_for_rig(1) is not None
    runtime.close_ipc_session(session.session_id)
    runtime.shutdown()


class _SessionClosingRuntime:
    def __init__(self):
        self.closed = []

    def close_ipc_session(self, session_id):
        self.closed.append(session_id)


def test_portal_session_cleanup_only_revokes_rpc_owned_leases():
    controller = RuntimeController.__new__(RuntimeController)
    controller.camera_runtime = _SessionClosingRuntime()
    controller._portal_camera_sessions = {"validation-a", "validation-b"}
    controller._portal_camera_sessions_lock = threading.RLock()

    revoked = controller.dispatch("camera.revoke_portal_sessions", {})

    assert revoked == 2
    assert set(controller.camera_runtime.closed) == {
        "validation-a",
        "validation-b",
    }
    assert controller._portal_camera_sessions == set()
