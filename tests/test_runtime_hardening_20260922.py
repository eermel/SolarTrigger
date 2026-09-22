from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.camera_worker import CameraWorker
from backend.camera_process_worker import ProcessCameraWorker
from backend.runtime_interlock import (
    MaintenanceActiveError,
    TriggerActiveError,
    start_maintenance_if_trigger_idle,
    trigger_start_section,
)
from services.camera_service import CameraService


class _FakeCamera:
    def init(self):
        return None

    def exit(self):
        return None


class _FakePlugin:
    name = "fake-profile"

    def __init__(self, camera, calls):
        self.camera = camera
        self.calls = calls

    def init_settings(self, **settings):
        self.calls.append(("init", dict(settings)))
        return {"ok": True}

    def set_exposure_settings(self, **settings):
        self.calls.append(("phase", dict(settings)))
        return True


def test_camera_service_reconnect_restores_latest_phase_state():
    calls = []
    service = CameraService(
        camera_factory=_FakeCamera,
        plugin_loader=lambda camera, _log: _FakePlugin(camera, calls),
        log_fn=lambda _message: None,
    )
    service.connect()
    service.init_settings(aperture="f/8", iso="100")
    service.apply_phase_settings(aperture="f/8", iso="400")
    service.invalidate_connection()

    assert not service.connected
    service.recover_runtime_connection()

    assert service.connected
    assert calls[-1] == (
        "init",
        {
            "aperture": "f/8",
            "iso": "400",
            "image_format": "RAW",
            "white_balance": "Daylight",
        },
    )


class _RecoverableTriggerService:
    def __init__(self):
        self.connected = True
        self.recover_calls = 0
        self.invalidate_calls = 0
        self.trigger_calls = 0

    def recover_runtime_connection(self):
        self.recover_calls += 1
        self.connected = True

    def invalidate_connection(self):
        self.invalidate_calls += 1
        self.connected = False

    def prepare_capture(self, intent):
        return SimpleNamespace(intent=intent)

    def trigger_prepared(self, prepared, deadline=None, monotonic_deadline=None):
        self.trigger_calls += 1
        if self.trigger_calls == 1:
            raise RuntimeError("USB transport disappeared")
        return {"ok": True}

    def close(self):
        return None


def test_real_prepare_trigger_path_recovers_after_usb_failure():
    service = _RecoverableTriggerService()
    worker = CameraWorker(
        rig_id=1,
        service_factory=lambda: service,
        log_fn=lambda _message: None,
    )
    worker.start()
    try:
        prepared = worker.prepare_capture("first")
        with pytest.raises(RuntimeError, match="USB transport disappeared"):
            worker.trigger_prepared(prepared)

        assert service.invalidate_calls == 1
        assert not service.connected

        prepared = worker.prepare_capture("second")
        assert service.recover_calls == 1
        assert worker.trigger_prepared(prepared) == {"ok": True}
    finally:
        worker.stop(timeout=1.0)


def _restoring_child(conn, rig_id, camera_entry, clock_spec, call_timeout_s):
    initialized = False
    conn.send({"kind": "ready"})
    while True:
        request = conn.recv()
        operation = request.get("operation")
        if operation == "__stop__":
            return
        if operation == "init_settings":
            initialized = True
            conn.send({"kind": "result", "value": {"initialized": True}})
            continue
        if operation == "clear_runtime_recovery_state":
            initialized = False
            conn.send({"kind": "result", "value": None})
            continue
        if operation == "prepare_capture":
            if not initialized:
                conn.send({
                    "kind": "error",
                    "class": "RuntimeError",
                    "code": None,
                    "message": "not initialized",
                })
            else:
                conn.send({
                    "kind": "result",
                    "value": SimpleNamespace(
                        token="prepared",
                        estimated_total_s=0.1,
                        exposures_s=[0.1],
                        planned_count=1,
                        plugin_name="fake",
                    ),
                })
            continue
        if operation == "test_photo_fast":
            import time
            while True:
                time.sleep(1)
        conn.send({
            "kind": "error",
            "class": "RuntimeError",
            "code": None,
            "message": f"unsupported {operation}",
        })


def test_process_camera_clear_forgets_parent_and_live_child_state():
    worker = ProcessCameraWorker(
        rig_id=1,
        call_timeout_s=0.05,
        process_target=_restoring_child,
        log_fn=lambda _message: None,
    )
    worker.configure_camera({"backend": "gphoto2", "model": "FAKE"})
    worker.start()
    try:
        worker.init_settings(aperture="f/8", iso="100")
        generation = worker.generation

        worker.clear_runtime_recovery_state()

        assert worker._runtime_init_settings is None
        assert worker.generation == generation
        with pytest.raises(RuntimeError, match="not initialized"):
            worker.prepare_capture(SimpleNamespace())
        assert worker.generation == generation
    finally:
        worker.stop(timeout=1.0)


def test_process_camera_stop_start_does_not_restore_previous_run():
    worker = ProcessCameraWorker(
        rig_id=1,
        call_timeout_s=0.05,
        process_target=_restoring_child,
        log_fn=lambda _message: None,
    )
    worker.configure_camera({"backend": "gphoto2", "model": "FAKE"})
    worker.start()
    worker.init_settings(aperture="f/8", iso="100")

    assert worker.stop(timeout=1.0) is True
    assert worker._runtime_init_settings is None

    worker.start()
    try:
        with pytest.raises(RuntimeError, match="not initialized"):
            worker.prepare_capture(SimpleNamespace())
    finally:
        worker.stop(timeout=1.0)


def test_process_camera_respawn_replays_configuration_not_photo():
    worker = ProcessCameraWorker(
        rig_id=1,
        call_timeout_s=0.05,
        process_target=_restoring_child,
        log_fn=lambda _message: None,
    )
    worker.configure_camera({"backend": "gphoto2", "model": "FAKE"})
    worker.start()
    try:
        worker.init_settings(aperture="f/8", iso="100")
        generation = worker.generation
        with pytest.raises(Exception):
            worker.test_photo_fast("1/500")
        assert not worker.running

        prepared = worker.prepare_capture(SimpleNamespace())
        assert prepared.token == "prepared"
        assert worker.generation == generation + 1
    finally:
        worker.stop(timeout=1.0)


def test_runtime_interlock_blocks_both_race_directions():
    with pytest.raises(MaintenanceActiveError):
        with trigger_start_section(lambda: True):
            pass

    started = []
    with pytest.raises(TriggerActiveError):
        start_maintenance_if_trigger_idle(
            lambda: True,
            lambda: started.append(True),
        )
    assert started == []

    with trigger_start_section(lambda: False):
        assert start_maintenance_if_trigger_idle(
            lambda: False,
            lambda: "started",
        ) == "started"
