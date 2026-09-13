from __future__ import annotations

import threading
import time

import pytest

from backend.camera_worker import CameraWorker
from backend.focuser_worker import FocuserWorker
from backend.generic_worker import (
    GenericWorker,
    WorkerTimeoutError,
    WorkerUnavailableError,
)
from backend.mount_worker import MountWorker


class BlockingCameraService:
    connected = True

    def __init__(self):
        self.release = threading.Event()

    def read_info(self):
        self.release.wait()
        return {"ok": True}

    def invalidate_connection(self):
        return None

    def close(self):
        return None


class BlockingStatusService:
    def __init__(self):
        self.release = threading.Event()

    def status(self):
        self.release.wait()
        return {"ok": True}

    def close(self):
        return None


def test_generic_worker_stop_is_bounded_when_job_never_returns():
    release = threading.Event()
    started = threading.Event()
    worker = GenericWorker(rig_id=901, device_kind="hang-test")
    worker.start()

    def hang():
        started.set()
        release.wait()

    future = worker.submit(hang)
    assert started.wait(1.0)

    before = time.monotonic()
    assert worker.stop(timeout=0.05) is False
    assert time.monotonic() - before < 0.5
    assert not worker.healthy

    release.set()
    future.result(timeout=1.0)


def test_camera_timeout_marks_worker_unavailable_and_fails_fast_afterward():
    service = BlockingCameraService()
    worker = CameraWorker(
        rig_id=902,
        service_factory=lambda: service,
        call_timeout_s=0.05,
    )
    worker.start()
    try:
        before = time.monotonic()
        with pytest.raises(WorkerTimeoutError):
            worker.read_info()
        assert time.monotonic() - before < 0.5
        assert not worker.healthy

        before = time.monotonic()
        with pytest.raises(WorkerUnavailableError):
            worker.read_info()
        assert time.monotonic() - before < 0.1
    finally:
        service.release.set()
        worker.stop(timeout=1.0)


@pytest.mark.parametrize(
    ("worker_type", "rig_id"),
    [
        (MountWorker, 903),
        (FocuserWorker, 904),
    ],
)
def test_mount_and_focuser_timeout_fail_closed(worker_type, rig_id):
    service = BlockingStatusService()
    worker = worker_type(
        rig_id=rig_id,
        service_factory=lambda: service,
        call_timeout_s=0.05,
    )
    worker.start()
    try:
        with pytest.raises(WorkerTimeoutError):
            worker.status()
        assert not worker.healthy
        with pytest.raises(WorkerUnavailableError):
            worker.status()
    finally:
        service.release.set()
        worker.shutdown(timeout=1.0)
