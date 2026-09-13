from datetime import datetime, timezone

import pytest

from scripts.eclipse_trigger import _build_totality_only_schedule


def test_totality_only_schedule_uses_naive_utc():
    now = datetime(2026, 9, 14, 0, 0, 0)

    schedule = _build_totality_only_schedule(now)
    window = schedule.windows[0]

    assert schedule.tstart == now
    assert schedule.tstart.tzinfo is None
    assert schedule.tend == datetime.max
    assert schedule.tend.tzinfo is None
    assert schedule.tmax == now

    assert window.name == "totality_override"
    assert window.photo_phase == "totality"
    assert window.start == now
    assert window.end == datetime.max

    current = datetime(2026, 9, 14, 0, 0, 1)
    assert schedule.phase_at(current) is window


def test_totality_only_schedule_rejects_aware_datetime():
    aware = datetime(2026, 9, 14, tzinfo=timezone.utc)

    with pytest.raises(
        ValueError,
        match="totality-only schedule requires naive UTC datetime",
    ):
        _build_totality_only_schedule(aware)
