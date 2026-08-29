import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from backend import camera_ipc_server
from backend.camera_ipc_server import CameraIpcServer, IpcError
from backend.generic_worker import ExpiredJobError


class FakeWorker:
    def __init__(self):
        self.token = object()
        self.triggered_token = None

    def prepare_capture(self, _intent):
        return SimpleNamespace(
            token=self.token,
            estimated_total_s=0.75,
            exposures_s=[0.25, 0.5],
            planned_count=2,
            plugin_name="fake-camera",
        )

    def trigger_prepared(self, token, deadline=None):
        self.triggered_token = token
        return {"triggered": True}

    def shoot_speed_list(
        self,
        speeds,
        photo_num_start=0,
        deadline=None,
        slowest_override_seconds=None,
    ):
        return {"frames": len(speeds), "planned": len(speeds)}


class FakeRuntime:
    def __init__(self, worker):
        self.worker = worker

    def get_for_rig(self, rig_id):
        return self.worker if rig_id == 3 else None


def test_prepare_capture_persists_logical_intent_metadata(tmp_path):
    worker = FakeWorker()
    server = CameraIpcServer(
        FakeRuntime(worker),
        endpoint_dir=tmp_path / "ipc",
        parent_pid=4321,
        log_fn=lambda _message: None,
    )
    session = server.activate_session("trace-session")

    response = server.handle_request(
        {
            "operation": "prepare_capture",
            "session_id": session,
            "params": {
                "rig_id": 3,
                "intent": {
                    "shutter_min": "1/4",
                    "shutter_max": "1/2",
                    "step_ev": 1.0,
                    "speeds": None,
                    "phase": "C2",
                    "target_time": "2026-08-12T18:00:00Z",
                    "deadline": "2026-08-12T18:00:01Z",
                    "overflow_policy": None,
                    "request_id": "capture-42",
                },
            },
        }
    )

    stored_session, rig_id, opaque_token, metadata = server._tokens[
        response["token_id"]
    ]
    assert (stored_session, rig_id, opaque_token) == (session, 3, worker.token)
    assert metadata == {
        "rig_id": 3,
        "phase": "C2",
        "target_time": "2026-08-12T18:00:00",
        "deadline": "2026-08-12T18:00:01",
        "request_id": "capture-42",
        "exposures_s": [0.25, 0.5],
        "planned_count": 2,
        "plugin_name": "fake-camera",
        "iso_applied": None,
        "corrections": None,
        "warnings": None,
        "plan_version": response["plan_version"],
    }

    assert server.handle_request(
        {
            "operation": "trigger_prepared",
            "session_id": session,
            "params": {"rig_id": 3, "token_id": response["token_id"]},
        }
    ) == {"triggered": True}
    assert worker.triggered_token is worker.token

    legacy_token = object()
    server._tokens["legacy-token"] = (session, 3, legacy_token)
    assert server.handle_request(
        {
            "operation": "trigger_prepared",
            "session_id": session,
            "params": {"rig_id": 3, "token_id": "legacy-token"},
        }
    ) == {"triggered": True}
    assert worker.triggered_token is legacy_token


def _shoot_request(session, **params):
    return {
        "operation": "shoot_speed_list",
        "session_id": session,
        "params": {"rig_id": 3, **params},
    }


def _capture_trace(monkeypatch, trace_path=None):
    events = []
    start = datetime(2026, 8, 12, 18, 0, tzinfo=timezone.utc)
    timestamps = iter((start, start + timedelta(milliseconds=5)))

    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            value = next(timestamps)
            return value if tz is not None else value.replace(tzinfo=None)

    monkeypatch.setattr(camera_ipc_server, "datetime", FixedDatetime)
    if trace_path is None:
        monkeypatch.setattr(
            camera_ipc_server.rig_trace,
            "trace_event",
            lambda kind, payload: events.append((kind, payload)),
        )
    else:
        original_trace_event = camera_ipc_server.rig_trace.trace_event
        monkeypatch.setattr(camera_ipc_server.rig_trace, "_PATH", trace_path)

        def record_trace(kind, payload):
            events.append((kind, payload))
            return original_trace_event(kind, payload)

        monkeypatch.setattr(
            camera_ipc_server.rig_trace, "trace_event", record_trace
        )
    return events


