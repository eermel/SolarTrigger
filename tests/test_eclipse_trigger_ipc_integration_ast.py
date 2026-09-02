"""AST guardrails for scheduler boundaries and strict camera IPC selection."""

import ast
from pathlib import Path


TRIGGER_PATH = Path(__file__).resolve().parents[1] / "scripts" / "eclipse_trigger.py"
TREE = ast.parse(TRIGGER_PATH.read_text(encoding="utf-8"))


def _function(name):
    return next(
        node
        for node in TREE.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )


def _calls(node):
    if isinstance(node, list):
        return [call for item in node for call in _calls(item)]
    return [item for item in ast.walk(node) if isinstance(item, ast.Call)]


def _call_name(call):
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def _named_calls(node, *names):
    wanted = set(names)
    return [call for call in _calls(node) if _call_name(call) in wanted]


def _service_method_calls(node, service="camera_service"):
    return [
        call
        for call in _calls(node)
        if isinstance(call.func, ast.Attribute)
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id == service
    ]


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
    return selection, selection.orelse[0]


def test_main_dispatches_all_six_grids_and_continuous_totality():
    main = _function("main")
    grid_calls = _named_calls(main, "_run_absolute_grid")

    assert len(grid_calls) == 6
    assert {
        call.args[1].value
        for call in grid_calls
        if len(call.args) > 1 and isinstance(call.args[1], ast.Constant)
    } == {"partial", "phase1a", "phase1b", "phase2", "phase3a", "phase3b"}

    continuous_calls = _named_calls(main, "_run_continuous_totality")
    assert len(continuous_calls) == 1
    assert all(
        call.args
        and isinstance(call.args[0], ast.Name)
        and call.args[0].id == "camera_service"
        for call in grid_calls + continuous_calls
    )


def test_absolute_grid_applies_then_prepares_or_delegates_then_triggers():
    function = _function("_run_absolute_grid")
    apply_call, = _named_calls(function, "apply_phase_settings")
    preparation_calls = _named_calls(
        function, "prepare_capture", "_prepare_totality_sub_bracket"
    )
    trigger_call, = _named_calls(function, "trigger_prepared")

    assert {_call_name(call) for call in preparation_calls} == {
        "prepare_capture",
        "_prepare_totality_sub_bracket",
    }
    assert apply_call.lineno < min(call.lineno for call in preparation_calls)
    assert max(call.lineno for call in preparation_calls) < trigger_call.lineno


def test_continuous_totality_applies_then_delegates_prepare_then_triggers():
    function = _function("_run_continuous_totality")
    apply_call, = _named_calls(function, "apply_phase_settings")
    delegated_call, = _named_calls(function, "_prepare_totality_sub_bracket")
    trigger_call, = _named_calls(function, "trigger_prepared")

    assert not _named_calls(function, "prepare_capture")
    assert apply_call.lineno < delegated_call.lineno < trigger_call.lineno


def test_totality_sub_bracket_prepares_full_multi_and_single_branches():
    function = _function("_prepare_totality_sub_bracket")
    full_branch = next(node for node in function.body if isinstance(node, ast.If))
    multi_branch = next(
        node
        for node in function.body
        if isinstance(node, ast.For)
        and isinstance(node.target, ast.Name)
        and node.target.id == "candidate_size"
    )
    single_branch = next(
        node
        for node in function.body
        if isinstance(node, ast.For)
        and isinstance(node.target, ast.Name)
        and node.target.id == "speed"
    )

    for branch in (full_branch, multi_branch, single_branch):
        prepare_calls = _named_calls(branch, "prepare_capture")
        assert len(prepare_calls) == 1
        assert prepare_calls[0] in _service_method_calls(branch)


def test_capture_speed_list_only_calls_shoot_speed_list_on_service():
    service_calls = _service_method_calls(_function("capture_speed_list"))

    assert len(service_calls) == 1
    assert _call_name(service_calls[0]) == "shoot_speed_list"


