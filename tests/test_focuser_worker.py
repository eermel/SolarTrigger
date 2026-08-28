from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

from backend.focuser_worker import FocuserWorker


class DummyFocuserService:
    def __init__(self, marker: str) -> None:
        self.marker = marker
        self.move_started = threading.Event()
        self.jog_started = threading.Event()
        self.jog_stop = threading.Event()
        self.jog_stopped = threading.Event()
        self.jog_thread: threading.Thread | None = None
        self.close_calls = 0

    def move_to(self, duration: float, wait: bool = False) -> str:
        self.move_started.set()
        time.sleep(duration)
        return self.marker

    def start_jog(self, direction: str, mode: str | None = None) -> None:
        self.jog_stop.clear()
        self.jog_stopped.clear()

        def jog() -> None:
            self.jog_started.set()
            while not self.jog_stop.wait(timeout=0.01):
                pass
            self.jog_stopped.set()

        self.jog_thread = threading.Thread(target=jog)
        self.jog_thread.start()

    def stop(self) -> None:
        self.jog_stop.set()
        if self.jog_thread is not None:
            self.jog_thread.join(timeout=0.2)

    def close(self) -> None:
        self.close_calls += 1


def _focuser_worker_threads(rig_id: str) -> list[threading.Thread]:
    name = f"focuser-worker-r{rig_id}"
    return [thread for thread in threading.enumerate() if thread.name == name]


def test_two_focuser_workers_operate_independently() -> None:
    long_duration = 0.3
    short_duration = 0.03
    service_a = DummyFocuserService("A complete")
    service_b = DummyFocuserService("B complete")
    worker_a = FocuserWorker(rig_id="A", service_factory=lambda: service_a)
    worker_b = FocuserWorker(rig_id="B", service_factory=lambda: service_b)
    worker_a.start()
    worker_b.start()

    try:
        assert len(_focuser_worker_threads("A")) == 1
        assert len(_focuser_worker_threads("B")) == 1

        with ThreadPoolExecutor(max_workers=2) as callers:
            long_future = callers.submit(worker_a.move_to, long_duration)
            assert service_a.move_started.wait(timeout=1.0)

            short_started_at = time.monotonic()
            short_future = callers.submit(worker_b.move_to, short_duration)
            assert service_b.move_started.wait(timeout=1.0)

            assert short_future.result(timeout=1.0) == "B complete"
            short_finished_at = time.monotonic()
            assert short_finished_at - short_started_at < long_duration
            assert not long_future.done()
            assert long_future.result(timeout=1.0) == "A complete"
    finally:
        worker_a.shutdown(timeout=1.0)
        worker_b.shutdown(timeout=1.0)

    assert not _focuser_worker_threads("A")
    assert not _focuser_worker_threads("B")
    assert service_a.close_calls == 1
    assert service_b.close_calls == 1


def test_stop_terminates_ongoing_jog_and_is_idempotent() -> None:
    service = DummyFocuserService("jog complete")
    worker = FocuserWorker(rig_id="stop", service_factory=lambda: service)
    worker.start()

    try:
        worker.start_jog("out")
        assert service.jog_started.wait(timeout=1.0)
        assert service.jog_thread is not None
        assert service.jog_thread.is_alive()

        stop_started_at = time.monotonic()
        worker.stop()
        assert time.monotonic() - stop_started_at < 0.2
        assert service.jog_stopped.wait(timeout=0.2)
        assert not service.jog_thread.is_alive()

        worker.stop()
        assert not service.jog_thread.is_alive()
    finally:
        if service.jog_thread is not None and service.jog_thread.is_alive():
            worker.stop()
        worker.shutdown(timeout=1.0)

    assert service.close_calls == 1
    assert service.jog_thread is not None
    assert not service.jog_thread.is_alive()
