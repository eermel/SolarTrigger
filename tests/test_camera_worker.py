from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from types import SimpleNamespace

from backend.camera_worker import CameraWorker
from plugins.camera.base import CameraPlugin, CaptureResult
from services.camera_service import CameraService, CaptureIntent


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


class FakeCamera:
    def __init__(self) -> None:
        self.init_calls = 0
        self.exit_calls = 0

    def init(self) -> None:
        self.init_calls += 1

    def exit(self) -> None:
        self.exit_calls += 1

    def get_abilities(self):
        return SimpleNamespace(model="Worker Test Camera")


class FakeCameraPlugin(CameraPlugin):
    name = "worker-test"

    @staticmethod
    def matches(model_string):
        return model_string == "Worker Test Camera"

    def init_settings(self, **_kwargs):
        return None

    def set_exposure_settings(self, **_kwargs):
        return None

    def shoot_speeds(
        self,
        v_max,
        v_min,
        step_il,
        photo_num_start=0,
        deadline=None,
    ):
        return CaptureResult(frames=1, planned=1, detail="fake capture")

    def trigger_prepared(self, prepared, deadline=None):
        return CaptureResult(
            frames=prepared.planned_count,
            planned=prepared.planned_count,
            detail="fake prepared capture",
        )


def make_fake_camera_service(camera: FakeCamera) -> CameraService:
    return CameraService(
        camera_factory=lambda: camera,
        plugin_loader=lambda connected_camera, log_fn: FakeCameraPlugin(
            connected_camera, log_fn
        ),
        log_fn=lambda _message: None,
    )


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


def test_single_camera_prepare_then_trigger_via_worker_stops_cleanly():
    camera = FakeCamera()
    worker = CameraWorker(
        rig_id=203,
        service_factory=lambda: make_fake_camera_service(camera),
    )
    intent = CaptureIntent(
        shutter_min=None,
        shutter_max=None,
        step_ev=None,
        speeds=["1/1000"],
        phase="C2",
        target_time=datetime(2026, 8, 12, 17, 46),
        deadline=None,
        overflow_policy="truncate",
    )

    worker.start()
    try:
        plugin = worker.connect()
        prepared = worker.prepare_capture(intent)
        result = worker.trigger_prepared(prepared)

        assert isinstance(plugin, FakeCameraPlugin)
        assert prepared.planned_count == 1
        assert result.frames == 1
        assert result.planned == prepared.planned_count
        assert result.detail == "fake prepared capture"
    finally:
        worker.stop(timeout=1.0)

    assert not worker.running
    assert not _camera_worker_threads(203)
    assert camera.init_calls == 1
    assert camera.exit_calls == 1
