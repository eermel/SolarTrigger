import pytest

from scripts.camera_ipc_client import CameraIpcClient, CameraIpcError
from scripts.fanout_camera_adapter import FanoutCameraAdapter

from test_camera_ipc_client_transport import StubServer, send_json


def test_client_log_has_exact_sanitized_fields_and_rig_id(tmp_path):
    socket_path = tmp_path / "camera.sock"
    response = {
        "ok": False,
        "error": {
            "code": "PROTOCOL_ERROR",
            "message": (
                "session_id=session-secret token_id=token-secret\r\nfailure"
            ),
        },
    }
    logs = []

    with StubServer(socket_path, [send_json(response)]):
        client = CameraIpcClient(socket_path, "session-secret", log_fn=logs.append)
        with pytest.raises(CameraIpcError):
            client.trigger_prepared(7, "token-secret")

    assert logs == [
        "CAMERA_IPC_ERROR code=PROTOCOL_ERROR operation=trigger_prepared "
        "rig_id=7 message=[redacted] [redacted] failure"
    ]
    assert "session-secret" not in logs[0]
    assert "token-secret" not in logs[0]
    assert "session_id" not in logs[0]
    assert "token_id" not in logs[0]
    assert "\r" not in logs[0]
    assert "\n" not in logs[0]


class _FailingIpcClient:
    def __init__(self, logs, *, already_logged):
        self.logs = logs
        self.already_logged = already_logged

    def list_active_camera_rigs(self):
        return {"rig_ids": [12]}

    def initialize(self, _rig_id, **_kwargs):
        error = CameraIpcError(
            "PROTOCOL_ERROR", "initialize", "token_id=secret\ninvalid response"
        )
        error.logged = self.already_logged
        if self.already_logged:
            self.logs.append(
                "CAMERA_IPC_ERROR code=PROTOCOL_ERROR operation=initialize "
                "rig_id=12 message=[redacted] invalid response"
            )
        raise error


@pytest.mark.parametrize("already_logged", [False, True])
def test_fanout_logs_each_rig_operation_failure_once(already_logged):
    logs = []
    adapter = FanoutCameraAdapter(
        _FailingIpcClient(logs, already_logged=already_logged), log_fn=logs.append
    )
    try:
        adapter.initialize()
    finally:
        adapter.close()

    assert logs == [
        "CAMERA_IPC_ERROR code=PROTOCOL_ERROR operation=initialize "
        "rig_id=12 message=[redacted] invalid response"
    ]
    assert "secret" not in logs[0]
    assert "token_id" not in logs[0]
    assert "\n" not in logs[0]
