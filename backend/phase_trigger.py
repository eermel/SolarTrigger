"""Real-time phase model for eclipse capture.

The runtime deliberately schedules phases and capture cycles, never individual
camera SET operations.  A camera/plugin owns one complete capture cycle and a
phase transition is observed as soon as that indivisible operation returns.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from math import ceil, isfinite
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
    if not isfinite(result):
        raise ValueError(f"{label} must be finite")
    if result < minimum:
        raise ValueError(f"{label} must be >= {minimum:g}")
    return result


def build_phase_schedule(
    timeline: Mapping[str, datetime],
    photo_setup: Mapping[str, Any],
    *,
    honor_timeline_bounds: bool = False,
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
    derived_tstart = c1 - timedelta(minutes=margin_min)
    derived_tend = c4 + timedelta(minutes=margin_min)
    if honor_timeline_bounds:
        tstart = timeline.get("TSTART", derived_tstart)
        tend = timeline.get("TEND", derived_tend)
    else:
        tstart = derived_tstart
        tend = derived_tend
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
        capture_lead_s: Callable[[PhaseWindow, datetime], float] | None = None,
        capture_with_target: Callable[
            [PhaseWindow, datetime, datetime], bool | None
        ] | None = None,
        next_capture_log: Callable[[PhaseWindow, datetime], None] | None = None,
    ) -> None:
        self.schedule = schedule
        self.now = now
        self.wait_until = wait_until
        self.enter_phase = enter_phase
        self.reconcile_phase = reconcile_phase
        self.capture = capture
        self.capture_with_target = capture_with_target
        self.log_error = log_error
        self.stopped = stopped
        self.override_phase = override_phase
        self.capture_lead_s = capture_lead_s or (lambda _window, _target: 0.0)
        self.next_capture_log = next_capture_log

    def run(self) -> None:
        active: PhaseWindow | None = None
        next_capture: datetime | None = None
        announced_next_capture: datetime | None = None

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
                announced_next_capture = None
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
            try:
                lead_s = float(self.capture_lead_s(window, next_capture))
            except (TypeError, ValueError):
                lead_s = 0.0
            if not isfinite(lead_s) or lead_s < 0:
                lead_s = 0.0

            # Never prepare a future phase while the previous phase still owns
            # the camera. Inside an active phase, start the SET preamble early.
            prepare_at = max(
                window.start,
                next_capture - timedelta(seconds=lead_s),
            )

            if current < prepare_at:
                if (
                    window.photo_phase == "partial"
                    and self.next_capture_log is not None
                    and announced_next_capture != next_capture
                ):
                    self.next_capture_log(window, next_capture)
                    announced_next_capture = next_capture
                self.wait_until(min(prepare_at, window.end))
                continue

            # Re-evaluate the phase immediately before every capture.
            current = self.now()
            if not window.contains(current):
                continue
            # Preserve the historical meaning of `started`: the actual
            # runtime instant at which this capture cycle is admitted.
            # `next_capture` remains the distinct desired PHOTO target.
            started = current
            settings_ok = True
            try:
                self.reconcile_phase(window)
            except Exception as exc:
                # A failed required SET leaves the physical camera state
                # unknown.  Do not take a normal eclipse photo with potentially
                # stale ISO/aperture values.  Skip this slot and retry settings
                # on the next cycle.  Emergency Totality has a separate
                # best-effort path and is intentionally unaffected.
                settings_ok = False
                self.log_error(
                    f"phase={window.name} stage=settings "
                    f"error={type(exc).__name__}: {exc}"
                )
            # An emergency override may arrive while phase settings are being
            # reconciled. Re-check it immediately before admitting a new PHOTO
            # group. A group already in progress remains atomic, but once the
            # override is visible no further normal capture may start.
            current = self.now()
            override = self.override_phase(current)
            if override is not None and override != window:
                continue
            if override is None and not window.contains(current):
                continue
            started = current

            if not settings_ok:
                announced_next_capture = None
                if window.interval_s > 0:
                    next_capture = started + timedelta(seconds=window.interval_s)
                else:
                    # Continuous phases must retry promptly, but never spin
                    # against a failing camera SET operation.
                    next_capture = started + timedelta(seconds=1)
                continue

            captured = None
            target_capture = next_capture
            try:
                if self.capture_with_target is not None:
                    captured = self.capture_with_target(
                        window,
                        started,
                        target_capture,
                    )
                else:
                    captured = self.capture(window, started)
            except Exception as exc:
                self.log_error(
                    f"phase={window.name} stage=photo "
                    f"error={type(exc).__name__}: {exc}"
                )

            announced_next_capture = None
            if captured is False:
                next_capture = window.end
            elif window.interval_s > 0:
                # Normal case: cadence remains anchored to the requested PHOTO
                # target even though preparation began early.
                #
                # Overrun case: if the target was already in the past when this
                # cycle was admitted, retain the historical behaviour and start
                # the next interval from the actual admission time.
                cadence_anchor = max(
                    target_capture,
                    started,
                )
                next_capture = cadence_anchor + timedelta(
                    seconds=window.interval_s
                )
            else:
                # Continuous means no deliberate delay after a real capture:
                # when camera work already consumed >= 1 s, self.now() wins.
                # An immediate failure/empty result, however, must not create
                # a millisecond retry storm against the camera IPC service.
                next_capture = max(
                    self.now(),
                    started + timedelta(seconds=1),
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
