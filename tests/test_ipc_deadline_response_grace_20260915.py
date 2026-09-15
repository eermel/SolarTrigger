import socket

import pytest

from scripts.camera_ipc_client import CameraIpcClient


def test_effective_timeout_adds_response_grace_after_hard_deadline(monkeypatch):
    monkeypatch.setattr(
        "scripts.camera_ipc_client.time.monotonic",
        lambda: 100.0,
    )

    timeout = CameraIpcClient._effective_timeout(
        120.0,
        deadline_monotonic=105.0,
    )

    assert timeout == pytest.approx(
        5.0 + CameraIpcClient.DEADLINE_RESPONSE_GRACE_S
    )


def test_effective_timeout_response_grace_never_exceeds_requested_timeout(
    monkeypatch,
):
    monkeypatch.setattr(
        "scripts.camera_ipc_client.time.monotonic",
        lambda: 100.0,
    )

    timeout = CameraIpcClient._effective_timeout(
        3.0,
        deadline_monotonic=105.0,
    )

    assert timeout == pytest.approx(3.0)


def test_effective_timeout_still_fails_immediately_after_deadline(monkeypatch):
    monkeypatch.setattr(
        "scripts.camera_ipc_client.time.monotonic",
        lambda: 105.0,
    )

    with pytest.raises(socket.timeout, match="deadline has passed"):
        CameraIpcClient._effective_timeout(
            120.0,
            deadline_monotonic=105.0,
        )


def test_effective_timeout_without_deadline_is_unchanged():
    assert CameraIpcClient._effective_timeout(12.5) == pytest.approx(12.5)
