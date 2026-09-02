import io
import json
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.camera_worker_runtime import CameraWorkerRuntime
from backend.state_store import StateStore
from backend.trigger_service import TriggerService


class FakeWorker:
    def __init__(self, *, rig_id, clock, log_fn):
        self.rig_id = rig_id
        self.clock = clock

    def start(self):
        pass

    def stop(self):
        pass


class ImmediateThread:
    def __init__(self, *, target, args, name, daemon):
        self.target = target
        self.args = args

    def start(self):
        self.target(*self.args)


class CompletedProcess:
    returncode = 0

    def __init__(self):
        self.stdout = io.StringIO("")

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode


class LifecycleServer:
    def __init__(
        self, runtime, *, clock=None, endpoint_dir=None, parent_pid=None, log_fn=None
    ):
        self._clock = clock
        self.socket_path = Path(endpoint_dir) / f"camera-ipc-{parent_pid}.sock"
        self._active_session = None
        self._active_rig_ids = None

    def start(self):
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        self.socket_path.touch()

    def stop(self):
        self.socket_path.unlink(missing_ok=True)

    def activate_session(self, session_id, rig_ids=None):
        self._active_session = session_id
        self._active_rig_ids = (
            None if rig_ids is None else tuple(sorted(set(rig_ids)))
        )
        return session_id

    def revoke_session(self, session_id):
        assert session_id == self._active_session
        self._active_session = None
        self._active_rig_ids = None


def _rig_config():
    return {
        "rigs": [
            {
                "rig_id": 1,
                "enabled": True,
                "devices": {"camera": {"backend": "gphoto2"}},
            }
        ]
    }


def _make_runtime(tmp_path, *, clock=None, server_type=LifecycleServer, events=None):
    servers = []

    def server_factory(runtime, **kwargs):
        server = server_type(
            runtime,
            endpoint_dir=tmp_path / "ipc",
            parent_pid=4321,
            **kwargs,
        )
        if events is not None:
            original_start = server.start

            def start():
                original_start()
                events.append("server-started")

            server.start = start
        servers.append(server)
        return server

    runtime = CameraWorkerRuntime(
        clock=clock,
        worker_factory=FakeWorker,
        ipc_server_factory=server_factory,
        log_fn=lambda _message: None,
    )
    return runtime, servers


def _make_service(tmp_path, runtime):
    store = StateStore(tmp_path / "state.json")
    store.update_section(
        "gps",
        {"synced": True, "sync_time": datetime.now(timezone.utc).isoformat()},
    )
    store.update_section("circumstances", {"loaded": True})
    store.update_section("capture", {"loaded": True})
    store.set("camera_config_file", "camera.json")

    eclipse = tmp_path / "todayeclipse.json"
    eclipse.write_text(
        json.dumps(
            {
                "TSTART": "10:00:00",
                "C1": "10:10:00",
                "C2": "10:20:00",
                "C3": "10:21:00",
                "C4": "10:30:00",
                "TEND": "10:40:00",
            }
        ),
        encoding="utf-8",
    )
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    trigger_script = scripts / "eclipse_trigger.py"
    trigger_script.write_text("", encoding="utf-8")
    configs = tmp_path / "configs"
    camera_configs = configs / "camera_cfg"
    camera_configs.mkdir(parents=True)
    (camera_configs / "camera.json").write_text("{}", encoding="utf-8")

    execution_plan_dir = configs / "execution_plan"
    execution_plan_dir.mkdir(parents=True)
    execution_plan_name = "test_execution_plan.json"
    (execution_plan_dir / execution_plan_name).write_text(
        json.dumps(
            {
                "schema_version": 2,
                "config_type": "execution_plan",
                "sequence_start_utc": "2027-08-02T10:00:00.000Z",
                "sequence_end_utc": "2027-08-02T10:40:00.000Z",
                "initial_state_required": {},
                "commands": [],
            }
        ),
        encoding="utf-8",
    )
    store.set("execution_plan_file", execution_plan_name)

    service = TriggerService(
        store,
        trigger_script,
        eclipse,
        tmp_path / "events.log",
        configs,
        lambda *args: None,
        lambda *args: None,
        camera_runtime=runtime,
        rig_config_loader=_rig_config,
    )
    return service


