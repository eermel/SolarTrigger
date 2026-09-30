from __future__ import annotations

import os
import signal
import time

import pytest

import backend.device_process_worker as device_process_worker
import backend.focuser_process_worker as focuser_process_worker
import backend.mount_process_worker as mount_process_worker
from backend.device_process_worker import MotionStateUnknownError, arm_parent_death_signal
from backend.focuser_process_worker import ProcessFocuserWorker
from backend.generic_worker import WorkerTimeoutError, WorkerUnavailableError
from backend.mount_process_worker import ProcessMountWorker
from plugins.mount.indi_client import IndiClientError


def _fake_device_child(conn, spec, call_timeout_s):
    conn.send({"kind": "ready"})

    while True:
        request = conn.recv()
        operation = request.get("operation")

        if operation == "__shutdown__":
            return

        if operation == "status":
            conn.send(
                {
                    "kind": "result",
                    "value": {
                        "rig_id": spec["rig_id"],
                        "backend": spec["backend"],
                        "supervisor_pid": spec.get("_supervisor_pid"),
                    },
                }
            )
            continue

        if operation in {"start_slew", "move_to"}:
            while True:
                time.sleep(1)

        if operation == "warmup":
            conn.send(
                {
                    "kind": "error",
                    "module": "plugins.mount.indi_client",
                    "class": "IndiClientError",
                    "code": "INDI_UNAVAILABLE",
                    "message": "INDI is unavailable",
                    "command": ["indi_getprop"],
                    "returncode": 1,
                    "stderr": "failed",
                }
            )
            continue

        conn.send(
            {
                "kind": "result",
                "value": {"operation": operation},
            }
        )


def _mount(timeout=0.05):
    worker = ProcessMountWorker(
        rig_id=1,
        backend="indi",
        device_config={"serial": "mount-1"},
        state_path="/tmp/process-worker-test-state.json",
        call_timeout_s=timeout,
        log_fn=lambda _message: None,
        process_target=_fake_device_child,
    )
    worker.start()
    return worker


def _focuser(timeout=0.05):
    worker = ProcessFocuserWorker(
        rig_id=2,
        backend="zwo_eaf",
        device_config={"serial": "focus-2"},
        state_path="/tmp/process-worker-test-state.json",
        call_timeout_s=timeout,
        log_fn=lambda _message: None,
        process_target=_fake_device_child,
    )
    worker.start()
    return worker


def _clean_exit_child(conn, spec, call_timeout_s):
    conn.send({"kind": "ready"})
    conn.recv()
    conn.close()


def test_process_start_log_includes_pid_generation_and_rig():
    logs = []
    worker = ProcessMountWorker(
        rig_id=3,
        backend="indi",
        device_config={"serial": "mount-3"},
        state_path="/tmp/process-worker-test-state.json",
        call_timeout_s=0.05,
        log_fn=logs.append,
        process_target=_fake_device_child,
    )

    try:
        worker.start()
        assert any(
            "mount child started rig=3 pid=" in message
            and "generation=1" in message
            for message in logs
        )
    finally:
        worker.shutdown()


def test_ipc_close_reports_pid_generation_alive_and_exitcode():
    worker = ProcessMountWorker(
        rig_id=4,
        backend="indi",
        device_config={"serial": "mount-4"},
        state_path="/tmp/process-worker-test-state.json",
        call_timeout_s=0.05,
        log_fn=lambda _message: None,
        process_target=_clean_exit_child,
    )
    worker.start()

    try:
        with pytest.raises(WorkerUnavailableError) as caught:
            worker.status()

        message = str(caught.value)
        assert "mount child IPC closed during status" in message
        assert "pid=" in message
        assert "generation=1" in message
        assert "alive=False" in message
        assert "exitcode=0" in message
    finally:
        worker.shutdown()


def test_mount_process_returns_normal_result():
    worker = _mount()

    try:
        assert worker.status() == {
            "rig_id": 1,
            "backend": "indi",
            "supervisor_pid": os.getpid(),
        }
        assert worker.generation == 1
    finally:
        worker.shutdown()


def test_focuser_process_returns_normal_result():
    worker = _focuser()

    try:
        assert worker.status() == {
            "rig_id": 2,
            "backend": "zwo_eaf",
            "supervisor_pid": os.getpid(),
        }
        assert worker.generation == 1
    finally:
        worker.shutdown()


