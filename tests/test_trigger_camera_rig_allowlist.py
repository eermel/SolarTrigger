from __future__ import annotations

import pytest

from backend.camera_ipc_server import CameraIpcServer, IpcError
from backend.trigger_service import TriggerValidationError, validate_execution_rigs


class DummyWorker:
    pass


class DummyRuntime:
    def __init__(self):
        self.workers = {
            1: DummyWorker(),
            2: DummyWorker(),
            3: DummyWorker(),
        }

    def active_camera_rig_ids(self):
        return tuple(sorted(self.workers))

    def get_for_rig(self, rig_id):
        return self.workers.get(rig_id)


def rig(rig_id, *, enabled, camera=True):
    return {
        "rig_id": rig_id,
        "enabled": enabled,
        "devices": {
            "camera": (
                {"backend": "gphoto2", "serial": f"CAM-{rig_id}"}
                if camera
                else None
            )
        },
    }


def test_execution_rigs_are_rig1_plus_enabled_secondary_rigs():
    config = {
        "rigs": [
            rig(1, enabled=False),
            rig(2, enabled=False),
            rig(3, enabled=True),
        ]
    }

    assert validate_execution_rigs(config) == (1, 3)


def test_execution_rejects_rig1_without_pilotable_camera():
    config = {
        "rigs": [
            rig(1, enabled=False, camera=False),
            rig(2, enabled=False, camera=False),
        ]
    }

    with pytest.raises(TriggerValidationError) as caught:
        validate_execution_rigs(config)

    assert caught.value.code == "RIG_CAMERA_REQUIRED"


def test_execution_ignores_disabled_secondary_rig_without_camera():
    config = {
        "rigs": [
            rig(1, enabled=False, camera=True),
            rig(2, enabled=False, camera=False),
        ]
    }

    assert validate_execution_rigs(config) == (1,)


def test_execution_rejects_enabled_secondary_rig_without_camera():
    config = {
        "rigs": [
            rig(1, enabled=False, camera=True),
            rig(2, enabled=True, camera=False),
        ]
    }

    with pytest.raises(TriggerValidationError) as caught:
        validate_execution_rigs(config)

    assert caught.value.code == "RIG_CAMERA_REQUIRED"


def test_ipc_session_lists_only_allowed_trigger_rigs(tmp_path):
    runtime = DummyRuntime()
    server = CameraIpcServer(runtime, endpoint_dir=tmp_path, parent_pid=4321)

    session = server.activate_session("trigger-session", (1, 3))

    result = server.handle_request({
        "operation": "list_active_camera_rigs",
        "params": {},
        "session_id": session,
    })

    assert result == {"rig_ids": [1, 3]}


def test_ipc_session_rejects_configured_but_disabled_rig(tmp_path):
    runtime = DummyRuntime()
    server = CameraIpcServer(runtime, endpoint_dir=tmp_path, parent_pid=4321)

    session = server.activate_session("trigger-session", (1, 3))

    with pytest.raises(IpcError) as caught:
        server._worker({"rig_id": 2})

    assert caught.value.code == "UNKNOWN_RIG"

    # Le worker existe bien : il est seulement interdit dans cette session
    # Trigger et reste donc disponible pour Controls.
    assert runtime.get_for_rig(2) is not None
