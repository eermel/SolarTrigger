import io
import json
import os
import threading
import time
from datetime import datetime, timezone

from backend.state_store import StateStore
from backend.trigger_service import TriggerService


class FailingProcess:
    def __init__(self, heartbeat_fd, stage):
        self.returncode = None
        self.stdout = io.StringIO("")
        duplicate = os.dup(heartbeat_fd)

        def writer():
            time.sleep(0.01)
            os.write(duplicate, (stage + "\n").encode())
            os.close(duplicate)

        threading.Thread(target=writer, daemon=True).start()

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        time.sleep(0.08)
        self.returncode = 2
        return self.returncode

    def terminate(self):
        self.returncode = -15

    def kill(self):
        self.returncode = -9


def _service(tmp_path, events):
    script = tmp_path / "scripts" / "eclipse_trigger.py"
    script.parent.mkdir(parents=True)
    script.write_text("", encoding="utf-8")
    configs = tmp_path / "configs"
    configs.mkdir()
    store = StateStore(tmp_path / "state.json")
    return TriggerService(
        store,
        script,
        tmp_path / "todayeclipse.json",
        configs,
        lambda text, level="info", source="trigger", **kwargs: None,
        lambda name, payload: events.append((name, payload)),
        heartbeat_timeout_s=1.0,
    )


def test_external_failure_is_not_published_as_idle(tmp_path):
    events = []
    service = _service(tmp_path, events)

    service.publish_external_failure(1, "CHILD_EXIT", "child died", exit_code=2)

    rig = service.state.snapshot("trigger")["rigs"]["1"]
    assert rig == {
        "running": False,
        "phase": "failed",
        "mode": None,
        "speed": None,
    }
    assert ("trigger_phase", {
        "rig_id": 1,
        "phase": "failed",
        "running": False,
        "code": "CHILD_EXIT",
        "message": "child died",
        "exit_code": 2,
    }) in events
    assert any(name == "trigger_failure" for name, _payload in events)


def test_child_recovery_requires_safe_heartbeat_boundary(tmp_path):
    service = _service(tmp_path, [])
    assert service._child_recovery_safe(
        rig_id=1,
        totality_only=True,
        recovery_attempt=0,
        last_stage="wait",
        heartbeat_timed_out=False,
    ) is True
    assert service._child_recovery_safe(
        rig_id=1,
        totality_only=True,
        recovery_attempt=0,
        last_stage="capture.begin",
        heartbeat_timed_out=False,
    ) is False
    assert service._child_recovery_safe(
        rig_id=1,
        totality_only=True,
        recovery_attempt=0,
        last_stage="capture.end",
        heartbeat_timed_out=True,
    ) is False
    assert service._child_recovery_safe(
        rig_id=1,
        totality_only=True,
        recovery_attempt=1,
        last_stage="wait",
        heartbeat_timed_out=False,
    ) is False


def test_totality_child_restarts_once_from_safe_boundary(tmp_path, monkeypatch):
    events = []
    service = _service(tmp_path, events)
    emergency = tmp_path / "emergency.json"
    emergency.write_text("{}", encoding="utf-8")
    service._active_photo_paths[1] = emergency
    recoveries = []

    def popen(_cmd, **kwargs):
        return FailingProcess(kwargs["pass_fds"][0], "wait")

    def recover(**kwargs):
        recoveries.append(kwargs)
        return "started"

    monkeypatch.setattr("backend.trigger_service.subprocess.Popen", popen)
    monkeypatch.setattr(service, "start_totality_only", recover)

    service._run(totality_only=True, rig_id=1, recovery_attempt=0)

    assert len(recoveries) == 1
    assert recoveries[0]["_recovery"] is True
    assert recoveries[0]["_child_recovery_attempt"] == 1
    assert service.state.snapshot("trigger")["rigs"]["1"]["phase"] == "recovering"


def test_totality_child_does_not_restart_from_unsafe_capture_boundary(
    tmp_path, monkeypatch
):
    events = []
    service = _service(tmp_path, events)
    emergency = tmp_path / "emergency.json"
    emergency.write_text("{}", encoding="utf-8")
    service._active_photo_paths[1] = emergency
    recoveries = []

    def popen(_cmd, **kwargs):
        return FailingProcess(kwargs["pass_fds"][0], "capture.begin")

    monkeypatch.setattr("backend.trigger_service.subprocess.Popen", popen)
    monkeypatch.setattr(
        service,
        "start_totality_only",
        lambda **kwargs: recoveries.append(kwargs) or "started",
    )

    service._run(totality_only=True, rig_id=1, recovery_attempt=0)

    assert recoveries == []
    assert service.state.snapshot("trigger")["rigs"]["1"]["phase"] == "failed"
    assert any(name == "trigger_failure" for name, _payload in events)


def test_systemd_runtime_explicitly_uses_control_group_kill_mode():
    installer = (
        __import__("pathlib").Path(__file__).resolve().parents[1]
        / "install"
        / "install_standalone_runtime_service.sh"
    ).read_text(encoding="utf-8")
    assert "KillMode=control-group" in installer


def test_frontend_has_failed_and_recovering_states_and_failure_alert():
    js = (
        __import__("pathlib").Path(__file__).resolve().parents[1]
        / "flask_app"
        / "static"
        / "js"
        / "solartrigger.js"
    ).read_text(encoding="utf-8")
    assert "recovering: '↻ RECOVERING'" in js
    assert "failed: '⚠ TRIGGER FAILED'" in js
    assert 'socket.on("trigger_failure"' in js
    assert "typeof s.running === 'boolean'" in js
