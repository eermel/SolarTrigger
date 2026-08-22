"""Structural guardrails for continuous Phase 2 totality scheduling."""

import ast
from pathlib import Path


TRIGGER_PATH = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "eclipse_trigger.py"
)
SOURCE = TRIGGER_PATH.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)


def _function_source(name):
    node = next(
        node
        for node in TREE.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == name
    )
    return ast.get_source_segment(SOURCE, node)


def test_continuous_totality_keeps_modern_prepared_architecture():
    continuous = _function_source("_run_continuous_totality")
    prepare = _function_source("_prepare_totality_sub_bracket")

    # Continuous Phase 2 must delegate C3 adaptation to FEAT-011.
    assert "_prepare_totality_sub_bracket(" in continuous

    # Capture must use the modern prepared contract.
    assert "camera_service.trigger_prepared(" in continuous
    assert "camera_service.prepare_capture(" in prepare

    # Legacy adapters must never return to continuous Phase 2.
    assert "capture_speed_list(" not in continuous
    assert "shoot_speed_list(" not in continuous
    assert "_PreparedTotalityTrigger" not in SOURCE

    # A PreparedCapture must not be treated as the old speeds dictionary.
    assert 'prepared["speeds"]' not in SOURCE
    assert "prepared['speeds']" not in SOURCE


def test_phase2_dispatch_has_negative_zero_positive_semantics():
    main_source = _function_source("main")

    assert "if interval_totality < 0:" in main_source
    assert "elif interval_totality == 0:" in main_source
    assert "_run_continuous_totality(" in main_source
    assert "_run_absolute_grid(" in main_source


def test_existing_c3_adaptation_remains_authoritative():
    prepare = _function_source("_prepare_totality_sub_bracket")

    assert "c3_adaptation=full" in prepare
    assert "c3_adaptation=reduced" in prepare
    assert "c3_adaptation=refused" in prepare
    assert "_c3_trigger_deadline(" in prepare
