import threading
import time

import pytest

from backend.camera_worker_runtime import CameraWorkerRuntime


class FakeWorker:
    def __init__(self, *, rig_id, clock, log_fn):
        self.rig_id = rig_id
        self.started = False
        self.stopped = False

    def configure_camera(self, _config):
        pass

    def start(self):
        self.started = True

    def stop(self, timeout=None):
        self.stopped = True


class BlockingRevokeServer:
    def __init__(self, runtime, *, clock=None, log_fn=print):
        self.runtime = runtime
        self.socket_path = "/tmp/test-camera-ipc.sock"
        self.started = False
        self.stopped = False
        self.sessions = set()
        self.revoke_entered = threading.Event()
        self.allow_revoke_finish = threading.Event()
        self.revoke_calls = 0

    def start(self):
        self.started = True

    def stop(self, timeout=None):
        self.stopped = True

    def activate_session(self, session_id, rig_ids=None):
        self.sessions.add(session_id)

    def revoke_session(self, session_id):
        self.revoke_calls += 1
        if session_id not in self.sessions:
            raise RuntimeError("server session is not active")

        # Match the production server: remove server-side authority first,
        # then spend time cleaning prepared state.
        self.sessions.remove(session_id)
        self.revoke_entered.set()
        assert self.allow_revoke_finish.wait(2.0)


def _config():
    return {
        "rigs": [
            {
                "rig_id": 1,
                "devices": {
                    "camera": {
                        "backend": "sony",
                        "manufacturer": "SONY",
                        "model": "ILCE-7M5",
                    }
                },
                "photo": {},
                "optics": {"focal_length_mm": 430},
            }
        ]
    }


def test_duplicate_close_cannot_release_runtime_lease_during_first_cleanup():
    holder = {}

    def server_factory(runtime, *, clock=None, log_fn=print):
        server = BlockingRevokeServer(
            runtime,
            clock=clock,
            log_fn=log_fn,
        )
        holder["server"] = server
        return server

    runtime = CameraWorkerRuntime(
        worker_factory=FakeWorker,
        ipc_server_factory=server_factory,
    )
    runtime.reconcile(_config())
    session = runtime.open_ipc_session([1])
    server = holder["server"]

    first_error = []

    def first_close():
        try:
            runtime.close_ipc_session(session.session_id)
        except BaseException as exc:
            first_error.append(exc)

    thread = threading.Thread(target=first_close)
    thread.start()

    assert server.revoke_entered.wait(1.0)

    # The production server-side session is already gone here, but runtime
    # ownership must remain frozen until the first closer finishes cleanup.
    with pytest.raises(
        RuntimeError,
        match="close is already in progress",
    ):
        runtime.close_ipc_session(session.session_id)

    assert server.revoke_calls == 1
    assert runtime.get_for_rig(1) is not None

    # Reconfiguration of the leased camera must still be rejected while the
    # first revoke is blocked in prepared-token cleanup.
    changed = _config()
    changed["rigs"][0]["devices"]["camera"]["model"] = "DIFFERENT"

    with pytest.raises(
        RuntimeError,
        match="while a trigger IPC session is active",
    ):
        runtime.reconcile(changed)

    assert server.stopped is False

    server.allow_revoke_finish.set()
    thread.join(2.0)
    assert not thread.is_alive()
    assert first_error == []

    assert server.revoke_calls == 1
    assert server.stopped is True

    # Once cleanup is complete, runtime ownership is released normally.
    runtime.reconcile(changed)


def test_closing_marker_is_cleared_after_revoke_failure():
    class FailingServer(BlockingRevokeServer):
        def revoke_session(self, session_id):
            self.revoke_calls += 1
            self.sessions.remove(session_id)
            raise RuntimeError("revoke failed")

    holder = {}

    def server_factory(runtime, *, clock=None, log_fn=print):
        server = FailingServer(runtime, clock=clock, log_fn=log_fn)
        holder["server"] = server
        return server

    runtime = CameraWorkerRuntime(
        worker_factory=FakeWorker,
        ipc_server_factory=server_factory,
    )
    runtime.reconcile(_config())
    session = runtime.open_ipc_session([1])

    with pytest.raises(RuntimeError, match="revoke failed"):
        runtime.close_ipc_session(session.session_id)

    # Failure cleanup must not leave a stale "closing" tombstone.
    assert session.session_id not in runtime._ipc_closing_session_ids
    assert session.session_id not in runtime._ipc_session_ids
