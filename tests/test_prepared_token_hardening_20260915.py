from backend.camera_ipc_server import (
    CameraIpcServer,
    IpcError,
    MAX_PREPARED_TOKENS_PER_RIG,
)
from backend.camera_process_worker import ProcessCameraWorker


class _DiscardWorker:
    def __init__(self):
        self.discarded = []

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


def test_process_worker_exposes_internal_discard_operation():
    assert "discard_prepared" in ProcessCameraWorker._REMOTE_METHODS


def test_revoke_session_discards_child_prepared_state(tmp_path):
    worker = _DiscardWorker()
    server = CameraIpcServer(_Runtime(worker), endpoint_dir=tmp_path)
    session = server.activate_session("session-a", rig_ids=[1])

    prepared = object()
    with server._state_lock:
        server._tokens["token-a"] = (
            session,
            1,
            prepared,
            {"rig_id": 1},
        )

    server.revoke_session(session)

    assert worker.discarded == [prepared]
    assert server._tokens == {}
    assert session not in server._active_sessions


def test_revoke_cleanup_failure_does_not_restore_session(tmp_path):
    class FailingWorker:
        def discard_prepared(self, prepared):
            raise RuntimeError("child unavailable")

    logs = []
    server = CameraIpcServer(
        _Runtime(FailingWorker()),
        endpoint_dir=tmp_path,
        log_fn=logs.append,
    )
    session = server.activate_session("session-a", rig_ids=[1])
    with server._state_lock:
        server._tokens["token-a"] = (
            session,
            1,
            object(),
            {"rig_id": 1},
        )

    server.revoke_session(session)

    assert server._tokens == {}
    assert session not in server._active_sessions
    assert any("prepared-token cleanup failed" in line for line in logs)


def test_prepared_token_limit_constant_is_small_and_positive():
    assert 1 <= MAX_PREPARED_TOKENS_PER_RIG <= 32
