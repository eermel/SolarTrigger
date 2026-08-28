"""Guardrails for strict camera IPC integration in the eclipse scheduler."""

import ast
from datetime import datetime, timedelta
from pathlib import Path
import sys
import types

import pytest


TRIGGER_PATH = Path(__file__).resolve().parents[1] / "scripts" / "eclipse_trigger.py"
SOURCE = TRIGGER_PATH.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)


def _function(name):
    return next(
        node
        for node in TREE.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )


def _call_names(node):
    if isinstance(node, list):
        return [name for item in node for name in _call_names(item)]
    names = []
    for call in (item for item in ast.walk(node) if isinstance(item, ast.Call)):
        if isinstance(call.func, ast.Name):
            names.append(call.func.id)
        elif isinstance(call.func, ast.Attribute):
            names.append(call.func.attr)
    return names


def _camera_selection():
    main = _function("main")
    selection = next(
        node
        for node in ast.walk(main)
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Name)
        and node.test.id == "_sim_mode"
        and node.orelse
        and isinstance(node.orelse[0], ast.If)
        and isinstance(node.orelse[0].test, ast.Name)
        and node.orelse[0].test.id == "ipc_socket"
    )
    ipc_branch = selection.orelse[0]
    assert isinstance(ipc_branch, ast.If)
    assert isinstance(ipc_branch.test, ast.Name)
    assert ipc_branch.test.id == "ipc_socket"
    return selection, ipc_branch


def test_startup_selects_simulation_ipc_and_legacy_camera_paths():
    selection, ipc_branch = _camera_selection()

    assert "_SimulationCameraService" in _call_names(selection.body)

    ipc_calls = _call_names(ipc_branch)
    for required in (
        "CameraIpcClient",
        "ping",
        "list_active_camera_rigs",
        "FanoutCameraAdapter",
        "initialize",
    ):
        assert required in ipc_calls

    legacy_calls = _call_names(ipc_branch.orelse)
    for required in (
        "unmount_camera",
        "CameraService",
        "connect",
        "init_settings",
        "get_battery_level",
    ):
        assert required in legacy_calls


def test_direct_camera_access_is_confined_to_legacy_branch():
    selection, ipc_branch = _camera_selection()
    direct_calls = {
        "unmount_camera",
        "CameraService",
        "connect",
        "init_settings",
        "get_battery_level",
    }

    assert direct_calls.isdisjoint(_call_names(selection.body))
    assert direct_calls.isdisjoint(_call_names(ipc_branch.body))
    assert direct_calls <= set(_call_names(ipc_branch.orelse))

    main = _function("main")
    finally_body = next(
        node.finalbody
        for node in ast.walk(main)
        if isinstance(node, ast.Try) and node.finalbody
    )
    close_if = next(
        node for node in finally_body if isinstance(node, ast.If)
    )
    assert ast.unparse(close_if.test) == "ipc_adapter is not None"
    assert ast.unparse(close_if.body[0].value.func.value) == "ipc_adapter"
    assert ast.unparse(close_if.orelse[0].test) == "camera_service is not None"
    assert ast.unparse(close_if.orelse[0].body[0].value.func.value) == "camera_service"


@pytest.mark.parametrize(
    "helper",
    [
        "_set_phase_exposure",
        "_prepare_totality_sub_bracket",
        "_run_continuous_totality",
        "_run_absolute_grid",
        "capture_speed_list",
    ],
)
def test_scheduler_helpers_only_call_camera_methods_on_supplied_service(helper):
    node = _function(helper)
    camera_calls = [
        call
        for call in ast.walk(node)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and call.func.attr
        in {
            "apply_phase_settings",
            "prepare_capture",
            "trigger_prepared",
            "shoot_speed_list",
        }
    ]
    assert camera_calls
    assert all(
        isinstance(call.func.value, ast.Name)
        and call.func.value.id == "camera_service"
        for call in camera_calls
    )


