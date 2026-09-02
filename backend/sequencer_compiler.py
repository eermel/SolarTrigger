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
            interval_s=None,
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
        if window.phase == "totality":
            # Totality is one continuous execution window:
            # C2 -> C3. Physical camera timings decide how many
            # SET/PHOTO commands fit inside it.
            targets.append(
                CaptureTarget(
                    target_time=window.start,
                    phase=window.phase,
                    phase_window=window.name,
                    sequence_index=0,
                    deadline=window.end,
                )
            )
        else:
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

    # Total blocking time of one native Sony bracket PHOTO, measured from
    # dispatch of bulb=1 until the camera has completed frame delivery,
    # bulb=0 and settle-idle. Keys are physical frame counts (3/5/7/9).
    bracket_atomic_ms_by_frames: dict[int, float] | None = None


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

    if action == "settle_idle":
        return _timing_ms(
            profile.settle_idle_ms,
            "settle_idle_ms",
        )

    if action == "bracket_press" and backend == "sony":
        raw_frames = operation.get("frames")

        if isinstance(raw_frames, bool):
            raise ValueError(
                "Sony native bracket frames must be an integer"
            )

        try:
            frames = int(raw_frames)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "Sony native bracket frames must be an integer"
            ) from exc

        durations = profile.bracket_atomic_ms_by_frames or {}

        if frames not in durations:
            raise ValueError(
                f"no calibrated Sony native bracket duration "
                f"for {frames} frames"
            )

        return _timing_ms(
            durations[frames],
            f"bracket_atomic_ms_by_frames[{frames}]",
        )

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

    # Exact-single sequences are deterministic from the measured USB
    # timings.  Calculate their complete timeline here.  Timing helpers such
    # as DELAY / SETTLE_IDLE advance the clock but will not become Trigger
    # commands in the final execution plan.
    #
    # Sony native bracket is different: bracket_press is one high-level PHOTO
    # command and its expect/release/idle protocol remains private to the
    # Sony plugin.
    trigger_action = operations[trigger_index].get("action")

    deterministic_post_trigger = (
        capture.backend in {"nikon", "nikon-dslr", "nikon-z"}
        or (
            capture.backend == "sony"
            and trigger_action == "trigger_capture"
        )
    )

    if deterministic_post_trigger:
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

            elif action == "settle_idle":
                duration_ms = _timing_ms(
                    profile.settle_idle_ms,
                    "settle_idle_ms",
                )

            elif action == "trigger_capture":
                duration_ms = _timing_ms(
                    profile.trigger_single_duration_ms,
                    "trigger_single_duration_ms",
                )

            elif action == "segment":
                duration_ms = 0.0

            else:
                raise ValueError(
                    "unsupported deterministic post-trigger operation: "
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


def _scheduled_static_bounds(
    scheduled: Iterable[ScheduledOperation],
) -> tuple[datetime, datetime]:
    """Return first command time and end of the last reserved operation."""

    timed = [
        item
        for item in scheduled
        if item.command_time is not None
    ]

    if not timed:
        raise ValueError("capture contains no statically scheduled command")

    start = min(
        item.command_time
        for item in timed
    )

    end = max(
        item.command_time
        + timedelta(milliseconds=item.duration_ms)
        for item in timed
    )

    return start, end


def _split_totality_single_photos(
    capture: AuditedRigCapture,
) -> list[AuditedRigCapture]:
    """Split TOTALITY into independently schedulable physical PHOTO units.

    A PHOTO unit can produce either:

      * one image through trigger_capture, or
      * N images through one native Sony bracket_press.

    exposure_plan describes the resulting physical images, whereas the
    Trigger plan contains one PHOTO command per physical camera operation.
    """

    operations = list(capture.operations)
    exposures = list(capture.exposure_plan)

    trigger_indexes = [
        index
        for index, operation in enumerate(operations)
        if operation.get("action") in {
            "trigger_capture",
            "bracket_press",
        }
    ]

    if not trigger_indexes:
        raise ValueError(
            f"totality contains no photo for RIG {capture.rig_id}"
        )

    result: list[AuditedRigCapture] = []

    operation_cursor = 0
    exposure_cursor = 0

    for trigger_index in trigger_indexes:
        trigger = operations[trigger_index]
        trigger_action = trigger.get("action")

        if trigger_action == "bracket_press":
            raw_frames = trigger.get("frames")

            if isinstance(raw_frames, bool):
                raise ValueError(
                    f"invalid Sony bracket frame count for "
                    f"RIG {capture.rig_id}"
                )

            try:
                frame_count = int(raw_frames)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"invalid Sony bracket frame count for "
                    f"RIG {capture.rig_id}"
                ) from exc

            if frame_count <= 0:
                raise ValueError(
                    f"invalid Sony bracket frame count for "
                    f"RIG {capture.rig_id}"
                )

            physical_views = trigger.get("physical_views")

            if (
                physical_views is not None
                and len(physical_views) != frame_count
            ):
                raise ValueError(
                    f"Sony bracket physical view mismatch for "
                    f"RIG {capture.rig_id}"
                )

        else:
            frame_count = 1

        exposure_end = exposure_cursor + frame_count

        if exposure_end > len(exposures):
            raise ValueError(
                f"totality exposure/trigger mismatch for "
                f"RIG {capture.rig_id}"
            )

        operation_end = trigger_index + 1

        if trigger_action == "bracket_press":
            # Runtime-private tail of the atomic Sony PHOTO.
            while (
                operation_end < len(operations)
                and operations[operation_end].get("action") in {
                    "expect_frames",
                    "bracket_release",
                    "settle_idle",
                }
            ):
                operation_end += 1

        else:
            # Timing helpers belonging to one ordinary exposure.
            while (
                operation_end < len(operations)
                and operations[operation_end].get("action") in {
                    "delay",
                    "settle_idle",
                }
            ):
                operation_end += 1

        unit_operations = tuple(
            deepcopy(operation)
            for operation in operations[
                operation_cursor:operation_end
            ]
        )

        unit_exposures = tuple(
            deepcopy(exposure)
            for exposure in exposures[
                exposure_cursor:exposure_end
            ]
        )

        result.append(
            replace(
                capture,
                exposure_plan=unit_exposures,
                planned_count=frame_count,
                operations=unit_operations,
            )
        )

        operation_cursor = operation_end
        exposure_cursor = exposure_end

    if exposure_cursor != len(exposures):
        raise ValueError(
            f"totality exposure/trigger mismatch for "
            f"RIG {capture.rig_id}"
        )

    return result


