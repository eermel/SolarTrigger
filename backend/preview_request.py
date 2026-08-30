"""Validation and normalization for exposure preview requests."""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from backend.exposure_selection import DEFAULT_SUPPORTED_ISOS, parse_speed


_ROOT_KEYS_LEGACY = {"intents"}
_ROOT_KEYS_OVERRIDE = {"intents", "rig_id", "rig_override"}
_INTENT_KEYS = {
    "shutter_min",
    "shutter_max",
    "step_ev",
    "speeds",
    "iso_target",
    "phase",
    "target_time",
    "deadline",
    "origin",
    "request_id",
}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _datetime(value: Any, field: str, *, nullable: bool = False) -> datetime | None:
    if value is None and nullable:
        return None
    _require(isinstance(value, str), f"{field} must be an ISO 8601 string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be a valid ISO 8601 datetime") from exc
    _require(parsed.tzinfo is not None, f"{field} must include a timezone")
    try:
        offset = parsed.utcoffset()
    except ValueError as exc:
        raise ValueError(f"{field} has an invalid timezone") from exc
    _require(offset is not None, f"{field} must include a timezone")
    try:
        return parsed.astimezone(timezone.utc).replace(tzinfo=None)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{field} is outside the supported datetime range") from exc


def _iso_target(value: Any) -> str | None:
    if value is None:
        return None
    _require(not isinstance(value, bool), "iso_target must be a positive integer")
    if isinstance(value, int):
        iso = value
    elif isinstance(value, str) and value.isascii() and value.isdigit():
        iso = int(value)
    else:
        raise ValueError("iso_target must be a positive integer")
    _require(iso > 0, "iso_target must be a positive integer")
    return str(iso)