def test_hung_mount_is_killed_and_next_command_respawns():
    worker = _mount()

    try:
        generation = worker.generation
        started = time.monotonic()

        with pytest.raises(WorkerTimeoutError):
            worker.start_slew("east")

        assert time.monotonic() - started < 3.0
        assert not worker.running
        assert worker.generation == generation

        assert worker.status()["backend"] == "indi"
        assert worker.generation == generation + 1
    finally:
        worker.shutdown()


def test_hung_focuser_is_killed_and_next_command_respawns():
    worker = _focuser()

    try:
        generation = worker.generation
        started = time.monotonic()

        with pytest.raises(WorkerTimeoutError):
            worker.move_to(12345)

        assert time.monotonic() - started < 3.0
        assert not worker.running
        assert worker.generation == generation

        assert worker.status()["backend"] == "zwo_eaf"
        assert worker.generation == generation + 1
    finally:
        worker.shutdown()


def test_mount_indi_error_contract_is_preserved():
    worker = _mount()

    try:
        with pytest.raises(IndiClientError) as caught:
            worker.warmup()

        assert caught.value.code == "INDI_UNAVAILABLE"
        assert caught.value.command == ["indi_getprop"]
        assert caught.value.returncode == 1
        assert caught.value.stderr == "failed"
    finally:
        worker.shutdown()


@pytest.mark.parametrize("factory", [_mount, _focuser])
def test_shutdown_is_bounded_for_hung_child(factory):
    worker = factory(timeout=5.0)

    conn = worker._conn
    assert conn is not None

    operation = (
        "start_slew"
        if worker.device_kind == "mount"
        else "move_to"
    )
    args = (
        ("east",)
        if worker.device_kind == "mount"
        else (12345,)
    )

    conn.send(
        {
            "operation": operation,
            "args": args,
            "kwargs": {},
        }
    )

    time.sleep(0.05)
    started = time.monotonic()

    assert worker.shutdown(timeout=0.05) is True
    assert time.monotonic() - started < 2.0
    assert not worker.running



def test_mount_timeout_interlocks_new_motion_until_physical_stop():
    worker = _mount()

    try:
        with pytest.raises(WorkerTimeoutError):
            worker.start_slew("east")

        assert worker.motion_state_unknown is True
        assert worker.motion_state_unknown_operation == "start_slew"

        # Telemetry is allowed, but does not prove that the physical mount
        # stopped.  Only an explicit recovery STOP may clear the interlock.
        assert worker.status()["backend"] == "indi"
        assert worker.motion_state_unknown is True

        with pytest.raises(MotionStateUnknownError) as caught:
            worker.home_start()

        assert caught.value.code == "MOTION_STATE_UNKNOWN"

        result = worker.stop()

        assert result["operation"] == "emergency_stop"
        assert worker.motion_state_unknown is False
        assert worker.motion_state_unknown_operation is None
    finally:
        worker.shutdown()


def test_focuser_timeout_interlocks_new_motion_until_stop():
    worker = _focuser()

    try:
        with pytest.raises(WorkerTimeoutError):
            worker.move_to(12345)

        assert worker.motion_state_unknown is True
        assert worker.motion_state_unknown_operation == "move_to"

        assert worker.status()["backend"] == "zwo_eaf"
        assert worker.motion_state_unknown is True

        with pytest.raises(MotionStateUnknownError) as caught:
            worker.start_jog("out")

        assert caught.value.code == "MOTION_STATE_UNKNOWN"

        result = worker.stop()

        assert result["operation"] == "stop"
        assert worker.motion_state_unknown is False
        assert worker.motion_state_unknown_operation is None
    finally:
        worker.shutdown()


def test_parent_death_signal_arms_sigterm_and_validates_parent(monkeypatch):
    calls = []

    class FakePrctl:
        argtypes = None
        restype = None

        def __call__(self, *args):
            calls.append(args)
            return 0

    class FakeLibc:
        prctl = FakePrctl()

    monkeypatch.setattr(device_process_worker.sys, "platform", "linux")
    monkeypatch.setattr(
        device_process_worker.ctypes,
        "CDLL",
        lambda *_args, **_kwargs: FakeLibc(),
    )
    monkeypatch.setattr(device_process_worker.os, "getppid", lambda: 4321)

    arm_parent_death_signal(4321)

    assert calls == [
        (
            device_process_worker._PR_SET_PDEATHSIG,
            int(signal.SIGTERM),
            0,
            0,
            0,
        )
    ]