def _prepare_request(session):
    return {
        "operation": "prepare_capture",
        "session_id": session,
        "params": {
            "rig_id": 3,
            "intent": {
                "shutter_min": "1/4",
                "shutter_max": "1/2",
                "step_ev": 1.0,
                "speeds": None,
                "phase": "C2",
                "target_time": "2026-08-12T17:59:59.900Z",
                "deadline": "2026-08-12T18:00:00.500Z",
                "overflow_policy": None,
                "request_id": "capture-42",
            },
        },
    }


def _trigger_request(session, token_id, **params):
    return {
        "operation": "trigger_prepared",
        "session_id": session,
        "params": {"rig_id": 3, "token_id": token_id, **params},
    }


def _prepared_server(tmp_path, worker, session_id="trace-session"):
    server = CameraIpcServer(
        FakeRuntime(worker),
        endpoint_dir=tmp_path / "ipc",
        parent_pid=4321,
        log_fn=lambda _message: None,
    )
    session = server.activate_session(session_id)
    prepared = server.handle_request(_prepare_request(session))
    return server, session, prepared["token_id"]


def _assert_trigger_context(payload):
    assert payload["rig_id"] == 3
    assert payload["phase"] == "C2"
    assert payload["target_time"] == "2026-08-12T17:59:59.900000"
    assert payload["request_id"] == "capture-42"
    assert payload["exposures_s"] == [0.25, 0.5]
    assert payload["planned_count"] == 2
    assert payload["plugin_name"] == "fake-camera"
    assert payload["start_utc"] == "2026-08-12T18:00:00+00:00"
    assert payload["end_utc"] == "2026-08-12T18:00:00.005000+00:00"
    assert payload["duration_ms"] == 5.0
    assert payload["latency_ms"] == 100.0
    assert "token_id" not in payload
    assert "session_id" not in payload


def _assert_jsonl_trace(trace_path, expected_payload):
    lines = [json.loads(line) for line in trace_path.read_text().splitlines()]
    assert len(lines) == 1
    event = lines[0]
    assert event.pop("kind") == "camera.trigger_prepared"
    timestamp = event.pop("timestamp")
    assert datetime.fromisoformat(timestamp).tzinfo is not None
    assert event == expected_payload


@pytest.mark.parametrize(
    ("trigger_params", "expected_deadline"),
    [
        ({}, None),
        (
            {"deadline": "2026-08-12T18:00:01Z"},
            "2026-08-12T18:00:01",
        ),
    ],
)
def test_trigger_prepared_traces_success_with_explicit_deadline_only(
    monkeypatch, tmp_path, trigger_params, expected_deadline
):
    trace_path = tmp_path / "rig_traces.jsonl"
    events = _capture_trace(monkeypatch, trace_path)
    worker = FakeWorker()
    server, session, token_id = _prepared_server(tmp_path, worker)

    result = server.handle_request(
        _trigger_request(session, token_id, **trigger_params)
    )

    assert result == {"triggered": True}
    assert len(events) == 1
    assert events[0][0] == "camera.trigger_prepared"
    payload = events[0][1]
    _assert_trigger_context(payload)
    assert payload["status"] == "success"
    if expected_deadline is None:
        assert "deadline" not in payload
    else:
        assert payload["deadline"] == expected_deadline
    _assert_jsonl_trace(trace_path, payload)


