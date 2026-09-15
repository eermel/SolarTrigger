from __future__ import annotations

import math
import threading
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.camera_ipc_server import CameraIpcServer
from backend.device_process_worker import (
    SupervisedDeviceProcess,
    WorkerUnavailableError,
    error_payload,
)
from backend.phase_trigger import _number
from backend.trigger_service import TriggerService, TriggerValidationError
from plugins.camera.nikon import NikonDSLRPlugin
from plugins.camera.sony import SonyPlugin
from scripts.camera_ipc_client import CameraIpcClient
from scripts.eclipse_trigger import exposure_rig, run_emergency_totality
from scripts.fanout_camera_adapter import FanoutCameraAdapter
from services.camera_service import CaptureIntent, PreparedCapture


class _State:
    def __init__(self, gps=None):
        self.gps = dict(gps or {})
        self.sections = {}
        self.values = {}
        self.trigger_updates = []

    def snapshot(self, key=None):
        if key == "gps":
            return dict(self.gps)
        return {}

    def update_section(self, section, values, persist=False):
        if section == "gps":
            self.gps.update(values)
            return dict(self.gps)
        self.sections.setdefault(section, {}).update(values)
        return dict(self.sections[section])

    def set(self, key, value, persist=False):
        self.values[key] = value

    def save(self):
        return None

    def update_trigger_rig(self, rig_id, values):
        self.trigger_updates.append((rig_id, dict(values)))
        return dict(values)


def test_phase_numbers_reject_nan_and_inf():
    for value in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValueError, match="finite"):
            _number(value, "interval_s")


def test_exposure_rig_rejects_string_boolean():
    config = {
        "config_type": "exposure_optimization",
        "atmospheric_attenuation_enabled": "false",
        "atmospheric_attenuation_replace_exposures": False,
        "rigs": [{"rig_id": 1, "photo": {}}],
    }
    with pytest.raises(ValueError, match="must be boolean"):
        exposure_rig(config, 1)


def test_emergency_totality_rejects_nonfinite_interval_before_camera_access():
    class Camera:
        def initialize(self, **kwargs):
            raise AssertionError("camera must not be touched")

    with pytest.raises(ValueError, match="finite"):
        run_emergency_totality(
            Camera(),
            {
                "phases": {
                    "totality": {
                        "enabled": True,
                        "interval_s": float("nan"),
                        "duration_s": None,
                    }
                }
            },
            {"photo": {}},
            threading.Event(),
        )


def test_ipc_client_timeout_rejects_nan_and_inf():
    for value in (float("nan"), float("inf")):
        with pytest.raises(ValueError, match="finite"):
            CameraIpcClient._effective_timeout(value)

    with pytest.raises(ValueError, match="finite"):
        CameraIpcClient._effective_timeout(
            5.0,
            deadline_monotonic=float("nan"),
        )


def test_trigger_start_rejects_gps_sync_in_progress():
    service = TriggerService.__new__(TriggerService)
    service.state = _State(
        {
            "synced": True,
            "gps_sync_running": True,
            "sync_time": datetime.now(timezone.utc).isoformat(),
        }
    )

    with pytest.raises(TriggerValidationError) as exc_info:
        service.validate_start(rig_id=1, require_gps=True)

    assert exc_info.value.code == "GPS_SYNC_IN_PROGRESS"


def test_gps_time_failure_invalidates_previous_sync(tmp_path):
    from backend.gps_controller import GpsController

    previous_sync_time = datetime.now(timezone.utc).isoformat()
    state = _State(
        {
            "synced": True,
            "sync_time": previous_sync_time,
            "gps_sync_running": True,
        }
    )

    controller = GpsController(
        state_store=state,
        config_file=tmp_path / "gps.json",
        timezone_fn=lambda *a, **k: 0.0,
        time_sync_fn=lambda *a, **k: False,
        log_fn=lambda *a, **k: None,
        emit_fn=lambda *a, **k: None,
    )
    controller._acquire = lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("source failed")
    )

    controller._run(
        1.0,
        mode="time_only",
        source=None,
        source_payload=None,
    )

    assert state.gps["synced"] is False
    assert state.gps["sync_time"] == previous_sync_time
    assert state.gps["gps_sync_running"] is False


