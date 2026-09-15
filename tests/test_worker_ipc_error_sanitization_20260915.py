import pytest

from backend.camera_ipc_server import CameraIpcServer, IpcError
from backend.generic_worker import WorkerTimeoutError, WorkerUnavailableError


def test_worker_timeout_ipc_message_is_stable_and_sanitized():
    def method():
        raise WorkerTimeoutError(
            "camera",
            1,
            "secret_operation token_id=abc123",
            12.345,
        )

    with pytest.raises(IpcError) as caught:
        CameraIpcServer._call_worker(method)

    assert caught.value.code == "DEVICE_TIMEOUT"
    assert caught.value.message == "camera worker operation timed out"
    assert "secret_operation" not in caught.value.message
    assert "abc123" not in caught.value.message


def test_worker_unavailable_ipc_message_is_stable_and_sanitized():
    def method():
        raise WorkerUnavailableError(
            "camera worker unavailable: prior_error=USB_SECRET\\nraw payload"
        )

    with pytest.raises(IpcError) as caught:
        CameraIpcServer._call_worker(method)

    assert caught.value.code == "DEVICE_UNAVAILABLE"
    assert caught.value.message == "camera worker is unavailable"
    assert "USB_SECRET" not in caught.value.message
    assert "\\n" not in caught.value.message
