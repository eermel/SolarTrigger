import threading

import pytest

from backend.camera_validation_scheduler import (
    CameraValidationScheduleCancelled,
    CameraValidationScheduleError,
    run_validation_recipe,
)


def _recipe():
    return {
        "commands": [
            {
                "action": "SET",
                "offset_ms": 0.0,
                "duration_ms": 20.0,
                "params": {
                    "parameter": "iso",
                    "value": "100",
                    "duration_ms": 20.0,
                },
            },
            {
                "action": "PHOTO",
                "offset_ms": 0.0,
                "duration_ms": 30.0,
                "params": {
                    "shutter": "1/500",
                    "expected_frames": 1,
                    "duration_ms": 30.0,
                    "validation_photo_id": "photo-01",
                },
            },
        ]
    }


class FakeCamera:
    def __init__(self, *, fail_set=False, fail_preflight=False):
        self.calls = []
        self.fail_set = fail_set
        self.fail_preflight = fail_preflight

    def preflight(self, rig_id, required_state):
        self.calls.append(("preflight", rig_id, required_state))
        if self.fail_preflight:
            raise RuntimeError("preflight failed")
        return {"ok": True}

    def set_parameter(self, rig_id, parameter, value, **kwargs):
        self.calls.append(("set", rig_id, parameter, value, kwargs))
        if self.fail_set:
            raise RuntimeError("set failed")
        return {"ok": True}

    def execute_photo(self, rig_id, params, **kwargs):
        self.calls.append(("photo", rig_id, params, kwargs))
        return {"frames": 1}


def test_validation_scheduler_preflights_then_dispatches_in_memory():
    camera = FakeCamera()

    run_validation_recipe(_recipe(), rig_id=2, camera_client=camera, log_fn=lambda _line: None)

    assert [call[0] for call in camera.calls] == ["preflight", "set", "photo"]
    assert camera.calls[0] == ("preflight", 2, {})
    assert camera.calls[1][4]["scheduled"] is True
    assert camera.calls[2][3]["scheduled"] is True
    assert camera.calls[2][2]["validation_target_utc"].endswith("Z")


def test_validation_scheduler_continues_after_command_error_without_replaying_photo():
    camera = FakeCamera(fail_set=True)

    run_validation_recipe(_recipe(), rig_id=1, camera_client=camera, log_fn=lambda _line: None)

    assert [call[0] for call in camera.calls] == ["preflight", "set", "photo"]
    assert sum(1 for call in camera.calls if call[0] == "photo") == 1


def test_validation_scheduler_preflight_failure_is_fatal():
    camera = FakeCamera(fail_preflight=True)

    with pytest.raises(RuntimeError, match="preflight failed"):
        run_validation_recipe(_recipe(), rig_id=1, camera_client=camera, log_fn=lambda _line: None)

    assert [call[0] for call in camera.calls] == ["preflight"]


def test_validation_scheduler_honours_cancellation_before_hardware_access():
    camera = FakeCamera()
    cancelled = threading.Event()
    cancelled.set()

    with pytest.raises(CameraValidationScheduleCancelled):
        run_validation_recipe(
            _recipe(),
            rig_id=1,
            camera_client=camera,
            stop_event=cancelled,
            log_fn=lambda _line: None,
        )

    assert camera.calls == []


def test_validation_scheduler_rejects_unordered_offsets():
    recipe = _recipe()
    recipe["commands"][0]["offset_ms"] = 10.0
    recipe["commands"][1]["offset_ms"] = 5.0

    with pytest.raises(CameraValidationScheduleError, match="invalid timing"):
        run_validation_recipe(recipe, rig_id=1, camera_client=FakeCamera())
