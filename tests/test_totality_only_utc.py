from datetime import datetime, timezone

import scripts.eclipse_trigger as trigger


def test_totality_only_no_longer_builds_a_datetime_schedule():
    """Emergency totality must not depend on a synthetic UTC schedule."""
    assert not hasattr(trigger, "_build_totality_only_schedule")


def test_emergency_target_is_only_a_protocol_sentinel():
    """CaptureIntent still needs a datetime, but it must not be wall-clock based."""
    assert trigger.EMERGENCY_TARGET_SENTINEL_UTC == datetime(
        1970, 1, 1, tzinfo=timezone.utc
    )


def test_emergency_preemption_has_dedicated_exception():
    """SIGUSR1 can escape the normal phase runtime into emergency mode."""
    assert issubclass(trigger.EmergencyTotalityRequested, RuntimeError)
