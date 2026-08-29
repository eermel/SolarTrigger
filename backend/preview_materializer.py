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
    """Extend a regular bracket for atmospheric attenuation when enabled.

    The atmospheric calculation can produce an arbitrary shutter duration.
    The returned plan must however contain only a shutter supported by the
    canonical camera grid.  The theoretical duration is returned separately
    so ISO materialization can compensate for the rounding.
    """

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
    extended = original_slowest * factor

    if extended <= original_slowest:
        return plan, False, None

    theoretical_slowest = format(extended, ".15g")

    # Do not put an impossible arbitrary duration in the preview plan.
    # Use the longest supported shutter which does not exceed the
    # atmospheric exposure target; ISO compensation preserves exposure.
    supported_slowest = select_supported_shutter_at_or_below(
        extended,
        DEFAULT_SUPPORTED_SHUTTERS,
    )

    return (
        (regular, fastest, supported_slowest, step, speeds),
        True,
        theoretical_slowest,
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


__all__ = [
    "PreviewMaterializationError",
    "apply_atmos_if_enabled",
    "assemble_exposures_s",
    "compute_iso_and_corrections",
    "normalize_intent_plan",
    "resolve_policy",
]
