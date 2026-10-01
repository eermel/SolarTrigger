import inspect
import queue

import pytest

import backend.camera_characterization as characterization
from backend.camera_characterization import CharacterizationJob


class _ExitedProcess:
    def __init__(self, exitcode):
        self.exitcode = exitcode

    def is_alive(self):
        return False

    def join(self, timeout=None):
        return None


def test_native_characterization_crash_is_contained_as_failed_job(tmp_path):
    job = CharacterizationJob()
    job.running = True
    job.job_id = "deadbeef"
    job.measurement_path = (
        tmp_path / "configs/camera_characterization/measurements/deadbeef.json"
    )

    job._monitor_process(_ExitedProcess(-11), queue.Queue())

    assert job.running is False
    assert job.result["status"] == "FAILED"
    assert "signal 11" in job.result["error"]
    assert any("signal 11" in line for line in job.logs)
    assert job.measurement_path.exists()


def test_characterization_uses_spawned_native_process():
    import inspect
    from backend import camera_characterization as module

    source = inspect.getsource(module.CharacterizationJob.start)
    assert 'multiprocessing.get_context("spawn")' in source
    assert "ctx.Process(" in source
    assert "target=_characterization_process_main" in source
    assert "target=self._run" not in source


def test_characterization_child_arms_parent_death_before_gphoto_open():
    source = inspect.getsource(characterization._characterization_process_main)

    assert "arm_parent_death_signal(expected_parent_pid)" in source
    assert source.index("arm_parent_death_signal(expected_parent_pid)") < source.index(
        "import_gphoto2"
    )


def test_characterization_spawn_passes_exact_supervisor_pid():
    source = inspect.getsource(characterization.CharacterizationJob.start)

    assert "target=_characterization_process_main" in source
    assert "os.getpid()" in source


class _HungProcess:
    def __init__(self):
        self.alive = True
        self.terminate_calls = 0
        self.kill_calls = 0
        self.join_calls = []

    def is_alive(self):
        return self.alive

    def join(self, timeout=None):
        self.join_calls.append(timeout)

    def terminate(self):
        self.terminate_calls += 1

    def kill(self):
        self.kill_calls += 1
        self.alive = False


def test_characterization_cancel_watchdog_escalates_boundedly():
    job = CharacterizationJob()
    process = _HungProcess()

    job._cancel_watchdog(process)

    assert process.terminate_calls == 1
    assert process.kill_calls == 1
    assert process.join_calls == [
        characterization.CANCEL_COOPERATIVE_GRACE_S,
        characterization.CANCEL_TERMINATE_GRACE_S,
        characterization.CANCEL_KILL_GRACE_S,
    ]
    assert job._cancel_watchdog_active is False


def test_characterization_repeated_cancel_starts_only_one_watchdog(monkeypatch):
    job = CharacterizationJob()
    process = _HungProcess()
    job._process = process
    job._command_queue = queue.Queue()

    started = []

    class DeferredThread:
        def __init__(self, *, target, args, name, daemon):
            started.append((target, args, name, daemon))

        def start(self):
            return None

    monkeypatch.setattr(characterization.threading, "Thread", DeferredThread)

    job.cancel()
    job.cancel()

    assert job.cancelled is True
    assert job._cancel_watchdog_active is True
    assert len(started) == 1
    assert started[0][0] == job._cancel_watchdog
    assert started[0][1] == (process,)


class _QueueStub:
    def __init__(self):
        self.items = []
        self.closed = False

    def put(self, item):
        self.items.append(item)

    def close(self):
        self.closed = True

    def join_thread(self):
        return None


class _ContextStub:
    def __init__(self, process):
        self.process = process
        self.queues = []

    def Queue(self):
        queue_obj = _QueueStub()
        self.queues.append(queue_obj)
        return queue_obj

    def Process(self, **_kwargs):
        return self.process


class _StartFailProcess:
    def start(self):
        raise RuntimeError("spawn failed")


def test_characterization_process_start_failure_rolls_back_job(
    tmp_path,
    monkeypatch,
):
    job = CharacterizationJob()
    process = _StartFailProcess()
    context = _ContextStub(process)
    monkeypatch.setattr(
        characterization.multiprocessing,
        "get_context",
        lambda method: context if method == "spawn" else None,
    )

    with pytest.raises(RuntimeError, match="spawn failed"):
        job.start(
            {
                "manufacturer": "Test",
                "model": "Camera",
                "transport_locator": "usb:001,002",
            },
            root=tmp_path,
        )

    assert job.running is False
    assert job._process is None
    assert job._command_queue is None
    assert job.result["status"] == "FAILED"
    assert "spawn failed" in job.result["error"]
    assert all(queue_obj.closed for queue_obj in context.queues)


