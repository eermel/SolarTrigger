"""Contract tests: budgets, no stale execution, real worker backpressure."""
from datetime import datetime, timedelta
from types import SimpleNamespace
import threading
import time

import pytest

from backend.camera_timing_contract import (
    SAFETY_POLICY,
    budget_ms,
    photo_budget_ms,
    validate_timing_contract_v3,
)
from backend.camera_worker import CameraWorker
from backend.generic_worker import BusyDeviceError, ExpiredJobError


def test_margin_uses_peak_not_median_and_never_rounds_down():
    assert budget_ms([280, 285, 300, 290, 286]) == 400
    assert budget_ms([804]) == 950
    with pytest.raises(ValueError):
        budget_ms([float("nan")])


def _base_contract(**overrides):
    contract = {
        "version": 3,
        "safety_policy": dict(SAFETY_POLICY),
        "set_overhead_ms": 350,
        "single_overhead_ms": 1250,
        "bracket_overhead_ms": 0,
        "bracket_inter_image_ms": 0,
        "supported_bracket_frames": [],
    }
    contract.update(overrides)
    return contract


def test_session_first_photo_overhead_is_optional_and_backward_compatible():
    # Old v3 profiles characterized before this field existed remain valid.
    validate_timing_contract_v3(_base_contract())


def test_session_first_photo_overhead_must_be_a_multiple_of_50_when_present():
    validate_timing_contract_v3(
        _base_contract(session_first_photo_overhead_ms=4550)
    )
    with pytest.raises(ValueError):
        validate_timing_contract_v3(
            _base_contract(session_first_photo_overhead_ms=4551)
        )
    with pytest.raises(ValueError):
        validate_timing_contract_v3(
            _base_contract(session_first_photo_overhead_ms=-50)
        )


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