def _profile_for_capture(
    capture: AuditedRigCapture,
    timing_profiles: dict[Any, CameraTimingProfile],
) -> CameraTimingProfile:
    profile = timing_profiles.get(capture.rig_id)

    if profile is None:
        profile = timing_profiles.get(capture.backend)

    if profile is None:
        raise ValueError(
            f"missing camera timing profile for "
            f"{capture.backend or 'none'}"
        )

    return profile


def compile_and_merge_scheduled_rigs(
    audited_by_rig: dict[int, Iterable[AuditedRigCapture]],
    initial_states: dict[int, dict[str, Any]],
    timing_profiles: dict[str, CameraTimingProfile],
) -> tuple[
    list[GlobalExecutionEvent],
    dict[int, dict[str, str]],
]:
    """Schedule every RIG independently, then merge chronologically.

    TOTALITY is continuous: its physical exposure list is repeated
    photo-by-photo from C2 until the next photo would collide with the
    preparation required for the first Diamond Ring capture at C3.
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

        captures.sort(
            key=lambda capture: (
                capture.target.target_time,
                capture.target.sequence_index,
            )
        )

        state = _normalize_camera_state(
            initial_states.get(rig_id, {})
        )

        scheduled: list[ScheduledOperation] = []
        rig_available_at: datetime | None = None

        for capture_index, capture in enumerate(captures):
            profile = _profile_for_capture(
                capture,
                timing_profiles,
            )

            if capture.target.phase != "totality":
                reduced, candidate_state = (
                    reduce_audited_capture_operations(
                        capture,
                        state,
                    )
                )

                candidate_scheduled = schedule_audited_capture(
                    reduced,
                    profile,
                )

                candidate_start, candidate_end = (
                    _scheduled_static_bounds(candidate_scheduled)
                )

                if (
                    capture.target.deadline is not None
                    and candidate_end > capture.target.deadline
                ):
                    # A camera operation, especially a native bracket,
                    # is atomic once started. Never launch it unless the
                    # complete operation fits inside its target window.
                    if (
                        capture.target.phase_window == "phase_3a"
                        and capture.target.sequence_index == 0
                    ):
                        raise ValueError(
                            f"C3 Diamond Ring does not fit inside its "
                            f"capture window for RIG {rig_id}"
                        )

                    continue

                # The final pre-C2 Diamond Ring must also leave enough
                # time for preparation of the first TOTALITY PHOTO.  C2 is a
                # physical anchor, so a preceding atomic capture is skipped
                # rather than allowed to consume its preparation interval.
                next_totality = next(
                    (
                        item
                        for item in captures[capture_index + 1:]
                        if item.target.phase == "totality"
                    ),
                    None,
                )

                if (
                    next_totality is not None
                    and capture.target.deadline
                    == next_totality.target.target_time
                ):
                    totality_profile = _profile_for_capture(
                        next_totality,
                        timing_profiles,
                    )

                    first_totality_unit = (
                        _split_totality_single_photos(
                            next_totality
                        )[0]
                    )

                    reduced_totality, _ = (
                        reduce_audited_capture_operations(
                            first_totality_unit,
                            candidate_state,
                        )
                    )

                    first_totality_scheduled = (
                        schedule_audited_capture(
                            reduced_totality,
                            totality_profile,
                        )
                    )

                    totality_prepare_start, _ = (
                        _scheduled_static_bounds(
                            first_totality_scheduled
                        )
                    )

                    if candidate_end > totality_prepare_start:
                        continue

                if (
                    rig_available_at is not None
                    and candidate_start < rig_available_at
                ):
                    # C3 itself is a hard anchor. It must never disappear
                    # silently because of an earlier camera operation.
                    if (
                        capture.target.phase_window == "phase_3a"
                        and capture.target.sequence_index == 0
                    ):
                        raise ValueError(
                            f"C3 Diamond Ring preparation overlaps "
                            f"previous camera operation for RIG {rig_id}"
                        )

                    # Periodic captures keep their absolute target time.
                    # If this RIG is still busy, skip this target rather
                    # than delaying it and corrupting eclipse timing.
                    continue

                scheduled.extend(candidate_scheduled)
                state = candidate_state
                rig_available_at = candidate_end
                continue

            if capture.target.deadline is None:
                raise ValueError(
                    f"totality deadline missing for RIG {rig_id}"
                )

            # Find the first C3 Diamond Ring capture for this same RIG.
            c3_capture = next(
                (
                    item
                    for item in captures[capture_index + 1:]
                    if item.target.phase_window == "phase_3a"
                    and item.target.sequence_index == 0
                ),
                None,
            )

            if c3_capture is None:
                raise ValueError(
                    f"C3 Diamond Ring capture missing for RIG {rig_id}"
                )

            units = _split_totality_single_photos(capture)

            totality_deadline = capture.target.deadline
            current_end: datetime | None = None
            unit_index = 0
            photo_index = 0

            while True:
                template = units[unit_index]

                # First physical totality photo remains anchored exactly
                # to C2. Later photos are shifted so their first USB command
                # starts immediately when the previous photo has finished.
                provisional_target = (
                    capture.target.target_time
                    if current_end is None
                    else current_end
                )

                candidate = replace(
                    template,
                    target=replace(
                        template.target,
                        target_time=provisional_target,
                        sequence_index=photo_index,
                        deadline=totality_deadline,
                    ),
                )

                reduced_candidate, candidate_state = (
                    reduce_audited_capture_operations(
                        candidate,
                        state,
                    )
                )

                candidate_scheduled = schedule_audited_capture(
                    reduced_candidate,
                    profile,
                )

                candidate_start, _candidate_end = (
                    _scheduled_static_bounds(candidate_scheduled)
                )

                if current_end is not None:
                    shift = current_end - candidate_start

                    candidate = replace(
                        reduced_candidate,
                        target=replace(
                            reduced_candidate.target,
                            target_time=(
                                reduced_candidate.target.target_time
                                + shift
                            ),
                        ),
                    )

                    candidate_scheduled = schedule_audited_capture(
                        candidate,
                        profile,
                    )

                candidate_start, candidate_end = (
                    _scheduled_static_bounds(candidate_scheduled)
                )

                if (
                    current_end is None
                    and rig_available_at is not None
                    and candidate_start < rig_available_at
                ):
                    raise ValueError(
                        f"C2 totality preparation overlaps previous "
                        f"camera operation for RIG {rig_id}"
                    )

                if (
                    current_end is not None
                    and candidate_start < current_end
                ):
                    raise ValueError(
                        f"continuous totality scheduling regression "
                        f"for RIG {rig_id}"
                    )

                # Compute the exact preparation boundary for C3 using the
                # camera state that would exist after this candidate photo.
                c3_profile = _profile_for_capture(
                    c3_capture,
                    timing_profiles,
                )

                reduced_c3, _c3_state = (
                    reduce_audited_capture_operations(
                        c3_capture,
                        candidate_state,
                    )
                )

                c3_scheduled = schedule_audited_capture(
                    reduced_c3,
                    c3_profile,
                )

                c3_prepare_start, _c3_end = (
                    _scheduled_static_bounds(c3_scheduled)
                )

                stop_at = min(
                    totality_deadline,
                    c3_prepare_start,
                )

                if candidate_end > stop_at:
                    break

                scheduled.extend(candidate_scheduled)

                state = candidate_state
                current_end = candidate_end

                photo_index += 1
                unit_index = (unit_index + 1) % len(units)

                if current_end >= totality_deadline:
                    break

            if photo_index == 0:
                raise ValueError(
                    f"no totality photo fits before C3 for RIG {rig_id}"
                )

            rig_available_at = current_end

        final_states[rig_id] = state
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
    """Serialize an internal UTC datetime with an explicit Z suffix."""
    if value is None:
        return None

    text = value.isoformat(timespec="milliseconds")

    if value.tzinfo is None:
        return text + "Z"

    if value.utcoffset() != timedelta(0):
        raise ValueError("execution-plan datetime must be UTC")

    if text.endswith("+00:00"):
        text = text[:-6]

    return text + "Z"


def _execution_command(
    event: GlobalExecutionEvent,
) -> dict[str, Any] | None:
    """Return the minimal command consumed by Trigger."""

    if event.command_time is None:
        return None

    operation = event.operation
    action = operation.get("action")

    if action == "set":
        params = {
            "parameter": operation.get("parameter"),
            "value": operation.get("value"),
        }
        if operation.get("fallback_parameter") is not None:
            params["fallback_parameter"] = operation.get("fallback_parameter")

        return {
            "time_utc": _isoformat_ms(event.command_time),
            "rig_id": event.rig_id,
            "action": "SET",
            "params": params,
        }

    if action in {"trigger_capture", "bracket_press"}:
        return {
            "time_utc": _isoformat_ms(event.command_time),
            "rig_id": event.rig_id,
            "action": "PHOTO",
            "params": {
                key: deepcopy(value)
                for key, value in operation.items()
                if key != "action"
            },
        }

    # delay, settle_idle, segment, expect_frames and bracket_release are
    # compiler/plugin implementation details, never Trigger instructions.
    return None


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
    """Build the minimal UTC command stream consumed by Trigger."""

    event_list = list(events)

    if not event_list:
        raise ValueError("execution plan contains no events")

    commands = [
        command
        for event in event_list
        if (command := _execution_command(event)) is not None
    ]

    commands.sort(
        key=lambda item: (
            item["time_utc"],
            item["rig_id"],
        )
    )

    if not commands:
        raise ValueError("execution plan contains no executable commands")

    if sequence_start is None:
        sequence_start = min(
            event.target_time
            for event in event_list
        )

    if sequence_end is None:
        sequence_end = max(
            event.target_time
            for event in event_list
        )

    return {
        "schema_version": 2,
        "config_type": "execution_plan",

        "sources": {
            "circumstances_file": circumstances_file,
            "photo_setup_file": photo_setup_file,
            "exposure_opt_file": exposure_opt_file,
        },

        "sequence_start_utc": _isoformat_ms(sequence_start),
        "sequence_end_utc": _isoformat_ms(sequence_end),

        # State that Trigger establishes once before sequence_start.
        "initial_state_required": {
            str(rig_id): deepcopy(
                _normalize_camera_state(state)
            )
            for rig_id, state in sorted(initial_states.items())
        },

        "commands": commands,
    }


def format_execution_plan_lines(
    plan: dict[str, Any],
) -> list[str]:
    """Render exactly the UTC commands that Trigger will execute."""

    if plan.get("config_type") != "execution_plan":
        raise ValueError("not an execution plan")

    lines: list[str] = []

    for command in plan.get("commands", []):
        params = command.get("params") or {}

        if command["action"] == "SET":
            detail = (
                f"SET {params.get('parameter')}="
                f"{params.get('value')}"
            )

        elif command["action"] == "PHOTO":
            detail = "PHOTO"

            exposure = (
                params.get("shutter")
                or params.get("centre")
            )
            iso = params.get("iso")

            if exposure is not None:
                detail += f" {exposure}"

            if iso is not None:
                detail += f" ISO{iso}"

        else:
            detail = str(command["action"])

        lines.append(
            f"{command['time_utc']} | "
            f"RIG{command['rig_id']} | "
            f"{detail}"
        )

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
