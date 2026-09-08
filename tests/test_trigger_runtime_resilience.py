from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from backend.execution_plan_runtime import ExecutionPlanRuntime
from backend.sequencer_compiler import (
    AuditedRigCapture,
    CaptureTarget,
    reduce_audited_capture_operations,
)
from plugins.camera import profile as profile_module
from backend.camera_ipc_server import CameraIpcServer, IpcError


class Clock:
    def __init__(self, start=None):
        self.current = start or datetime(2027, 8, 2, 10, 0, 0)

    def now(self):
        return self.current

    def remaining(self, target):
        return (target - self.current).total_seconds()

    def sleep(self, seconds):
        self.current += timedelta(seconds=seconds)


def _command(clock, second, action, params, index):
    return {
        "time": clock.now() + timedelta(seconds=second),
        "rig_id": 1,
        "action": action,
        "params": dict(params),
        "index": index,
    }


def test_prepare_reduces_past_sets_to_one_current_state_preflight():
    clock = Clock(datetime(2027, 8, 2, 10, 0, 5))
    calls = []

    class Camera:
        def preflight(self, rig_id, state):
            calls.append((rig_id, dict(state)))

    plan = {
        "initial_state_required": {"1": {"iso": "100"}},
        "_commands_runtime": [
            {
                "time": datetime(2027, 8, 2, 10, 0, 1),
                "rig_id": 1,
                "action": "SET",
                "params": {"parameter": "iso", "value": "200"},
                "index": 0,
            },
            {
                "time": datetime(2027, 8, 2, 10, 0, 2),
                "rig_id": 1,
                "action": "PHOTO",
                "params": {},
                "index": 1,
            },
            {
                "time": datetime(2027, 8, 2, 10, 0, 3),
                "rig_id": 1,
                "action": "SET",
                "params": {"parameter": "iso", "value": "400"},
                "index": 2,
            },
            {
                "time": datetime(2027, 8, 2, 10, 0, 10),
                "rig_id": 1,
                "action": "PHOTO",
                "params": {},
                "index": 3,
            },
        ],
    }

    runtime = ExecutionPlanRuntime(
        clock=clock,
        camera_client=Camera(),
        log_fn=lambda _line: None,
    )
    runtime.prepare_for_execution(plan)

    assert calls == [(1, {"iso": "400"})]


def test_past_commands_are_never_turned_into_pending_replay():
    clock = Clock(datetime(2027, 8, 2, 10, 0, 5))
    calls = []

    class Camera:
        def set_parameter(self, *args, **kwargs):
            calls.append(("SET", args, kwargs))

        def execute_photo(self, *args, **kwargs):
            calls.append(("PHOTO", args, kwargs))

    runtime = ExecutionPlanRuntime(
        clock=clock,
        camera_client=Camera(),
        log_fn=lambda _line: None,
    )
    runtime._run_rig(
        1,
        [
            {
                "time": datetime(2027, 8, 2, 10, 0, 1),
                "rig_id": 1,
                "action": "SET",
                "params": {
                    "parameter": "iso",
                    "value": "200",
                    "timing_contract_version": 2,
                    "duration_ms": 100,
                },
                "index": 0,
            },
            {
                "time": datetime(2027, 8, 2, 10, 0, 2),
                "rig_id": 1,
                "action": "PHOTO",
                "params": {
                    "timing_contract_version": 2,
                    "duration_ms": 100,
                },
                "index": 1,
            },
        ],
    )
    assert calls == []


def test_failed_set_is_checked_then_retried_asap_and_photo_continues():
    clock = Clock()
    calls = []

    class Camera:
        def __init__(self):
            self.first = True
            self.iso = "100"

        def set_parameter(self, rig_id, parameter, value, **kwargs):
            calls.append(("SET", parameter, value))
            if self.first:
                self.first = False
                raise RuntimeError("USB transient")
            self.iso = str(value)

        def get_parameter(self, rig_id, parameter):
            calls.append(("GET", parameter))
            return self.iso

        def execute_photo(self, rig_id, params, **kwargs):
            calls.append(("PHOTO",))

    params = {
        "timing_contract_version": 2,
        "duration_ms": 100,
    }
    runtime = ExecutionPlanRuntime(
        clock=clock,
        camera_client=Camera(),
        log_fn=lambda _line: None,
    )
    runtime._run_rig(
        1,
        [
            _command(
                clock, 1, "SET",
                {**params, "parameter": "iso", "value": "200"}, 0,
            ),
            _command(clock, 3, "PHOTO", params, 1),
        ],
    )

    assert calls.count(("SET", "iso", "200")) == 2
    assert ("GET", "iso") in calls
    assert ("PHOTO",) in calls