class _StartedProcess:
    def __init__(self, *, stoppable=True):
        self.alive = False
        self.stoppable = stoppable
        self.terminate_calls = 0
        self.kill_calls = 0
        self.join_calls = []

    def start(self):
        self.alive = True

    def is_alive(self):
        return self.alive

    def terminate(self):
        self.terminate_calls += 1
        if self.stoppable:
            self.alive = False

    def kill(self):
        self.kill_calls += 1
        if self.stoppable:
            self.alive = False

    def join(self, timeout=None):
        self.join_calls.append(timeout)


class _FailingMonitorThread:
    def __init__(self, **_kwargs):
        pass

    def start(self):
        raise RuntimeError("monitor start failed")


def test_characterization_monitor_start_failure_kills_child_and_rolls_back(
    tmp_path,
    monkeypatch,
):
    job = CharacterizationJob()
    process = _StartedProcess(stoppable=True)
    context = _ContextStub(process)
    monkeypatch.setattr(
        characterization.multiprocessing,
        "get_context",
        lambda method: context if method == "spawn" else None,
    )
    monkeypatch.setattr(
        characterization.threading,
        "Thread",
        _FailingMonitorThread,
    )

    with pytest.raises(RuntimeError, match="monitor start failed"):
        job.start(
            {
                "manufacturer": "Test",
                "model": "Camera",
                "transport_locator": "usb:001,002",
            },
            root=tmp_path,
        )

    assert process.terminate_calls == 1
    assert process.is_alive() is False
    assert job.running is False
    assert job._process is None
    assert job.result["status"] == "FAILED"


def test_characterization_monitor_start_failure_retains_surviving_child(
    tmp_path,
    monkeypatch,
):
    job = CharacterizationJob()
    process = _StartedProcess(stoppable=False)
    context = _ContextStub(process)
    monkeypatch.setattr(
        characterization.multiprocessing,
        "get_context",
        lambda method: context if method == "spawn" else None,
    )
    monkeypatch.setattr(
        characterization.threading,
        "Thread",
        _FailingMonitorThread,
    )

    with pytest.raises(RuntimeError, match="monitor start failed"):
        job.start(
            {
                "manufacturer": "Test",
                "model": "Camera",
                "transport_locator": "usb:001,002",
            },
            root=tmp_path,
        )

    assert process.terminate_calls == 1
    assert process.kill_calls == 1
    assert process.is_alive() is True
    assert job.running is True
    assert job.cancelled is True
    assert job._process is process
    assert job.result["status"] == "FAILED"
    assert "still alive" in job.result["error"]


def test_characterization_global_runtime_watchdog_terminates_hung_child(tmp_path):
    job = CharacterizationJob(max_runtime_s=0.01)
    process = _HungProcess()
    commands = queue.Queue()
    events = queue.Queue()

    job.running = True
    job.job_id = "timeout"
    job._process = process
    job._command_queue = commands
    job._monitor_started = True
    job._started_monotonic = characterization.time.monotonic() - 1.0
    job.measurement_path = (
        tmp_path / "configs/camera_characterization/measurements/timeout.json"
    )

    job._monitor_process(process, events)

    assert job.running is False
    assert job.result["status"] == "FAILED"
    assert "global runtime limit" in job.result["error"]
    assert process.terminate_calls == 1
    assert process.kill_calls == 1
    assert job._process is None
    assert job._started_monotonic is None
    assert job.measurement_path.exists()


def test_characterization_global_runtime_limit_is_one_hour_by_default():
    assert characterization.CHARACTERIZATION_MAX_RUNTIME_S == 3600.0
    assert CharacterizationJob().max_runtime_s == 3600.0


def test_deferred_characterization_waits_before_gphoto_open():
    source = inspect.getsource(characterization._characterization_process_main)
    gate = source.index("job.wait_for_start()")
    gphoto = source.index("import_gphoto2")
    assert gate < gphoto


def test_release_camera_open_signals_deferred_child_once():
    job = CharacterizationJob()
    commands = queue.Queue()
    job.running = True
    job._command_queue = commands
    job._camera_open_deferred = True

    assert job.release_camera_open() is True
    assert commands.get_nowait() == ("start", None)
    assert job._camera_open_deferred is False
    assert job.release_camera_open() is True


def test_cancelled_deferred_characterization_cannot_open_camera():
    job = CharacterizationJob()
    job.running = True
    job.cancelled = True
    job._command_queue = queue.Queue()
    job._camera_open_deferred = True

    assert job.release_camera_open() is False
    assert job._camera_open_deferred is False