def test_gps_location_only_failure_preserves_time_authority(tmp_path):
    from backend.gps_controller import GpsController

    sync_time = datetime.now(timezone.utc).isoformat()
    state = _State(
        {
            "synced": True,
            "sync_time": sync_time,
            "gps_sync_running": True,
        }
    )
    controller = GpsController(
        state_store=state,
        config_file=tmp_path / "gps.json",
        timezone_fn=lambda *a, **k: 0.0,
        time_sync_fn=lambda *a, **k: True,
        log_fn=lambda *a, **k: None,
        emit_fn=lambda *a, **k: None,
    )
    controller._acquire = lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("position failed")
    )

    controller._run(
        1.0,
        mode="location_only",
        source=None,
        source_payload=None,
    )

    assert state.gps["synced"] is True
    assert state.gps["sync_time"] == sync_time


class _PreparedWorker:
    def __init__(self):
        self.discarded = []

    def discard_prepared(self, prepared):
        self.discarded.append(prepared)


class _Runtime:
    def __init__(self, worker):
        self.worker = worker

    def get_for_rig(self, rig_id):
        return self.worker if rig_id == 1 else None

    def active_camera_rig_ids(self):
        return (1,)


def test_ipc_discard_prepared_is_session_scoped_and_idempotent(tmp_path):
    worker = _PreparedWorker()
    server = CameraIpcServer(
        _Runtime(worker),
        endpoint_dir=tmp_path,
    )
    server.activate_session("session", [1])
    prepared = object()
    server._tokens["token"] = (
        "session",
        1,
        prepared,
        {},
    )

    request = {
        "operation": "discard_prepared",
        "session_id": "session",
        "params": {
            "rig_id": 1,
            "token_id": "token",
        },
    }

    first = server.handle_request(request)
    second = server.handle_request(request)

    assert first == {"rig_id": 1, "discarded": True}
    assert second == {"rig_id": 1, "discarded": False}
    assert worker.discarded == [prepared]


class _FailingTriggerIpc:
    def __init__(self):
        self.discards = []

    def list_active_camera_rigs(self):
        return {"rig_ids": [1]}

    def prepare_capture(self, rig_id, intent):
        return {
            "token_id": "prepared-token",
            "estimated_total_s": 1.0,
            "exposures_s": [0.001],
            "planned_count": 1,
            "plugin_name": "fake",
            "request_id": getattr(intent, "request_id", None),
        }

    def trigger_prepared(self, rig_id, token_id, deadline=None):
        raise RuntimeError("transport failed before trigger")

    def discard_prepared(self, rig_id, token_id):
        self.discards.append((rig_id, token_id))
        return {"rig_id": rig_id, "discarded": True}


def test_fanout_discards_failed_unconsumed_prepared_token():
    ipc = _FailingTriggerIpc()
    adapter = FanoutCameraAdapter(ipc, log_fn=lambda *_: None)
    intent = CaptureIntent(
        shutter_min="1/1000",
        shutter_max="1/1000",
        step_ev=1.0,
        speeds=None,
        phase="partial",
        target_time=datetime(2027, 8, 2, tzinfo=timezone.utc),
        deadline=None,
        overflow_policy=None,
        request_id="req",
    )

    try:
        prepared = adapter.prepare_capture(intent)
        with pytest.raises(RuntimeError):
            adapter.trigger_prepared(prepared)
        assert ipc.discards == [(1, "prepared-token")]
    finally:
        adapter.close()


class _Conn:
    def __init__(self):
        self.sent = []
        self.closed = False

    def send(self, payload):
        self.sent.append(payload)

    def close(self):
        self.closed = True


class _ImmortalProcess:
    def __init__(self):
        self.terminate_calls = 0
        self.kill_calls = 0
        self.join_calls = 0
        self.exitcode = None

    def is_alive(self):
        return True

    def terminate(self):
        self.terminate_calls += 1

    def kill(self):
        self.kill_calls += 1

    def join(self, timeout=None):
        self.join_calls += 1


def _device_supervisor():
    return SupervisedDeviceProcess(
        rig_id=1,
        device_kind="mount",
        process_spec={},
        process_target=lambda *a, **k: None,
        call_timeout_s=0.01,
        log_fn=lambda *_: None,
    )


def test_device_shutdown_retains_immortal_child_and_blocks_respawn():
    supervisor = _device_supervisor()
    process = _ImmortalProcess()
    supervisor._started = True
    supervisor._process = process
    supervisor._conn = _Conn()

    assert supervisor.shutdown(timeout=0) is False
    assert supervisor._process is process
    assert supervisor._started is False
    assert process.terminate_calls == 1
    assert process.kill_calls == 1

    with pytest.raises(WorkerUnavailableError):
        supervisor._ensure_process_locked()


