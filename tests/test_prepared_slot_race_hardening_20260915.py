import threading
from datetime import datetime, timezone

import pytest

from backend.camera_ipc_server import (
    CameraIpcServer,
    IpcError,
    MAX_PREPARED_TOKENS_PER_RIG,
)
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
        "params": {
            "rig_id": 1,
            "intent": _intent(),
        },
    }


class _BlockingWorker:
    def __init__(self, target):
        self.target = target
        self.started = 0
        self.cv = threading.Condition()
        self.release = threading.Event()
        self.discarded = []

    def prepare_capture(self, intent):
        del intent
        with self.cv:
            self.started += 1
            self.cv.notify_all()
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

    def wait_started(self, count):
        with self.cv:
            return self.cv.wait_for(lambda: self.started >= count, timeout=2.0)


class _Runtime:
    def __init__(self, worker):
        self.worker = worker

    def get_for_rig(self, rig_id):
        return self.worker if rig_id == 1 else None

    def active_camera_rig_ids(self):
        return (1,)

    def get_policy_config_for_rig(self, rig_id):
        return None


def test_concurrent_prepare_reservations_enforce_strict_cap(tmp_path):
    worker = _BlockingWorker(MAX_PREPARED_TOKENS_PER_RIG)
    server = CameraIpcServer(_Runtime(worker), endpoint_dir=tmp_path)
    session = server.activate_session("session-a", rig_ids=[1])

    results = []
    failures = []

    def prepare():
        try:
            results.append(server.handle_request(_request(session)))
        except Exception as exc:
            failures.append(exc)

    threads = [
        threading.Thread(target=prepare)
        for _ in range(MAX_PREPARED_TOKENS_PER_RIG)
    ]
    for thread in threads:
        thread.start()

    assert worker.wait_started(MAX_PREPARED_TOKENS_PER_RIG)

    with pytest.raises(IpcError) as caught:
        server.handle_request(_request(session))
    assert caught.value.code == "TOO_MANY_PREPARED"

    worker.release.set()
    for thread in threads:
        thread.join(2.0)
        assert not thread.is_alive()

    assert failures == []
    assert len(results) == MAX_PREPARED_TOKENS_PER_RIG
    with server._state_lock:
        assert len(server._tokens) == MAX_PREPARED_TOKENS_PER_RIG
        assert server._prepare_reservations == {}


def test_revoked_session_cannot_publish_late_prepared_token(tmp_path):
    worker = _BlockingWorker(1)
    server = CameraIpcServer(_Runtime(worker), endpoint_dir=tmp_path)
    session = server.activate_session("session-a", rig_ids=[1])

    result = {}
    done = threading.Event()

    def prepare():
        try:
            result["value"] = server.handle_request(_request(session))
        except BaseException as exc:
            result["error"] = exc
        finally:
            done.set()

    thread = threading.Thread(target=prepare)
    thread.start()
    assert worker.wait_started(1)

    server.revoke_session(session)
    worker.release.set()

    assert done.wait(2.0)
    thread.join(2.0)

    assert "value" not in result
    assert isinstance(result.get("error"), IpcError)
    assert result["error"].code == "INVALID_SESSION"
    with server._state_lock:
        assert server._tokens == {}
        assert server._prepare_reservations == {}
    assert len(worker.discarded) == 1


def test_failed_prepare_releases_reservation(tmp_path):
    class FailingWorker:
        def prepare_capture(self, intent):
            raise RuntimeError("prepare failed")

    server = CameraIpcServer(_Runtime(FailingWorker()), endpoint_dir=tmp_path)
    session = server.activate_session("session-a", rig_ids=[1])

    with pytest.raises(RuntimeError, match="prepare failed"):
        server.handle_request(_request(session))

    with server._state_lock:
        assert server._prepare_reservations == {}
