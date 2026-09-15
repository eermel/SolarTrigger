import threading
from datetime import datetime, timezone

from backend.camera_ipc_server import CameraIpcServer, IpcError
from services.camera_service import PreparedCapture


def _intent():
    return {
        "shutter_min": None,
        "shutter_max": None,
        "step_ev": None,
        "speeds": ["1/100"],
        "phase": "totality",
        "target_time": datetime.now(timezone.utc).isoformat(),
        "deadline": None,
        "overflow_policy": None,
    }


def _request(session):
    return {
        "operation": "prepare_capture",
        "session_id": session,
        "params": {"rig_id": 1, "intent": _intent()},
    }


class _BlockingWorker:
    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()
        self.discarded = []

    def prepare_capture(self, intent):
        del intent
        self.started.set()
        assert self.release.wait(2.0)
        return PreparedCapture(
            token=object(),
            estimated_total_s=0.1,
            exposures_s=[0.01],
            planned_count=1,
            plugin_name="test",
        )

    def discard_prepared(self, prepared):
        self.discarded.append(prepared)
        return True


class _Runtime:
    def __init__(self, worker):
        self.worker = worker

    def get_for_rig(self, rig_id):
        return self.worker if rig_id == 1 else None

    def active_camera_rig_ids(self):
        return (1,)

    def get_policy_config_for_rig(self, rig_id):
        return None


def test_reused_session_id_cannot_accept_old_inflight_prepare(tmp_path):
    worker = _BlockingWorker()
    server = CameraIpcServer(_Runtime(worker), endpoint_dir=tmp_path)

    session = server.activate_session("session-a", rig_ids=[1])
    with server._state_lock:
        old_lease = server._session_leases[session]

    outcome = {}

    def run_prepare():
        try:
            outcome["value"] = server.handle_request(_request(session))
        except BaseException as exc:
            outcome["error"] = exc

    thread = threading.Thread(target=run_prepare)
    thread.start()
    assert worker.started.wait(2.0)

    server.revoke_session(session)
    assert server.activate_session("session-a", rig_ids=[1]) == session
    with server._state_lock:
        new_lease = server._session_leases[session]
    assert new_lease is not old_lease

    worker.release.set()
    thread.join(2.0)
    assert not thread.is_alive()

    assert "value" not in outcome
    assert isinstance(outcome.get("error"), IpcError)
    assert outcome["error"].code == "INVALID_SESSION"
    assert len(worker.discarded) == 1

    with server._state_lock:
        assert server._tokens == {}
        assert server._prepare_reservations == {}
        assert server._active_sessions[session] == frozenset({1})
        assert server._session_leases[session] is new_lease


def test_repeated_activate_same_live_session_preserves_lease(tmp_path):
    worker = _BlockingWorker()
    server = CameraIpcServer(_Runtime(worker), endpoint_dir=tmp_path)

    session = server.activate_session("session-a", rig_ids=[1])
    with server._state_lock:
        first = server._session_leases[session]

    assert server.activate_session("session-a", rig_ids=[1]) == session

    with server._state_lock:
        assert server._session_leases[session] is first


def test_revoke_removes_lease_identity(tmp_path):
    worker = _BlockingWorker()
    server = CameraIpcServer(_Runtime(worker), endpoint_dir=tmp_path)

    session = server.activate_session("session-a", rig_ids=[1])
    server.revoke_session(session)

    with server._state_lock:
        assert session not in server._active_sessions
        assert session not in server._session_leases
