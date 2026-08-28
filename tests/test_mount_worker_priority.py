from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from backend.mount_worker import MountWorker


class RecordingMountService:
    def __init__(self) -> None:
        self.executed: list[str] = []
        self.block_started = threading.Event()
        self.release_block = threading.Event()

    def close(self) -> None:
        pass

    def set_speed(self, value: str) -> None:
        self.executed.append(value)
        if value == "blocker":
            self.block_started.set()
            assert self.release_block.wait(1.0)

    def stop(self) -> None:
        self.executed.append("stop")


def _wait_for_queued_jobs(worker: MountWorker, count: int) -> None:
    deadline = time.monotonic() + 1.0
    while worker._worker._queue.qsize() < count:
        if time.monotonic() >= deadline:
            pytest.fail(f"expected {count} queued mount jobs")
        time.sleep(0.001)


def test_stop_overtakes_queued_manual_calls_after_in_flight_call():
    service = RecordingMountService()
    worker = MountWorker(rig_id=304, service_factory=lambda: service)
    worker.start()

    try:
        with ThreadPoolExecutor(max_workers=4) as callers:
            blocker = callers.submit(worker.set_speed, "blocker")
            assert service.block_started.wait(1.0)

            manuals = [
                callers.submit(worker.set_speed, f"manual-{index}")
                for index in range(2)
            ]
            _wait_for_queued_jobs(worker, 2)
            stop = callers.submit(worker.stop)
            _wait_for_queued_jobs(worker, 3)
            service.release_block.set()

            blocker.result(timeout=1.0)
            stop.result(timeout=1.0)
            for manual in manuals:
                manual.result(timeout=1.0)
    finally:
        service.release_block.set()
        worker.shutdown(timeout=1.0)

    assert service.executed == [
        "blocker",
        "stop",
        "manual-0",
        "manual-1",
    ]
