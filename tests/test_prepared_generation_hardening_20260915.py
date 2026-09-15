from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.camera_ipc_server import CameraIpcServer, IpcError
from services.camera_service import PreparedCapture


class _Result:
    frames = 1
    planned = 1
    detail = "ok"


class _Worker:
    def __init__(self, generation):
        self.generation = generation
        self.trigger_calls = []

    def trigger_prepared(self, prepared, **kwargs):
        self.trigger_calls.append((prepared, dict(kwargs)))
        return _Result()


class _Runtime:
    def __init__(self, worker):
        self.worker = worker

    def get_for_rig(self, rig_id):
        return self.worker if rig_id == 1 else None

    def active_camera_rig_ids(self):
        return (1,)


def _server(tmp_path, worker):
    server = CameraIpcServer(
        _Runtime(worker),
        endpoint_dir=tmp_path,
        parent_pid=4242,
        log_fn=lambda *_args, **_kwargs: None,
    )
    server.activate_session("session-a", (1,))
    return server


def _prepared():
    return PreparedCapture(
        token="child-token",
        estimated_total_s=0.1,
        exposures_s=[0.01],
        planned_count=1,
        plugin_name="test",
    )


def _request(token_id):
    return {
        "operation": "trigger_prepared",
        "session_id": "session-a",
        "params": {
            "rig_id": 1,
            "token_id": token_id,
            "deadline": None,
            "deadline_monotonic": None,
        },
    }


def test_stale_prepared_generation_is_rejected_before_new_worker_call(tmp_path):
    worker = _Worker(generation=2)
    server = _server(tmp_path, worker)
    token_id = "server-token"

    server._tokens[token_id] = (
        "session-a",
        1,
        _prepared(),
        {
            "rig_id": 1,
            "phase": "totality",
            "worker_generation": 1,
        },
    )

    with pytest.raises(IpcError) as caught:
        server.handle_request(_request(token_id))

    assert caught.value.code == "UNKNOWN_TOKEN"
    assert "previous camera worker generation" in caught.value.message
    assert worker.trigger_calls == []
    assert token_id not in server._tokens


def test_same_prepared_generation_executes_normally(tmp_path):
    worker = _Worker(generation=7)
    server = _server(tmp_path, worker)
    token_id = "server-token"

    server._tokens[token_id] = (
        "session-a",
        1,
        _prepared(),
        {
            "rig_id": 1,
            "phase": "totality",
            "target_time": datetime.now(timezone.utc).isoformat(),
            "worker_generation": 7,
        },
    )

    result = server.handle_request(_request(token_id))

    assert result["frames"] == 1
    assert result["planned"] == 1
    assert len(worker.trigger_calls) == 1
    assert token_id not in server._tokens


def test_legacy_token_without_generation_remains_compatible(tmp_path):
    worker = _Worker(generation=99)
    server = _server(tmp_path, worker)
    token_id = "legacy-token"

    server._tokens[token_id] = (
        "session-a",
        1,
        _prepared(),
        {"rig_id": 1, "phase": "totality"},
    )

    result = server.handle_request(_request(token_id))

    assert result["frames"] == 1
    assert len(worker.trigger_calls) == 1


def test_worker_without_generation_metadata_is_not_rejected(tmp_path):
    class _LegacyWorker:
        def __init__(self):
            self.trigger_calls = []

        def trigger_prepared(self, prepared, **kwargs):
            self.trigger_calls.append((prepared, kwargs))
            return _Result()

    worker = _LegacyWorker()
    server = _server(tmp_path, worker)
    token_id = "legacy-worker-token"

    server._tokens[token_id] = (
        "session-a",
        1,
        _prepared(),
        {
            "rig_id": 1,
            "phase": "totality",
            "worker_generation": 1,
        },
    )

    result = server.handle_request(_request(token_id))

    assert result["frames"] == 1
    assert len(worker.trigger_calls) == 1
