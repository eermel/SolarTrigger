import threading

from backend.trigger_service import (
    TOTALITY_CHILD_RECOVERY_MAX_ATTEMPTS,
    TriggerService,
)


def _service():
    service = TriggerService.__new__(TriggerService)
    service._lock = threading.RLock()
    return service


def test_totality_recovery_ignores_unsafe_stage_and_heartbeat_timeout():
    service = _service()

    plan = service._child_recovery_plan(
        rig_id=1,
        totality_only=False,
        recovery_attempt=0,
        last_stage="capture.begin",
        heartbeat_timed_out=True,
        recovery_window_open=True,
        totality_window_open=True,
    )

    assert plan == "totality"


def test_totality_recovery_is_bounded():
    service = _service()

    plan = service._child_recovery_plan(
        rig_id=1,
        totality_only=True,
        recovery_attempt=TOTALITY_CHILD_RECOVERY_MAX_ATTEMPTS,
        last_stage="capture.begin",
        heartbeat_timed_out=True,
        recovery_window_open=True,
        totality_window_open=True,
    )

    assert plan is None


def test_non_totality_recovery_keeps_existing_safe_boundary_policy():
    service = _service()

    assert service._child_recovery_plan(
        rig_id=1,
        totality_only=False,
        recovery_attempt=0,
        last_stage="capture.end",
        heartbeat_timed_out=False,
        recovery_window_open=True,
        totality_window_open=False,
    ) == "same"

    assert service._child_recovery_plan(
        rig_id=1,
        totality_only=False,
        recovery_attempt=0,
        last_stage="capture.begin",
        heartbeat_timed_out=False,
        recovery_window_open=True,
        totality_window_open=False,
    ) is None
