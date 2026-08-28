from __future__ import annotations

import time

import pytest

from backend.camera_worker import CameraWorker
from backend.generic_worker import ExpiredJobError, PRIORITY_SEQUENCER


class RecordingCameraService:
    def __init__(self) -> None:
        self.prepare_calls = 0

    def prepare_capture(self, _intent) -> None:
        self.prepare_calls += 1

    def close(self) -> None:
        pass


def test_expired_sequencer_job_propagates_without_calling_camera_service():
    logs = []
    service = RecordingCameraService()
    worker = CameraWorker(
        rig_id=401,
        service_factory=lambda: service,
        log_fn=logs.append,
    )

    def prepare_capture():
        return worker._ensure_service().prepare_capture(object())

    future = worker._worker.submit_with_priority(
        PRIORITY_SEQUENCER,
        prepare_capture,
        worker_deadline=time.monotonic() - 1.0,
        purpose="prepare-capture",
    )
    worker.start()
    try:
        with pytest.raises(ExpiredJobError) as raised:
            future.result(timeout=1.0)

        assert raised.value.code == "EXPIRED"
        assert service.prepare_calls == 0
        assert worker._worker.last_error is None
        assert logs == [
            "camera worker for rig 401 skipped expired job: prepare-capture"
        ]
    finally:
        worker.stop(timeout=1.0)
