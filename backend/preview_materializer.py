"""Pure helpers for materializing per-rig exposure previews."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from typing import Any

from backend.atmo import facteur_atmospherique, interpolate_altitude
from backend.exposure_selection import (
    DEFAULT_SUPPORTED_ISOS,
    DEFAULT_SUPPORTED_SHUTTERS,
    parse_speed,
    safe_shutter_and_iso,
    select_supported_shutter_at_or_below,
)
from backend import sony_exposure_planner
from backend.nikon_exposure_planner import (
    _speeds_between as nikon_speeds_between,
)
from backend.motion_constraint_resolver import resolve_motion_constraint
from services.camera_service import _normalized_speed_plan


class PreviewMaterializationError(ValueError):
    """A configuration error that prevents a trustworthy preview."""

    code = "CONFIG_INVALID"


def _value(source: Any, name: str, default: Any = None) -> Any:
    if isinstance(source, Mapping):
        return source.get(name, default)
    return getattr(source, name, default)


def normalize_intent_plan(intent: Any) -> tuple[bool, str, str, float, list[str] | None]:
    """Return a canonical regular bracket or an exact irregular speed list."""

    explicit = _value(intent, "speeds")
    if explicit is not None:
        fastest, slowest, step, regular = _normalized_speed_plan(explicit)
        # Validate with the stricter, shared parser as part of materialization.
        ordered = sorted({str(item) for item in explicit}, key=parse_speed)
        return regular, fastest, slowest, float(step), None if regular else ordered

    fastest = _value(intent, "shutter_max")
    slowest = _value(intent, "shutter_min")
    step = _value(intent, "step_ev")
    if fastest is None or slowest is None:
        raise ValueError("intent shutter bounds are incomplete")
    fastest_s = parse_speed(fastest)
    slowest_s = parse_speed(slowest)
    if fastest_s > slowest_s:
        fastest, slowest = slowest, fastest
    if step is None:
        step = 1.0
    step = float(step)
    if not math.isfinite(step) or step <= 0:
        raise ValueError("intent EV step must be positive and finite")
    return True, str(fastest), str(slowest), step, None


def _format_seconds_as_speed(seconds: float) -> str:
    """Format an exposure duration like the runtime atmospheric transformer."""
    if seconds <= 0:
        return "0"
    if seconds >= 1.0:
        return f"{seconds:g}"
    return f"1/{1.0 / seconds:g}"


def _context(eclipse_ctx: Mapping[str, Any] | Callable[[], Mapping[str, Any]]) -> Mapping[str, Any]:
    context = eclipse_ctx() if callable(eclipse_ctx) else eclipse_ctx
    if not isinstance(context, Mapping):
        raise PreviewMaterializationError("eclipse context must be a mapping")
    return context


def apply_atmos_if_enabled(
    rig_snapshot: Mapping[str, Any],
    plan: tuple[bool, str, str, float, list[str] | None],
    target_time: Any,
    eclipse_ctx: Mapping[str, Any] | Callable[[], Mapping[str, Any]],
) -> tuple[
    tuple[bool, str, str, float, list[str] | None],
    bool,
    str | None,
]:
    """Extend a regular EV bracket exactly like the runtime Atmos transformer."""

    photo = rig_snapshot.get("photo")
    enabled = isinstance(photo, Mapping) and photo.get("atmos_enabled") is True
    if not enabled:
        return plan, False, None

    regular, fastest, slowest, step, speeds = plan
    if not regular or speeds is not None:
        return plan, False, None

    context = _context(eclipse_ctx)
    timeline = context.get("timeline")
    altitudes = context.get("altitudes", context)
    location = context.get("location", context.get("_circumstances_location"))
    altitude_m = (
        location.get("altitude_m")
        if isinstance(location, Mapping)
        else context.get("altitude_m")
    )

    if not isinstance(timeline, Mapping) or altitude_m is None:
        raise PreviewMaterializationError(
            "atmospheric eclipse context is incomplete"
        )

    try:
        solar_altitude = interpolate_altitude(
            target_time,
            dict(timeline),
            dict(altitudes),
        )
        factor = facteur_atmospherique(solar_altitude, altitude_m)
    except (TypeError, ValueError, KeyError) as exc:
        raise PreviewMaterializationError(
            "atmospheric eclipse context is invalid"
        ) from exc

    original_slowest = parse_speed(slowest)
    target_slowest = original_slowest * float(factor)

    if target_slowest <= original_slowest:
        return plan, False, None

    # Same algorithm as scripts/eclipse_trigger.py:
    # advance by complete EV steps until the atmospheric target is reached
    # or exceeded.  The resulting bracket therefore contains the newly
    # added exposure(s).
    next_exposure = original_slowest * (2.0 ** step)
    while next_exposure < target_slowest:
        next_exposure *= 2.0 ** step

    extended_slowest = _format_seconds_as_speed(next_exposure)

    return (
        (True, fastest, extended_slowest, step, None),
        True,
        None,
    )


def compute_iso_and_corrections(
    requested_iso: int | str | None,
    final_slowest: str,
    rig_photo_cfg: Mapping[str, Any],
    *,
    theoretical_slowest: str | None = None,
) -> tuple[str | None, list[str], list[str]]:
    """Normalize preview ISO and return stable, de-duplicated diagnostics."""

    if requested_iso is None:
        return None, [], []

    requested_slowest = theoretical_slowest or final_slowest

    result = safe_shutter_and_iso(
        requested_slowest,
        requested_iso,
        final_slowest,
        supported_shutters=DEFAULT_SUPPORTED_SHUTTERS,
        supported_isos=DEFAULT_SUPPORTED_ISOS,
        iso_max=rig_photo_cfg.get("iso_max"),
        iso_compensation_enabled=rig_photo_cfg.get(
            "iso_compensation_enabled", True
        ),
    )
    return str(result["iso"]), list(dict.fromkeys(result["corrections"])), list(dict.fromkeys(result["warnings"]))


def resolve_policy(rig_cfg: Mapping[str, Any]) -> str:
    """Resolve the rig's motion policy without applying a time ceiling."""

    return resolve_motion_constraint(dict(rig_cfg))