def test_device_error_payload_marks_only_baseexception_fatal():
    ordinary = error_payload(RuntimeError("x"))
    fatal = error_payload(SystemExit("bye"))
    assert "fatal" not in ordinary
    # serve_worker adds the fatal marker around error_payload; preserve the
    # ordinary wire schema while fatal classification remains explicit there.
    assert fatal["class"] == "SystemExit"


def test_sony_phase_settings_fail_closed_on_set_error(monkeypatch):
    plugin = SonyPlugin(None, lambda *_: None)
    plugin._known_settings = {"iso": "100"}

    def fake_set_state(name, value):
        if name == "capturemode":
            return True, False, ""
        return False, False, "usb failed"

    monkeypatch.setattr(plugin, "_set_state", fake_set_state)

    with pytest.raises(RuntimeError, match="could not be applied"):
        plugin.set_exposure_settings(iso="200")


def test_sony_bracket_press_failure_is_explicit(monkeypatch):
    plugin = SonyPlugin(None, lambda *_: None)
    plugin._known_settings = {
        "capturemode": "Continuous Bracket 1 EV 3 Img.",
        "shutterspeed": "1/1000",
    }
    bracket = SimpleNamespace(
        mode_string="Continuous Bracket 1 EV 3 Img.",
        centre="1/1000",
        views=("1/2000", "1/1000", "1/500"),
        nimg=3,
    )
    monkeypatch.setattr(
        plugin,
        "_set",
        lambda name, value: (
            (False, False, "press failed")
            if name == "bulb" and value == 1
            else (True, False, "")
        ),
    )

    with pytest.raises(RuntimeError, match="bracket press failed"):
        plugin._fire_bracket(bracket)


def test_nikon_phase_settings_fail_closed(monkeypatch):
    plugin = NikonDSLRPlugin(None, lambda *_: None)
    monkeypatch.setattr(plugin, "_set", lambda *_a, **_k: False)

    with pytest.raises(RuntimeError, match="ISO"):
        plugin.set_exposure_settings(iso="200")


class _FakeProc:
    def __init__(self):
        self.stdout = []
        self.returncode = None
        self.terminated = False

    def poll(self):
        return 0 if self.terminated else None

    def terminate(self):
        self.terminated = True
        self.returncode = 0

    def kill(self):
        self.terminated = True
        self.returncode = -9

    def wait(self, timeout=None):
        self.terminated = True
        if self.returncode is None:
            self.returncode = 0
        return self.returncode


def test_trigger_supervisor_cleans_cancel_after_popen_before_publish(
    monkeypatch,
    tmp_path,
):
    service = TriggerService.__new__(TriggerService)
    service._lock = threading.RLock()
    service._procs = {i: None for i in range(1, 5)}
    service._starting_by_rig = {i: False for i in range(1, 5)}
    service._analysis_suppressed_by_rig = {i: False for i in range(1, 5)}
    service._manual_stop_requested_by_rig = {i: False for i in range(1, 5)}
    service._cancel_start_requested_by_rig = {i: False for i in range(1, 5)}
    service._supervisor_threads = {i: None for i in range(1, 5)}
    service._active_circumstances_paths = {1: tmp_path / "circ.json"}
    service._active_photo_paths = {1: tmp_path / "photo.json"}
    service._active_exposure_opt_paths = {1: tmp_path / "expo.json"}
    service.trigger_script = tmp_path / "eclipse_trigger.py"
    service.project_dir = tmp_path
    service.state = _State()
    service.log = lambda *a, **k: None
    service.emit = lambda *a, **k: None
    service._starting_by_rig[1] = True

    proc = _FakeProc()

    def fake_popen(*args, **kwargs):
        # Exact race: STOP/cancel is observed only after Popen returns but
        # before _run publishes the process into service._procs.
        service._cancel_start_requested_by_rig[1] = True
        return proc

    monkeypatch.setattr(
        "backend.trigger_service.subprocess.Popen",
        fake_popen,
    )

    service._run(
        simulate=True,
        speed=1.0,
        dry_run=False,
        ipc_session=None,
        rig_id=1,
    )

    assert service._starting_by_rig[1] is False
    assert service._procs[1] is None
    assert service._cancel_start_requested_by_rig[1] is False
    assert 1 not in service._active_photo_paths
    assert 1 not in service._active_exposure_opt_paths
    assert 1 not in service._active_circumstances_paths
