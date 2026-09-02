"""Pure execution-plan timeline compiler for the eclipse Sequencer.

This module does not touch hardware and does not sleep.

It expands eclipse circumstances + Photo Setup into deterministic capture
targets. Camera-specific preparation and USB latency compensation are added
in a later compilation stage.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Iterable


@dataclass(frozen=True)
class CaptureTarget:
    target_time: datetime
    phase: str
    phase_window: str
    sequence_index: int
    deadline: datetime | None = None


@dataclass(frozen=True)
class SequenceWindow:
    name: str
    phase: str
    start: datetime
    end: datetime
    interval_s: float | None


def _positive_number(value: Any, field: str) -> float:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or value <= 0
    ):
        raise ValueError(f"{field} must be > 0")
    return float(value)


def _nonnegative_number(value: Any, field: str) -> float:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or value < 0
    ):
        raise ValueError(f"{field} must be >= 0")
    return float(value)


def build_sequence_windows(
    timeline: dict[str, datetime],
    photo_config: dict[str, Any],
    *,
    sequence_margin_min: float = 60,
) -> list[SequenceWindow]:
    """Build the five canonical eclipse capture windows.

    Windows:
      PHASE 1A : START   -> C2-DR   partial
      PHASE 1B : C2-DR   -> C2      diamond_ring
      PHASE 2  : C2      -> C3      totality
      PHASE 3A : C3      -> C3+DR   diamond_ring
      PHASE 3B : C3+DR   -> END     partial

    START = C1 - sequence_margin
    END   = C4 + sequence_margin
    """

    for key in ("C1", "C2", "C3", "C4"):
        if key not in timeline or not isinstance(timeline[key], datetime):
            raise ValueError(f"timeline.{key} is required")

    phases = photo_config.get("phases")
    if not isinstance(phases, dict):
        raise ValueError("photo_config.phases is required")

    partial = phases.get("partial")
    diamond = phases.get("diamond_ring")
    totality = phases.get("totality")

    if not isinstance(partial, dict):
        raise ValueError("photo_config.phases.partial is required")
    if not isinstance(diamond, dict):
        raise ValueError("photo_config.phases.diamond_ring is required")
    if not isinstance(totality, dict):
        raise ValueError("photo_config.phases.totality is required")

    margin_s = (
        _nonnegative_number(
            sequence_margin_min,
            "sequence_margin_min",
        )
        * 60.0
    )

    dr_duration_s = _positive_number(
        diamond.get("duration_s", diamond.get("duration")),
        "diamond_ring.duration_s",
    )

    partial_interval_s = _positive_number(
        partial.get("interval_s", partial.get("interval")),
        "partial.interval_s",
    )

    diamond_interval_s = _positive_number(
        diamond.get("interval_s", diamond.get("interval")),
        "diamond_ring.interval_s",
    )

    totality_interval_s = _positive_number(
        totality.get("interval_s", totality.get("interval")),
        "totality.interval_s",
    )

    c1 = timeline["C1"]
    c2 = timeline["C2"]
    c3 = timeline["C3"]
    c4 = timeline["C4"]

    start = c1 - timedelta(seconds=margin_s)
    end = c4 + timedelta(seconds=margin_s)

    c2_dr = c2 - timedelta(seconds=dr_duration_s)
    c3_dr = c3 + timedelta(seconds=dr_duration_s)

    if not start <= c2_dr <= c2 <= c3 <= c3_dr <= end:
        raise ValueError("invalid Sequencer phase ordering")

    return [
        SequenceWindow(
            name="phase_1a",
            phase="partial",
            start=start,
            end=c2_dr,
            interval_s=partial_interval_s,
        ),
        SequenceWindow(
            name="phase_1b",
            phase="diamond_ring",
            start=c2_dr,
            end=c2,
            interval_s=diamond_interval_s,
        ),
        SequenceWindow(
            name="phase_2",
            phase="totality",
            start=c2,
            end=c3,
            interval_s=totality_interval_s,
        ),
        SequenceWindow(
            name="phase_3a",
            phase="diamond_ring",
            start=c3,
            end=c3_dr,
            interval_s=diamond_interval_s,
        ),
        SequenceWindow(
            name="phase_3b",
            phase="partial",
            start=c3_dr,
            end=end,
            interval_s=partial_interval_s,
        ),
    ]


def _periodic_targets(
    window: SequenceWindow,
) -> Iterable[CaptureTarget]:
    """Generate targets on [start, end), never crossing phase boundary."""

    assert window.interval_s is not None

    interval = timedelta(seconds=window.interval_s)
    target = window.start
    index = 0

    while target < window.end:
        next_target = target + interval

        yield CaptureTarget(
            target_time=target,
            phase=window.phase,
            phase_window=window.name,
            sequence_index=index,
            deadline=min(next_target, window.end),
        )

        index += 1
        target = next_target


def compile_capture_targets(
    timeline: dict[str, datetime],
    photo_config: dict[str, Any],
    *,
    sequence_margin_min: float = 60,
) -> list[CaptureTarget]:
    """Compile deterministic logical capture targets for the whole sequence.

    Totality is represented by one logical target at C2. The camera-specific
    prepared capture may then run/repeat according to the totality execution
    policy. We deliberately do not guess physical cycle timing here.
    """

    windows = build_sequence_windows(
        timeline,
        photo_config,
        sequence_margin_min=sequence_margin_min,
    )

    targets: list[CaptureTarget] = []

    for window in windows:
        targets.extend(_periodic_targets(window))

    targets.sort(
        key=lambda item: (
            item.target_time,
            item.phase_window,
            item.sequence_index,
        )
    )

    return targets


__all__ = [
    "CaptureTarget",
    "SequenceWindow",
    "build_sequence_windows",
    "compile_capture_targets",
]


# ---------------------------------------------------------------------------
# Per-RIG exposure materialization
# ---------------------------------------------------------------------------

from copy import deepcopy

from backend.preview_materializer import (
    apply_atmos_if_enabled,
    expand_executable_shutters,
    normalize_intent_plan,
    resolve_policy,
)
from backend.motion_exposure_policy import (
    compute_motion_exposure_ceiling,
    materialize_exposure_plan,
)


@dataclass(frozen=True)
class MaterializedRigCapture:
    rig_id: int
    backend: str
    target: CaptureTarget
    aperture: str | None
    iso_requested: int
    original_shutters: tuple[str, ...]
    final_exposure_plan: tuple[dict[str, Any], ...]
    atmos_applied: bool
    motion_policy: str
    motion_ceiling_s: float | None
    corrections: tuple[str, ...]
    warnings: tuple[str, ...]


def _camera_backend(rig: dict[str, Any]) -> str:
    devices = rig.get("devices")
    camera = (
        devices.get("camera")
        if isinstance(devices, dict)
        else None
    )

    if not isinstance(camera, dict):
        return ""

    return str(camera.get("backend") or "").strip().lower()


def sequencer_rig_is_active(rig: dict[str, Any]) -> bool:
    """RIG1 is mandatory; RIG2-4 require enabled=true."""

    rig_id = rig.get("rig_id")

    if rig_id == 1:
        return True

    return rig.get("enabled") is True


def apply_exposure_optimization(
    rig: dict[str, Any],
    exposure_opt_config: dict[str, Any],
) -> dict[str, Any]:
    """Apply Sequencer Exposure Optimization overrides to one RIG copy."""

    result = deepcopy(rig)
    photo = result.setdefault("photo", {})

    if not isinstance(photo, dict):
        raise ValueError("rig.photo must be an object")

    atmos = exposure_opt_config.get(
        "atmospheric_attenuation_enabled"
    )
    if atmos is not None:
        if not isinstance(atmos, bool):
            raise ValueError(
                "atmospheric_attenuation_enabled must be boolean"
            )
        photo["atmos_enabled"] = atmos

    rig_id = result.get("rig_id")

    overrides = exposure_opt_config.get("rigs", [])
    if overrides is None:
        overrides = []

    if not isinstance(overrides, list):
        raise ValueError("exposure optimization rigs must be an array")

    for item in overrides:
        if not isinstance(item, dict):
            continue

        if item.get("rig_id") != rig_id:
            continue

        override_photo = item.get("photo")
        if isinstance(override_photo, dict):
            photo.update(deepcopy(override_photo))

        break

    return result


def _phase_photo_config(
    photo_config: dict[str, Any],
    phase: str,
) -> dict[str, Any]:
    phases = photo_config.get("phases")

    if not isinstance(phases, dict):
        raise ValueError("photo_config.phases is required")

    cfg = phases.get(phase)

    if not isinstance(cfg, dict):
        raise ValueError(
            f"photo_config.phases.{phase} is required"
        )

    if cfg.get("enabled") is False:
        raise ValueError(f"photo phase {phase} is disabled")

    return cfg


def materialize_capture_target_for_rig(
    target: CaptureTarget,
    rig: dict[str, Any],
    photo_config: dict[str, Any],
    exposure_opt_config: dict[str, Any],
    eclipse_context: dict[str, Any],
    *,
    eclipse_config: dict[str, Any] | None = None,
) -> MaterializedRigCapture:
    """Materialize one logical Sequencer target for one RIG.

    This follows the same shared materialization helpers as Exposure
    Optimization preview.  It does not instantiate or touch a camera.
    """

    rig = apply_exposure_optimization(
        rig,
        exposure_opt_config,
    )

    rig_id = rig.get("rig_id")
    if not isinstance(rig_id, int) or isinstance(rig_id, bool):
        raise ValueError("rig_id must be an integer")

    backend = _camera_backend(rig)

    phase_cfg = _phase_photo_config(
        photo_config,
        target.phase,
    )

    iso = phase_cfg.get("iso")
    if (
        not isinstance(iso, int)
        or isinstance(iso, bool)
        or iso <= 0
    ):
        raise ValueError(
            f"photo_config.phases.{target.phase}.iso "
            "must be a positive integer"
        )

    intent = {
        "shutter_min": phase_cfg.get("shutter_min"),
        "shutter_max": phase_cfg.get("shutter_max"),
        "step_ev": phase_cfg.get("step_ev"),
        "speeds": phase_cfg.get("speeds"),
    }

    original_plan = normalize_intent_plan(intent)

    original_shutters = expand_executable_shutters(
        rig,
        original_plan,
    )

    plan, atmos_applied, _theoretical_slowest = (
        apply_atmos_if_enabled(
            rig,
            original_plan,
            target.target_time,
            eclipse_context,
        )
    )

    motion_policy = resolve_policy(rig)
    motion_ceiling_s = None
    corrections: list[str] = []
    warnings: list[str] = []

    physical_shutters = expand_executable_shutters(
        rig,
        plan,
    )

    exposure_plan = [
        {
            "shutter": str(speed),
            "iso": iso,
        }
        for speed in physical_shutters
    ]

    if motion_policy != "none":
        policy = deepcopy(rig)

        if eclipse_config is not None:
            policy["eclipse"] = deepcopy(eclipse_config)

        motion_ceiling_s = compute_motion_exposure_ceiling(
            policy,
            target.target_time,
        )

        if motion_ceiling_s is not None:
            _regular, _fastest, _slowest, step, _speeds = plan

            materialized = materialize_exposure_plan(
                speeds=physical_shutters,
                shutter_min=None,
                shutter_max=None,
                step_ev=step,
                iso_requested=iso,
                iso_max=int(
                    rig.get("photo", {}).get(
                        "iso_max",
                        6400,
                    )
                ),
                t_max=motion_ceiling_s,
                iso_compensation_enabled=(
                    rig.get("photo", {}).get(
                        "iso_compensation_enabled",
                        True,
                    )
                ),
            )

            exposure_plan = [
                {
                    "shutter": str(item["shutter"]),
                    "iso": int(item["iso"]),
                }
                for item in materialized["exposure_plan"]
            ]

            corrections = list(
                materialized.get("corrections", [])
            )
            warnings = list(
                materialized.get("warnings", [])
            )

    return MaterializedRigCapture(
        rig_id=rig_id,
        backend=backend,
        target=target,
        aperture=phase_cfg.get("aperture"),
        iso_requested=iso,
        original_shutters=tuple(original_shutters),
        final_exposure_plan=tuple(exposure_plan),
        atmos_applied=bool(atmos_applied),
        motion_policy=motion_policy,
        motion_ceiling_s=motion_ceiling_s,
        corrections=tuple(corrections),
        warnings=tuple(warnings),
    )


def materialize_capture_targets(
    targets: Iterable[CaptureTarget],
    rigs: Iterable[dict[str, Any]],
    photo_config: dict[str, Any],
    exposure_opt_config: dict[str, Any],
    eclipse_context: dict[str, Any],
    *,
    eclipse_config: dict[str, Any] | None = None,
) -> list[MaterializedRigCapture]:
    """Materialize every logical target for every active RIG."""

    active_rigs = [
        rig
        for rig in rigs
        if isinstance(rig, dict)
        and sequencer_rig_is_active(rig)
    ]

    result: list[MaterializedRigCapture] = []

    for target in targets:
        for rig in active_rigs:
            result.append(
                materialize_capture_target_for_rig(
                    target,
                    rig,
                    photo_config,
                    exposure_opt_config,
                    eclipse_context,
                    eclipse_config=eclipse_config,
                )
            )

    return result


# ---------------------------------------------------------------------------
# Camera-specific prepared/audited execution
# ---------------------------------------------------------------------------

from services.camera_service import CaptureIntent


@dataclass(frozen=True)
class AuditedRigCapture:
    rig_id: int
    backend: str
    target: CaptureTarget
    aperture: str | None
    exposure_plan: tuple[dict[str, Any], ...]
    prepared_mode: str
    estimated_total_s: float | None
    planned_count: int | None
    operations: tuple[dict[str, Any], ...]


def _capture_intent_from_materialized(
    capture: MaterializedRigCapture,
) -> CaptureIntent:
    """Convert the final per-RIG exposure plan into the runtime contract."""

    return CaptureIntent(
        shutter_min=None,
        shutter_max=None,
        step_ev=None,
        speeds=None,
        phase=capture.target.phase,
        target_time=capture.target.target_time,
        deadline=capture.target.deadline,
        overflow_policy="truncate",
        origin="sequencer",
        request_id=(
            f"sequencer-rig{capture.rig_id}-"
            f"{capture.target.phase_window}-"
            f"{capture.target.sequence_index}"
        ),
        exposure_plan=[
            {
                "shutter": str(item["shutter"]),
                "iso": int(item["iso"]),
            }
            for item in capture.final_exposure_plan
        ],
    )


def audit_materialized_sony_capture(
    capture: MaterializedRigCapture,
) -> AuditedRigCapture:
    """Prepare and audit one Sony capture without touching hardware."""

    if capture.backend != "sony":
        raise ValueError(
            f"expected Sony backend, got {capture.backend!r}"
        )

    # Lazy import keeps the pure Sequencer module usable without gphoto2.
    from plugins.camera.sony import SonyPlugin

    plugin = SonyPlugin(
        None,
        lambda *_args, **_kwargs: None,
    )

    intent = _capture_intent_from_materialized(capture)

    prepared = plugin.prepare_capture(intent)
    operations = plugin.audit_prepared_capture(prepared)

    mode = (
        str(prepared.token[0])
        if isinstance(prepared.token, tuple) and prepared.token
        else "unknown"
    )

    return AuditedRigCapture(
        rig_id=capture.rig_id,
        backend=capture.backend,
        target=capture.target,
        aperture=capture.aperture,
        exposure_plan=tuple(
            {
                "shutter": str(item["shutter"]),
                "iso": int(item["iso"]),
            }
            for item in capture.final_exposure_plan
        ),
        prepared_mode=mode,
        estimated_total_s=prepared.estimated_total_s,
        planned_count=prepared.planned_count,
        operations=tuple(
            deepcopy(operation)
            for operation in operations
        ),
    )


def audit_materialized_nikon_capture(
    capture: MaterializedRigCapture,
) -> AuditedRigCapture:
    """Prepare and audit one Nikon capture without touching hardware."""

    if capture.backend not in {"nikon", "nikon-dslr", "nikon-z"}:
        raise ValueError(
            f"expected Nikon backend, got {capture.backend!r}"
        )

    from plugins.camera.nikon import NikonDSLRPlugin

    plugin = NikonDSLRPlugin(
        None,
        lambda *_args, **_kwargs: None,
    )

    intent = _capture_intent_from_materialized(capture)

    prepared = plugin.prepare_capture(intent)
    operations = plugin.audit_prepared_capture(prepared)

    mode = (
        str(prepared.token[0])
        if isinstance(prepared.token, tuple) and prepared.token
        else "unknown"
    )

    return AuditedRigCapture(
        rig_id=capture.rig_id,
        backend=capture.backend,
        target=capture.target,
        aperture=capture.aperture,
        exposure_plan=tuple(
            {
                "shutter": str(item["shutter"]),
                "iso": int(item["iso"]),
            }
            for item in capture.final_exposure_plan
        ),
        prepared_mode=mode,
        estimated_total_s=prepared.estimated_total_s,
        planned_count=prepared.planned_count,
        operations=tuple(
            deepcopy(operation)
            for operation in operations
        ),
    )


def audit_materialized_capture(
    capture: MaterializedRigCapture,
) -> AuditedRigCapture:
    """Dispatch a materialized RIG capture to its offline camera compiler."""

    if capture.backend == "sony":
        return audit_materialized_sony_capture(capture)

    if capture.backend in {"nikon", "nikon-dslr", "nikon-z"}:
        return audit_materialized_nikon_capture(capture)

    raise ValueError(
        f"Sequencer audit backend not implemented: "
        f"{capture.backend or 'none'}"
    )


# ---------------------------------------------------------------------------
# Device timing / command scheduling
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CameraTimingProfile:
    """Measured command timing for one camera backend.

    Values are milliseconds.

    trigger_latency_ms is the delay between sending the physical trigger
    command and the desired physical start of exposure.

    set_* values are reservation durations used to schedule preparation
    commands far enough ahead of the trigger command.
    """

    backend: str

    set_iso_ms: float = 0.0
    set_capturemode_ms: float = 0.0
    set_shutter_ms: float = 0.0

    trigger_single_latency_ms: float = 0.0
    trigger_single_duration_ms: float = 0.0
    bracket_press_latency_ms: float = 0.0

    bracket_release_ms: float = 0.0
    settle_idle_ms: float = 0.0


@dataclass(frozen=True)
class ScheduledOperation:
    rig_id: int
    backend: str
    phase: str
    phase_window: str
    sequence_index: int

    target_time: datetime
    command_time: datetime | None

    timing_relation: str
    operation: dict[str, Any]
    duration_ms: float = 0.0


def _timing_ms(value: Any, field: str) -> float:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
    ):
        raise ValueError(f"{field} must be numeric")

    value = float(value)

    if value < 0:
        raise ValueError(f"{field} must be >= 0")

    return value


def _set_operation_duration_ms(
    operation: dict[str, Any],
    profile: CameraTimingProfile,
) -> float:
    parameter = operation.get("parameter")

    if parameter == "iso":
        return _timing_ms(
            profile.set_iso_ms,
            "set_iso_ms",
        )

    if parameter == "capturemode":
        return _timing_ms(
            profile.set_capturemode_ms,
            "set_capturemode_ms",
        )

    if parameter in ("shutterspeed", "shutterspeed2"):
        return _timing_ms(
            profile.set_shutter_ms,
            "set_shutter_ms",
        )

    return 0.0


def _operation_reservation_duration_ms(
    backend: str,
    operation: dict[str, Any],
    profile: CameraTimingProfile,
) -> float:
    """Return statically known blocking/reservation duration."""

    action = operation.get("action")

    if action == "set":
        return _set_operation_duration_ms(
            operation,
            profile,
        )

    if action == "delay":
        return _timing_ms(
            operation.get("duration_ms"),
            "delay duration_ms",
        )

    if (
        action == "trigger_capture"
        and backend in {"nikon", "nikon-dslr", "nikon-z"}
    ):
        return _timing_ms(
            profile.trigger_single_duration_ms,
            "trigger_single_duration_ms",
        )

    # Sony native bracket post-trigger work and any other operation whose
    # blocking duration is not statically known are deliberately not guessed.
    return 0.0


def schedule_audited_capture(
    capture: AuditedRigCapture,
    profile: CameraTimingProfile,
) -> list[ScheduledOperation]:
    """Assign command times around one physical capture target.

    PREPARE operations are scheduled backwards from the triggering command.

    Sony bracket:
        bracket_press / bulb=1 is the trigger anchor.

    Single:
        trigger_capture is the trigger anchor.

    Post-trigger operations are retained in execution order. Their exact
    runtime timestamps cannot be known statically because frame delivery and
    camera busy time are runtime-dependent.
    """

    if profile.backend != capture.backend:
        raise ValueError(
            f"timing profile backend {profile.backend!r} "
            f"does not match capture backend {capture.backend!r}"
        )

    operations = [
        deepcopy(item)
        for item in capture.operations
    ]

    trigger_index = None
    trigger_latency_ms = None

    for index, operation in enumerate(operations):
        action = operation.get("action")

        if action == "bracket_press":
            trigger_index = index
            trigger_latency_ms = _timing_ms(
                profile.bracket_press_latency_ms,
                "bracket_press_latency_ms",
            )
            break

        if action == "trigger_capture":
            trigger_index = index
            trigger_latency_ms = _timing_ms(
                profile.trigger_single_latency_ms,
                "trigger_single_latency_ms",
            )
            break

    if trigger_index is None:
        raise ValueError(
            "audited capture contains no physical trigger operation"
        )

    target_time = capture.target.target_time

    trigger_command_time = (
        target_time
        - timedelta(milliseconds=trigger_latency_ms)
    )

    command_times: list[datetime | None] = [
        None
        for _ in operations
    ]

    command_times[trigger_index] = trigger_command_time

    # Schedule preparation backwards.
    cursor = trigger_command_time

    for index in range(trigger_index - 1, -1, -1):
        operation = operations[index]
        action = operation.get("action")

        if action == "segment":
            command_times[index] = cursor
            continue

        if action != "set":
            raise ValueError(
                "unsupported pre-trigger audited operation: "
                f"{action!r}"
            )

        duration_ms = _set_operation_duration_ms(
            operation,
            profile,
        )

        cursor = (
            cursor
            - timedelta(milliseconds=duration_ms)
        )

        command_times[index] = cursor

    # Nikon executes exposure plans strictly photo-by-photo:
    #
    #   trigger_capture()
    #   sleep(50 ms)
    #   SET next shutter / ISO
    #   trigger_capture()
    #   ...
    #
    # Unlike Sony native bracket post-trigger activity, this sequence is
    # deterministic from the measured blocking command durations.  Keep the
    # first physical trigger anchored to target_time, then schedule every
    # following Nikon operation forwards in exact execution order.
    if capture.backend in {"nikon", "nikon-dslr", "nikon-z"}:
        cursor = (
            trigger_command_time
            + timedelta(
                milliseconds=_timing_ms(
                    profile.trigger_single_duration_ms,
                    "trigger_single_duration_ms",
                )
            )
        )

        for index in range(trigger_index + 1, len(operations)):
            operation = operations[index]
            action = operation.get("action")

            command_times[index] = cursor

            if action == "set":
                duration_ms = _set_operation_duration_ms(
                    operation,
                    profile,
                )

            elif action == "delay":
                duration_ms = _timing_ms(
                    operation.get("duration_ms"),
                    "delay duration_ms",
                )

            elif action == "trigger_capture":
                duration_ms = _timing_ms(
                    profile.trigger_single_duration_ms,
                    "trigger_single_duration_ms",
                )

            else:
                raise ValueError(
                    "unsupported Nikon post-trigger audited operation: "
                    f"{action!r}"
                )

            cursor = (
                cursor
                + timedelta(milliseconds=duration_ms)
            )

    result: list[ScheduledOperation] = []

    for index, operation in enumerate(operations):
        if index < trigger_index:
            relation = "prepare"
        elif index == trigger_index:
            relation = "trigger"
        else:
            relation = "post_trigger"

        result.append(
            ScheduledOperation(
                rig_id=capture.rig_id,
                backend=capture.backend,
                phase=capture.target.phase,
                phase_window=capture.target.phase_window,
                sequence_index=capture.target.sequence_index,
                target_time=target_time,
                command_time=command_times[index],
                timing_relation=relation,
                operation=operation,
                duration_ms=_operation_reservation_duration_ms(
                    capture.backend,
                    operation,
                    profile,
                ),
            )
        )

    return result


def schedule_audited_captures(
    captures: Iterable[AuditedRigCapture],
    timing_profiles: dict[str, CameraTimingProfile],
) -> list[ScheduledOperation]:
    """Schedule multiple audited RIG captures."""

    result: list[ScheduledOperation] = []

    for capture in captures:
        profile = timing_profiles.get(capture.backend)

        if profile is None:
            raise ValueError(
                f"missing camera timing profile for "
                f"{capture.backend or 'none'}"
            )

        result.extend(
            schedule_audited_capture(
                capture,
                profile,
            )
        )

    return result


# ---------------------------------------------------------------------------
# Stateful command reduction
# ---------------------------------------------------------------------------

from dataclasses import replace


_STATEFUL_SET_PARAMETERS = {
    "sony": frozenset({
        "iso",
        "capturemode",
        "shutterspeed",
    }),
    "nikon": frozenset({
        "iso",
        "shutterspeed2",
    }),
    "nikon-dslr": frozenset({
        "iso",
        "shutterspeed2",
    }),
    "nikon-z": frozenset({
        "iso",
        "shutterspeed2",
    }),
}


def _normalize_camera_state(
    state: dict[str, Any] | None,
) -> dict[str, str]:
    """Normalize a Trigger-provided initial camera state."""

    if state is None:
        return {}

    if not isinstance(state, dict):
        raise ValueError("camera initial state must be an object")

    return {
        str(key): str(value)
        for key, value in state.items()
        if value is not None
    }


def reduce_audited_capture_operations(
    capture: AuditedRigCapture,
    camera_state: dict[str, Any] | None,
) -> tuple[AuditedRigCapture, dict[str, str]]:
    """Remove physical SET commands that would not change camera state.

    The input state is the state guaranteed by Trigger before TSTART, or the
    state resulting from the previous Sequencer capture.

    Only explicitly stateful parameters for the backend are eligible for
    removal. Trigger commands and runtime synchronization operations are
    never removed.
    """

    state = _normalize_camera_state(camera_state)

    stateful = _STATEFUL_SET_PARAMETERS.get(
        capture.backend,
        frozenset(),
    )

    reduced: list[dict[str, Any]] = []

    operations = list(capture.operations)

    for index, raw_operation in enumerate(operations):
        operation = deepcopy(raw_operation)

        if operation.get("action") != "set":
            reduced.append(operation)
            continue

        parameter = str(operation.get("parameter") or "")
        value = str(operation.get("value"))

        if parameter not in stateful:
            reduced.append(operation)
            continue

        # Sony native bracket preparation has a physical protocol:
        #
        #   Single Shot
        #   SET centre shutterspeed
        #   Continuous Bracket ...
        #
        # The centre shutter SET must be issued while the camera is in
        # Single Shot mode even when its logical value is unchanged.
        # Do not let generic state reduction remove that transaction.
        sony_bracket_centre_set = False

        if (
            capture.backend == "sony"
            and parameter == "shutterspeed"
            and index > 0
            and index + 1 < len(operations)
        ):
            previous = operations[index - 1]
            following = operations[index + 1]

            sony_bracket_centre_set = (
                previous.get("action") == "set"
                and previous.get("parameter") == "capturemode"
                and str(previous.get("value")) == "Single Shot"
                and following.get("action") == "set"
                and following.get("parameter") == "capturemode"
                and str(following.get("value")).startswith(
                    "Continuous Bracket "
                )
            )

        if (
            state.get(parameter) == value
            and not sony_bracket_centre_set
        ):
            # No USB transaction required.
            continue

        reduced.append(operation)
        state[parameter] = value

    return (
        replace(
            capture,
            operations=tuple(reduced),
        ),
        state,
    )


def reduce_audited_captures(
    captures: Iterable[AuditedRigCapture],
    initial_states: dict[int, dict[str, Any]] | None = None,
) -> tuple[list[AuditedRigCapture], dict[int, dict[str, str]]]:
    """Reduce a complete ordered execution plan using per-RIG camera state.

    Captures for each RIG must be supplied in chronological order.
    """

    initial_states = initial_states or {}

    if not isinstance(initial_states, dict):
        raise ValueError("initial_states must be an object")

    states: dict[int, dict[str, str]] = {
        int(rig_id): _normalize_camera_state(state)
        for rig_id, state in initial_states.items()
    }

    last_target_by_rig: dict[int, datetime] = {}
    result: list[AuditedRigCapture] = []

    for capture in captures:
        previous_target = last_target_by_rig.get(capture.rig_id)

        if (
            previous_target is not None
            and capture.target.target_time < previous_target
        ):
            raise ValueError(
                f"captures for RIG {capture.rig_id} "
                "must be chronologically ordered"
            )

        reduced, new_state = reduce_audited_capture_operations(
            capture,
            states.get(capture.rig_id),
        )

        states[capture.rig_id] = new_state
        last_target_by_rig[capture.rig_id] = (
            capture.target.target_time
        )

        result.append(reduced)

    return result, states


# ---------------------------------------------------------------------------
# Multi-RIG merge
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GlobalExecutionEvent:
    rig_id: int
    backend: str
    phase: str
    phase_window: str
    sequence_index: int

    target_time: datetime
    command_time: datetime | None

    timing_relation: str
    operation_index: int
    operation: dict[str, Any]
    duration_ms: float = 0.0


def merge_scheduled_operations(
    scheduled_by_rig: dict[int, Iterable[ScheduledOperation]],
) -> list[GlobalExecutionEvent]:
    """Merge independently scheduled RIG timelines into one global plan.

    RIG scheduling remains independent. Equal command times are preserved:
    the merge does not serialize simultaneous commands artificially.
    """

    events: list[GlobalExecutionEvent] = []

    for rig_id, scheduled in scheduled_by_rig.items():
        if (
            not isinstance(rig_id, int)
            or isinstance(rig_id, bool)
            or rig_id <= 0
        ):
            raise ValueError("scheduled_by_rig keys must be positive integers")

        for operation_index, item in enumerate(scheduled):
            if item.rig_id != rig_id:
                raise ValueError(
                    f"scheduled operation RIG mismatch: "
                    f"key={rig_id}, event={item.rig_id}"
                )

            events.append(
                GlobalExecutionEvent(
                    rig_id=item.rig_id,
                    backend=item.backend,
                    phase=item.phase,
                    phase_window=item.phase_window,
                    sequence_index=item.sequence_index,
                    target_time=item.target_time,
                    command_time=item.command_time,
                    timing_relation=item.timing_relation,
                    operation_index=operation_index,
                    operation=deepcopy(item.operation),
                    duration_ms=item.duration_ms,
                )
            )

    def sort_key(event: GlobalExecutionEvent):
        # Runtime/post-trigger events without static time stay after all
        # statically scheduled commands. Their local order is preserved.
        if event.command_time is None:
            return (
                1,
                event.target_time,
                event.rig_id,
                event.sequence_index,
                event.operation_index,
            )

        return (
            0,
            event.command_time,
            event.rig_id,
            event.sequence_index,
            event.operation_index,
        )

    events.sort(key=sort_key)

    return events


def validate_static_rig_feasibility(
    scheduled_by_rig: dict[int, Iterable[ScheduledOperation]],
) -> None:
    """Reject statically proven command overlaps within one RIG.

    Different RIGs are independent and are intentionally not compared.
    Operations without a static command_time are runtime-dependent and cannot
    participate in this compile-time feasibility check.
    """

    for rig_id, scheduled in scheduled_by_rig.items():
        timed = [
            item
            for item in scheduled
            if item.command_time is not None
        ]

        timed.sort(
            key=lambda item: (
                item.command_time,
                item.sequence_index,
            )
        )

        reserved_until: datetime | None = None
        reserving_operation: ScheduledOperation | None = None

        for item in timed:
            duration_ms = _timing_ms(
                item.duration_ms,
                "scheduled duration_ms",
            )

            if (
                reserved_until is not None
                and item.command_time < reserved_until
            ):
                previous_action = (
                    reserving_operation.operation.get("action")
                    if reserving_operation is not None
                    else "unknown"
                )

                raise ValueError(
                    f"static command overlap for RIG {rig_id}: "
                    f"{item.operation.get('action')!r} at "
                    f"{item.command_time.isoformat(timespec='milliseconds')} "
                    f"starts before previous {previous_action!r} reservation "
                    f"ends at "
                    f"{reserved_until.isoformat(timespec='milliseconds')}"
                )

            end_time = (
                item.command_time
                + timedelta(milliseconds=duration_ms)
            )

            if (
                reserved_until is None
                or end_time > reserved_until
            ):
                reserved_until = end_time
                reserving_operation = item


def compile_and_merge_scheduled_rigs(
    audited_by_rig: dict[int, Iterable[AuditedRigCapture]],
    initial_states: dict[int, dict[str, Any]],
    timing_profiles: dict[str, CameraTimingProfile],
) -> tuple[
    list[GlobalExecutionEvent],
    dict[int, dict[str, str]],
]:
    """Reduce, schedule and merge already-audited captures per RIG.

    Each RIG is processed independently before the global merge.
    """

    scheduled_by_rig: dict[int, list[ScheduledOperation]] = {}
    final_states: dict[int, dict[str, str]] = {}

    for rig_id in sorted(audited_by_rig):
        captures = list(audited_by_rig[rig_id])

        for capture in captures:
            if capture.rig_id != rig_id:
                raise ValueError(
                    f"audited capture RIG mismatch: "
                    f"key={rig_id}, capture={capture.rig_id}"
                )

        reduced, states = reduce_audited_captures(
            captures,
            {
                rig_id: initial_states.get(rig_id, {}),
            },
        )

        final_states[rig_id] = states.get(rig_id, {})

        scheduled: list[ScheduledOperation] = []

        for capture in reduced:
            profile = timing_profiles.get(capture.rig_id)
            if profile is None:
                profile = timing_profiles.get(capture.backend)

            if profile is None:
                raise ValueError(
                    f"missing camera timing profile for "
                    f"{capture.backend or 'none'}"
                )

            scheduled.extend(
                schedule_audited_capture(
                    capture,
                    profile,
                )
            )

        scheduled_by_rig[rig_id] = scheduled

    validate_static_rig_feasibility(
        scheduled_by_rig
    )

    return (
        merge_scheduled_operations(scheduled_by_rig),
        final_states,
    )


# ---------------------------------------------------------------------------
# Canonical execution-plan document
# ---------------------------------------------------------------------------


def _isoformat_ms(value: datetime | None) -> str | None:
    if value is None:
        return None

    return value.isoformat(timespec="milliseconds")


def build_execution_plan_document(
    events: Iterable[GlobalExecutionEvent],
    *,
    initial_states: dict[int, dict[str, Any]],
    final_states: dict[int, dict[str, Any]] | None = None,
    circumstances_file: str | None = None,
    photo_setup_file: str | None = None,
    exposure_opt_file: str | None = None,
    sequence_start: datetime | None = None,
    sequence_end: datetime | None = None,
) -> dict[str, Any]:
    """Build the canonical, JSON-serializable Sequencer execution plan.

    This document is an execution description only. It does not initialize
    cameras and does not execute any command.
    """

    event_list = list(events)

    if not event_list:
        raise ValueError("execution plan contains no events")

    normalized_initial_states = {
        int(rig_id): _normalize_camera_state(state)
        for rig_id, state in initial_states.items()
    }

    normalized_final_states = {
        int(rig_id): _normalize_camera_state(state)
        for rig_id, state in (final_states or {}).items()
    }

    # One logical target per RIG/capture occurrence.
    target_keys: set[tuple[Any, ...]] = set()
    targets: list[dict[str, Any]] = []

    for event in event_list:
        key = (
            event.rig_id,
            event.phase_window,
            event.sequence_index,
            event.target_time,
        )

        if key in target_keys:
            continue

        target_keys.add(key)

        targets.append({
            "target_time": _isoformat_ms(event.target_time),
            "rig_id": event.rig_id,
            "backend": event.backend,
            "phase": event.phase,
            "phase_window": event.phase_window,
            "sequence_index": event.sequence_index,
        })

    targets.sort(
        key=lambda item: (
            item["target_time"],
            item["rig_id"],
            item["sequence_index"],
        )
    )

    serialized_events: list[dict[str, Any]] = []

    for event in event_list:
        serialized_events.append({
            "command_time": _isoformat_ms(event.command_time),
            "target_time": _isoformat_ms(event.target_time),
            "rig_id": event.rig_id,
            "backend": event.backend,
            "phase": event.phase,
            "phase_window": event.phase_window,
            "sequence_index": event.sequence_index,
            "timing_relation": event.timing_relation,
            "operation_index": event.operation_index,
            "operation": deepcopy(event.operation),
            "duration_ms": event.duration_ms,
        })

    target_start = min(
        event.target_time
        for event in event_list
    )

    target_end = max(
        event.target_time
        for event in event_list
    )

    # Direct callers that do not provide the logical Sequencer window retain
    # the historical target-bound behaviour.  The complete plan service
    # provides the canonical C1-margin / C4+margin boundaries.
    if sequence_start is None:
        sequence_start = target_start

    if sequence_end is None:
        sequence_end = target_end

    absolute_command_times = [
        event.command_time
        for event in event_list
        if event.command_time is not None
    ]

    command_start = (
        min(absolute_command_times)
        if absolute_command_times
        else None
    )

    command_end = (
        max(absolute_command_times)
        if absolute_command_times
        else None
    )

    return {
        "schema_version": 1,
        "config_type": "execution_plan",

        "sources": {
            "circumstances_file": circumstances_file,
            "photo_setup_file": photo_setup_file,
            "exposure_opt_file": exposure_opt_file,
        },

        # Trigger must establish these states before TSTART.
        # These are requirements, not Sequencer commands.
        "initial_state_required": {
            str(rig_id): deepcopy(state)
            for rig_id, state in sorted(
                normalized_initial_states.items()
            )
        },

        "sequence_start": _isoformat_ms(sequence_start),
        "sequence_end": _isoformat_ms(sequence_end),

        "target_start": _isoformat_ms(target_start),
        "target_end": _isoformat_ms(target_end),

        "command_start": _isoformat_ms(command_start),
        "command_end": _isoformat_ms(command_end),

        "targets": targets,

        "events": serialized_events,

        # Mainly useful for audit/debug and deterministic tests.
        "final_state_expected": {
            str(rig_id): deepcopy(state)
            for rig_id, state in sorted(
                normalized_final_states.items()
            )
        },
    }


def format_execution_plan_lines(
    plan: dict[str, Any],
) -> list[str]:
    """Render a compact human-readable audit of an execution plan."""

    if plan.get("config_type") != "execution_plan":
        raise ValueError("not an execution plan")

    lines: list[str] = []

    targets_by_key = {
        (
            item["rig_id"],
            item["phase_window"],
            item["sequence_index"],
        ): item
        for item in plan.get("targets", [])
    }

    emitted_targets: set[tuple[Any, ...]] = set()

    events = list(plan.get("events", []))

    # The canonical global event list keeps runtime-dependent Sony
    # post-trigger operations (command_time=None) after all statically
    # scheduled commands.  That ordering is useful for the canonical
    # document but misleading for a human audit: EXPECT_FRAMES,
    # BRACKET_RELEASE and SETTLE_IDLE appear detached from their capture.
    #
    # For display only, restore each runtime operation immediately after
    # the trigger/capture it belongs to.  Do not mutate the execution plan.
    runtime_by_capture: dict[tuple[Any, ...], list[dict[str, Any]]] = {}

    for event in events:
        if event.get("command_time") is not None:
            continue

        key = (
            event["rig_id"],
            event["phase_window"],
            event["sequence_index"],
        )
        runtime_by_capture.setdefault(key, []).append(event)

    display_events: list[dict[str, Any]] = []

    for event in events:
        if event.get("command_time") is None:
            continue

        display_events.append(event)

        if str(event.get("timing_relation") or "").lower() != "trigger":
            continue

        key = (
            event["rig_id"],
            event["phase_window"],
            event["sequence_index"],
        )

        display_events.extend(
            runtime_by_capture.pop(key, [])
        )

    # Defensive fallback for malformed/unusual plans whose runtime
    # operations have no corresponding statically scheduled trigger.
    for remaining in runtime_by_capture.values():
        display_events.extend(remaining)

    for event in display_events:
        rig_id = event["rig_id"]
        backend = str(event["backend"]).upper()
        relation = str(event["timing_relation"]).upper()
        operation = event["operation"]

        command_time = event.get("command_time")

        if command_time is None:
            clock = "RUNTIME"
        else:
            clock = command_time[11:23]

        action = str(operation.get("action") or "").upper()

        if action == "SET":
            detail = (
                f'SET {operation.get("parameter")}='
                f'{operation.get("value")}'
            )

        elif action == "BRACKET_PRESS":
            detail = (
                f'BRACKET PRESS {operation.get("parameter")}'
                f'={operation.get("value")} '
                f'centre={operation.get("centre")} '
                f'step={operation.get("step_ev")}EV '
                f'frames={operation.get("frames")}'
            )

        elif action == "EXPECT_FRAMES":
            detail = (
                f'EXPECT {operation.get("count")} FRAMES'
            )

        elif action == "BRACKET_RELEASE":
            detail = (
                f'BRACKET RELEASE {operation.get("parameter")}'
                f'={operation.get("value")}'
            )

        elif action == "TRIGGER_CAPTURE":
            detail = (
                f'TRIGGER CAPTURE '
                f'{operation.get("shutter")} '
                f'ISO{operation.get("iso")}'
            )

        elif action == "SETTLE_IDLE":
            detail = "SETTLE IDLE"

        elif action == "SEGMENT":
            detail = (
                f'SEGMENT {operation.get("description")}'
            )

        else:
            detail = action

        lines.append(
            f"{clock} | RIG{rig_id} | {backend} | "
            f"{relation} | {detail}"
        )

        key = (
            rig_id,
            event["phase_window"],
            event["sequence_index"],
        )

        # Emit the physical target marker immediately after its trigger.
        if (
            relation == "TRIGGER"
            and key not in emitted_targets
            and key in targets_by_key
        ):
            target = targets_by_key[key]
            target_clock = target["target_time"][11:23]

            lines.append(
                f"{target_clock} | RIG{rig_id} | TARGET | "
                f'{target["phase"].upper()}'
            )

            emitted_targets.add(key)

    return lines


# ---------------------------------------------------------------------------
# Initial state requirements supplied by Trigger
# ---------------------------------------------------------------------------


def derive_initial_state_required(
    captures_by_rig: dict[int, Iterable[AuditedRigCapture]],
) -> dict[int, dict[str, str]]:
    """Derive the camera state Trigger should establish before TSTART.

    We inspect the first capture of each RIG and absorb every stateful SET
    that can safely be established in advance.

    Trigger is responsible for creating this state. These are requirements,
    never execution-plan events.
    """

    result: dict[int, dict[str, str]] = {}

    for rig_id in sorted(captures_by_rig):
        captures = list(captures_by_rig[rig_id])

        if not captures:
            continue

        first = captures[0]

        stateful = _STATEFUL_SET_PARAMETERS.get(
            first.backend,
            frozenset(),
        )

        state: dict[str, str] = {}

        for operation in first.operations:
            if operation.get("action") != "set":
                continue

            parameter = str(operation.get("parameter") or "")

            if parameter not in stateful:
                continue

            value = str(operation.get("value"))

            # Keep only the first useful value of each parameter.
            #
            # For Sony bracket preparation this deliberately means:
            #   capturemode = Single Shot
            # rather than the later Continuous Bracket mode.
            state.setdefault(parameter, value)

        result[rig_id] = state

    return result