def test_hardware_access_is_reachable_only_from_legacy_direct_branch():
    selection, ipc_branch = _camera_selection()
    direct_hardware = {
        "CameraService",
        "unmount_camera",
        "connect",
        "init_settings",
        "get_battery_level",
    }

    assert direct_hardware.isdisjoint(
        {_call_name(call) for call in _calls(selection.body)}
    )
    assert direct_hardware.isdisjoint(
        {_call_name(call) for call in _calls(ipc_branch.body)}
    )
    main = _function("main")
    legacy_calls = _calls(ipc_branch.orelse)
    hardware_calls = [
        call for call in _calls(main) if _call_name(call) in direct_hardware
    ]
    assert {_call_name(call) for call in hardware_calls} == direct_hardware
    assert all(call in legacy_calls for call in hardware_calls)

    cleanup = next(
        node
        for node in ast.walk(main)
        if isinstance(node, ast.If)
        and ast.unparse(node.test) == "ipc_adapter is not None"
    )
    assert ast.unparse(cleanup.body[0].value.func) == "ipc_adapter.close"
    legacy_close = cleanup.orelse[0]
    assert isinstance(legacy_close, ast.If)
    assert ast.unparse(legacy_close.test) == "camera_service is not None"
    assert ast.unparse(legacy_close.body[0].value.func) == "camera_service.close"
    camera_close_calls = [
        call
        for call in _calls(main)
        if isinstance(call.func, ast.Attribute)
        and ast.unparse(call.func) == "camera_service.close"
    ]
    assert camera_close_calls == [legacy_close.body[0].value]


def test_execution_plan_v2_uses_direct_camera_ipc_without_fanout():
    function = _function("_run_execution_plan_v2")
    calls = _calls(function)

    camera_ipc_calls = [
        call for call in calls
        if _call_name(call) == "CameraIpcClient"
    ]
    fanout_calls = [
        call for call in calls
        if _call_name(call) == "FanoutCameraAdapter"
    ]

    assert len(camera_ipc_calls) == 1
    assert fanout_calls == []


def test_execution_plan_v2_simulation_branch_has_no_camera_ipc():
    function = _function("_run_execution_plan_v2")

    simulation_branch = next(
        node
        for node in function.body
        if isinstance(node, ast.If)
        and ast.unparse(node.test) == "_sim_mode"
    )

    simulation_calls = {
        _call_name(call)
        for call in _calls(simulation_branch.body)
    }

    assert "_SimulationExecutionCamera" in simulation_calls
    assert "CameraIpcClient" not in simulation_calls
    assert "FanoutCameraAdapter" not in simulation_calls
    assert "CameraService" not in simulation_calls


def test_execution_plan_v2_real_branch_uses_camera_ipc_directly():
    function = _function("_run_execution_plan_v2")

    simulation_branch = next(
        node
        for node in function.body
        if isinstance(node, ast.If)
        and ast.unparse(node.test) == "_sim_mode"
    )

    real_calls = {
        _call_name(call)
        for call in _calls(simulation_branch.orelse)
    }

    assert "CameraIpcClient" in real_calls
    assert "FanoutCameraAdapter" not in real_calls
    assert "CameraService" not in real_calls


def test_main_execution_plan_branch_returns_before_legacy_engine():
    main = _function("main")

    execution_branch = next(
        node
        for node in ast.walk(main)
        if isinstance(node, ast.If)
        and ast.unparse(node.test) == "args.execution_plan"
    )

    assert len(execution_branch.body) == 2
    assert isinstance(execution_branch.body[0], ast.Expr)
    assert _call_name(execution_branch.body[0].value) == "_run_execution_plan_v2"
    assert isinstance(execution_branch.body[1], ast.Return)


def test_execution_plan_failure_exits_process_nonzero():
    main = _function("main")

    try_node = next(
        node
        for node in main.body
        if isinstance(node, ast.Try)
    )

    handler = next(
        handler
        for handler in try_node.handlers
        if handler.type is not None
        and ast.unparse(handler.type) == "Exception"
    )

    plan_failure_branch = next(
        node
        for node in handler.body
        if isinstance(node, ast.If)
        and ast.unparse(node.test) == "args.execution_plan"
    )

    exit_raise = next(
        node
        for node in plan_failure_branch.body
        if isinstance(node, ast.Raise)
    )

    assert isinstance(exit_raise.exc, ast.Call)
    assert _call_name(exit_raise.exc) == "SystemExit"
    assert len(exit_raise.exc.args) == 1
    assert isinstance(exit_raise.exc.args[0], ast.Constant)
    assert exit_raise.exc.args[0].value == 1
