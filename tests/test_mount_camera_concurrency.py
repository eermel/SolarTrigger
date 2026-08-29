from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

from backend.camera_worker import CameraWorker
from backend.mount_worker import MountWorker


class DummyCameraService:
    def __init__(self) -> None:
        self.capture_started = threading.Event()
        self.capture_release = threading.Event()
        self.close_calls = 0

    def shoot_speed_list(self, duration, **_kwargs):
        self.capture_started.set()
        if not self.capture_release.wait(timeout=5.0):
            raise TimeoutError("camera test release was not signalled")
        return "camera-finished"

    def close(self) -> None:
        self.close_calls += 1


class DummyMountService:
    def __init__(self) -> None:
        self.close_calls = 0
        self.warmup_calls = 0
        self.warmup_started = threading.Event()

    def warmup(self) -> str:
        self.warmup_calls += 1
        self.warmup_started.set()
        return "mount-ready"

    def stop(self) -> None:
        pass

    def close(self) -> None:
        self.close_calls += 1


def _worker_threads(device_kind: str, rig_id: int) -> list[threading.Thread]:
    name = f"{device_kind}-worker-r{rig_id}"
    return [thread for thread in threading.enumerate() if thread.name == name]


def test_mount_commands_run_while_camera_is_busy_on_one_persistent_thread():
    camera_rig_id = 401
    mount_rig_id = 402
    camera_service = DummyCameraService()
    mount_service = DummyMountService()

    camera_worker = CameraWorker(
        rig_id=camera_rig_id,
        service_factory=lambda: camera_service,
    )
    mount_worker = MountWorker(
        rig_id=mount_rig_id,
        service_factory=lambda: mount_service,
    )

    camera_worker.start()
    mount_worker.start()

    try:
        with ThreadPoolExecutor(max_workers=4) as callers:
            camera_future = callers.submit(camera_worker.test_photo, 0.5)

            assert camera_service.capture_started.wait(timeout=1.0)

            mount_futures = [
                callers.submit(mount_worker.warmup)
                for _ in range(3)
            ]

            assert mount_service.warmup_started.wait(timeout=1.0)
            assert len(_worker_threads("mount", mount_rig_id)) == 1

            # Les commandes monture doivent pouvoir se terminer pendant que
            # la caméra est volontairement maintenue occupée.
            assert [
                future.result(timeout=1.0)
                for future in mount_futures
            ] == [
                "mount-ready",
                "mount-ready",
                "mount-ready",
            ]

            assert not camera_future.done()

            camera_service.capture_release.set()

            assert camera_future.result(timeout=1.0) == "camera-finished"

    finally:
        # Garantit qu'aucun échec d'assertion ne laisse la caméra bloquée.
        camera_service.capture_release.set()
        camera_worker.stop(timeout=1.0)
        mount_worker.shutdown(timeout=1.0)

    assert not camera_worker.running
    assert not mount_worker.running
    assert not _worker_threads("camera", camera_rig_id)
    assert not _worker_threads("mount", mount_rig_id)
    assert camera_service.close_calls == 1
    assert mount_service.close_calls == 1
    assert mount_service.warmup_calls == 3
