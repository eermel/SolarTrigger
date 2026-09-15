import threading
from pathlib import Path

import pytest

from backend.camera_worker_runtime import CameraWorkerRuntime


class FakeWorker:
    def __init__(self, rig_id, clock=None, log_fn=None):
        self.rig_id = rig_id
        self.config = None
        self.started = False
        self.stopped = False

    def configure_camera(self, config):
        self.config = dict(config)

    def start(self):
        self.started = True

    def stop(self, timeout=None):
        self.stopped = True


class BlockingRevokeServer:
    def __init__(self, runtime, **kwargs):
        self.runtime = runtime
        self.socket_path = Path("/tmp/test-camera-ipc-close-race.sock")
        self.sessions = {}
        self.revoke_entered = threading.Event()
        self.allow_revoke_finish = threading.Event()
        self.stopped = False

    def start(self):
        return self.socket_path

    def activate_session(self, session_id, rig_ids=None):
        allowed = None if rig_ids is None else frozenset(rig_ids)
        for other in self.sessions.values():
            if allowed is None or other is None:
                raise RuntimeError("scope conflict")
            if allowed & other:
                raise RuntimeError("scope conflict")
        self.sessions[session_id] = allowed

    def revoke_session(self, session_id):
        # Match production ordering: the server-side lease disappears before
        # prepared-token cleanup finishes.
        self.sessions.pop(session_id)
        self.revoke_entered.set()
        assert self.allow_revoke_finish.wait(2.0)

    def stop(self, timeout=None):
        self.stopped = True


def cfg(alias1="cam-a", alias2="cam-b"):
    return {
        "rigs": [
            {
                "rig_id": 1,
                "devices": {
                    "camera": {
                        "backend": "sony",
                        "manufacturer": "SONY",
                        "model": "ILCE-7M5",
                        "alias": alias1,
                    },
                    "mount": None,
                },
                "optics": {"focal_length_mm": 430},
                "photo": {
                    "atmos_enabled": False,
                    "anti_trailing_enabled": False,
                    "motion_tolerance_px": 1.0,
                    "iso_compensation_enabled": True,
                    "iso_max": 6400,
                },
            },
            {
                "rig_id": 2,
                "devices": {
                    "camera": {
                        "backend": "sony",
                        "manufacturer": "SONY",
                        "model": "ILCE-7M5",
                        "alias": alias2,
                    },
                    "mount": None,
                },
                "optics": {"focal_length_mm": 200},
                "photo": {
                    "atmos_enabled": False,
                    "anti_trailing_enabled": False,
                    "motion_tolerance_px": 1.0,
                    "iso_compensation_enabled": True,
                    "iso_max": 6400,
                },
            },
        ]
    }


def runtime():
    return CameraWorkerRuntime(
        worker_factory=FakeWorker,
        ipc_server_factory=BlockingRevokeServer,
    )


def test_close_keeps_worker_binding_frozen_until_revoke_cleanup_finishes():
    rt = runtime()
    rt.reconcile(cfg(alias1="before"))
    old_worker = rt.get_for_rig(1)
    lease = rt.open_ipc_session((1,))
    server = rt._ipc_server

    errors = []

    def closer():
        try:
            rt.close_ipc_session(lease.session_id)
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=closer)
    thread.start()
    assert server.revoke_entered.wait(1.0)

    with pytest.raises(
        RuntimeError,
        match="cannot reconfigure camera for RIG 1",
    ):
        rt.reconcile(cfg(alias1="after"))

    assert rt.get_for_rig(1) is old_worker
    assert old_worker.stopped is False

    server.allow_revoke_finish.set()
    thread.join(1.0)
    assert not thread.is_alive()
    assert errors == []

    rt.reconcile(cfg(alias1="after"))
    assert rt.get_for_rig(1) is not old_worker
    assert old_worker.stopped is True


def test_close_blocks_new_overlapping_lease_until_cleanup_finishes():
    rt = runtime()
    rt.reconcile(cfg())
    lease = rt.open_ipc_session((1,))
    server = rt._ipc_server

    thread = threading.Thread(
        target=lambda: rt.close_ipc_session(lease.session_id)
    )
    thread.start()
    assert server.revoke_entered.wait(1.0)

    # The server has already forgotten the old lease, but runtime ownership
    # must still prevent an overlapping lease from entering.
    assert server.sessions == {}
    with pytest.raises(
        RuntimeError,
        match="already owns one of these RIGs",
    ):
        rt.open_ipc_session((1,))

    # A disjoint RIG remains available.
    lease2 = rt.open_ipc_session((2,))
    assert lease2.session_id in rt._ipc_session_ids

    server.allow_revoke_finish.set()
    thread.join(1.0)
    assert not thread.is_alive()

    rt.close_ipc_session(lease2.session_id)


def test_revoke_failure_still_releases_runtime_lease_after_cleanup_attempt():
    class FailingServer(BlockingRevokeServer):
        def revoke_session(self, session_id):
            self.sessions.pop(session_id)
            raise RuntimeError("cleanup failed")

    rt = CameraWorkerRuntime(
        worker_factory=FakeWorker,
        ipc_server_factory=FailingServer,
    )
    rt.reconcile(cfg(alias1="before"))
    lease = rt.open_ipc_session((1,))

    with pytest.raises(RuntimeError, match="cleanup failed"):
        rt.close_ipc_session(lease.session_id)

    assert lease.session_id not in rt._ipc_session_ids
    assert lease.session_id not in rt._ipc_session_rigs

    # The failed cleanup must not leave the RIG permanently frozen.
    rt.reconcile(cfg(alias1="after"))
