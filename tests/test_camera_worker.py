from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

from backend.camera_worker import CameraWorker


class DummyService:
    def __init__(self) -> None:
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1

    def do_sleep(self, result, seconds):
        time.sleep(seconds)
        return result

    def shoot_speed_list(self, speeds, **_kwargs):
        return self.do_sleep(*speeds)


def _camera_worker_threads(rig_id: int) -> list[threading.Thread]:
    name = f"camera-worker-r{rig_id}"
    return [thread for thread in threading.enumerate() if thread.name == name]


def test_two_camera_workers_do_not_block_each_other():
    services = [DummyService(), DummyService()]
    worker_a = CameraWorker(rig_id=201, service_factory=lambda: services[0])
    worker_b = CameraWorker(rig_id=202, service_factory=lambda: services[1])
    worker_a.start()
    worker_b.start()

    try:
        with ThreadPoolExecutor(max_workers=2) as callers:
            long_future = callers.submit(worker_a.test_photo, ("long", 0.5))
            time.sleep(0.05)

            started_at = time.monotonic()
            short_future = callers.submit(worker_b.test_photo, ("short", 0.01))

            assert short_future.result(timeout=0.2) == "short"
            assert time.monotonic() - started_at < 0.2
            assert not long_future.done()
            assert long_future.result(timeout=1.0) == "long"
    finally:
        worker_a.stop(timeout=1.0)
        worker_b.stop(timeout=1.0)

    assert not worker_a.running
    assert not worker_b.running
    assert not _camera_worker_threads(201)
    assert not _camera_worker_threads(202)
    assert [service.close_calls for service in services] == [1, 1]