def test_parent_death_signal_rejects_child_already_reparented(monkeypatch):
    class FakePrctl:
        argtypes = None
        restype = None

        def __call__(self, *_args):
            return 0

    class FakeLibc:
        prctl = FakePrctl()

    monkeypatch.setattr(device_process_worker.sys, "platform", "linux")
    monkeypatch.setattr(
        device_process_worker.ctypes,
        "CDLL",
        lambda *_args, **_kwargs: FakeLibc(),
    )
    monkeypatch.setattr(device_process_worker.os, "getppid", lambda: 1)

    with pytest.raises(RuntimeError, match="supervisor disappeared"):
        arm_parent_death_signal(4321)


@pytest.mark.parametrize(
    ("module", "entrypoint", "worker_name", "spec"),
    [
        (
            mount_process_worker,
            mount_process_worker._mount_process_main,
            "MountWorker",
            {
                "_supervisor_pid": 1234,
                "state_path": "/tmp/state.json",
                "backend": "indi",
                "rig_id": 1,
                "device_config": {},
            },
        ),
        (
            focuser_process_worker,
            focuser_process_worker._focuser_process_main,
            "FocuserWorker",
            {
                "_supervisor_pid": 1234,
                "state_path": "/tmp/state.json",
                "backend": "zwo_eaf",
                "rig_id": 2,
                "device_config": {},
            },
        ),
    ],
)
def test_hardware_child_arms_parent_death_before_worker_construction(
    monkeypatch,
    module,
    entrypoint,
    worker_name,
    spec,
):
    events = []

    monkeypatch.setattr(
        module,
        "arm_parent_death_signal",
        lambda parent_pid: events.append(("arm", parent_pid)),
    )

    class DummyWorker:
        def __init__(self, **_kwargs):
            events.append(("construct", worker_name))

    monkeypatch.setattr(module, worker_name, DummyWorker)
    monkeypatch.setattr(
        module,
        "serve_worker",
        lambda *_args, **_kwargs: events.append(("serve", worker_name)),
    )

    entrypoint(object(), dict(spec), 1.0)

    assert events == [
        ("arm", 1234),
        ("construct", worker_name),
        ("serve", worker_name),
    ]


class _CloseTrackingConn:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class _StartFailProcess:
    def start(self):
        raise RuntimeError("synthetic process spawn failure")


class _StartFailContext:
    def __init__(self):
        self.parent = _CloseTrackingConn()
        self.child = _CloseTrackingConn()
        self.process = _StartFailProcess()

    def Pipe(self, duplex=True):
        assert duplex is True
        return self.parent, self.child

    def Process(self, **_kwargs):
        return self.process


def test_mount_process_spawn_failure_rolls_back_and_closes_pipe():
    worker = ProcessMountWorker(
        rig_id=9,
        backend="indi",
        device_config={"serial": "mount-9"},
        state_path="/tmp/process-worker-test-state.json",
        call_timeout_s=0.05,
        log_fn=lambda _message: None,
        process_target=_fake_device_child,
    )
    context = _StartFailContext()
    worker._ctx = context

    with pytest.raises(RuntimeError, match="synthetic process spawn failure"):
        worker.start()

    assert worker._started is False
    assert worker._process is None
    assert worker._conn is None
    assert worker.generation == 0
    assert context.parent.closed is True
    assert context.child.closed is True


def test_focuser_stop_jog_clears_unknown_motion_after_timeout():
    worker = _focuser()

    try:
        with pytest.raises(WorkerTimeoutError):
            worker.move_to(12345)

        assert worker.motion_state_unknown is True

        result = worker.stop_jog()

        assert result["operation"] == "stop_jog"
        assert worker.motion_state_unknown is False
        assert worker.motion_state_unknown_operation is None

        # New motion is admitted after the explicit physical jog STOP.
        assert worker.start_jog("out")["operation"] == "start_jog"
    finally:
        worker.shutdown()
