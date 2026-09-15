import io
from pathlib import Path

import backend.trigger_service as trigger_service
from backend.trigger_service import TriggerService


class _State:
    def __init__(self):
        self.updates = []

    def update_trigger_rig(self, rig_id, payload):
        self.updates.append((rig_id, dict(payload)))


class _StubbornProc:
    def __init__(self):
        self.stdout = _ExplodingStdout()
        self.returncode = None
        self.terminate_calls = 0
        self.kill_calls = 0

    def poll(self):
        return None

    def terminate(self):
        self.terminate_calls += 1
        raise OSError("terminate failed")

    def kill(self):
        self.kill_calls += 1
        raise OSError("kill failed")

    def wait(self, timeout=None):
        raise OSError("wait failed")


class _ExplodingStdout:
    def readline(self):
        raise RuntimeError("stdout supervision failed")


class _DeadProc:
    def __init__(self):
        self.stdout = io.StringIO("")
        self.returncode = 0

    def poll(self):
        return 0

    def wait(self, timeout=None):
        return 0


class _IpcSession:
    socket_path = "/tmp/test-camera.sock"
    session_id = "session-1"


class _CameraRuntime:
    def __init__(self):
        self.closed = []

    def close_ipc_session(self, session_id):
        self.closed.append(session_id)


def _service(tmp_path, camera_runtime=None):
    script = tmp_path / "scripts" / "eclipse_trigger.py"
    script.parent.mkdir(parents=True)
    script.write_text("# test\n", encoding="utf-8")

    state = _State()
    emitted = []
    logs = []
    service = TriggerService(
        state_store=state,
        trigger_script=script,
        json_file=tmp_path / "unused.json",
        configs_dir=tmp_path,
        log_fn=lambda text, level="info", source="trigger", **kwargs: logs.append(
            (text, level, source, kwargs)
        ),
        emit_fn=lambda event, payload: emitted.append((event, dict(payload))),
        camera_runtime=camera_runtime,
        rig_config_loader=lambda: {"rigs": []},
    )
    return service, state, emitted, logs


def _prepare_inputs(service, tmp_path, rig_id=1):
    service._active_circumstances_paths[rig_id] = tmp_path / "circ.json"
    service._active_photo_paths[rig_id] = tmp_path / "photo.json"
    service._active_exposure_opt_paths[rig_id] = tmp_path / "exp.json"


def test_supervisor_never_publishes_idle_or_forgets_live_child(monkeypatch, tmp_path):
    camera_runtime = _CameraRuntime()
    service, state, emitted, logs = _service(tmp_path, camera_runtime)
    _prepare_inputs(service, tmp_path)

    proc = _StubbornProc()
    monkeypatch.setattr(trigger_service.subprocess, "Popen", lambda *a, **k: proc)

    service._run(rig_id=1, ipc_session=_IpcSession())

    assert service._procs[1] is proc
    assert service._analysis_suppressed_by_rig[1] is True
    assert service._starting_by_rig[1] is False
    assert camera_runtime.closed == ["session-1"]

    phases = [
        payload["phase"]
        for _rig_id, payload in state.updates
        if "phase" in payload
    ]
    assert "idle" not in phases

    emitted_phases = [
        payload["phase"]
        for event, payload in emitted
        if event == "trigger_phase"
    ]
    assert "idle" not in emitted_phases

    assert any(
        "child process is still alive" in text
        for text, _level, _source, _kwargs in logs
    )


def test_normal_dead_child_still_cleans_to_idle(monkeypatch, tmp_path):
    service, state, emitted, _logs = _service(tmp_path)
    _prepare_inputs(service, tmp_path)

    proc = _DeadProc()
    monkeypatch.setattr(trigger_service.subprocess, "Popen", lambda *a, **k: proc)

    service._run(rig_id=1)

    assert service._procs[1] is None
    phases = [
        payload["phase"]
        for _rig_id, payload in state.updates
        if "phase" in payload
    ]
    assert phases[-1] == "idle"
