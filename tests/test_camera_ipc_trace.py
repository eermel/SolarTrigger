from types import SimpleNamespace

from backend.camera_ipc_server import CameraIpcServer


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