def _speed(value: Any, field: str) -> str:
    _require(isinstance(value, str) and bool(value), f"{field} must be a shutter string")
    try:
        parsed = parse_speed(value)
    except (ArithmeticError, TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a valid shutter string") from exc
    _require(math.isfinite(parsed) and parsed > 0, f"{field} must be a positive shutter")
    return value


def _positive_number(value: Any, field: str) -> float:
    _require(
        isinstance(value, (int, float)) and not isinstance(value, bool),
        f"{field} must be a positive number",
    )
    result = float(value)
    _require(
        math.isfinite(result) and result > 0,
        f"{field} must be a positive finite number",
    )
    return result


def _rig_override(payload: dict[str, Any]) -> tuple[int | None, dict[str, Any] | None]:
    keys = set(payload)

    if keys == _ROOT_KEYS_LEGACY:
        return None, None

    _require(
        keys == _ROOT_KEYS_OVERRIDE,
        "payload must contain intents only, or intents + rig_id + rig_override",
    )

    rig_id = payload.get("rig_id")
    _require(
        isinstance(rig_id, int)
        and not isinstance(rig_id, bool)
        and 1 <= rig_id <= 4,
        "rig_id must be an integer from 1 to 4",
    )

    override = payload.get("rig_override")
    _require(
        isinstance(override, dict),
        "rig_override must be an object",
    )
    _require(
        set(override) == {"optics", "photo"},
        "rig_override must contain exactly optics and photo",
    )

    optics = override.get("optics")
    _require(
        isinstance(optics, dict),
        "rig_override.optics must be an object",
    )
    _require(
        set(optics) == {"focal_length_mm"},
        "rig_override.optics must contain exactly focal_length_mm",
    )

    focal = optics.get("focal_length_mm")
    if focal is not None:
        focal = _positive_number(
            focal,
            "rig_override.optics.focal_length_mm",
        )

    photo = override.get("photo")
    _require(
        isinstance(photo, dict),
        "rig_override.photo must be an object",
    )

    expected_photo_keys = {
        "anti_trailing_enabled",
        "motion_tolerance_px",
        "iso_compensation_enabled",
        "iso_max",
        "atmos_enabled",
    }
    _require(
        set(photo) == expected_photo_keys,
        "rig_override.photo contains invalid or missing fields",
    )

    for field in (
        "anti_trailing_enabled",
        "iso_compensation_enabled",
        "atmos_enabled",
    ):
        _require(
            isinstance(photo.get(field), bool),
            f"rig_override.photo.{field} must be a boolean",
        )

    tolerance = _positive_number(
        photo.get("motion_tolerance_px"),
        "rig_override.photo.motion_tolerance_px",
    )

    iso_max = photo.get("iso_max")
    _require(
        isinstance(iso_max, int) and not isinstance(iso_max, bool),
        "rig_override.photo.iso_max must be an integer",
    )
    _require(
        iso_max in DEFAULT_SUPPORTED_ISOS,
        "rig_override.photo.iso_max must be a supported ISO",
    )

    return rig_id, {
        "optics": {
            "focal_length_mm": focal,
        },
        "photo": {
            "anti_trailing_enabled": photo["anti_trailing_enabled"],
            "motion_tolerance_px": tolerance,
            "iso_compensation_enabled": photo["iso_compensation_enabled"],
            "iso_max": iso_max,
            "atmos_enabled": photo["atmos_enabled"],
        },
    }


def _phases(config: Any) -> Mapping[str, Any]:
    _require(isinstance(config, Mapping), "config must be an object")
    sequence = config.get("sequence")
    _require(isinstance(sequence, Mapping), "config.sequence must be an object")
    common = sequence.get("common")
    _require(isinstance(common, Mapping), "config.sequence.common must be an object")
    phases = common.get("phases")
    _require(isinstance(phases, Mapping), "config.sequence.common.phases must be an object")
    return phases


def _intent(value: Any, index: int, phases: Mapping[str, Any]) -> dict[str, Any]:
    label = f"intents[{index}]"
    _require(isinstance(value, dict), f"{label} must be an object")
    _require(all(isinstance(key, str) for key in value), f"{label} keys must be strings")
    _require(not (set(value) - _INTENT_KEYS), f"{label} contains unknown keys")

    phase = value.get("phase")
    _require(isinstance(phase, str) and bool(phase), f"{label}.phase must be a string")
    _require(phase in phases, f"{label}.phase does not exist in config")

    speeds_value = value.get("speeds")
    lower = value.get("shutter_min")
    upper = value.get("shutter_max")
    uses_speeds = speeds_value is not None
    uses_bounds = lower is not None or upper is not None
    _require(uses_speeds != uses_bounds, f"{label} must use speeds or shutter bounds")

    if uses_speeds:
        _require(
            isinstance(speeds_value, list) and bool(speeds_value),
            f"{label}.speeds must be a non-empty array",
        )
        speeds = [_speed(item, f"{label}.speeds") for item in speeds_value]
        shutter_min = shutter_max = None
    else:
        _require(lower is not None and upper is not None, f"{label} requires both shutter bounds")
        shutter_min = _speed(lower, f"{label}.shutter_min")
        shutter_max = _speed(upper, f"{label}.shutter_max")
        speeds = None

    step_value = value.get("step_ev", 1.0)
    if step_value is None:
        step_value = 1.0
    _require(
        isinstance(step_value, (int, float)) and not isinstance(step_value, bool),
        f"{label}.step_ev must be a number",
    )
    try:
        step_ev = float(step_value)
    except (OverflowError, ValueError) as exc:
        raise ValueError(f"{label}.step_ev must be positive and finite") from exc
    _require(
        math.isfinite(step_ev) and step_ev > 0,
        f"{label}.step_ev must be positive and finite",
    )

    origin = value.get("origin", phase)
    _require(isinstance(origin, str) and bool(origin), f"{label}.origin must be a string")
    request_id = value.get("request_id")
    _require(
        request_id is None or isinstance(request_id, str),
        f"{label}.request_id must be a string or null",
    )

    return {
        "shutter_min": shutter_min,
        "shutter_max": shutter_max,
        "step_ev": step_ev,
        "speeds": speeds,
        "iso_target": _iso_target(value.get("iso_target")),
        "phase": phase,
        "target_time": _datetime(value.get("target_time"), f"{label}.target_time"),
        "deadline": _datetime(value.get("deadline"), f"{label}.deadline", nullable=True),
        "origin": origin,
        "request_id": request_id,
    }


def validate_payload(
    payload: Any,
    config: Any,
) -> tuple[list[dict[str, Any]], int | None, dict[str, Any] | None]:
    """Validate a complete preview request.

    The optional RIG override is returned separately and is never persisted.
    """

    _require(isinstance(payload, dict), "payload must be an object")
    _require(
        all(isinstance(key, str) for key in payload),
        "payload keys must be strings",
    )

    rig_id, rig_override = _rig_override(payload)

    intents = payload.get("intents")
    _require(isinstance(intents, list), "intents must be an array")

    phases = _phases(config)
    normalized = [
        _intent(intent, index, phases)
        for index, intent in enumerate(intents)
    ]

    return normalized, rig_id, rig_override


def validate_and_normalize(payload: Any, config: Any) -> list[dict[str, Any]]:
    """Backward-compatible intent-only validation API."""

    intents, _rig_id, _rig_override_value = validate_payload(
        payload,
        config,
    )
    return intents


__all__ = ["validate_and_normalize", "validate_payload"]
