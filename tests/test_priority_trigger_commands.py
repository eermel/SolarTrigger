from pathlib import Path
import sys
from types import ModuleType

import pytest


pytest.importorskip("flask")
pytest.importorskip("flask_socketio")
sys.modules.setdefault("gphoto2", ModuleType("gphoto2"))

import flask_app.app as flask_module
from backend import camera_characterization_routes, camera_validation_routes


ROOT = Path(__file__).resolve().parents[1]


def _source(path):
    return (ROOT / path).read_text(encoding="utf-8")


def test_priority_trigger_routes_bypass_camera_maintenance_guards():
    characterization = _source("backend/camera_characterization_routes.py")
    validation = _source("backend/camera_validation_routes.py")

    for source in (characterization, validation):
        assert '"/api/trigger/stop"' in source
        assert '"/api/trigger/totality_only"' in source
        allow = source.index('"/api/trigger/stop"')
        block = source.index('path.startswith("/api/trigger/")', allow)
        assert allow < block


def test_stop_cancels_backend_command_before_runtime_ownership():
    command = flask_module._begin_trigger_start_command((1, 2))
    try:
        assert flask_module._cancel_pending_trigger_start(1) is True
        assert flask_module._trigger_start_command_cancelled(command, 1) is True
        assert flask_module._trigger_start_command_cancelled(command, 2) is False
    finally:
        flask_module._end_trigger_start_command(command)


def test_pending_preflight_wait_releases_when_command_leaves_preflight():
    command = flask_module._begin_trigger_start_command((1,))
    try:
        flask_module._set_trigger_start_command_stage(command, "launching")
        assert flask_module._wait_pending_trigger_preflight_release(
            1,
            0.01,
        ) is True
    finally:
        flask_module._end_trigger_start_command(command)


def test_pending_preflight_wait_is_bounded():
    command = flask_module._begin_trigger_start_command((1,))
    try:
        flask_module._set_trigger_start_command_stage(command, "preflight")
        assert flask_module._wait_pending_trigger_preflight_release(
            1,
            0.0,
        ) is False
    finally:
        flask_module._end_trigger_start_command(command)


def test_emergency_totality_does_not_use_normal_start_admission_guard():
    app_source = _source("flask_app/app.py")
    start = app_source.index(
        '@app.route("/api/trigger/totality_only", methods=["POST"])'
    )
    end = app_source.index("def _emit_trigger", start)
    route = app_source[start:end]

    assert "_trigger_start_guarded(" not in route
    assert "_system_maintenance_running()" in route
    assert "_cancel_pending_trigger_start(rig_id)" in route
    assert "_preempt_camera_maintenance_for_emergency()" in route
    assert "_trigger_service.start_totality_only(rig_id=rig_id)" in route


class _FakeMaintenanceJob:
    def __init__(self, running):
        self.running = running
        self.cancel_calls = 0

    def cancel(self):
        self.cancel_calls += 1
        self.running = False


def test_emergency_cancels_camera_maintenance_before_start(monkeypatch):
    from backend import camera_characterization, camera_validation

    characterization = _FakeMaintenanceJob(True)
    validation = _FakeMaintenanceJob(True)
    monkeypatch.setattr(camera_characterization, "JOB", characterization)
    monkeypatch.setattr(camera_validation, "JOB", validation)

    result = flask_module._preempt_camera_maintenance_for_emergency(
        timeout_s=0.1,
    )

    assert result == ["characterization", "validation"]
    assert characterization.cancel_calls == 1
    assert validation.cancel_calls == 1


def test_emergency_camera_maintenance_wait_fails_closed(monkeypatch):
    from backend import camera_characterization, camera_validation
    from backend.trigger_service import TriggerValidationError

    class _StuckJob(_FakeMaintenanceJob):
        def cancel(self):
            self.cancel_calls += 1

    characterization = _StuckJob(True)
    validation = _FakeMaintenanceJob(False)
    monkeypatch.setattr(camera_characterization, "JOB", characterization)
    monkeypatch.setattr(camera_validation, "JOB", validation)

    with pytest.raises(TriggerValidationError) as excinfo:
        flask_module._preempt_camera_maintenance_for_emergency(
            timeout_s=0.0,
        )

    assert excinfo.value.code == "CAMERA_MAINTENANCE_PREEMPT_TIMEOUT"
    assert characterization.cancel_calls == 1