def test_ambiguous_failed_set_is_not_resent_when_get_proves_it_landed():
    clock = Clock()
    calls = []

    class Camera:
        def __init__(self):
            self.first = True
            self.iso = "100"

        def set_parameter(self, rig_id, parameter, value, **kwargs):
            calls.append(("SET", parameter, value))
            if self.first:
                self.first = False
                self.iso = str(value)  # body accepted it before USB reply failed
                raise RuntimeError("reply lost")
            self.iso = str(value)

        def get_parameter(self, rig_id, parameter):
            calls.append(("GET", parameter))
            return self.iso

        def execute_photo(self, rig_id, params, **kwargs):
            calls.append(("PHOTO",))

    params = {"timing_contract_version": 2, "duration_ms": 100}
    runtime = ExecutionPlanRuntime(
        clock=clock,
        camera_client=Camera(),
        log_fn=lambda _line: None,
    )
    runtime._run_rig(
        1,
        [
            _command(
                clock, 1, "SET",
                {**params, "parameter": "iso", "value": "200"}, 0,
            ),
            _command(clock, 3, "PHOTO", params, 1),
        ],
    )

    assert calls.count(("SET", "iso", "200")) == 1
    assert ("GET", "iso") in calls
    assert ("PHOTO",) in calls


def test_photo_usb_failure_never_stops_rig_and_is_never_replayed():
    clock = Clock()
    calls = []

    class Camera:
        def __init__(self):
            self.count = 0

        def execute_photo(self, rig_id, params, **kwargs):
            self.count += 1
            calls.append(params["id"])
            if self.count == 1:
                raise RuntimeError("battery removed")

    runtime = ExecutionPlanRuntime(
        clock=clock,
        camera_client=Camera(),
        log_fn=lambda _line: None,
    )
    params = {"timing_contract_version": 2, "duration_ms": 100}
    runtime._run_rig(
        1,
        [
            _command(clock, 1, "PHOTO", {**params, "id": 1}, 0),
            _command(clock, 3, "PHOTO", {**params, "id": 2}, 1),
        ],
    )

    assert calls == [1, 2]



def test_camera_ipc_allows_disjoint_rig_sessions_and_keeps_them_isolated(tmp_path):
    class Runtime:
        def __init__(self):
            self.workers = {1: object(), 2: object()}
        def active_camera_rig_ids(self):
            return (1, 2)
        def get_for_rig(self, rig_id):
            return self.workers.get(rig_id)

    server = CameraIpcServer(
        Runtime(), endpoint_dir=tmp_path / "ipc", parent_pid=4321,
        log_fn=lambda _line: None,
    )
    session1 = server.activate_session("rig-1-session", (1,))
    session2 = server.activate_session("rig-2-session", (2,))

    assert server.handle_request({
        "operation": "list_active_camera_rigs", "params": {},
        "session_id": session1,
    }) == {"rig_ids": [1]}
    assert server.handle_request({
        "operation": "list_active_camera_rigs", "params": {},
        "session_id": session2,
    }) == {"rig_ids": [2]}

    server.revoke_session(session1)
    assert server.handle_request({
        "operation": "list_active_camera_rigs", "params": {},
        "session_id": session2,
    }) == {"rig_ids": [2]}


def test_camera_ipc_rejects_overlapping_rig_leases(tmp_path):
    class Runtime:
        def active_camera_rig_ids(self):
            return (1, 2)
        def get_for_rig(self, _rig_id):
            return object()

    server = CameraIpcServer(
        Runtime(), endpoint_dir=tmp_path / "ipc", parent_pid=4321,
        log_fn=lambda _line: None,
    )
    server.activate_session("first", (1,))
    with pytest.raises(IpcError) as caught:
        server.activate_session("second", (1, 2))
    assert caught.value.code == "SESSION_ACTIVE"

