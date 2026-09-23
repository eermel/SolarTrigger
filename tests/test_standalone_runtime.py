from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest

from backend.camera_worker_runtime import CameraWorkerRuntime
from backend.runtime_daemon import (
    RuntimeController,
    RuntimeEventJournal,
    RuntimeLogJournal,
    RuntimeUnixServer,
)
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
        self.gps = {
            "connected": True,
            "synced": True,
            "lat": 48.0,
            "lon": 2.0,
            "sync_time": "2026-09-22T20:30:46+00:00",
            "timezone": "UTC+2.0",
        }
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
            return {
                "pid": 4242,
                "trigger": self.trigger,
                "gps": self.gps,
            }
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
    assert second_state.snapshot("gps") == first._client.call(
        "trigger.status"
    )["gps"]
    assert second_state.snapshot("gps")["synced"] is True


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


def test_offline_update_switches_release_then_reboots():
    root = Path(__file__).resolve().parents[1]
    script = (root / "install" / "solartrigger-release-update").read_text(
        encoding="utf-8"
    )

    switch = script.index('alink "$destination" "$ACTIVE"')
    reboot = script.index('/usr/bin/systemctl reboot')
    assert switch < reboot
    assert 'systemctl restart "$RUNTIME_SERVICE"' not in script
    assert 'systemctl restart "$PORTAL_SERVICE"' not in script


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


def test_runtime_log_journal_is_sequenced_and_cursor_based():
    journal = RuntimeLogJournal(size=3)
    journal.append("one", "info", "trigger", rig_id=1)
    journal.append("two", "warning", "trigger", rig_id=1)
    journal.append("three", "info", "camera", rig_id=1)
    journal.append("four", "error", "trigger", rig_id=1)

    result = journal.read(after_seq=2, limit=10)

    assert [entry["seq"] for entry in result["entries"]] == [3, 4]
    assert [entry["text"] for entry in result["entries"]] == ["three", "four"]
    assert result["latest_seq"] == 4
    assert result["oldest_seq"] == 2


def test_runtime_event_journal_preserves_audio_event_order():
    journal = RuntimeEventJournal(size=10)
    journal.append(
        "audio_play",
        {"filename": "human_wav/first_contact_minus_1m.wav", "rig_id": 1},
    )
    journal.append(
        "trigger_phase",
        {"phase": "partial", "rig_id": 1},
    )

    result = journal.read(after_seq=0, limit=10)

    assert [entry["seq"] for entry in result["entries"]] == [1, 2]
    assert result["entries"][0]["event"] == "audio_play"
    assert result["entries"][0]["payload"]["rig_id"] == 1
    assert result["entries"][1]["event"] == "trigger_phase"


def test_runtime_controller_exposes_logs_and_ui_events_without_portal():
    controller = RuntimeController.__new__(RuntimeController)
    controller.runtime_id = "runtime-test"
    controller.log_journal = RuntimeLogJournal()
    controller.event_journal = RuntimeEventJournal()

    controller._runtime_log("capture continues", "warning", "trigger", rig_id=1)
    controller._runtime_emit(
        "audio_play",
        {"filename": "human_wav/totality_minus_1m.wav", "source": "trigger", "rig_id": 1},
    )

    logs = controller.dispatch("logs.read", {"after_seq": 0, "limit": 10})
    events = controller.dispatch("events.read", {"after_seq": 0, "limit": 10})
    relay = controller.dispatch(
        "relay.read",
        {"log_after_seq": 0, "event_after_seq": 0, "limit": 10},
    )

    assert logs["runtime_id"] == "runtime-test"
    assert logs["entries"][0]["text"] == "capture continues"
    assert events["runtime_id"] == "runtime-test"
    assert events["entries"][0]["event"] == "audio_play"
    assert relay["runtime_id"] == "runtime-test"
    assert relay["logs"]["entries"][0]["seq"] == 1
    assert relay["events"]["entries"][0]["payload"]["source"] == "trigger"


def test_standalone_runtime_does_not_discard_trigger_ui_events():
    root = Path(__file__).resolve().parents[1]
    source = (root / "backend" / "runtime_daemon.py").read_text(encoding="utf-8")

    assert "emit_fn=self._runtime_emit" in source
    assert "emit_fn=lambda event, payload: None" not in source


