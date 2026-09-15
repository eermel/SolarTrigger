from datetime import datetime, timedelta

import pytest

from backend.phase_trigger import PhaseRuntime, PhaseSchedule, PhaseWindow


class _OverrideNow(RuntimeError):
    pass


def _schedule():
    start = datetime(2026, 8, 2, 10, 0, 0)
    window = PhaseWindow(
        name="partial_before",
        photo_phase="partial",
        start=start,
        end=start + timedelta(minutes=10),
        interval_s=60.0,
    )
    return PhaseSchedule(
        tstart=window.start,
        tend=window.end,
        tmax=window.start + timedelta(minutes=5),
        windows=(window,),
    ), window


def test_override_arriving_during_reconcile_blocks_new_normal_capture():
    schedule, window = _schedule()
    current = [window.start]
    override_requested = [False]
    captures = []

    def now():
        return current[0]

    def wait_until(target):
        current[0] = target

    def reconcile(_window):
        override_requested[0] = True

    def override(_now):
        if override_requested[0]:
            raise _OverrideNow()
        return None

    runtime = PhaseRuntime(
        schedule,
        now=now,
        wait_until=wait_until,
        enter_phase=lambda _window: None,
        reconcile_phase=reconcile,
        capture=lambda _window, started: captures.append(started),
        log_error=lambda _message: None,
        override_phase=override,
    )

    with pytest.raises(_OverrideNow):
        runtime.run()

    assert captures == []


def test_capture_still_runs_when_no_override_arrives():
    schedule, window = _schedule()
    current = [window.start]
    captures = []

    def now():
        return current[0]

    def wait_until(target):
        current[0] = target

    def capture(_window, started):
        captures.append(started)
        return False

    runtime = PhaseRuntime(
        schedule,
        now=now,
        wait_until=wait_until,
        enter_phase=lambda _window: None,
        reconcile_phase=lambda _window: None,
        capture=capture,
        log_error=lambda _message: None,
        override_phase=lambda _now: None,
    )
    runtime.run()

    assert captures == [window.start]
