from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from backend.focuser_worker import FocuserWorker


class RecordingFocuserService:
    def __init__(self) -> None:
        self.executed: list[str] = []
        self.block_started = threading.Event()
        self.release_block = threading.Event()

    def close(self) -> None:
        pass

    def move_to(self, position: str, wait: bool = False) -> None:
        self.executed.append(position)
        if position == "blocker":
            self.block_started.set()
            assert self.release_block.wait(1.0)

    def stop(self) -> None:
        self.executed.append("stop")

    def stop_jog(self) -> None:
        self.executed.append("stop_jog")


def _wait_for_queued_jobs(worker: FocuserWorker, count: int) -> None:
    deadline = time.monotonic() + 1.0
    while worker._worker._queue.qsize() < count:
        if time.monotonic() >= deadline:
            pytest.fail(f"expected {count} queued focuser jobs")
        time.sleep(0.001)


@pytest.mark.parametrize("stop_method", ["stop", "stop_jog"])
def test_stop_calls_overtake_queued_manual_calls(stop_method: str) -> None:
    service = RecordingFocuserService()
    worker = FocuserWorker(rig_id=304, service_factory=lambda: service)
    worker.start()

    try:
        with ThreadPoolExecutor(max_workers=4) as callers:
            blocker = callers.submit(worker.move_to, "blocker")
            assert service.block_started.wait(1.0)

            manuals = [
                callers.submit(worker.move_to, f"manual-{index}")
                for index in range(2)
            ]
            _wait_for_queued_jobs(worker, 2)
            stop = callers.submit(getattr(worker, stop_method))
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
        stop_method,
        "manual-0",
        "manual-1",
    ]
