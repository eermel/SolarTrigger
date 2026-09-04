import pytest

from backend.camera_worker import CameraWorker


class RecoverableFakeService:
    def __init__(self):
        self.connected = True
        self.connect_calls = 0
        self.invalidate_calls = 0
        self.photo_calls = 0
        self.closed = False

    def connect(self):
        self.connect_calls += 1
        self.connected = True
        return object()

    def invalidate_connection(self):
        self.invalidate_calls += 1
        self.connected = False

    def execute_photo(self, params):
        self.photo_calls += 1

        if self.photo_calls == 1:
            raise RuntimeError("USB camera disappeared")

        return {
            "status": "ok",
            "params": dict(params),
        }

    def close(self):
        self.closed = True


def test_worker_invalidates_failed_transport_and_reconnects_next_photo():
    service = RecoverableFakeService()

    worker = CameraWorker(
        rig_id=1,
        service_factory=lambda: service,
        log_fn=lambda _message: None,
    )
    worker.start()

    try:
        with pytest.raises(
            RuntimeError,
            match="USB camera disappeared",
        ):
            worker.execute_photo({
                "shutter": "1/500",
                "expected_frames": 1,
            })

        # L'échec d'une tâche ne tue pas le worker.
        assert worker.running is True
        assert service.invalidate_calls == 1
        assert service.connected is False

        result = worker.execute_photo({
            "shutter": "1/250",
            "expected_frames": 1,
        })

        assert service.connect_calls == 1
        assert service.photo_calls == 2
        assert result["status"] == "ok"

    finally:
        worker.stop(timeout=2.0)

    assert service.closed is True
