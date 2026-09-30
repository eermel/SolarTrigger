from datetime import datetime, timezone

import pytest

from backend.trigger_service import TriggerValidationError
from backend.trigger_start_preflight import prepare_trigger_hardware


class FakeState:
    def __init__(self, gps, events):
        self.gps = dict(gps)
        self.events = events

    def snapshot(self, section):
        assert section == "gps"
        self.events.append("gps")
        return dict(self.gps)


class FakeCameraWorker:
    def __init__(self, events, *, fail=None):
        self.events = events
        self.fail = fail
        self.required_state = None

    def preflight(self, required_state):
        self.events.append("camera.preflight")
        self.required_state = dict(required_state)
        if self.fail is not None:
            raise self.fail
        return {
            "ok": True,
            "model": "TEST CAMERA",
            "changed": [],
        }


class FakeCameraRuntime:
    def __init__(self, events, worker):
        self.events = events
        self.worker = worker

    def reconcile(self, _config):
        self.events.append("camera.reconcile")

    def get_for_rig(self, rig_id):
        self.events.append(f"camera.get:{rig_id}")
        return self.worker


class FakeMountWorker:
    def __init__(self, events, *, sync_fail=None, tracking_fail=None):
        self.events = events
        self.sync_fail = sync_fail
        self.tracking_fail = tracking_fail
        self.sync_args = None

    def sync_site_time(self, *args):
        self.events.append("mount.sync")
        self.sync_args = args
        if self.sync_fail is not None:
            raise self.sync_fail
        return {"status": "ok"}

    def set_tracking_mode(self, mode):
        self.events.append(f"mount.mode:{mode}")
        if self.tracking_fail is not None:
            raise self.tracking_fail
        return {"tracking_mode": mode}

    def start_tracking(self):
        self.events.append("mount.start")
        if self.tracking_fail is not None:
            raise self.tracking_fail
        return {"tracking_enabled": True}


class FakeMountRuntime:
    def __init__(self, events, worker):
        self.events = events
        self.worker = worker

    def reconcile(self, _config):
        self.events.append("mount.reconcile")

    def get_for_rig(self, rig_id):
        self.events.append(f"mount.get:{rig_id}")
        return self.worker


def _gps(**overrides):
    value = {
        "synced": True,
        "sync_time": "2026-09-30T08:00:00+00:00",
        "lat": 48.0,
        "lon": 2.0,
        "alt": 100.0,
        "utc_offset_minutes": 120,
    }
    value.update(overrides)
    return value


def _config(*, mount=True):
    return {
        "rigs": [
            {
                "rig_id": 1,
                "enabled": True,
                "devices": {
                    "camera": {
                        "backend": "profile-test",
                        "serial": "CAM-1",
                    },
                    "mount": (
                        {
                            "backend": "onstep",
                            "serial": "MOUNT-1",
                        }
                        if mount
                        else None
                    ),
                },
            }
        ]
    }


def _run(
    *,
    gps=None,
    camera_fail=None,
    mount_worker_marker=True,
    mount_sync_fail=None,
    mount_tracking_fail=None,
):
    events = []
    camera_worker = FakeCameraWorker(events, fail=camera_fail)
    camera_runtime = FakeCameraRuntime(events, camera_worker)
    mount_worker = (
        FakeMountWorker(
            events,
            sync_fail=mount_sync_fail,
            tracking_fail=mount_tracking_fail,
        )
        if mount_worker_marker
        else None
    )
    mount_runtime = FakeMountRuntime(events, mount_worker)

    def config_loader():
        events.append("config")
        return _config(mount=mount_worker_marker)

    result = prepare_trigger_hardware(
        rig_id=1,
        state_store=FakeState(gps or _gps(), events),
        rig_config_loader=config_loader,
        camera_runtime=camera_runtime,
        mount_runtime=mount_runtime,
        camera_required_state_loader=lambda: {
            "iso": "100",
            "f-number": "f/8",
        },
        trigger_active_fn=lambda rig_id: events.append(
            f"active:{rig_id}"
        ) or False,
        log_fn=lambda _rig_id, _message: None,
        now_fn=lambda: datetime(
            2026, 9, 30, 8, 5, 0, tzinfo=timezone.utc
        ),
    )
    return result, events, mount_worker, camera_worker


