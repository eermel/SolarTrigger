"""Anchor-first Sequencer planner.

The eclipse contacts are the primary photographic anchors:

    1. build C2 and C3 contact transitions first;
    2. fill complete totality cycles between them;
    3. build pre-C2 phases backwards to TSTART;
    4. build post-C3 phases forwards to TEND.

This module is pure planning code.  It never touches camera hardware and never
sleeps.  Every candidate capture is materialized at its final timestamp so
atmospheric/motion corrections are evaluated for the time at which the photo
will actually be taken.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any, Callable, Iterable

from backend.sequencer_compiler import (
    AuditedRigCapture,
    CameraTimingProfile,
    CaptureTarget,
    GlobalExecutionEvent,
    ScheduledOperation,
    SequenceWindow,
    _mechanical_vibration_delay_delta,
    _normalize_camera_state,
    _scheduled_static_bounds,
    _split_totality_single_photos,
    audit_materialized_capture,
    build_sequence_windows,
    materialize_capture_target_for_rig,
    merge_scheduled_operations,
    reduce_audited_capture_operations,
    schedule_audited_capture,
    validate_static_rig_feasibility,
)


# Existing policy retained, but it is now isolated at the contact anchor.
CONTACT_TRANSITION_LEAD_S = 1.0
CONTACT_MAX_FRAMES = 5
C3_CONTACT_MAX_EXPOSURE_S = 1.0 / 500.0
CRITICAL_TRANSITION_MARGIN_MS = 250.0

# Iterative placement tolerance. Re-materialization matters when exposure
# correction depends on timestamp; eclipse attenuation changes slowly enough
# that this converges in a few iterations, but we fail closed if it does not.
PLACEMENT_TOLERANCE_MS = 0.5
PLACEMENT_MAX_ITERATIONS = 8
PACK_GUARD = 10000


CaptureFactory = Callable[
    [str, str, datetime, int, datetime | None],
    AuditedRigCapture,
]


def _shutter_seconds(value: Any) -> float:
    text = str(value).strip()
    if not text:
        raise ValueError("empty shutter value")
    try:
        if "/" in text:
            numerator, denominator = text.split("/", 1)
            seconds = float(numerator) / float(denominator)
        else:
            seconds = float(text)
    except (TypeError, ValueError, ZeroDivisionError) as exc:
        raise ValueError(f"invalid shutter value: {value!r}") from exc
    if seconds <= 0:
        raise ValueError(f"invalid shutter value: {value!r}")
    return seconds


def _validate_contact_capture(
    capture: AuditedRigCapture,
    *,
    c3: bool,
) -> None:
    count = len(capture.exposure_plan)
    if count > CONTACT_MAX_FRAMES:
        raise ValueError(
            f"contact PHOTO exceeds {CONTACT_MAX_FRAMES} exposures "
            f"for RIG {capture.rig_id}: {count}"
        )

    if c3:
        unsafe = [
            str(exposure.get("shutter"))
            for exposure in capture.exposure_plan
            if _shutter_seconds(exposure.get("shutter"))
            > C3_CONTACT_MAX_EXPOSURE_S + 1e-12
        ]
        if unsafe:
            raise ValueError(
                f"C3 contact exposure slower than 1/500 s for "
                f"RIG {capture.rig_id}: {', '.join(unsafe)}"
            )


def _full_schedule(
    capture: AuditedRigCapture,
    profile: CameraTimingProfile,
) -> tuple[list[ScheduledOperation], datetime, datetime]:
    """Schedule without state reduction.

    Critical anchor placement must not depend on whatever state a previous
    capture happens to leave behind.  Using the full audited preparation makes
    the reserved boundary conservative and deterministic.  The final emitted
    plan is reduced later, after all target timestamps are fixed.
    """
    scheduled = schedule_audited_capture(capture, profile)
    start, end = _scheduled_static_bounds(scheduled)
    return scheduled, start, end


def _contact_trigger_operations(
    scheduled: Iterable[ScheduledOperation],
) -> list[ScheduledOperation]:
    """Return physical trigger operations from one contact capture."""
    return [
        item
        for item in scheduled
        if item.operation.get("action")
        in {"trigger_capture", "bracket_press"}
    ]


def _contact_execution_policy(
    capture: AuditedRigCapture,
    scheduled: Iterable[ScheduledOperation],
) -> tuple[str, list[ScheduledOperation]]:
    """Determine contact policy from the real prepared execution.

    A native bracket is the atomic contact anchor. It may be accompanied by
    one or more explicitly separated auxiliary singles. One trigger_capture
    per exposure without a native bracket is sequential.
    """
    triggers = _contact_trigger_operations(scheduled)
    exposure_count = len(capture.exposure_plan)

    if exposure_count <= 0:
        raise ValueError(
            f"empty contact exposure plan for RIG {capture.rig_id}"
        )

    if not triggers:
        raise ValueError(
            f"contact capture has no physical trigger "
            f"for RIG {capture.rig_id}"
        )

    if len(triggers) == 1:
        operation = triggers[0].operation

        try:
            frames = int(
                operation.get(
                    "frames",
                    operation.get("expected_frames", 1),
                )
                or 1
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"invalid contact frame count for RIG {capture.rig_id}"
            ) from exc

        if (
            operation.get("action") == "bracket_press"
            or frames > 1
            or exposure_count > 1
        ):
            return "atomic_bracket", triggers

    native_brackets = [
        item
        for item in triggers
        if item.operation.get("action") == "bracket_press"
        and item.operation.get("contact_anchor") is True
    ]

    if len(native_brackets) == 1:
        try:
            physical_count = sum(
                int(
                    item.operation.get(
                        "frames",
                        item.operation.get("expected_frames", 1),
                    )
                    or 1
                )
                for item in triggers
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"invalid contact frame count for RIG {capture.rig_id}"
            ) from exc

        if physical_count == exposure_count:
            return "atomic_bracket", triggers

    if (
        len(triggers) == exposure_count
        and all(
            item.operation.get("action") == "trigger_capture"
            for item in triggers
        )
    ):
        return "sequential", triggers

    raise ValueError(
        f"ambiguous contact execution for RIG {capture.rig_id}: "
        f"{len(triggers)} triggers for {exposure_count} exposures"
    )


def _make_contact_anchor(
    factory: CaptureFactory,
    profile: CameraTimingProfile,
    *,
    contact_time: datetime,
    phase_window: str,
    c3: bool,
) -> tuple[
    AuditedRigCapture,
    list[ScheduledOperation],
    datetime,
    datetime,
]:
    """Build a C2/C3 anchor according to physical camera strategy.

    Atomic bracket:
        keep the validated policy: trigger the priority native bracket one
        second before contact and allow the complete reservation to finish.
        An auxiliary Atmos single may be scheduled before C2.

    Sequential:
        place the modeled physical start of the middle exposure exactly
        on the contact. Other exposures are positioned from characterized
        SET/PHOTO timings, without hard-coded camera offsets.
    """
    target_time = (
        contact_time
        - timedelta(seconds=CONTACT_TRANSITION_LEAD_S)
    )

    for _ in range(PLACEMENT_MAX_ITERATIONS):
        capture = factory(
            "diamond_ring",
            phase_window,
            target_time,
            0,
            None,
        )

        _validate_contact_capture(
            capture,
            c3=c3,
        )

        full_scheduled, full_start, full_end = _full_schedule(
            capture,
            profile,
        )

        policy, _ = _contact_execution_policy(
            capture,
            full_scheduled,
        )

        # BRACKET NATIVE / ATOMIC:
        # preserve the already validated -1 second policy.
        if policy == "atomic_bracket":
            if full_end >= contact_time:
                return (
                    capture,
                    full_scheduled,
                    full_start,
                    full_end,
                )

            target_time += contact_time - full_end

            if target_time > contact_time:
                raise ValueError(
                    f"contact PHOTO cannot cross "
                    f"{'C3' if c3 else 'C2'} "
                    f"for RIG {capture.rig_id}"
                )

            continue

        # SEQUENTIAL:
        # remove redundant SETs inside the group before calculating
        # the actual deterministic trigger positions.
        reduced, _state_after = (
            reduce_audited_capture_operations(
                capture,
                {},
            )
        )

        scheduled = schedule_audited_capture(
            reduced,
            profile,
        )

        start, end = _scheduled_static_bounds(
            scheduled
        )

        reduced_policy, triggers = (
            _contact_execution_policy(
                reduced,
                scheduled,
            )
        )

        if reduced_policy != "sequential":
            raise ValueError(
                f"contact strategy changed after SET reduction "
                f"for RIG {capture.rig_id}"
            )

        # A unique middle photograph is required.
        if len(triggers) % 2 == 0:
            raise ValueError(
                f"sequential contact group requires an odd "
                f"number of exposures for RIG {capture.rig_id}: "
                f"{len(triggers)}"
            )

        middle = triggers[len(triggers) // 2]

        if middle.command_time is None:
            raise ValueError(
                f"sequential contact trigger has no command time "
                f"for RIG {capture.rig_id}"
            )

        # target_time models desired physical exposure start.
        # Convert USB dispatch time back to the same modeled reference.
        middle_physical_time = (
            middle.command_time
            + timedelta(
                milliseconds=float(
                    profile.trigger_single_latency_ms
                )
            )
        )

        delta = (
            contact_time
            - middle_physical_time
        )

        if (
            abs(delta.total_seconds() * 1000.0)
            <= PLACEMENT_TOLERANCE_MS
        ):
            if not (
                start <= contact_time <= end
            ):
                raise ValueError(
                    f"sequential contact reservation does not "
                    f"contain {'C3' if c3 else 'C2'} "
                    f"for RIG {capture.rig_id}"
                )

            return (
                capture,
                scheduled,
                start,
                end,
            )

        target_time += delta

    raise ValueError(
        f"contact anchor placement did not converge "
        f"for RIG {capture.rig_id}"
    )


def _fit_full_capture_ending_at(
    factory: CaptureFactory,
    profile: CameraTimingProfile,
    *,
    phase: str,
    phase_window: str,
    end_at: datetime,
    deadline: datetime | None,
) -> tuple[AuditedRigCapture, datetime, datetime]:
    """Re-materialize until the complete reservation ends at ``end_at``."""
    guess = end_at
    capture: AuditedRigCapture | None = None

    for _ in range(PLACEMENT_MAX_ITERATIONS):
        capture = factory(phase, phase_window, guess, 0, deadline)
        _scheduled, start, end = _full_schedule(capture, profile)
        delta = end_at - end
        if abs(delta.total_seconds() * 1000.0) <= PLACEMENT_TOLERANCE_MS:
            return capture, start, end
        guess += delta

    assert capture is not None
    raise ValueError(
        f"backward capture placement did not converge for RIG {capture.rig_id}"
    )


def _fit_full_capture_starting_at(
    factory: CaptureFactory,
    profile: CameraTimingProfile,
    *,
    phase: str,
    phase_window: str,
    start_at: datetime,
    deadline: datetime | None,
) -> tuple[AuditedRigCapture, datetime, datetime]:
    """Re-materialize until the first reserved command starts at start_at."""
    guess = start_at
    capture: AuditedRigCapture | None = None

    for _ in range(PLACEMENT_MAX_ITERATIONS):
        capture = factory(phase, phase_window, guess, 0, deadline)
        _scheduled, start, end = _full_schedule(capture, profile)
        delta = start_at - start
        if abs(delta.total_seconds() * 1000.0) <= PLACEMENT_TOLERANCE_MS:
            return capture, start, end
        guess += delta

    assert capture is not None
    raise ValueError(
        f"forward capture placement did not converge for RIG {capture.rig_id}"
    )


def _fit_reduced_capture_starting_at(
    factory: CaptureFactory,
    profile: CameraTimingProfile,
    state: dict[str, str],
    *,
    phase: str,
    phase_window: str,
    start_at: datetime,
    deadline: datetime | None,
) -> tuple[AuditedRigCapture, dict[str, str], datetime, datetime]:
    """Place one capture from its real predecessor state at max throughput."""
    guess = start_at
    capture: AuditedRigCapture | None = None
    next_state: dict[str, str] | None = None

    for _ in range(PLACEMENT_MAX_ITERATIONS):
        capture = factory(phase, phase_window, guess, 0, deadline)
        reduced, next_state = reduce_audited_capture_operations(capture, state)
        scheduled = schedule_audited_capture(reduced, profile)
        start, end = _scheduled_static_bounds(scheduled)
        delta = start_at - start
        if abs(delta.total_seconds() * 1000.0) <= PLACEMENT_TOLERANCE_MS:
            return capture, next_state, start, end
        guess += delta

    assert capture is not None and next_state is not None
    raise ValueError(
        f"state-aware forward placement did not converge for RIG {capture.rig_id}"
    )


def _pack_continuous_backward(
    factory: CaptureFactory,
    profile: CameraTimingProfile,
    window: SequenceWindow,
    *,
    boundary_end: datetime,
) -> list[AuditedRigCapture]:
    """Pack interval=0 captures backwards from an already reserved boundary."""
    result: list[AuditedRigCapture] = []
    cursor = min(boundary_end, window.end)

    for _ in range(PACK_GUARD):
        capture, start, end = _fit_full_capture_ending_at(
            factory,
            profile,
            phase=window.phase,
            phase_window=window.name,
            end_at=cursor,
            deadline=window.end,
        )
        if end <= start:
            raise ValueError(
                f"non-positive capture reservation for RIG {capture.rig_id}"
            )
        if start < window.start:
            break
        result.append(capture)
        cursor = start
    else:
        raise ValueError("backward max-throughput packing did not converge")

    result.reverse()
    return result


def _pack_continuous_forward(
    factory: CaptureFactory,
    profile: CameraTimingProfile,
    window: SequenceWindow,
    *,
    boundary_start: datetime,
    initial_state: dict[str, str],
) -> tuple[list[AuditedRigCapture], dict[str, str]]:
    """Pack interval=0 forwards using the real state left by C3."""
    result: list[AuditedRigCapture] = []
    cursor = max(boundary_start, window.start)
    state = dict(initial_state)

    for _ in range(PACK_GUARD):
        capture, candidate_state, start, end = _fit_reduced_capture_starting_at(
            factory,
            profile,
            state,
            phase=window.phase,
            phase_window=window.name,
            start_at=cursor,
            deadline=window.end,
        )
        if end <= start:
            raise ValueError(
                f"non-positive capture reservation for RIG {capture.rig_id}"
            )
        if end > window.end:
            break
        result.append(capture)
        state = candidate_state
        cursor = end
    else:
        raise ValueError("forward max-throughput packing did not converge")

    return result, state


def _pack_periodic_backward(
    factory: CaptureFactory,
    profile: CameraTimingProfile,
    window: SequenceWindow,
    *,
    boundary_end: datetime,
    origin: datetime | None = None,
) -> list[AuditedRigCapture]:
    """Build a periodic phase backwards from its contact-side boundary."""
    if window.interval_s is None or window.interval_s <= 0:
        raise ValueError("periodic backward packing requires interval > 0")

    interval = timedelta(seconds=window.interval_s)
    anchor = window.end if origin is None else origin
    target = anchor - interval
    cursor = min(boundary_end, window.end)
    result: list[AuditedRigCapture] = []

    for _ in range(PACK_GUARD):
        if target < window.start:
            break
        capture = factory(
            window.phase,
            window.name,
            target,
            0,
            window.end,
        )
        _scheduled, start, end = _full_schedule(capture, profile)
        if start >= window.start and end <= cursor:
            result.append(capture)
            cursor = start
        target -= interval
    else:
        raise ValueError("backward periodic packing did not converge")

    result.reverse()
    return result


def _pack_periodic_forward(
    factory: CaptureFactory,
    profile: CameraTimingProfile,
    window: SequenceWindow,
    *,
    boundary_start: datetime,
    origin: datetime | None = None,
    include_origin: bool = True,
) -> list[AuditedRigCapture]:
    """Build a periodic phase forwards from its contact-side boundary."""
    if window.interval_s is None or window.interval_s <= 0:
        raise ValueError("periodic forward packing requires interval > 0")

    interval = timedelta(seconds=window.interval_s)
    anchor = window.start if origin is None else origin
    target = anchor if include_origin else anchor + interval
    cursor = max(boundary_start, window.start)
    result: list[AuditedRigCapture] = []

    for _ in range(PACK_GUARD):
        if target >= window.end:
            break
        capture = factory(
            window.phase,
            window.name,
            target,
            0,
            window.end,
        )
        _scheduled, start, end = _full_schedule(capture, profile)
        if start >= cursor and end <= window.end:
            result.append(capture)
            cursor = end
        target += interval
    else:
        raise ValueError("forward periodic packing did not converge")

    return result


def _place_unit_starting_at(
    unit: AuditedRigCapture,
    profile: CameraTimingProfile,
    state: dict[str, str],
    start_at: datetime,
) -> tuple[AuditedRigCapture, dict[str, str], datetime, datetime]:
    reduced, next_state = reduce_audited_capture_operations(unit, state)
    scheduled = schedule_audited_capture(reduced, profile)
    start, _end = _scheduled_static_bounds(scheduled)
    delta = start_at - start
    shifted = replace(
        unit,
        target=replace(
            unit.target,
            target_time=unit.target.target_time + delta,
        ),
    )
    reduced_shifted = replace(reduced, target=shifted.target)
    scheduled = schedule_audited_capture(reduced_shifted, profile)
    start, end = _scheduled_static_bounds(scheduled)

    # ``end`` already includes the complete characterized camera reservation.
    # Mechanical vibration is therefore reserved only after all camera
    # exposure/USB/tail overheads have completed.  No WAIT is emitted:
    # this availability boundary simply shifts the next physical PHOTO.
    available_end = (
        end
        + _mechanical_vibration_delay_delta(
            reduced_shifted
        )
    )

    return shifted, next_state, start, available_end


def _pack_totality_to_c3_anchor(
    factory: CaptureFactory,
    profile: CameraTimingProfile,
    window: SequenceWindow,
    *,
    boundary_start: datetime,
    c3_capture: AuditedRigCapture,
    margin: timedelta,
    initial_state: dict[str, str],
) -> tuple[list[AuditedRigCapture], dict[str, str]]:
    """Fill totality greedily up to the *actual* C3 preparation boundary.

    C2 and the C3 contact PHOTO are fixed first.  Totality is then packed
    physical PHOTO unit by physical PHOTO unit.  After every candidate unit
    we reduce the already-fixed C3 contact capture from the state that this
    candidate would leave behind.  This gives the real C3 PREP start for that
    state, rather than a conservative full-preparation estimate.

    Complete ladders are repeated while they fit.  The final ladder may be
    partial: useful totality photos are not discarded merely because the next
    slower unit would collide with the protected C3 transition.
    """
    result: list[AuditedRigCapture] = []
    cursor = max(boundary_start, window.start)
    state = dict(initial_state)
    cycle_index = 0

    for _guard in range(PACK_GUARD):
        if cursor >= window.end:
            break

        cycle = factory(
            "totality",
            window.name,
            cursor,
            cycle_index,
            c3_capture.target.target_time,
        )
        units = _split_totality_single_photos(cycle)
        if not units:
            raise ValueError(f"empty totality cycle for RIG {cycle.rig_id}")

        accepted_in_cycle = 0

        for unit in units:
            placed, candidate_state, start, end = _place_unit_starting_at(
                unit,
                profile,
                state,
                cursor,
            )
            if start < cursor - timedelta(milliseconds=PLACEMENT_TOLERANCE_MS):
                raise ValueError(
                    f"totality placement regression for RIG {cycle.rig_id}"
                )

            # C3 PHOTO target is already fixed.  Only its preparation duration
            # depends on the state left by totality.  Recompute that exact
            # boundary for every candidate and keep the safety margin outside
            # the contact reservation.
            reduced_c3, _state_after_c3 = reduce_audited_capture_operations(
                c3_capture,
                candidate_state,
            )
            c3_scheduled = schedule_audited_capture(reduced_c3, profile)
            c3_prepare_start, _c3_end = _scheduled_static_bounds(c3_scheduled)
            stop_at = min(window.end, c3_prepare_start - margin)

            if end > stop_at:
                if not result:
                    raise ValueError(
                        f"no totality photo fits before C3 for RIG {cycle.rig_id}"
                    )
                return result, state

            result.append(placed)
            state = candidate_state
            cursor = end
            accepted_in_cycle += 1

        if accepted_in_cycle != len(units):
            # Defensive: the non-fitting case returns immediately above.
            break

        cycle_index += 1
    else:
        raise ValueError("totality packing did not converge")

    if not result:
        raise ValueError("no totality photo fits between C2 and C3")

    return result, state


def _pack_complete_totality_cycles(
    factory: CaptureFactory,
    profile: CameraTimingProfile,
    window: SequenceWindow,
    *,
    boundary_start: datetime,
    boundary_end: datetime,
    initial_state: dict[str, str],
) -> tuple[list[AuditedRigCapture], dict[str, str]]:
    """Fill only complete totality ladders between the two contact anchors."""
    result: list[AuditedRigCapture] = []
    cursor = max(boundary_start, window.start)
    accepted_cycles = 0
    state = dict(initial_state)

    for _ in range(PACK_GUARD):
        if cursor >= boundary_end:
            break

        # Re-materialize every cycle at its real start time. This keeps
        # atmospheric/motion correction tied to the actual execution time.
        cycle = factory(
            "totality",
            window.name,
            cursor,
            accepted_cycles,
            boundary_end,
        )
        units = _split_totality_single_photos(cycle)
        if not units:
            raise ValueError(f"empty totality cycle for RIG {cycle.rig_id}")

        candidate_units: list[AuditedRigCapture] = []
        candidate_cursor = cursor
        candidate_state = dict(state)

        for unit in units:
            placed, candidate_state, start, end = _place_unit_starting_at(
                unit,
                profile,
                candidate_state,
                candidate_cursor,
            )
            if start < candidate_cursor - timedelta(milliseconds=PLACEMENT_TOLERANCE_MS):
                raise ValueError(
                    f"totality placement regression for RIG {cycle.rig_id}"
                )
            candidate_units.append(placed)
            candidate_cursor = end

        # A cycle is all-or-nothing: never leave an incomplete exposure ladder
        # immediately before C3.
        if candidate_cursor > boundary_end:
            break

        result.extend(candidate_units)
        cursor = candidate_cursor
        state = candidate_state
        accepted_cycles += 1
    else:
        raise ValueError("totality packing did not converge")

    if accepted_cycles == 0:
        raise ValueError("no complete totality cycle fits between C2 and C3")

    return result, state


def _renumber_capture_targets(
    captures: Iterable[AuditedRigCapture],
) -> list[AuditedRigCapture]:
    ordered = sorted(
        captures,
        key=lambda capture: (
            capture.target.target_time,
            capture.target.phase_window,
        ),
    )
    counters: dict[str, int] = {}
    result: list[AuditedRigCapture] = []

    for capture in ordered:
        window = capture.target.phase_window
        index = counters.get(window, 0)
        counters[window] = index + 1
        result.append(
            replace(
                capture,
                target=replace(capture.target, sequence_index=index),
            )
        )
    return result


def _factory_for_rig(
    *,
    rig: dict[str, Any],
    photo_config: dict[str, Any],
    exposure_opt_config: dict[str, Any],
    eclipse_context: dict[str, Any],
    eclipse_config: dict[str, Any] | None,
) -> CaptureFactory:
    def factory(
        phase: str,
        phase_window: str,
        target_time: datetime,
        sequence_index: int,
        deadline: datetime | None,
    ) -> AuditedRigCapture:
        target = CaptureTarget(
            target_time=target_time,
            phase=phase,
            phase_window=phase_window,
            sequence_index=sequence_index,
            deadline=deadline,
        )
        materialized = materialize_capture_target_for_rig(
            target,
            rig,
            photo_config,
            exposure_opt_config,
            eclipse_context,
            eclipse_config=eclipse_config,
        )
        return audit_materialized_capture(materialized)

    return factory


def build_anchor_first_capture_plan(
    *,
    timeline: dict[str, datetime],
    photo_config: dict[str, Any],
    sequence_margin_min: float,
    active_rigs: Iterable[dict[str, Any]],
    exposure_opt_config: dict[str, Any],
    eclipse_context: dict[str, Any],
    timing_profiles: dict[int, CameraTimingProfile],
    eclipse_config: dict[str, Any] | None = None,
) -> dict[int, list[AuditedRigCapture]]:
    """Build final capture timestamps from C2/C3 outward.

    Contact anchors are placed before any other photograph.  The surrounding
    phases are then packed into the remaining free intervals.  This function
    returns captures only; SET deduplication and final command scheduling happen
    afterwards, once chronological order is known.
    """
    windows = build_sequence_windows(
        timeline,
        photo_config,
        sequence_margin_min=sequence_margin_min,
    )
    by_name = {window.name: window for window in windows}
    required_windows = {
        "phase_1a", "phase_1b", "phase_2", "phase_3a", "phase_3b"
    }
    if set(by_name) != required_windows:
        raise ValueError("anchor-first planner requires the canonical five windows")

    c2 = timeline["C2"]
    c3 = timeline["C3"]
    margin = timedelta(milliseconds=CRITICAL_TRANSITION_MARGIN_MS)
    result: dict[int, list[AuditedRigCapture]] = {}

    for rig in active_rigs:
        rig_id = rig.get("rig_id")
        if not isinstance(rig_id, int) or isinstance(rig_id, bool):
            raise ValueError("active RIG has invalid rig_id")
        profile = timing_profiles.get(rig_id)
        if profile is None:
            raise ValueError(f"missing camera timing profile for RIG {rig_id}")

        factory = _factory_for_rig(
            rig=rig,
            photo_config=photo_config,
            exposure_opt_config=exposure_opt_config,
            eclipse_context=eclipse_context,
            eclipse_config=eclipse_config,
        )

        # 1. CONTACTS FIRST -------------------------------------------------
        c2_capture, _c2_sched, c2_start, c2_end = _make_contact_anchor(
            factory,
            profile,
            contact_time=c2,
            phase_window="phase_1b",
            c3=False,
        )
        c3_capture, _c3_sched, c3_start, c3_end = _make_contact_anchor(
            factory,
            profile,
            contact_time=c3,
            phase_window="phase_3a",
            c3=True,
        )

        totality_start = c2_end
        if totality_start >= c3:
            raise ValueError(
                f"C2 contact reservation leaves no totality window "
                f"for RIG {rig_id}"
            )

        # 2. BETWEEN C2 AND C3 ---------------------------------------------
        # C2 is self-contained for placement; derive the camera state it leaves
        # behind.  C3 itself is already fixed, but its real PREP boundary is
        # state-dependent, so totality is admitted PHOTO by PHOTO against that
        # exact boundary.
        _c2_reduced, c2_state = reduce_audited_capture_operations(
            c2_capture, {}
        )
        totality, _totality_state = _pack_totality_to_c3_anchor(
            factory,
            profile,
            by_name["phase_2"],
            boundary_start=totality_start,
            c3_capture=c3_capture,
            margin=margin,
            initial_state=c2_state,
        )

        # 3. BUILD BACKWARDS FROM C2 --------------------------------------
        pre_dr_window = by_name["phase_1b"]
        pre_dr_boundary = c2_start - margin
        if pre_dr_window.interval_s == 0:
            pre_dr = _pack_continuous_backward(
                factory,
                profile,
                pre_dr_window,
                boundary_end=pre_dr_boundary,
            )
        else:
            pre_dr = _pack_periodic_backward(
                factory,
                profile,
                pre_dr_window,
                boundary_end=pre_dr_boundary,
                origin=c2_capture.target.target_time,
            )

        pre_partial_window = by_name["phase_1a"]
        pre_partial = _pack_periodic_backward(
            factory,
            profile,
            pre_partial_window,
            boundary_end=pre_partial_window.end,
        )

        # 4. BUILD FORWARDS FROM C3 ---------------------------------------
        post_dr_window = by_name["phase_3a"]
        _c3_reduced, c3_state = reduce_audited_capture_operations(
            c3_capture, {}
        )
        if post_dr_window.interval_s == 0:
            post_dr, _post_dr_state = _pack_continuous_forward(
                factory,
                profile,
                post_dr_window,
                boundary_start=c3_end,
                initial_state=c3_state,
            )
        else:
            post_dr = _pack_periodic_forward(
                factory,
                profile,
                post_dr_window,
                boundary_start=c3_end,
                origin=c3_capture.target.target_time,
                include_origin=False,
            )

        post_partial_window = by_name["phase_3b"]
        post_partial = _pack_periodic_forward(
            factory,
            profile,
            post_partial_window,
            boundary_start=post_partial_window.start,
            origin=post_partial_window.start,
            include_origin=True,
        )

        captures = (
            pre_partial
            + pre_dr
            + [c2_capture]
            + totality
            + [c3_capture]
            + post_dr
            + post_partial
        )
        result[rig_id] = _renumber_capture_targets(captures)

    return result


def schedule_anchor_first_capture_plan(
    audited_by_rig: dict[int, Iterable[AuditedRigCapture]],
    *,
    initial_states: dict[int, dict[str, Any]],
    timing_profiles: dict[int, CameraTimingProfile],
) -> tuple[list[GlobalExecutionEvent], dict[int, dict[str, str]]]:
    """Reduce SETs chronologically and emit the final global command stream."""
    scheduled_by_rig: dict[int, list[ScheduledOperation]] = {}
    final_states: dict[int, dict[str, str]] = {}

    for rig_id in sorted(audited_by_rig):
        captures = sorted(
            list(audited_by_rig[rig_id]),
            key=lambda capture: (
                capture.target.target_time,
                capture.target.phase_window,
                capture.target.sequence_index,
            ),
        )
        profile = timing_profiles.get(rig_id)
        if profile is None:
            raise ValueError(f"missing camera timing profile for RIG {rig_id}")

        state = _normalize_camera_state(initial_states.get(rig_id, {}))
        scheduled: list[ScheduledOperation] = []

        for capture in captures:
            reduced, state = reduce_audited_capture_operations(capture, state)
            scheduled.extend(schedule_audited_capture(reduced, profile))

        scheduled_by_rig[rig_id] = scheduled
        final_states[rig_id] = state

    validate_static_rig_feasibility(scheduled_by_rig)
    return merge_scheduled_operations(scheduled_by_rig), final_states


__all__ = [
    "CONTACT_MAX_FRAMES",
    "C3_CONTACT_MAX_EXPOSURE_S",
    "CONTACT_TRANSITION_LEAD_S",
    "build_anchor_first_capture_plan",
    "schedule_anchor_first_capture_plan",
]