def test_all_main_scheduler_entries_receive_selected_camera_object():
    main = _function("main")
    entries = [
        call
        for call in ast.walk(main)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
        and call.func.id in {"_run_absolute_grid", "_run_continuous_totality"}
    ]
    assert entries
    assert all(
        call.args
        and isinstance(call.args[0], ast.Name)
        and call.args[0].id == "camera_service"
        for call in entries
    )


if "gphoto2" not in sys.modules:
    fake_gphoto2 = types.ModuleType("gphoto2")
    fake_gphoto2.GPhoto2Error = RuntimeError
    fake_gphoto2.GP_LOG_ERROR = 0
    fake_gphoto2.GP_LOG_VERBOSE = 1
    fake_gphoto2.GP_LOG_DEBUG = 2
    fake_gphoto2.GP_LOG_DATA = 3
    fake_gphoto2.use_python_logging = lambda mapping=None: None
    fake_gphoto2.check_result = lambda *args, **kwargs: None
    sys.modules["gphoto2"] = fake_gphoto2

saved_argv = sys.argv
sys.argv = [sys.argv[0]]
from plugins.camera.base import CaptureResult
from scripts import eclipse_trigger as trigger
sys.argv = saved_argv


class InstrumentedAdapter:
    def __init__(self, clock):
        self.clock = clock
        self.events = []

    def apply_phase_settings(self, aperture=None, iso=None):
        self.events.append(("apply", aperture, iso))

    def prepare_capture(self, intent):
        self.events.append(("prepare", intent.target_time, intent.deadline))
        return types.SimpleNamespace(
            token=intent,
            estimated_total_s=None,
            exposures_s=None,
        )

    def trigger_prepared(self, prepared, deadline=None):
        self.events.append(("trigger", self.clock["now"], deadline))
        return CaptureResult(frames=1, planned=1, detail="instrumented adapter")


@pytest.fixture
def instrumented_scheduler(monkeypatch):
    start = datetime(2026, 8, 12, 20, 0, 0)
    clock = {"now": start}
    monkeypatch.setattr(trigger, "now", lambda: clock["now"])
    monkeypatch.setattr(trigger, "_log", lambda message: None)
    monkeypatch.setattr(trigger, "_watchdog_write", lambda *args: None)

    def wait_until(service, target, deadline=None):
        assert isinstance(service, InstrumentedAdapter)
        clock["now"] = target

    monkeypatch.setattr(trigger, "_usb_wait_or_hold", wait_until)
    return start, clock, InstrumentedAdapter(clock)


@pytest.mark.parametrize(
    "phase",
    ["partial", "phase1a", "phase1b", "phase2", "phase3a", "phase3b"],
)
def test_phase_grid_keeps_apply_prepare_trigger_order_and_deadline(
    instrumented_scheduler, phase
):
    start, _, adapter = instrumented_scheduler
    target = start + timedelta(seconds=1)
    phase_end = start + timedelta(seconds=2)

    trigger._run_absolute_grid(
        adapter,
        phase,
        ["1/1000"],
        target,
        phase_end,
        10,
        aperture="f/8",
        iso="100",
        deadline=phase_end,
    )

    assert [event[0] for event in adapter.events] == ["apply", "prepare", "trigger"]
    assert adapter.events[1][1:] == (target, phase_end)
    assert adapter.events[2][1:] == (target, phase_end)


def test_continuous_totality_keeps_apply_prepare_trigger_order_and_c3_deadline(
    instrumented_scheduler,
):
    start, clock, adapter = instrumented_scheduler
    c3 = start + timedelta(seconds=1)

    original_trigger = adapter.trigger_prepared

    def trigger_once(prepared, deadline=None):
        result = original_trigger(prepared, deadline)
        clock["now"] = c3
        return result

    adapter.trigger_prepared = trigger_once

    trigger._run_continuous_totality(
        adapter,
        ["1/1000"],
        start,
        c3,
        aperture="f/8",
        iso="100",
    )

    assert [event[0] for event in adapter.events] == ["apply", "prepare", "trigger"]
    assert adapter.events[1][1:] == (start, c3)
    assert adapter.events[2][1:] == (start, c3)