def test_portal_runtime_relay_skips_stale_audio_on_first_attachment():
    root = Path(__file__).resolve().parents[1]
    source = (root / "flask_app" / "app.py").read_text(encoding="utf-8")

    assert "def _thread_runtime_relay():" in source
    assert "if initial or first_attachment or event_gap:" in source
    assert "socketio.emit(event, payload, namespace=\"/\")" in source


def test_runtime_migration_upgrades_legacy_wsgi_entrypoint():
    root = Path(__file__).resolve().parents[1]
    script = (
        root
        / "install"
        / "install_standalone_runtime_service.sh"
    ).read_text(encoding="utf-8")

    assert 'cat > "$APP_DIR/wsgi.py"' in script
    assert "from app import app, socketio, start_background_threads" in script
    assert "start_background_threads()" in script


def test_runtime_status_refreshes_and_exposes_current_runtime_gps(tmp_path):
    controller = RuntimeController.__new__(RuntimeController)
    controller.state_file = tmp_path / "state.json"
    controller.started_utc = datetime(2026, 9, 22, 20, 0, tzinfo=timezone.utc)
    controller.state = StateStore(controller.state_file)

    controller.state.update_section(
        "gps",
        {
            "connected": False,
            "synced": False,
            "sync_time": None,
        },
        persist=True,
    )

    persisted = StateStore(controller.state_file)
    persisted.update_section(
        "gps",
        {
            "connected": True,
            "synced": True,
            "lat": 48.0,
            "lon": 2.0,
            "sync_time": "2026-09-22T20:30:46+00:00",
            "timezone": "UTC+2.0",
        },
        persist=True,
    )

    controller.trigger = SimpleNamespace(
        is_active_or_starting=lambda _rig_id: False,
    )

    result = controller.dispatch("trigger.status", {})

    assert result["gps"]["synced"] is True
    assert result["gps"]["sync_time"] == "2026-09-22T20:30:46+00:00"


def test_runtime_trigger_snapshot_exposes_active_input_filenames(tmp_path):
    controller = RuntimeController.__new__(RuntimeController)
    controller.state = StateStore(tmp_path / "runtime-state.json")
    controller.trigger = SimpleNamespace(
        active_inputs_snapshot=lambda: {
            "1": {
                "circumstances_file": "debug_rig_1.json",
                "photo_file": "photo_setup.json",
                "exposure_opt_file": "expo.json",
            }
        },
        is_active_or_starting=lambda rig_id: rig_id == 1,
    )

    snapshot = controller._trigger_snapshot()

    assert snapshot["rigs"]["1"]["running"] is True
    assert snapshot["rigs"]["1"]["inputs"] == {
        "circumstances_file": "debug_rig_1.json",
        "photo_file": "photo_setup.json",
        "exposure_opt_file": "expo.json",
    }
    assert snapshot["rigs"]["2"]["inputs"] == {}



def test_rollback_does_not_downgrade_root_maintenance_helper():
    root = Path(__file__).resolve().parents[1]
    script = (root / "install" / "solartrigger-release-update").read_text(
        encoding="utf-8"
    )

    rollback = script.split("rollback_release() {", 1)[1].split(
        'case "${1:-}" in', 1
    )[0]
    assert 'alink "$rollback_destination" "$ACTIVE"' in rollback
    assert "refresh_root_helpers" not in rollback



def test_legacy_migration_restores_active_path_before_shared_moves():
    root = Path(__file__).resolve().parents[1]
    script = (root / "install" / "solartrigger-release-update").read_text(
        encoding="utf-8"
    )

    migration = script.split("migrate_legacy_layout() {", 1)[1].split(
        "validate_and_extract() {", 1
    )[0]

    move_active = migration.index('mv -- "$ACTIVE" "$destination"')
    relink_active = migration.index('alink "$destination" "$ACTIVE"')
    move_var = migration.index('mv -- "$destination/var" "$SHARED_VAR"')
    move_venv = migration.index('mv -- "$destination/venv" "$SHARED_VENV"')

    assert move_active < relink_active < move_var
    assert move_active < relink_active < move_venv



def test_web_release_install_does_not_replace_root_helpers():
    root = Path(__file__).resolve().parents[1]
    script = (root / "install" / "solartrigger-release-update").read_text(
        encoding="utf-8"
    )

    assert "refresh_root_helpers" not in script
    assert "/usr/local/sbin/solartrigger-release-update.next" not in script
    assert "/usr/local/sbin/solartrigger-system-update" not in script
