from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_scheduler_dispatch_and_budget_logs_include_command_index():
    source = (ROOT / "backend/execution_plan_runtime.py").read_text(encoding="utf-8")

    assert 'f"index={command[\'index\']} "' in source
    assert "budget_overrun_ms=" in source


def test_trigger_script_formats_analysis_only_outside_totality_override():
    source = (ROOT / "scripts/eclipse_trigger.py").read_text(encoding="utf-8")

    assert "TriggerRunAnalysis" in source
    assert "format_trigger_run_analysis" in source
    assert "analysis.mark_fatal(exc)" in source
    assert "if not _photo_override_event.is_set():" in source


def test_trigger_service_can_suppress_analysis_after_operator_preemption():
    source = (ROOT / "backend/trigger_service.py").read_text(encoding="utf-8")

    assert "_analysis_suppressed_by_rig" in source
    assert 'line.startswith("TRIGGER_RUN_ANALYSIS ")' in source
    assert "self._analysis_suppressed_by_rig[rig_id] = True" in source
