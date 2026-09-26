import queue

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
