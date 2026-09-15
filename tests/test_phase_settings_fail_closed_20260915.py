from datetime import datetime, timedelta

from backend.phase_trigger import PhaseRuntime, PhaseSchedule, PhaseWindow


def _run_one_window(*, interval_s, reconcile, capture, logs):
    start = datetime(2026, 8, 2, 10, 0, 0)
    end = start + timedelta(seconds=5 if interval_s == 0 else interval_s + 1)
    window = PhaseWindow(
        name="totality" if interval_s == 0 else "partial_before",
        photo_phase="totality" if interval_s == 0 else "partial",
        start=start,
        end=end,
        interval_s=interval_s,
    )
    schedule = PhaseSchedule(
        tstart=start,
        tend=end,
        tmax=start,
        windows=(window,),
    )

    current = [start]
    reconcile_calls = [0]

    def now():
        return current[0]

    def wait_until(target):
        current[0] = target

    def wrapped_reconcile(w):
        reconcile_calls[0] += 1
        return reconcile(w, reconcile_calls[0])

    runtime = PhaseRuntime(
        schedule,
        now=now,
        wait_until=wait_until,
        enter_phase=lambda _w: None,
        reconcile_phase=wrapped_reconcile,
        capture=capture,
        log_error=logs.append,
    )
    runtime.run()
    return reconcile_calls[0]


def test_failed_reconcile_skips_partial_photo_and_does_not_use_stale_settings():
    photos = []
    logs = []

    def reconcile(_window, call):
        if call == 1:
            raise RuntimeError("set iso failed")

    calls = _run_one_window(
        interval_s=3.0,
        reconcile=reconcile,
        capture=lambda _window, started: photos.append(started) or True,
        logs=logs,
    )

    assert calls >= 2
    assert len(photos) == 1
    assert any("stage=settings" in line and "set iso failed" in line for line in logs)


def test_failed_reconcile_in_continuous_phase_retries_without_photo_storm():
    photos = []
    logs = []
    seen = []

    def reconcile(_window, call):
        seen.append(call)
        if call < 3:
            raise RuntimeError("camera set failed")

    calls = _run_one_window(
        interval_s=0.0,
        reconcile=reconcile,
        capture=lambda _window, started: photos.append(started) or False,
        logs=logs,
    )

    assert calls == 3
    assert len(photos) == 1
    assert len(logs) == 2


def test_successful_reconcile_still_allows_capture():
    photos = []
    logs = []

    _run_one_window(
        interval_s=3.0,
        reconcile=lambda _window, _call: None,
        capture=lambda _window, started: photos.append(started) or False,
        logs=logs,
    )

    assert len(photos) == 1
    assert logs == []
