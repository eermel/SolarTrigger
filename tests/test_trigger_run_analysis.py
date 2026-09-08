from datetime import datetime

from backend.trigger_run_analysis import (
    TriggerRunAnalysis,
    format_trigger_run_analysis,
)


def _plan():
    return {
        "_commands_runtime": [
            {
                "time": datetime(2027, 8, 2, 10, 0, 1),
                "rig_id": 1,
                "action": "SET",
                "params": {"parameter": "iso", "value": "100"},
                "index": 0,
            },
            {
                "time": datetime(2027, 8, 2, 10, 0, 2),
                "rig_id": 1,
                "action": "PHOTO",
                "params": {"shutter": "1/500"},
                "index": 1,
            },
        ]
    }


def test_natural_complete_run_is_pass():
    analysis = TriggerRunAnalysis(_plan(), plan_name="rig1.plan")
    analysis.observe(
        "EXECUTION_PLAN rig=1 action=SET index=0 "
        "scheduled=2027-08-02T10:00:01Z "
        "dispatch=2027-08-02T10:00:01Z lateness_ms=+1.000"
    )
    analysis.observe(
        "EXECUTION_PLAN rig=1 action=PHOTO index=1 "
        "scheduled=2027-08-02T10:00:02Z "
        "dispatch=2027-08-02T10:00:02Z lateness_ms=+3.000"
    )

    report = analysis.finalize()
    rig = report["rigs"][0]

    assert report["verdict"] == "PASS"
    assert rig["verdict"] == "PASS"
    assert rig["completed"] == 2
    assert rig["coverage_pct"] == 100.0
    assert rig["timing"]["mean_ms"] == 2.0
    assert rig["timing"]["median_ms"] == 2.0
    assert rig["timing"]["p95_ms"] == 3.0
    assert rig["timing"]["max_ms"] == 3.0
    assert rig["timing"]["worst_index"] == 1


def test_recovered_set_and_budget_overrun_are_warning():
    analysis = TriggerRunAnalysis(_plan())
    analysis.observe(
        "EXECUTION_PLAN rig=1 action=SET index=0 "
        "scheduled=x dispatch=x lateness_ms=+2.000"
    )
    analysis.observe(
        "WARNING execution_plan rig=1 action=SET index=0 "
        "parameter=iso code=USB retry_asap=1"
    )
    analysis.observe(
        "EXECUTION_PLAN rig=1 pending_set_recovered "
        "parameter=iso index=0"
    )
    analysis.observe(
        "WARNING execution_plan rig=1 index=0 "
        "budget_overrun_ms=120.0; elapsed commands will be skipped"
    )
    analysis.observe(
        "WARNING execution_plan rig=1 skip_past index=1 time=x"
    )

    report = analysis.finalize()
    rig = report["rigs"][0]

    assert report["verdict"] == "WARNING"
    assert rig["recovered"] == 1
    assert rig["budget_overruns"] == 1
    assert rig["skipped"] == 1


def test_lost_photo_is_fail():
    analysis = TriggerRunAnalysis(_plan())
    analysis.observe(
        "EXECUTION_PLAN rig=1 action=PHOTO index=1 "
        "scheduled=x dispatch=x lateness_ms=+5.000"
    )
    analysis.observe(
        "WARNING execution_plan rig=1 action=PHOTO index=1 "
        "code=USB photo_lost=1; continuing absolute timeline"
    )

    report = analysis.finalize()

    assert report["verdict"] == "FAIL"
    assert report["rigs"][0]["photo_lost"] == 1


def test_fatal_error_forces_fail():
    analysis = TriggerRunAnalysis(_plan())
    analysis.mark_fatal("scheduler exploded")

    report = analysis.finalize()
    lines = format_trigger_run_analysis(report)

    assert report["verdict"] == "FAIL"
    assert any("FATAL error=scheduler exploded" in line for line in lines)
    assert all(line.startswith("TRIGGER_RUN_ANALYSIS ") for line in lines)
