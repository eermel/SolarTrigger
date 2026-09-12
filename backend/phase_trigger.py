"""Real-time phase model for eclipse capture.

The runtime deliberately schedules phases and capture cycles, never individual
camera SET operations.  A camera/plugin owns one complete capture cycle and a
phase transition is observed as soon as that indivisible operation returns.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from math import ceil
from typing import Any, Callable, Mapping


PHASE_PARTIAL_BEFORE = "partial_before"
PHASE_DIAMOND_C2 = "diamond_ring_c2"
PHASE_TOTALITY = "totality"
PHASE_DIAMOND_C3 = "diamond_ring_c3"
PHASE_PARTIAL_AFTER = "partial_after"


@dataclass(frozen=True)
class PhaseWindow:
    name: str
    photo_phase: str
    start: datetime
    end: datetime
    interval_s: float

    def contains(self, instant: datetime) -> bool:
        return self.start <= instant < self.end


@dataclass(frozen=True)
class PhaseSchedule:
    tstart: datetime
    tend: datetime
    tmax: datetime
    windows: tuple[PhaseWindow, ...]

    def phase_at(self, instant: datetime) -> PhaseWindow | None:
        for window in self.windows:
            if window.contains(instant):
                return window
        return None


def _number(value: Any, label: str, *, minimum: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a number")
    result = float(value)
    if result < minimum:
        raise ValueError(f"{label} must be >= {minimum:g}")
    return result


def build_phase_schedule(
    timeline: Mapping[str, datetime],
    photo_setup: Mapping[str, Any],
) -> PhaseSchedule:
    """Build the five half-open phase windows from the three input files."""

    phases = photo_setup.get("phases")
    if not isinstance(phases, Mapping):
        raise ValueError("photo_setup.phases must be an object")
    partial = phases.get("partial")
    diamond = phases.get("diamond_ring")
    if not isinstance(partial, Mapping) or not isinstance(diamond, Mapping):
        raise ValueError("partial and diamond_ring phases are required")

    try:
        c1 = timeline["C1"]
        c4 = timeline["C4"]
    except KeyError as exc:
        raise ValueError(f"missing eclipse contact: {exc.args[0]}") from exc

    margin_min = _number(
        photo_setup.get("sequence_margin_min", 60),
        "sequence_margin_min",
    )
    tstart = c1 - timedelta(minutes=margin_min)
    tend = c4 + timedelta(minutes=margin_min)
    partial_interval_s = _number(
        partial.get("interval_s"),
        "phases.partial.interval_s",
        minimum=0.001,
    )

    c2 = timeline.get("C2")
    c3 = timeline.get("C3")
    if c2 is None and c3 is None:
        tmax = timeline.get("TMAX", c1 + (c4 - c1) / 2)
        return PhaseSchedule(
            tstart=tstart,
            tend=tend,
            tmax=tmax,
            windows=(PhaseWindow(
                PHASE_PARTIAL_BEFORE,
                "partial",
                tstart,
                tend,
                partial_interval_s,
            ),),
        )
    if c2 is None or c3 is None:
        raise ValueError("C2 and C3 must both be present or absent")

    diamond_duration_s = _number(
        diamond.get("duration_s"),
        "phases.diamond_ring.duration_s",
    )
    overlap_s = _number(
        diamond.get("totality_overlap_s", 5),
        "phases.diamond_ring.totality_overlap_s",
        minimum=5,
    )
    diamond_interval_s = _number(
        diamond.get("interval_s", 0),
        "phases.diamond_ring.interval_s",
    )

    if not c1 < c2 < c3 < c4:
        raise ValueError("contacts must satisfy C1 < C2 < C3 < C4")

    tmax = c2 + (c3 - c2) / 2
    d = timedelta(seconds=diamond_duration_s)
    x = timedelta(seconds=overlap_s)

    boundaries = (
        tstart,
        c2 - d,
        c2 + x,
        c3 - x,
        c3 + d,
        tend,
    )
    if any(left >= right for left, right in zip(boundaries, boundaries[1:])):
        raise ValueError("phase windows overlap or have no positive duration")

    windows = (
        PhaseWindow(
            PHASE_PARTIAL_BEFORE,
            "partial",
            boundaries[0],
            boundaries[1],
            partial_interval_s,
        ),
        PhaseWindow(
            PHASE_DIAMOND_C2,
            "diamond_ring",
            boundaries[1],
            boundaries[2],
            diamond_interval_s,
        ),
        PhaseWindow(
            PHASE_TOTALITY,
            "totality",
            boundaries[2],
            boundaries[3],
            0.0,
        ),
        PhaseWindow(
            PHASE_DIAMOND_C3,
            "diamond_ring",
            boundaries[3],
            boundaries[4],
            diamond_interval_s,
        ),
        PhaseWindow(
            PHASE_PARTIAL_AFTER,
            "partial",
            boundaries[4],
            boundaries[5],
            partial_interval_s,
        ),
    )
    return PhaseSchedule(tstart=tstart, tend=tend, tmax=tmax, windows=windows)


def aligned_partial_slot(
    tmax: datetime,
    interval_s: float,
    not_before: datetime,
) -> datetime:
    """First ``TMAX + n*interval`` slot at or after ``not_before``."""

    interval_s = _number(interval_s, "partial interval", minimum=0.001)
    offset_s = (not_before - tmax).total_seconds()
    steps = ceil(offset_s / interval_s)
    return tmax + timedelta(seconds=steps * interval_s)


class PhaseRuntime:
    """Run capture cycles from the current time until TEND.

    ``capture`` performs one complete plugin-owned exposure sequence. Returning
    ``False`` ends capture attempts for that phase; raised failures are logged
    before the following cycle continues. Only the first capture after TSTART
    is aligned to TMAX. Later cycles, including the first post-C3 partial
    capture, use their actual start time plus the configured interval, so an
    overrun never causes a capture to be skipped.
    """

    def __init__(
        self,
        schedule: PhaseSchedule,
        *,
        now: Callable[[], datetime],
        wait_until: Callable[[datetime], None],
        enter_phase: Callable[[PhaseWindow], None],
        reconcile_phase: Callable[[PhaseWindow], None],
        capture: Callable[[PhaseWindow, datetime], bool | None],
        log_error: Callable[[str], None],
        stopped: Callable[[], bool] = lambda: False,
        override_phase: Callable[[datetime], PhaseWindow | None] = lambda _now: None,
    ) -> None:
        self.schedule = schedule
        self.now = now
        self.wait_until = wait_until
        self.enter_phase = enter_phase
        self.reconcile_phase = reconcile_phase
        self.capture = capture
        self.log_error = log_error
        self.stopped = stopped
        self.override_phase = override_phase

    def run(self) -> None:
        active: PhaseWindow | None = None
        next_capture: datetime | None = None

        while not self.stopped():
            current = self.now()
            override = self.override_phase(current)
            if override is None and current >= self.schedule.tend:
                return
            window = override or self.schedule.phase_at(current)
            if window is None:
                self.wait_until(self.schedule.tstart)
                continue

            if active != window:
                active = window
                next_capture = self._first_capture(window, current)
                try:
                    self.enter_phase(window)
                except Exception as exc:  # settings failure must not stop photos
                    self.log_error(
                        f"phase={window.name} stage=settings "
                        f"error={type(exc).__name__}: {exc}"
                    )

            assert next_capture is not None
            if next_capture >= window.end:
                self.wait_until(window.end)
                continue
            if current < next_capture:
                self.wait_until(min(next_capture, window.end))
                continue

            # Re-evaluate the phase immediately before every capture.
            current = self.now()
            if not window.contains(current):
                continue
            started = current
            try:
                self.reconcile_phase(window)
            except Exception as exc:
                # A failed SET is unknown and therefore retried before the
                # next photo.  The current photo is still attempted.
                self.log_error(
                    f"phase={window.name} stage=settings "
                    f"error={type(exc).__name__}: {exc}"
                )
            captured = None
            try:
                captured = self.capture(window, started)
            except Exception as exc:
                self.log_error(
                    f"phase={window.name} stage=photo "
                    f"error={type(exc).__name__}: {exc}"
                )

            if captured is False:
                next_capture = window.end
            elif window.interval_s > 0:
                next_capture = started + timedelta(seconds=window.interval_s)
            else:
                # Continuous means no deliberate cadence delay.  A monotonic
                # minimum step prevents a tight loop if a mocked or failed
                # capture consumes no observable time.
                next_capture = max(
                    self.now(),
                    started + timedelta(milliseconds=1),
                )

    def _first_capture(
        self,
        window: PhaseWindow,
        current: datetime,
    ) -> datetime:
        if window.name != PHASE_PARTIAL_BEFORE:
            return current
        return aligned_partial_slot(
            self.schedule.tmax,
            window.interval_s,
            max(current, window.start),
        )


__all__ = [
    "PHASE_DIAMOND_C2",
    "PHASE_DIAMOND_C3",
    "PHASE_PARTIAL_AFTER",
    "PHASE_PARTIAL_BEFORE",
    "PHASE_TOTALITY",
    "PhaseRuntime",
    "PhaseSchedule",
    "PhaseWindow",
    "aligned_partial_slot",
    "build_phase_schedule",
]
