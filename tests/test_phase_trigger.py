from datetime import datetime, timedelta

import pytest

from backend.phase_trigger import (
    PHASE_DIAMOND_C2,
    PHASE_DIAMOND_C3,
    PHASE_PARTIAL_AFTER,
    PHASE_PARTIAL_BEFORE,
    PHASE_TOTALITY,
    PhaseRuntime,
    aligned_partial_slot,
    build_phase_schedule,
)


BASE = datetime(2027, 8, 2, 10, 0, 0)


def _timeline():
    return {
        "C1": BASE,
        "C2": BASE + timedelta(hours=1),
        "TMAX": BASE + timedelta(hours=1, minutes=1),
        "C3": BASE + timedelta(hours=1, minutes=2),
        "C4": BASE + timedelta(hours=2),
    }


def _photo():
    return {
        "sequence_margin_min": 10,
        "phases": {
            "partial": {"interval_s": 60},
            "diamond_ring": {
                "interval_s": 1,
                "duration_s": 30,
                "totality_overlap_s": 5,
            },
            "totality": {"interval_s": 0},
        },
    }


def test_builds_five_symmetric_half_open_windows():
    schedule = build_phase_schedule(_timeline(), _photo())

    assert schedule.tmax == BASE + timedelta(hours=1, minutes=1)
    assert schedule.tstart == BASE - timedelta(minutes=10)
    assert schedule.tend == BASE + timedelta(hours=2, minutes=10)
    assert [window.name for window in schedule.windows] == [
        PHASE_PARTIAL_BEFORE,
        PHASE_DIAMOND_C2,
        PHASE_TOTALITY,
        PHASE_DIAMOND_C3,
        PHASE_PARTIAL_AFTER,
    ]
    assert schedule.windows[0].end == _timeline()["C2"] - timedelta(seconds=30)
    assert schedule.windows[1].end == _timeline()["C2"] + timedelta(seconds=5)
    assert schedule.windows[2].end == _timeline()["C3"] - timedelta(seconds=5)
    assert schedule.windows[3].end == _timeline()["C3"] + timedelta(seconds=30)


def test_new_phase_wins_at_every_exact_boundary():
    schedule = build_phase_schedule(_timeline(), _photo())

    for previous, following in zip(schedule.windows, schedule.windows[1:]):
        assert schedule.phase_at(previous.end) == following
    assert schedule.phase_at(schedule.tend) is None


def test_tmax_is_computed_from_c2_and_c3_not_read_from_file():
    timeline = _timeline()
    timeline["TMAX"] = BASE + timedelta(days=10)

    schedule = build_phase_schedule(timeline, _photo())

    assert schedule.tmax == timeline["C2"] + (timeline["C3"] - timeline["C2"]) / 2


def test_partial_first_slot_is_aligned_to_tmax():
    tmax = BASE + timedelta(minutes=10)

    assert aligned_partial_slot(tmax, 180, BASE) == BASE + timedelta(minutes=1)
    assert aligned_partial_slot(tmax, 180, BASE + timedelta(minutes=1)) == BASE + timedelta(minutes=1)
    assert aligned_partial_slot(tmax, 180, BASE + timedelta(minutes=1, seconds=1)) == BASE + timedelta(minutes=4)


def test_rejects_overlap_below_five_seconds():
    photo = _photo()
    photo["phases"]["diamond_ring"]["totality_overlap_s"] = 4.9

    with pytest.raises(ValueError, match="must be >= 5"):
        build_phase_schedule(_timeline(), photo)


class _Clock:
    def __init__(self, current):
        self.current = current

    def now(self):
        return self.current

    def wait_until(self, target):
        self.current = max(self.current, target)


def test_only_pre_c2_first_photo_is_aligned_to_tmax():
    schedule = build_phase_schedule(_timeline(), _photo())
    partial_after = schedule.windows[4]
    clock = _Clock(partial_after.start)
    captures = []

    def capture(_window, started):
        captures.append(started)
        clock.current += timedelta(seconds=2)

    PhaseRuntime(
        schedule,
        now=clock.now,
        wait_until=clock.wait_until,
        enter_phase=lambda _window: None,
        reconcile_phase=lambda _window: None,
        capture=capture,
        log_error=lambda _message: None,
        stopped=lambda: len(captures) == 2,
    ).run()

    assert captures == [
        partial_after.start,
        partial_after.start + timedelta(seconds=60),
    ]


def test_overrun_starts_next_cycle_immediately_without_skipping():
    schedule = build_phase_schedule(_timeline(), _photo())
    clock = _Clock(schedule.tstart)
    captures = []

    def capture(window, started):
        captures.append((window.name, started))
        clock.current += timedelta(seconds=75)
        if len(captures) == 3:
            clock.current = schedule.windows[0].end

    PhaseRuntime(
        schedule,
        now=clock.now,
        wait_until=clock.wait_until,
        enter_phase=lambda _window: None,
        reconcile_phase=lambda _window: None,
        capture=capture,
        log_error=lambda _message: None,
        stopped=lambda: len(captures) >= 3,
    ).run()

    assert [second - first for first, second in zip(
        [item[1] for item in captures],
        [item[1] for item in captures][1:],
    )] == [timedelta(seconds=75), timedelta(seconds=75)]


def test_restart_enters_current_phase_and_never_replays_past_phases():
    schedule = build_phase_schedule(_timeline(), _photo())
    totality = schedule.windows[2]
    clock = _Clock(totality.start + timedelta(seconds=1))
    entered = []
    captured = []

    PhaseRuntime(
        schedule,
        now=clock.now,
        wait_until=clock.wait_until,
        enter_phase=lambda window: entered.append(window.name),
        reconcile_phase=lambda _window: None,
        capture=lambda window, started: captured.append((window.name, started)),
        log_error=lambda _message: None,
        stopped=lambda: bool(captured),
    ).run()

    assert entered == [PHASE_TOTALITY]
    assert captured == [(PHASE_TOTALITY, totality.start + timedelta(seconds=1))]


def test_settings_and_photo_errors_do_not_stop_following_capture():
    schedule = build_phase_schedule(_timeline(), _photo())
    diamond = schedule.windows[1]
    clock = _Clock(diamond.start)
    errors = []
    attempts = []
    reconciliations = []

    def enter(_window):
        raise RuntimeError("set failed")

    def capture(_window, _started):
        attempts.append(clock.now())
        clock.current += timedelta(seconds=1)
        if len(attempts) == 1:
            raise RuntimeError("photo failed")

    def reconcile(_window):
        reconciliations.append(clock.now())
        if len(reconciliations) == 1:
            raise RuntimeError("retry set")

    PhaseRuntime(
        schedule,
        now=clock.now,
        wait_until=clock.wait_until,
        enter_phase=enter,
        reconcile_phase=reconcile,
        capture=capture,
        log_error=errors.append,
        stopped=lambda: len(attempts) >= 2,
    ).run()

    assert len(reconciliations) == 2

    assert len(attempts) == 2
    assert any("stage=settings" in message for message in errors)
    assert any("stage=photo" in message for message in errors)
