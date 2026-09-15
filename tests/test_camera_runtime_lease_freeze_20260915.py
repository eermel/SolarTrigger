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


class FakeIpcServer:
    def __init__(self, runtime, **kwargs):
        self.runtime = runtime
        self.socket_path = Path("/tmp/test-camera-ipc.sock")
        self.sessions = {}
        self.stopped = False

    def start(self):
        return self.socket_path

    def activate_session(self, session_id, rig_ids=None):
        self.sessions[session_id] = (
            None if rig_ids is None else frozenset(rig_ids)
        )

    def revoke_session(self, session_id):
        self.sessions.pop(session_id)

    def stop(self, timeout=None):
        self.stopped = True


def cfg(*, alias1="cam-a", alias2="cam-b", iso1=800, iso2=1600):
    return {
        "eclipse": {
            "reference_site": {"lat": 24.0, "lon": 35.0}
        },
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
                    "mount": {
                        "control": "none",
                        "geometry": "fixed",
                        "tracking": False,
                    },
                },
                "optics": {"focal_length_mm": 430},
                "photo": {
                    "atmos_enabled": True,
                    "anti_trailing_enabled": True,
                    "motion_tolerance_px": 1.0,
                    "iso_compensation_enabled": True,
                    "iso_max": iso1,
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
                    "mount": {
                        "control": "none",
                        "geometry": "fixed",
                        "tracking": False,
                    },
                },
                "optics": {"focal_length_mm": 200},
                "photo": {
                    "atmos_enabled": False,
                    "anti_trailing_enabled": False,
                    "motion_tolerance_px": 2.0,
                    "iso_compensation_enabled": True,
                    "iso_max": iso2,
                },
            },
        ],
    }


def runtime():
    return CameraWorkerRuntime(
        worker_factory=FakeWorker,
        ipc_server_factory=FakeIpcServer,
    )


def test_active_lease_rejects_camera_rebinding_for_same_rig():
    rt = runtime()
    rt.reconcile(cfg(alias1="before"))
    worker = rt.get_for_rig(1)
    rt.open_ipc_session((1,))

    with pytest.raises(
        RuntimeError,
        match="cannot reconfigure camera for RIG 1",
    ):
        rt.reconcile(cfg(alias1="after"))

    assert rt.get_for_rig(1) is worker
    assert worker.stopped is False


def test_active_lease_freezes_policy_across_unrelated_reconcile():
    rt = runtime()
    rt.reconcile(cfg(iso1=800, iso2=1600))
    lease = rt.open_ipc_session((1,))

    assert rt.get_policy_config_for_rig(1)["photo"]["iso_max"] == 800

    # RIG 2 remains reconfigurable while RIG 1 owns its independent lease.
    rt.reconcile(cfg(alias2="cam-b-new", iso1=6400, iso2=3200))

    # The leased RIG keeps the exact policy captured at Trigger start.
    assert rt.get_policy_config_for_rig(1)["photo"]["iso_max"] == 800
    # The unleased RIG sees the newly reconciled policy.
    assert rt.get_policy_config_for_rig(2)["photo"]["iso_max"] == 3200

    rt.close_ipc_session(lease.session_id)

    # Once the lease is gone, the latest runtime config becomes visible again.
    assert rt.get_policy_config_for_rig(1)["photo"]["iso_max"] == 6400


def test_disjoint_rig_can_be_rebound_while_other_rig_is_leased():
    rt = runtime()
    rt.reconcile(cfg(alias1="cam-a", alias2="cam-b"))
    rig1_worker = rt.get_for_rig(1)
    rig2_worker = rt.get_for_rig(2)
    rt.open_ipc_session((1,))

    rt.reconcile(cfg(alias1="cam-a", alias2="cam-b-new"))

    assert rt.get_for_rig(1) is rig1_worker
    assert rig1_worker.stopped is False
    assert rt.get_for_rig(2) is not rig2_worker
    assert rig2_worker.stopped is True


def test_second_disjoint_trigger_gets_its_own_frozen_policy():
    rt = runtime()
    rt.reconcile(cfg(iso1=800, iso2=1600))
    lease1 = rt.open_ipc_session((1,))

    rt.reconcile(cfg(iso1=6400, iso2=3200))
    lease2 = rt.open_ipc_session((2,))

    assert rt.get_policy_config_for_rig(1)["photo"]["iso_max"] == 800
    assert rt.get_policy_config_for_rig(2)["photo"]["iso_max"] == 3200

    rt.close_ipc_session(lease2.session_id)
    assert rt.get_policy_config_for_rig(1)["photo"]["iso_max"] == 800

    rt.close_ipc_session(lease1.session_id)
    assert rt.get_policy_config_for_rig(1)["photo"]["iso_max"] == 6400
