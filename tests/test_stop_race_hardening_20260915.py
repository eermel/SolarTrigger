import io
import subprocess
import threading

import pytest

import backend.trigger_service as trigger_service
from backend.trigger_service import TriggerService


class _FakeSupervisor:
    def __init__(self, service):
        self.service = service
        self.joined = False

    def join(self, timeout=None):
        self.joined = True
        with self.service._lock:
            self.service._starting_by_rig[1] = False


class _FakeProc:
    def __init__(self):
        self.returncode = None
        self.terminated = False
        self.killed = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        self.returncode = 0
        return 0

    def kill(self):
        self.killed = True
        self.returncode = -9


def _service_shell():
    service = TriggerService.__new__(TriggerService)
    service._lock = threading.RLock()
    service._procs = {rig_id: None for rig_id in range(1, 5)}
    service._starting_by_rig = {rig_id: False for rig_id in range(1, 5)}
    service._analysis_suppressed_by_rig = {rig_id: False for rig_id in range(1, 5)}
    service._manual_stop_requested_by_rig = {rig_id: False for rig_id in range(1, 5)}
    service._cancel_start_requested_by_rig = {rig_id: False for rig_id in range(1, 5)}
    service._supervisor_threads = {rig_id: None for rig_id in range(1, 5)}
    service.log = lambda *args, **kwargs: None
    return service


def test_stop_cancels_start_before_popen_is_published():
    service = _service_shell()
    service._starting_by_rig[1] = True
    supervisor = _FakeSupervisor(service)
    service._supervisor_threads[1] = supervisor

    result = service.stop(1)

    assert supervisor.joined is True
    assert service._cancel_start_requested_by_rig[1] is True
    assert service._manual_stop_requested_by_rig[1] is True
    assert result["status"] == "stopped"
    assert result["still_running"] is False


def test_graceful_stop_does_not_wait_for_supervisor_or_atomic_photo():
    service = _service_shell()
    proc = _FakeProc()
    service._procs[1] = proc
    supervisor = _FakeSupervisor(service)
    service._supervisor_threads[1] = supervisor

    result = service.stop(1)

    assert proc.terminated is True
    assert supervisor.joined is False
    assert result["status"] == "stopping"
    assert result["still_running"] is True


class _UnkillableProc:
    def __init__(self):
        self.returncode = None
        self.stdout = io.StringIO("")
        self.terminate_calls = 0
        self.kill_calls = 0

    def poll(self):
        return None

    def terminate(self):
        self.terminate_calls += 1

    def kill(self):
        self.kill_calls += 1

    def wait(self, timeout=None):
        raise subprocess.TimeoutExpired("eclipse_trigger.py", timeout)


class _HeartbeatStub:
    def __init__(self, **_kwargs):
        pass

    def start(self):
        return self

    def stop(self):
        return None

    def snapshot(self):
        return (None, False)


def _run_service_shell(tmp_path):
    service = _service_shell()
    service.trigger_script = tmp_path / "eclipse_trigger.py"
    service.trigger_script.write_text("", encoding="utf-8")
    service.project_dir = tmp_path
    circumstances = tmp_path / "circumstances.json"
    photo = tmp_path / "photo.json"
    exposure = tmp_path / "exposure.json"
    for path in (circumstances, photo, exposure):
        path.write_text("{}", encoding="utf-8")
    service._active_circumstances_paths = {1: circumstances}
    service._active_photo_paths = {1: photo}
    service._active_exposure_opt_paths = {1: exposure}
    service._run_ids_by_rig = {rig_id: None for rig_id in range(1, 5)}
    service._stopping_by_rig = {rig_id: False for rig_id in range(1, 5)}
    service.run_journal = None
    service.heartbeat_timeout_s = 1.0
    service.camera_runtime = None
    service.state = type(
        "State",
        (),
        {
            "update_trigger_rig": lambda self, *_args, **_kwargs: None,
            "clear_trigger_failure": lambda self, *_args, **_kwargs: None,
        },
    )()
    service.emit = lambda *_args, **_kwargs: None
    service.failure_alert_fn = None
    service._clear_active_inputs = lambda _rig_id: None
    service._journal_finish = lambda *_args, **_kwargs: None
    service._log_rig = lambda *_args, **_kwargs: None
    service._child_recovery_safe = lambda **_kwargs: False
    return service


def test_cancel_after_popen_retains_unkillable_child_for_later_stop(
    tmp_path,
    monkeypatch,
):
    service = _run_service_shell(tmp_path)
    service._starting_by_rig[1] = True
    proc = _UnkillableProc()

    def popen(*_args, **_kwargs):
        with service._lock:
            service._cancel_start_requested_by_rig[1] = True
            service._manual_stop_requested_by_rig[1] = True
        return proc

    monkeypatch.setattr(trigger_service.subprocess, "Popen", popen)
    monkeypatch.setattr(trigger_service, "HeartbeatSupervisor", _HeartbeatStub)

    service._run(rig_id=1)

    assert service._procs[1] is proc
    assert service._starting_by_rig[1] is False
    assert proc.terminate_calls >= 1
    assert proc.kill_calls >= 1

    before = proc.kill_calls
    result = service.stop(1, force=True)

    assert proc.kill_calls == before + 1
    assert result["still_running"] is True


def test_supervision_failure_after_popen_retains_unkillable_child(
    tmp_path,
    monkeypatch,
):
    service = _run_service_shell(tmp_path)
    service._starting_by_rig[1] = True
    proc = _UnkillableProc()

    class FailingHeartbeat:
        def __init__(self, **_kwargs):
            pass

        def start(self):
            raise RuntimeError("heartbeat startup failed")

    monkeypatch.setattr(
        trigger_service.subprocess,
        "Popen",
        lambda *_args, **_kwargs: proc,
    )
    monkeypatch.setattr(
        trigger_service,
        "HeartbeatSupervisor",
        FailingHeartbeat,
    )

    service._run(rig_id=1)

    assert service._procs[1] is proc
    assert service._starting_by_rig[1] is False
    assert proc.terminate_calls >= 1
    assert proc.kill_calls >= 1


def test_totality_override_does_not_preempt_child_still_in_startup():
    service = _service_shell()
    proc = _FakeProc()
    service._procs[1] = proc
    service._starting_by_rig[1] = True

    assert service.start_totality_only(1) is False
    assert proc.terminated is False
    assert proc.killed is False
