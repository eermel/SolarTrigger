"""Contract tests: budgets, no stale execution, real worker backpressure."""
from datetime import datetime, timedelta
from types import SimpleNamespace
import threading
import time

import pytest

from backend.camera_timing_contract import budget_ms, photo_budget_ms
from backend.execution_plan_runtime import ExecutionPlanRuntime
from backend.camera_worker import CameraWorker
from backend.generic_worker import BusyDeviceError, ExpiredJobError


def test_margin_uses_peak_not_median_and_never_rounds_down():
    assert budget_ms([280, 285, 300, 290, 286]) == 400
    assert budget_ms([804]) == 950
    with pytest.raises(ValueError):
        budget_ms([float("nan")])


def test_exposure_is_not_counted_twice():
    block = {"duration_ms": 1000, "reference_exposure_s": .002}
    assert photo_budget_ms(block, .002) == 1000
    assert photo_budget_ms(block, .001) == 1000
    assert photo_budget_ms(block, 1.002) == 2100


class Clock:
    def __init__(self): self.current = datetime(2027, 8, 2, 10)
    def now(self): return self.current
    def remaining(self, target): return (target - self.current).total_seconds()
    def sleep(self, seconds): self.current += timedelta(seconds=seconds)


def commands(clock):
    result = []
    for second in (1, 5, 9):
        for offset, parameter in enumerate(("iso", "capture_setup", None)):
            params = {"duration_ms": 900, "timing_contract_version": 2}
            if parameter:
                params.update(parameter=parameter, value="100" if parameter == "iso" else {"shutter": "1/500"})
            else:
                params.update(shutter="1/500")
            result.append({"time": clock.now() + timedelta(seconds=second+offset), "rig_id": 1,
                           "action": "SET" if parameter else "PHOTO", "params": params, "index": len(result)})
    return result


def test_usb_failure_skips_photo_but_resumes_future_complete_preparation():
    clock, calls, logs = Clock(), [], []
    class Camera:
        def set_parameter(self, rig, parameter, value, **kwargs):
            calls.append(("SET", parameter, clock.now().second))
            assert kwargs["scheduled"] is True
            if parameter == "iso" and clock.now().second == 1:
                raise RuntimeError("USB unavailable")
        def execute_photo(self, rig, params, **kwargs):
            calls.append(("PHOTO", clock.now().second))
    runtime = ExecutionPlanRuntime(clock=clock, camera_client=Camera(), log_fn=logs.append)
    runtime._run_rig(1, commands(clock))
    assert [c for c in calls if c[0] == "PHOTO"] == [("PHOTO", 7), ("PHOTO", 11)]
    assert sum(c[:2] == ("SET", "iso") for c in calls) == 3  # No replay at PHOTO time.
    assert any("unapplied_settings" in line for line in logs)


def test_slow_capture_drops_elapsed_group_without_stopping_rig():
    clock, calls = Clock(), []
    class Camera:
        def set_parameter(self, rig, parameter, value, **kwargs):
            calls.append((parameter, clock.now().second))
        def execute_photo(self, rig, params, **kwargs):
            calls.append(("PHOTO", clock.now().second))
            if clock.now().second == 3:
                clock.sleep(4.5)
    runtime = ExecutionPlanRuntime(clock=clock, camera_client=Camera(), log_fn=lambda _: None)
    runtime._run_rig(1, commands(clock))
    assert calls == [("iso", 1), ("capture_setup", 2), ("PHOTO", 3),
                     ("iso", 9), ("capture_setup", 10), ("PHOTO", 11)]


def test_busy_worker_does_not_queue_missed_photos():
    entered, release = threading.Event(), threading.Event()
    calls = []
    class Service:
        connected = True
        def execute_photo(self, params):
            calls.append(params)
            entered.set()
            assert release.wait(2)
        def close(self): pass
    worker = CameraWorker(1, service_factory=Service, log_fn=lambda _: None)
    worker.start()
    task = threading.Thread(target=lambda: worker.execute_photo({"id": 1}))
    task.start()
    try:
        assert entered.wait(1)
        with pytest.raises(BusyDeviceError):
            worker.execute_photo({"id": 2}, reject_if_busy=True,
                                 worker_deadline=time.monotonic()+.1)
    finally:
        release.set()
        task.join(2)
        worker.stop(2)
    assert calls == [{"id": 1}]


def test_expired_command_never_reaches_camera():
    calls = []
    service = SimpleNamespace(connected=True, execute_photo=lambda p: calls.append(p), close=lambda: None)
    worker = CameraWorker(1, service_factory=lambda: service, log_fn=lambda _: None)
    worker.start()
    try:
        with pytest.raises(ExpiredJobError):
            worker.execute_photo({}, reject_if_busy=True, worker_deadline=time.monotonic()-1)
    finally:
        worker.stop(2)
    assert calls == []