def test_import_construction_and_reconcile_are_lazy(tmp_path):
    runtime, servers = _make_runtime(tmp_path)
    service = _make_service(tmp_path, runtime)

    runtime.reconcile(_rig_config())

    assert service.camera_runtime is runtime
    assert servers == []
    assert not (tmp_path / "ipc").exists()


def test_start_passes_session_after_server_start_and_restart_keeps_clock(
    tmp_path, monkeypatch
):
    events = []
    clock = object()
    runtime, servers = _make_runtime(tmp_path, clock=clock, events=events)
    service = _make_service(tmp_path, runtime)
    launches = []

    def popen(_cmd, **kwargs):
        events.append("popen")
        socket_path = Path(kwargs["env"]["SET_CAMERA_IPC_SOCKET"])
        launches.append(
            (
                socket_path,
                kwargs["env"]["SET_CAMERA_IPC_SESSION"],
                socket_path.exists(),
                list(_cmd),
            )
        )
        return CompletedProcess()

    monkeypatch.setattr("backend.trigger_service.threading.Thread", ImmediateThread)
    monkeypatch.setattr("backend.trigger_service.subprocess.Popen", popen)

    assert service.start() is True
    assert events == ["server-started", "popen"]
    assert launches[0][1]
    assert launches[0][2] is True

    cmd = launches[0][3]
    plan_index = cmd.index("--execution-plan")
    assert Path(cmd[plan_index + 1]) == (
        tmp_path
        / "configs"
        / "execution_plan"
        / "test_execution_plan.json"
    )

    assert not launches[0][0].exists()
    assert runtime._ipc_server is None

    events.clear()
    assert service.start() is True
    assert events == ["server-started", "popen"]
    assert len(servers) == 2
    assert servers[0]._clock is clock
    assert servers[1]._clock is clock
    assert runtime._clock is clock
    assert not launches[1][0].exists()


def test_ipc_startup_failure_prevents_child_process(tmp_path, monkeypatch):
    class FailingServer(LifecycleServer):
        def start(self):
            raise RuntimeError("IPC startup failed")

    runtime, _servers = _make_runtime(tmp_path, server_type=FailingServer)
    service = _make_service(tmp_path, runtime)
    popen_called = False

    def popen(*args, **kwargs):
        nonlocal popen_called
        popen_called = True

    monkeypatch.setattr("backend.trigger_service.subprocess.Popen", popen)

    with pytest.raises(RuntimeError, match="IPC startup failed"):
        service.start()

    assert popen_called is False
    assert service._starting is False
    assert service.state.snapshot("trigger")["running"] is False
    assert runtime._ipc_server is None
    assert not (tmp_path / "ipc" / "camera-ipc-4321.sock").exists()


def test_popen_error_closes_session_and_removes_socket(tmp_path, monkeypatch):
    runtime, _servers = _make_runtime(tmp_path)
    service = _make_service(tmp_path, runtime)
    socket_seen = None

    def popen(_cmd, **kwargs):
        nonlocal socket_seen
        socket_seen = Path(kwargs["env"]["SET_CAMERA_IPC_SOCKET"])
        assert socket_seen.exists()
        raise OSError("cannot spawn trigger")

    monkeypatch.setattr("backend.trigger_service.threading.Thread", ImmediateThread)
    monkeypatch.setattr("backend.trigger_service.subprocess.Popen", popen)

    assert service.start() is True
    assert socket_seen is not None
    assert not socket_seen.exists()
    assert runtime._ipc_server is None
    assert runtime._ipc_session_ids == set()


