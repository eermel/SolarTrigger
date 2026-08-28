"""Helpers for selecting supported shutter speeds and ISO values."""

import math


DEFAULT_SUPPORTED_SHUTTERS = [
    "8", "4", "2", "1", "1/2", "1/4", "1/8", "1/15", "1/30",
    "1/60", "1/125", "1/250", "1/500", "1/1000", "1/2000",
    "1/4000", "1/8000",
]
DEFAULT_SUPPORTED_ISOS = [100, 200, 400, 800, 1600, 3200, 6400]


def parse_speed(value: str) -> float:
    """Convert a shutter-speed string such as ``1/125`` to seconds."""
    if not isinstance(value, str):
        raise ValueError("shutter speed must be a string")

    value = value.strip()
    if not value:
        raise ValueError("shutter speed must not be empty")

    try:
        if "/" in value:
            if value.count("/") != 1:
                raise ValueError
            numerator, denominator = value.split("/")
            speed = float(numerator) / float(denominator)
        else:
            speed = float(value)
    except (TypeError, ValueError, ZeroDivisionError) as exc:
        raise ValueError("invalid shutter speed") from exc

    if not math.isfinite(speed) or speed <= 0:
        raise ValueError("shutter speed must be positive and finite")
    return speed


def select_supported_shutter_at_or_below(
    t_max_s: float,
    supported: list[str],
) -> str:
    """Return the longest supported shutter no longer than ``t_max_s``."""
    if isinstance(t_max_s, bool):
        raise ValueError("maximum shutter speed must be numeric")
    try:
        maximum = float(t_max_s)
    except (TypeError, ValueError) as exc:
        raise ValueError("maximum shutter speed must be numeric") from exc
    if not math.isfinite(maximum) or maximum <= 0:
        raise ValueError("maximum shutter speed must be positive and finite")
    if not supported:
        raise ValueError("supported shutter list must not be empty")

    candidates = []
    for shutter in supported:
        seconds = parse_speed(shutter)
        if seconds <= maximum:
            candidates.append((seconds, shutter))
    if not candidates:
        raise ValueError("no supported shutter is at or below the maximum")
    return max(candidates, key=lambda candidate: candidate[0])[1]


def _canonical_supported_shutter(seconds: float, supported: list[str]) -> str:
    """Return the supported shutter string exactly matching ``seconds``."""
    for shutter in supported:
        if parse_speed(shutter) == seconds:
            return shutter
    raise ValueError("requested shutter is not in the supported shutter grid")


def normalize_iso_up(required_iso: float, supported_isos: list[int]) -> int:
    """Round an ISO requirement up to the nearest supported ISO value."""
    if isinstance(required_iso, bool):
        raise ValueError("required ISO must be numeric")
    try:
        required = float(required_iso)
    except (TypeError, ValueError) as exc:
        raise ValueError("required ISO must be numeric") from exc
    if not math.isfinite(required) or required <= 0:
        raise ValueError("required ISO must be positive and finite")
    if not supported_isos:
        raise ValueError("supported ISO list must not be empty")

    normalized = []
    for iso in supported_isos:
        if isinstance(iso, bool):
            raise ValueError("supported ISO values must be positive integers")
        try:
            iso_value = int(iso)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "supported ISO values must be positive integers"
            ) from exc
        if iso_value != iso or iso_value <= 0:
            raise ValueError("supported ISO values must be positive integers")
        normalized.append(iso_value)

    candidates = [iso for iso in normalized if iso >= required]
    if not candidates:
        raise ValueError("required ISO exceeds the supported ISO range")
    return min(candidates)


def safe_shutter_and_iso(
    t_requested: str,
    iso_requested: int | str,
    t_max: str,
    *,
    supported_shutters=None,
    supported_isos=None,
    iso_max=None,
) -> dict:
    """Apply a shutter ceiling and compensate exposure with supported ISO."""
    shutters = (DEFAULT_SUPPORTED_SHUTTERS if supported_shutters is None
                else supported_shutters)
    isos = DEFAULT_SUPPORTED_ISOS if supported_isos is None else supported_isos
    requested_seconds = parse_speed(t_requested)
    maximum_seconds = parse_speed(t_max)

    if isinstance(iso_requested, bool):
        raise ValueError("requested ISO must be a positive integer")
    try:
        requested_iso = int(iso_requested)
    except (TypeError, ValueError) as exc:
        raise ValueError("requested ISO must be a positive integer") from exc
    if str(requested_iso) != str(iso_requested).strip() or requested_iso <= 0:
        raise ValueError("requested ISO must be a positive integer")

    corrections = []
    warnings = []
    applied_seconds = requested_seconds
    if requested_seconds > maximum_seconds:
        applied_shutter = select_supported_shutter_at_or_below(
            maximum_seconds, shutters
        )
        applied_seconds = parse_speed(applied_shutter)
        corrections.append("shutter_limited")
    else:
        applied_shutter = _canonical_supported_shutter(
            requested_seconds, shutters
        )

    required_iso = requested_iso * requested_seconds / applied_seconds
    if not math.isclose(required_iso, requested_iso):
        corrections.append("iso_compensated")
    normalized_iso = normalize_iso_up(required_iso, isos)
    if not math.isclose(normalized_iso, required_iso):
        corrections.append("iso_rounded")

    applied_iso = normalized_iso
    if iso_max is not None:
        if isinstance(iso_max, bool):
            raise ValueError("maximum ISO must be a positive integer")
        try:
            maximum_iso = int(iso_max)
        except (TypeError, ValueError) as exc:
            raise ValueError("maximum ISO must be a positive integer") from exc
        if maximum_iso != iso_max or maximum_iso <= 0:
            raise ValueError("maximum ISO must be a positive integer")
        if normalized_iso > maximum_iso:
            applied_iso = maximum_iso
            warnings.append("iso_capped")

    return {
        "shutter": applied_shutter,
        "iso": applied_iso,
        "corrections": corrections,
        "warnings": warnings,
    }
