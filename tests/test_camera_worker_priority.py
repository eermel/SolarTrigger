from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from backend.camera_worker import CameraWorker
from backend.generic_worker import BusyDeviceError


class RecordingCameraService:
    def __init__(self) -> None:
        self.connected = True
        self.model = "Priority Test Camera"
        self.plugin = None
        self.executed: list[str] = []
        self.block_started = threading.Event()
        self.release_block = threading.Event()

    def close(self) -> None:
        pass

    def init_settings(self, **_kwargs):
        self.executed.append("manual")

    def prepare_capture(self, _intent):
        self.executed.append("sequencer")

    def shoot_speed_list(self, speeds, **_kwargs):
        label = speeds[0]
        self.executed.append(label)
        if label == "blocker":
            self.block_started.set()
            assert self.release_block.wait(1.0)
        return label

    def get_battery_level(self):
        self.executed.append("diagnostic")
        return 75


def _wait_for_queued_jobs(worker: CameraWorker, count: int) -> None:
    deadline = time.monotonic() + 1.0
    while worker._worker._queue.qsize() < count:
        if time.monotonic() >= deadline:
            pytest.fail(f"expected {count} queued camera jobs")
        time.sleep(0.001)


def test_probe_info_rejects_while_manual_job_is_running():
    service = RecordingCameraService()
    worker = CameraWorker(rig_id=301, service_factory=lambda: service)
    worker.start()

    try:
        with ThreadPoolExecutor(max_workers=1) as callers:
            blocker = callers.submit(worker.test_photo, ["blocker"])
            assert service.block_started.wait(1.0)

            with pytest.raises(BusyDeviceError):
                worker.probe_info()

            service.release_block.set()
            assert blocker.result(timeout=1.0) == "blocker"
    finally:
        service.release_block.set()
        worker.stop(timeout=1.0)


def test_sequencer_call_overtakes_queued_manual_calls():
    service = RecordingCameraService()
    worker = CameraWorker(rig_id=302, service_factory=lambda: service)
    worker.start()

    try:
        with ThreadPoolExecutor(max_workers=4) as callers:
            blocker = callers.submit(worker.test_photo, ["blocker"])
            assert service.block_started.wait(1.0)

            manuals = [
                callers.submit(worker.init_settings) for _ in range(2)
            ]
            _wait_for_queued_jobs(worker, 2)
            sequencer = callers.submit(worker.prepare_capture, object())
            _wait_for_queued_jobs(worker, 3)
            service.release_block.set()

            assert blocker.result(timeout=1.0) == "blocker"
            sequencer.result(timeout=1.0)
            for manual in manuals:
                manual.result(timeout=1.0)
    finally:
        service.release_block.set()
        worker.stop(timeout=1.0)

    assert service.executed == [
        "blocker",
        "sequencer",
        "manual",
        "manual",
    ]


def test_test_photo_overtakes_queued_diagnostic():
    service = RecordingCameraService()
    worker = CameraWorker(rig_id=303, service_factory=lambda: service)
    worker.start()

    try:
        with ThreadPoolExecutor(max_workers=3) as callers:
            blocker = callers.submit(worker.test_photo, ["blocker"])
            assert service.block_started.wait(1.0)

            diagnostic = callers.submit(worker.get_battery_level)
            _wait_for_queued_jobs(worker, 1)
            manual = callers.submit(worker.test_photo, ["test-photo"])
            _wait_for_queued_jobs(worker, 2)
            service.release_block.set()

            assert blocker.result(timeout=1.0) == "blocker"
            assert manual.result(timeout=1.0) == "test-photo"
            assert diagnostic.result(timeout=1.0) == 75
    finally:
        service.release_block.set()
        worker.stop(timeout=1.0)

    assert service.executed == ["blocker", "test-photo", "diagnostic"]
