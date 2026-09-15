import threading

import pytest

from backend.camera_worker_runtime import CameraWorkerRuntime


class _Server:
    def __init__(self):
        self.runtime = None
        self.revoked = []
        self.stopped = 0
        self.helper_completed = False
        self.fail_revoke = False

    def revoke_session(self, session_id):
        self.revoked.append(session_id)

        # Prove the CameraWorkerRuntime global lock is not held while revoke
        # performs potentially slow child-process cleanup.
        done = threading.Event()

        def helper():
            self.runtime.active_camera_rig_ids()
            self.helper_completed = True
            done.set()

        thread = threading.Thread(target=helper)
        thread.start()
        assert done.wait(0.5), "runtime lock leaked across revoke_session()"
        thread.join()

        if self.fail_revoke:
            raise RuntimeError("revoke failed")

    def stop(self, timeout=2.0):
        self.stopped += 1


def _runtime(server):
    runtime = CameraWorkerRuntime(worker_factory=lambda **_kwargs: None)
    runtime._registry = {1: object()}
    runtime._ipc_server = server
    runtime._ipc_session_ids = {"session-a"}
    server.runtime = runtime
    return runtime


def test_close_ipc_session_does_not_hold_runtime_lock_during_revoke():
    server = _Server()
    runtime = _runtime(server)

    runtime.close_ipc_session("session-a")

    assert server.revoked == ["session-a"]
    assert server.helper_completed is True
    assert server.stopped == 1
    assert runtime._ipc_server is None
    assert runtime._ipc_session_ids == set()


def test_close_ipc_session_remains_authoritative_when_revoke_raises():
    server = _Server()
    server.fail_revoke = True
    runtime = _runtime(server)

    with pytest.raises(RuntimeError, match="revoke failed"):
        runtime.close_ipc_session("session-a")

    assert runtime._ipc_session_ids == set()
    assert runtime._ipc_server is None
    assert server.stopped == 1


def test_new_session_opened_during_revoke_prevents_server_stop():
    class _RacingServer(_Server):
        def revoke_session(self, session_id):
            self.revoked.append(session_id)
            with self.runtime._lock:
                self.runtime._ipc_session_ids.add("session-b")

    server = _RacingServer()
    runtime = _runtime(server)

    runtime.close_ipc_session("session-a")

    assert runtime._ipc_server is server
    assert runtime._ipc_session_ids == {"session-b"}
    assert server.stopped == 0