def test_clean_exit_revokes_session_stops_service_and_removes_socket(
    tmp_path, monkeypatch
):
    runtime, servers = _make_runtime(tmp_path)
    service = _make_service(tmp_path, runtime)
    socket_seen = None

    def popen(_cmd, **kwargs):
        nonlocal socket_seen
        socket_seen = Path(kwargs["env"]["SET_CAMERA_IPC_SOCKET"])
        assert socket_seen.exists()
        return CompletedProcess()

    monkeypatch.setattr("backend.trigger_service.threading.Thread", ImmediateThread)
    monkeypatch.setattr("backend.trigger_service.subprocess.Popen", popen)

    assert service.start() is True
    assert len(servers) == 1
    assert servers[0]._active_session is None
    assert runtime._ipc_session_ids == set()
    assert runtime._ipc_server is None
    assert socket_seen is not None
    assert not socket_seen.exists()
    assert service.state.snapshot("trigger") == {
        "running": False,
        "phase": "idle",
        "mode": None,
        "speed": None,
    }


def test_forced_stop_terminates_then_kills_and_closes_session(tmp_path, monkeypatch):
    runtime, _servers = _make_runtime(tmp_path)
    service = _make_service(tmp_path, runtime)
    process_ready = threading.Event()
    killed = threading.Event()

    class BlockingStdout:
        def readline(self):
            killed.wait(timeout=2)
            return ""

    class ForcedKillProcess:
        def __init__(self):
            self.stdout = BlockingStdout()
            self.returncode = None
            self.terminated = False
            self.killed = False

        def poll(self):
            return self.returncode

        def terminate(self):
            self.terminated = True

        def kill(self):
            self.killed = True
            self.returncode = -9
            killed.set()

        def wait(self, timeout=None):
            if self.returncode is None:
                if timeout == 3:
                    raise subprocess.TimeoutExpired("trigger", timeout)
                killed.wait(timeout=timeout)
            return self.returncode

    proc = ForcedKillProcess()

    def popen(_cmd, **kwargs):
        assert Path(kwargs["env"]["SET_CAMERA_IPC_SOCKET"]).exists()
        process_ready.set()
        return proc

    monkeypatch.setattr("backend.trigger_service.subprocess.Popen", popen)

    assert service.start() is True
    assert process_ready.wait(timeout=2)
    deadline = time.monotonic() + 2
    while service._proc is None and time.monotonic() < deadline:
        time.sleep(0.01)
    assert service._proc is proc
    socket_path = runtime._ipc_server.socket_path

    result = service.stop()

    deadline = time.monotonic() + 2
    while runtime._ipc_server is not None and time.monotonic() < deadline:
        time.sleep(0.01)

    assert result == {"status": "stopped", "forced": True, "still_running": False}
    assert proc.terminated is True
    assert proc.killed is True
    assert runtime._ipc_server is None
    assert runtime._ipc_session_ids == set()
    assert not socket_path.exists()

def test_simulation_bypasses_rig_camera_validation_and_runtime(tmp_path, monkeypatch):
    runtime, _servers = _make_runtime(tmp_path)
    service = _make_service(tmp_path, runtime)

    def forbidden_validation(_config):
        raise AssertionError(
            "validate_execution_rigs must not run in simulation"
        )

    def forbidden_reconcile(_config):
        raise AssertionError(
            "camera runtime reconcile must not run in simulation"
        )

    def forbidden_ipc(*_args, **_kwargs):
        raise AssertionError(
            "camera IPC must not open in simulation"
        )

    class NoRunThread:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            pass

    monkeypatch.setattr(
        "backend.trigger_service.validate_execution_rigs",
        forbidden_validation,
    )
    monkeypatch.setattr(runtime, "reconcile", forbidden_reconcile)
    monkeypatch.setattr(runtime, "open_ipc_session", forbidden_ipc)
    monkeypatch.setattr(
        "backend.trigger_service.threading.Thread",
        NoRunThread,
    )

    assert service.start(simulate=True) is True