def _profile(commands):
    return {
        "schema_version": 1,
        "config_type": "camera_profile",
        "backend": "profile-test_camera",
        "manufacturer": "Sony",
        "model": "Sony Alpha-A6600 (PC Control)",
        "strategy": "sequential",
        "commands": {
            **commands,
            "capture_target": {
                "path": "/capturetarget", "value": "card+sdram",
                "get": True, "set": True,
            },
            "raw": {
                "path": "/raw", "value": "RAW",
                "get": True, "set": True,
            },
            "iso": {
                "path": "/iso", "value": "100",
                "values": {"100": "100", "200": "200"},
                "get": True, "set": True,
            },
            "shutter": {
                "path": "/shutter", "value": "1/500",
                "values": {"1/500": "1/500", "1/250": "1/250"},
                "get": True, "set": True,
            },
            "trigger_single": {"method": "trigger_capture"},
        },
        "warnings": [],
        "brackets": {},
    }


def test_get_only_manual_mode_mismatch_has_actionable_a6600_message(monkeypatch):
    profile = _profile({
        "manual_mode": {
            "path": "/manual", "value": "M",
            "get": True, "set": False,
        },
    })
    plugin = profile_module.ProfilePlugin(object(), profile=profile)

    nodes = {
        "/manual": SimpleNamespace(
            get_value=lambda: "A", get_readonly=lambda: 1
        ),
        "/capturetarget": SimpleNamespace(
            get_value=lambda: "card+sdram", get_readonly=lambda: 0
        ),
        "/raw": SimpleNamespace(
            get_value=lambda: "RAW", get_readonly=lambda: 0
        ),
        "/iso": SimpleNamespace(
            get_value=lambda: "100", get_readonly=lambda: 0
        ),
        "/shutter": SimpleNamespace(
            get_value=lambda: "1/500", get_readonly=lambda: 0
        ),
    }
    monkeypatch.setattr(
        profile_module,
        "widget",
        lambda _camera, path: (None, nodes[path]),
    )

    with pytest.raises(profile_module.CameraPreflightError) as error:
        plugin.preflight({"iso": "100"})

    assert "Sony A6600" in str(error.value)
    assert "manual mode (M)" in str(error.value)


def test_preflight_get_first_does_not_resend_equal_iso(monkeypatch):
    profile = _profile({
        "manual_mode": {
            "path": "/manual", "value": "M",
            "get": True, "set": False,
        },
    })
    plugin = profile_module.ProfilePlugin(object(), profile=profile)

    values = {
        "/manual": "M",
        "/capturetarget": "card+sdram",
        "/raw": "RAW",
        "/iso": "100",
        "/shutter": "1/500",
    }
    nodes = {
        path: SimpleNamespace(
            get_value=(lambda p=path: values[p]),
            get_readonly=(lambda p=path: 1 if p == "/manual" else 0),
        )
        for path in values
    }
    writes = []
    monkeypatch.setattr(
        profile_module,
        "widget",
        lambda _camera, path: (None, nodes[path]),
    )
    monkeypatch.setattr(
        profile_module,
        "write_checked",
        lambda _camera, path, value: writes.append((path, value)),
    )

    plugin.preflight({"iso": "100"})
    assert writes == []


def test_profile_backend_state_reduction_removes_unchanged_iso():
    target = CaptureTarget(
        target_time=datetime(2027, 8, 2, 10, 0, 10),
        phase="partial",
        phase_window="phase_1a",
        sequence_index=0,
        deadline=None,
    )
    capture = AuditedRigCapture(
        rig_id=1,
        backend="profile-test_camera",
        target=target,
        aperture=None,
        exposure_plan=tuple(),
        prepared_mode="profile",
        estimated_total_s=1.0,
        planned_count=1,
        operations=(
            {"action": "set", "parameter": "iso", "value": "100"},
            {"action": "set", "parameter": "shutterspeed", "value": "1/250"},
            {"action": "trigger_capture", "shutter": "1/250"},
        ),
    )

    reduced, state = reduce_audited_capture_operations(
        capture,
        {"iso": "100", "shutterspeed": "1/500"},
    )

    assert not any(
        op.get("action") == "set" and op.get("parameter") == "iso"
        for op in reduced.operations
    )
    assert any(
        op.get("action") == "set" and op.get("parameter") == "shutterspeed"
        for op in reduced.operations
    )
    assert state["iso"] == "100"
    assert state["shutterspeed"] == "1/250"