def assemble_exposures_s(
    plan: tuple[bool, str, str, float, list[str] | None],
) -> list[float]:
    """Expand a normalized plan into an ordered list of exposure seconds."""

    regular, fastest, slowest, step, speeds = plan
    if not regular or speeds is not None:
        return [parse_speed(speed) for speed in (speeds or [])]
    current = parse_speed(fastest)
    end = parse_speed(slowest)
    ratio = 2.0 ** step
    result: list[float] = []
    while current <= end * (1.0 + 1e-12):
        result.append(current)
        current *= ratio
    if not math.isclose(result[-1], end, rel_tol=1e-12, abs_tol=1e-15):
        result.append(end)
    return result



def _camera_backend(rig_snapshot: Mapping[str, Any]) -> str:
    devices = rig_snapshot.get("devices")
    camera = devices.get("camera") if isinstance(devices, Mapping) else None
    if not isinstance(camera, Mapping):
        return ""
    return str(camera.get("backend") or "").strip().lower()


def _generic_regular_shutters(
    fastest: str,
    slowest: str,
    step_ev: float,
) -> list[str]:
    """Expand a generic EV range on the shared photographic shutter grid."""

    fastest_s = parse_speed(fastest)
    slowest_s = parse_speed(slowest)
    if fastest_s > slowest_s:
        fastest_s, slowest_s = slowest_s, fastest_s

    ev_fast = math.log2(fastest_s)
    ev_slow = math.log2(slowest_s)
    count = round((ev_slow - ev_fast) / step_ev) + 1
    count = max(1, count)

    supported = [
        (str(speed), parse_speed(speed))
        for speed in DEFAULT_SUPPORTED_SHUTTERS
    ]

    result = []
    previous = None

    for index in range(count):
        target_ev = ev_fast + index * step_ev
        selected = min(
            supported,
            key=lambda item: abs(math.log2(item[1]) - target_ev),
        )[0]

        if selected != previous:
            result.append(selected)
        previous = selected

    return result


def expand_executable_shutters(
    rig_snapshot: Mapping[str, Any],
    plan: tuple[bool, str, str, float, list[str] | None],
) -> list[str]:
    """Return the shutter sequence the configured camera planner will execute."""

    regular, fastest, slowest, step, speeds = plan

    if speeds is not None:
        return [str(speed) for speed in speeds]

    backend = _camera_backend(rig_snapshot)

    if backend in {"nikon-dslr", "nikon-z"}:
        return nikon_speeds_between(
            fastest,
            slowest,
            float(step),
        )

    if backend == "sony":
        _real_step, _count, sequence = sony_exposure_planner.plan(
            fastest,
            slowest,
            float(step),
        )

        shutters = []
        for item in sequence:
            if isinstance(item, sony_exposure_planner.SinglePhoto):
                shutters.append(str(item.speed))
            else:
                shutters.extend(str(view) for view in item.views)
        return shutters

    # Simulation / generic / unbound compatibility path.
    if regular:
        return _generic_regular_shutters(
            fastest,
            slowest,
            float(step),
        )

    return [str(speed) for speed in (speeds or [])]


def format_photo_shutter(speed: Any) -> str:
    """Return a compact photographic shutter notation."""

    seconds = parse_speed(speed)

    if seconds >= 1.0:
        return f"{seconds:g}"

    reciprocal = 1.0 / seconds
    denominator = round(reciprocal)

    if denominator > 0 and abs(reciprocal - denominator) <= 0.02:
        return f"1/{denominator}"

    return f"{seconds:g}"


def build_exposure_diff_lines(
    original_shutters: list[str],
    original_iso: Any,
    final_shutters: list[str],
    final_iso: Any,
) -> list[str]:
    """Return only visible exposure differences.

    Sequence alignment is intentional: Atmos extends the slow tail and
    Anti-blur truncates/changes the slow tail. Camera-specific planners are
    applied before this comparison.
    """

    original_iso_text = str(original_iso)
    final_iso_text = str(final_iso)

    lines = []
    common = min(len(original_shutters), len(final_shutters))

    for index in range(common):
        old_shutter = format_photo_shutter(original_shutters[index])
        new_shutter = format_photo_shutter(final_shutters[index])

        if (
            old_shutter == new_shutter
            and original_iso_text == final_iso_text
        ):
            continue

        lines.append(
            f"({old_shutter} ; {original_iso_text}) "
            f"→ ({new_shutter} ; {final_iso_text})"
        )

    for speed in final_shutters[common:]:
        lines.append(
            f"+ ({format_photo_shutter(speed)} ; {final_iso_text})"
        )

    for speed in original_shutters[common:]:
        lines.append(
            f"- ({format_photo_shutter(speed)} ; {original_iso_text})"
        )

    return lines

__all__ = [
    "build_exposure_diff_lines",
    "format_photo_shutter",
    "expand_executable_shutters",
    "PreviewMaterializationError",
    "apply_atmos_if_enabled",
    "assemble_exposures_s",
    "compute_iso_and_corrections",
    "normalize_intent_plan",
    "resolve_policy",
]
