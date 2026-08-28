from __future__ import annotations

import logging
from typing import Any, Callable

import pytest

from scripts.camera_ipc_client import CameraIpcError
from scripts.fanout_camera_adapter import FanoutCameraAdapter


class OneFailingRigClient:
    def __init__(self, failing_operation: str) -> None:
        self.failing_operation = failing_operation
        self.session_id = "session-secret-must-not-leak"

    def list_active_camera_rigs(self) -> dict[str, tuple[int, int]]:
        return {"rig_ids": (1, 2)}

    def shoot_speed_list(
        self,
        rig_id: int,
        _speeds: list[str],
        *,
        photo_num_start: int = 0,
        deadline: Any = None,
        slowest_override_seconds: float | None = None,
    ) -> dict[str, int]:
        if rig_id == 1:
            raise CameraIpcError("INTERNAL_ERROR", self.failing_operation, "x")
        return {"frames": 3, "planned": 4}


def _shoot_speed_list(adapter: FanoutCameraAdapter) -> Any:
    return adapter.shoot_speed_list(["1/100"])


@pytest.mark.parametrize(
    ("operation", "invoke"),
    [("shoot_speed_list", _shoot_speed_list)],
)
def test_fanout_isolates_rig_error_and_logs_sanitized_rig_id(
    operation: str,
    invoke: Callable[[FanoutCameraAdapter], Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    logger = logging.getLogger(__name__)
    client = OneFailingRigClient(operation)
    adapter = FanoutCameraAdapter(client, log_fn=logger.error)

    try:
        with caplog.at_level(logging.ERROR, logger=logger.name):
            result = invoke(adapter)
    finally:
        adapter.close()

    assert result.frames >= 3
    assert result.planned >= 4
    errors = [
        record.getMessage()
        for record in caplog.records
        if "CAMERA_IPC_ERROR" in record.getMessage()
    ]
    assert len(errors) == 1
    assert "rig_id=1" in errors[0]
    assert client.session_id not in errors[0]
