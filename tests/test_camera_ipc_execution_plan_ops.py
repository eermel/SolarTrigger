from scripts.camera_ipc_client import CameraIpcClient


class FakeIpcClient(CameraIpcClient):
    def __init__(self):
        self.calls = []

    def _call(self, operation, params, *, timeout_s, deadline=None):
        self.calls.append(
            {
                "operation": operation,
                "params": params,
                "timeout_s": timeout_s,
                "deadline": deadline,
            }
        )
        return {"ok": True}


def test_set_parameter_uses_rig_specific_ipc_operation():
    client = FakeIpcClient()

    client.set_parameter(
        2,
        "shutterspeed2",
        "1/500",
        fallback_parameter="shutterspeed",
    )

    assert client.calls == [
        {
            "operation": "camera.set_parameter",
            "params": {
                "rig_id": 2,
                "parameter": "shutterspeed2",
                "value": "1/500",
                "fallback_parameter": "shutterspeed",
            },
            "timeout_s": client.DEFAULT_TIMEOUT_S,
            "deadline": None,
        }
    ]


def test_execute_photo_uses_rig_specific_ipc_operation():
    client = FakeIpcClient()

    params = {
        "shutter": "1/250",
        "iso": 100,
        "expected_frames": 1,
    }

    client.execute_photo(1, params)

    assert client.calls == [
        {
            "operation": "camera.execute_photo",
            "params": {
                "rig_id": 1,
                "params": params,
            },
            "timeout_s": client.DEFAULT_TIMEOUT_S,
            "deadline": None,
        }
    ]


def test_phase_runtime_camera_calls_use_operation_sized_timeouts():
    client = FakeIpcClient()

    client.initialize(1)
    client.apply_phase_settings(1)
    client.prepare_capture(1, {})
    client.trigger_prepared(1, "token")

    assert [call["timeout_s"] for call in client.calls] == [
        client.CONTROL_TIMEOUT_S,
        client.CONTROL_TIMEOUT_S,
        client.CONTROL_TIMEOUT_S,
        client.CAPTURE_TIMEOUT_S,
    ]
    assert client.CONTROL_TIMEOUT_S > client.DEFAULT_TIMEOUT_S
    assert client.CAPTURE_TIMEOUT_S >= client.CONTROL_TIMEOUT_S
