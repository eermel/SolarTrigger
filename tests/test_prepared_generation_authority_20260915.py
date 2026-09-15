from datetime import datetime, timezone

import pytest

from backend.camera_ipc_server import CameraIpcServer, IpcError
from services.camera_service import PreparedCapture


class _Worker:
    def __init__(self):
        self.generation = 2
        self.trigger_calls = []

    def prepare_capture(self, _intent):
        prepared = PreparedCapture(
            token="child-token",
            estimated_total_s=0.1,
            exposures_s=[0.01],
            planned_count=1,
            plugin_name="test",
        )
        # Simulate the exact generation which produced the child-local token.
        prepared._process_worker_generation = 1

        # Simulate another actor changing the visible worker generation before
        # CameraIpcServer gets a chance to inspect worker.generation.
        self.generation = 2
        return prepared

    def trigger_prepared(self, prepared, **kwargs):
        self.trigger_calls.append((prepared, kwargs))
        raise AssertionError("stale prepared token must not reach worker")


class _Runtime:
    def __init__(self, worker):
        self.worker = worker

    def get_for_rig(self, rig_id):
        return self.worker if rig_id == 1 else None

    def active_camera_rig_ids(self):
        return (1,)

    def get_policy_config_for_rig(self, _rig_id):
        return {}


def _server(tmp_path):
    worker = _Worker()
    server = CameraIpcServer(
        _Runtime(worker),
        endpoint_dir=tmp_path,
        parent_pid=4242,
        log_fn=lambda *_args, **_kwargs: None,
    )
    server.activate_session("session-a", (1,))
    return server, worker


def _intent():
    now = datetime.now(timezone.utc)
    return {
        "shutter_min": "1/500",
        "shutter_max": "1/500",
        "step_ev": 1.0,
        "speeds": None,
        "phase": "partial",
        "target_time": now.isoformat(),
        "deadline": None,
        "overflow_policy": "truncate",
        "origin": "partial",
        "request_id": "req-1",
        "exposure_plan": None,
    }


def test_server_records_generation_stamped_on_prepared_not_later_worker_value(
    tmp_path,
):
    server, worker = _server(tmp_path)

    prepared_response = server.handle_request(
        {
            "operation": "prepare_capture",
            "session_id": "session-a",
            "params": {"rig_id": 1, "intent": _intent()},
        }
    )

    token_id = prepared_response["token_id"]
    stored = server._tokens[token_id]
    metadata = stored[3]

    assert metadata["worker_generation"] == 1
    assert worker.generation == 2

    with pytest.raises(IpcError) as caught:
        server.handle_request(
            {
                "operation": "trigger_prepared",
                "session_id": "session-a",
                "params": {
                    "rig_id": 1,
                    "token_id": token_id,
                    "deadline": None,
                    "deadline_monotonic": None,
                },
            }
        )

    assert caught.value.code == "UNKNOWN_TOKEN"
    assert worker.trigger_calls == []