def test_gps_is_first_verification_and_blocks_all_hardware():
    events = []

    def forbidden_config():
        raise AssertionError("configuration must not be loaded before GPS passes")

    with pytest.raises(TriggerValidationError) as caught:
        prepare_trigger_hardware(
            rig_id=1,
            state_store=FakeState(_gps(synced=False), events),
            rig_config_loader=forbidden_config,
            camera_runtime=None,
            mount_runtime=None,
            camera_required_state_loader=lambda: pytest.fail(
                "camera state must not be loaded before GPS passes"
            ),
            trigger_active_fn=lambda _rig_id: pytest.fail(
                "trigger state must not be queried before GPS passes"
            ),
        )

    assert caught.value.code == "GPS_NOT_SYNCED"
    assert events == ["gps"]


def test_preflight_order_is_camera_then_mount_sync_then_solar_tracking():
    result, events, mount_worker, camera_worker = _run()

    assert events == [
        "gps",
        "active:1",
        "config",
        "camera.reconcile",
        "camera.get:1",
        "camera.preflight",
        "mount.reconcile",
        "mount.get:1",
        "mount.sync",
        "mount.mode:solar",
        "mount.start",
    ]
    assert camera_worker.required_state == {
        "iso": "100",
        "f-number": "f/8",
    }
    assert mount_worker.sync_args == (
        48.0,
        2.0,
        100.0,
        "2026-09-30T08:05:00",
        2.0,
    )
    assert result["camera"]["model"] == "TEST CAMERA"
    assert result["mount"]["tracking"]["tracking_enabled"] is True


def test_rig_without_mount_still_gets_camera_preflight():
    result, events, _mount_worker, camera_worker = _run(
        mount_worker_marker=False
    )

    assert events == [
        "gps",
        "active:1",
        "config",
        "camera.reconcile",
        "camera.get:1",
        "camera.preflight",
        "mount.reconcile",
        "mount.get:1",
    ]
    assert camera_worker.required_state == {
        "iso": "100",
        "f-number": "f/8",
    }
    assert result["mount"] is None


def test_camera_failure_prevents_any_mount_operation():
    events = []
    camera_runtime = FakeCameraRuntime(
        events,
        FakeCameraWorker(events, fail=RuntimeError("camera offline")),
    )
    mount_runtime = FakeMountRuntime(
        events,
        FakeMountWorker(events),
    )

    with pytest.raises(TriggerValidationError) as caught:
        prepare_trigger_hardware(
            rig_id=1,
            state_store=FakeState(_gps(), events),
            rig_config_loader=lambda: events.append("config") or _config(),
            camera_runtime=camera_runtime,
            mount_runtime=mount_runtime,
            camera_required_state_loader=lambda: {
                "iso": "100",
                "f-number": "f/8",
            },
            trigger_active_fn=lambda _rig_id: False,
            now_fn=lambda: datetime(
                2026, 9, 30, 8, 5, 0, tzinfo=timezone.utc
            ),
        )

    assert caught.value.code == "CAMERA_PREFLIGHT_FAILED"
    assert "mount.reconcile" not in events
    assert "mount.sync" not in events
    assert "mount.start" not in events


def test_mount_sync_failure_prevents_tracking_activation():
    events = []
    camera_runtime = FakeCameraRuntime(events, FakeCameraWorker(events))
    mount_runtime = FakeMountRuntime(
        events,
        FakeMountWorker(events, sync_fail=RuntimeError("sync rejected")),
    )

    with pytest.raises(TriggerValidationError) as caught:
        prepare_trigger_hardware(
            rig_id=1,
            state_store=FakeState(_gps(), events),
            rig_config_loader=lambda: events.append("config") or _config(),
            camera_runtime=camera_runtime,
            mount_runtime=mount_runtime,
            camera_required_state_loader=lambda: {
                "iso": "100",
                "f-number": "f/8",
            },
            trigger_active_fn=lambda _rig_id: False,
            now_fn=lambda: datetime(
                2026, 9, 30, 8, 5, 0, tzinfo=timezone.utc
            ),
        )

    assert caught.value.code == "MOUNT_SYNC_FAILED"
    assert "mount.sync" in events
    assert "mount.mode:solar" not in events
    assert "mount.start" not in events
