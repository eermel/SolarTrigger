import pytest

import backend.trigger_service as trigger_service


class _State:
    def __init__(self):
        self.updates = []

    def update_trigger_rig(self, rig_id, patch):
        self.updates.append((rig_id, dict(patch)))


class _FailingThread:
    def __init__(self, *args, **kwargs):
        pass

    def start(self):
        raise RuntimeError("thread start failed")


class _Session:
    socket_path = "/tmp/camera.sock"
    session_id = "session-1"


class _CameraRuntime:
    def __init__(self, close_error=False):
        self.close_error = close_error
        self.closed = []

    def reconcile(self, _config):
        return None

    def open_ipc_session(self, _rig_ids):
        return _Session()

    def close_ipc_session(self, session_id):
        self.closed.append(session_id)
        if self.close_error:
            raise RuntimeError("close failed")


def _service(tmp_path, *, camera_runtime=None, rig_config_loader=None):
    state = _State()
    emitted = []
    logs = []
    service = trigger_service.TriggerService(
        state,
        tmp_path / "scripts" / "eclipse_trigger.py",
        tmp_path / "today.json",
        tmp_path / "configs",
        lambda *args, **kwargs: logs.append((args, kwargs)),
        lambda event, payload: emitted.append((event, payload)),
        camera_runtime=camera_runtime,
        rig_config_loader=rig_config_loader,
        product_configs_dir=tmp_path / "product",
    )
    service._resolve_totality_input = lambda rig_id: (
        service._active_photo_paths.__setitem__(
            rig_id, tmp_path / "photo_totality.json"
        )
        or tmp_path / "photo_totality.json"
    )
    return service, state, emitted, logs


def test_emergency_totality_thread_start_failure_rolls_ui_state_back(
    tmp_path, monkeypatch
):
    service, state, emitted, _logs = _service(tmp_path)
    monkeypatch.setattr(trigger_service.threading, "Thread", _FailingThread)

    with pytest.raises(RuntimeError, match="thread start failed"):
        service.start_totality_only(1)

    assert state.updates == [
        (
            1,
            {
                "running": True,
                "phase": "totality_override",
                "mode": "totality_override",
                "speed": 1.0,
            },
        ),
        (
            1,
            {
                "running": False,
                "phase": "idle",
                "mode": None,
                "speed": None,
            },
        ),
    ]
    assert emitted == [
        ("trigger_phase", {"rig_id": 1, "phase": "totality_override"}),
        ("trigger_phase", {"rig_id": 1, "phase": "idle"}),
    ]
    assert service._starting_by_rig[1] is False
    assert service._analysis_suppressed_by_rig[1] is False
    assert service._manual_stop_requested_by_rig[1] is False
    assert service._cancel_start_requested_by_rig[1] is False
    assert service._supervisor_threads[1] is None


def test_emergency_totality_ipc_close_failure_does_not_mask_start_error(
    tmp_path, monkeypatch
):
    runtime = _CameraRuntime(close_error=True)
    config = {
        "rigs": [
            {
                "rig_id": 1,
                "enabled": True,
                "devices": {
                    "camera": {"backend": "sony"},
                    "mount": None,
                    "focuser": None,
                },
            }
        ]
    }
    service, state, emitted, logs = _service(
        tmp_path,
        camera_runtime=runtime,
        rig_config_loader=lambda: config,
    )
    monkeypatch.setattr(trigger_service.threading, "Thread", _FailingThread)

    with pytest.raises(RuntimeError, match="thread start failed"):
        service.start_totality_only(1)

    assert runtime.closed == ["session-1"]
    assert state.updates[-1][1]["running"] is False
    assert state.updates[-1][1]["phase"] == "idle"
    assert emitted[-1] == (
        "trigger_phase",
        {"rig_id": 1, "phase": "idle"},
    )
    assert any(
        args
        and "Camera IPC session close error: close failed" in str(args[0])
        for args, _kwargs in logs
    )
