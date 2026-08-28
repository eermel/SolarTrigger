from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

from backend.camera_worker import CameraWorker
from backend.focuser_worker import FocuserWorker


class DummyFocuserService:
    def __init__(self) -> None:
        self.move_started = threading.Event()
        self.close_calls = 0

    def move_to(self, duration: float, **_kwargs) -> str:
        self.move_started.set()
        time.sleep(duration)
        return "focuser-finished"

    def close(self) -> None:
        self.close_calls += 1


class DummyCameraService:
    def __init__(self) -> None:
        self.close_calls = 0

    def shoot_speed_list(self, duration: float, **_kwargs) -> str:
        time.sleep(duration)
        return "camera-finished"

    def close(self) -> None:
        self.close_calls += 1


def _worker_threads(device_kind: str, rig_id: int) -> list[threading.Thread]:
    name = f"{device_kind}-worker-r{rig_id}"
    return [thread for thread in threading.enumerate() if thread.name == name]


def test_camera_shoots_run_while_focuser_is_busy_on_same_rig() -> None:
    rig_id = 601
    focuser_service = DummyFocuserService()
    camera_service = DummyCameraService()
    focuser_worker = FocuserWorker(
        rig_id=rig_id,
        service_factory=lambda: focuser_service,
    )
    camera_worker = CameraWorker(
        rig_id=rig_id,
        service_factory=lambda: camera_service,
    )
    focuser_worker.start()
    camera_worker.start()

    try:
        assert len(_worker_threads("focuser", rig_id)) == 1
        assert len(_worker_threads("camera", rig_id)) == 1

        with ThreadPoolExecutor(max_workers=4) as callers:
            focuser_future = callers.submit(focuser_worker.move_to, 0.5)
            assert focuser_service.move_started.wait(timeout=0.2)

            started_at = time.monotonic()
            camera_futures = [
                callers.submit(camera_worker.test_photo, 0.05)
                for _ in range(3)
            ]

            assert [future.result(timeout=0.2) for future in camera_futures] == [
                "camera-finished",
                "camera-finished",
                "camera-finished",
            ]
            assert time.monotonic() - started_at < 0.2
            assert not focuser_future.done()
            assert focuser_future.result(timeout=1.0) == "focuser-finished"
    finally:
        camera_worker.stop(timeout=1.0)
        focuser_worker.shutdown(timeout=1.0)

    assert not camera_worker.running
    assert not focuser_worker.running
    assert not _worker_threads("camera", rig_id)
    assert not _worker_threads("focuser", rig_id)
    assert camera_service.close_calls == 1
    assert focuser_service.close_calls == 1
