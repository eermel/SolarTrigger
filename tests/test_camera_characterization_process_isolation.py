import queue

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
