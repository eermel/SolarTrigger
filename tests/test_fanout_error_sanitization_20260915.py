import pytest

from scripts.camera_ipc_client import CameraIpcError
from scripts.fanout_camera_adapter import FanoutCameraAdapter


class _Ipc:
    def list_active_camera_rigs(self):
        return {"rig_ids": [1]}

    def initialize(self, rig_id, **kwargs):
        raise CameraIpcError(
            "PROTOCOL_ERROR",
            "initialize",
            "token_id=secret\ninvalid response",
        )


def test_complete_fanout_failure_exception_is_sanitized():
    logs = []
    adapter = FanoutCameraAdapter(_Ipc(), log_fn=logs.append)
    try:
        with pytest.raises(RuntimeError) as caught:
            adapter.initialize()

        text = str(caught.value)
        assert "PROTOCOL_ERROR" in text
        assert "RIG 1" in text
        assert "secret" not in text
        assert "token_id" not in text
        assert "\n" not in text
        assert caught.value.__cause__ is None
    finally:
        adapter.close()


def test_complete_non_ipc_failure_reports_only_exception_type():
    class Ipc:
        def list_active_camera_rigs(self):
            return {"rig_ids": [1]}

        def initialize(self, rig_id, **kwargs):
            raise ValueError("sensitive\npayload")

    adapter = FanoutCameraAdapter(Ipc(), log_fn=lambda _msg: None)
    try:
        with pytest.raises(RuntimeError) as caught:
            adapter.initialize()

        text = str(caught.value)
        assert "ValueError" in text
        assert "sensitive" not in text
        assert "payload" not in text
        assert caught.value.__cause__ is None
    finally:
        adapter.close()