@pytest.mark.parametrize(
    ("worker_error", "expected_code", "expected_message", "expected_status"),
    [
        (
            ExpiredJobError(),
            "EXPIRED",
            "camera worker job expired",
            "expired",
        ),
        (
            IpcError("CAMERA_ERROR", "capture failed"),
            "CAMERA_ERROR",
            "capture failed",
            "error",
        ),
        (
            RuntimeError("private worker detail"),
            "INTERNAL_ERROR",
            "camera operation failed",
            "error",
        ),
    ],
)
def test_trigger_prepared_traces_expired_and_errors(
    monkeypatch,
    tmp_path,
    worker_error,
    expected_code,
    expected_message,
    expected_status,
):
    class FailingWorker(FakeWorker):
        def trigger_prepared(self, token, deadline=None):
            raise worker_error

    trace_path = tmp_path / "rig_traces.jsonl"
    events = _capture_trace(monkeypatch, trace_path)
    server, session, token_id = _prepared_server(tmp_path, FailingWorker())

    with pytest.raises(IpcError) as caught:
        server.handle_request(_trigger_request(session, token_id))

    assert (caught.value.code, caught.value.message) == (
        expected_code,
        expected_message,
    )
    assert len(events) == 1
    assert events[0][0] == "camera.trigger_prepared"
    payload = events[0][1]
    _assert_trigger_context(payload)
    assert "deadline" not in payload
    assert payload["status"] == expected_status
    assert payload["code"] == expected_code
    assert payload["message"] == expected_message
    _assert_jsonl_trace(trace_path, payload)


def test_shoot_speed_list_traces_success_with_deadline(monkeypatch, tmp_path):
    events = _capture_trace(monkeypatch)
    server = CameraIpcServer(
        FakeRuntime(FakeWorker()),
        endpoint_dir=tmp_path / "ipc",
        parent_pid=4321,
        log_fn=lambda _message: None,
    )
    session = server.activate_session("trace-session")
    speeds = ["1/1000", "1/500"]

    result = server.handle_request(
        _shoot_request(
            session,
            speeds=speeds,
            photo_num_start=7,
            deadline="2026-08-12T18:00:01Z",
        )
    )

    assert result == {"frames": 2, "planned": 2}
    assert len(events) == 1
    kind, payload = events[0]
    assert kind == "camera.shoot_speed_list"
    assert payload == {
        "rig_id": 3,
        "speeds": speeds,
        "photo_num_start": 7,
        "phase": None,
        "target_time": None,
        "deadline": "2026-08-12T18:00:01",
        "start_utc": "2026-08-12T18:00:00+00:00",
        "end_utc": "2026-08-12T18:00:00.005000+00:00",
        "duration_ms": 5.0,
        "latency_ms": None,
        "status": "success",
        "frames": 2,
        "planned": 2,
    }


@pytest.mark.parametrize(
    ("worker_error", "expected_status", "expected_code", "expected_message"),
    [
        (
            ExpiredJobError(),
            "expired",
            "EXPIRED",
            "camera worker job expired",
        ),
        (
            IpcError("CAMERA_ERROR", "capture failed"),
            "error",
            "CAMERA_ERROR",
            "capture failed",
        ),
    ],
)
def test_shoot_speed_list_traces_errors_without_deadline(
    monkeypatch,
    tmp_path,
    worker_error,
    expected_status,
    expected_code,
    expected_message,
):
    class FailingWorker(FakeWorker):
        def shoot_speed_list(self, *args, **kwargs):
            raise worker_error

    events = _capture_trace(monkeypatch)
    server = CameraIpcServer(
        FakeRuntime(FailingWorker()),
        endpoint_dir=tmp_path / "ipc",
        parent_pid=4321,
        log_fn=lambda _message: None,
    )
    session = server.activate_session("trace-session")

    with pytest.raises(IpcError) as caught:
        server.handle_request(
            _shoot_request(session, speeds=["1/250"], photo_num_start=4)
        )

    assert (caught.value.code, caught.value.message) == (
        expected_code,
        expected_message,
    )
    assert len(events) == 1
    kind, payload = events[0]
    assert kind == "camera.shoot_speed_list"
    assert payload["rig_id"] == 3
    assert payload["speeds"] == ["1/250"]
    assert payload["photo_num_start"] == 4
    assert payload["phase"] is None
    assert payload["target_time"] is None
    assert "deadline" not in payload
    assert payload["start_utc"] == "2026-08-12T18:00:00+00:00"
    assert payload["end_utc"] == "2026-08-12T18:00:00.005000+00:00"
    assert payload["duration_ms"] == 5.0
    assert payload["latency_ms"] is None
    assert payload["status"] == expected_status
    assert payload["code"] == expected_code
    assert payload["message"] == expected_message
