"""Shared expansion of logical exposure ranges into physical camera views."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from backend import sony_exposure_planner
from backend.camera_profiles import exposure_planning_capabilities
from backend.exposure_selection import (
    DEFAULT_SUPPORTED_SHUTTERS,
    parse_speed,
)
from backend.nikon_exposure_planner import (
    NIKON_SPEEDS,
    _speeds_between as nikon_speeds_between,
)


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
    *,
    supported_shutters: list[str] | None = None,
) -> list[str]:
    """Expand a generic EV range on the shared photographic shutter grid."""

    fastest_s = parse_speed(fastest)
    slowest_s = parse_speed(slowest)
    if fastest_s > slowest_s:
        fastest_s, slowest_s = slowest_s, fastest_s

    if not math.isfinite(step_ev) or step_ev <= 0:
        raise ValueError("EV step must be finite and positive")

    ev_fast = math.log2(fastest_s)
    ev_slow = math.log2(slowest_s)
    count = round((ev_slow - ev_fast) / step_ev) + 1
    count = max(1, count)

    grid = (
        DEFAULT_SUPPORTED_SHUTTERS
        if supported_shutters is None
        else supported_shutters
    )
    supported = [
        (str(speed), parse_speed(str(speed)))
        for speed in grid
    ]
    if not supported:
        raise ValueError("supported shutter list must not be empty")

    result: list[str] = []
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
    """Return the exact shutter sequence the configured planner will execute."""

    regular, fastest, slowest, step, speeds = plan

    if speeds is not None:
        return [str(speed) for speed in speeds]

    backend = _camera_backend(rig_snapshot)

    profile_capabilities = exposure_planning_capabilities(backend)
    profile_shutters = profile_capabilities.get("shutter_values") or []

    if regular and profile_shutters:
        return _generic_regular_shutters(
            fastest,
            slowest,
            float(step),
            supported_shutters=profile_shutters,
        )

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

        shutters: list[str] = []
        for item in sequence:
            if isinstance(item, sony_exposure_planner.SinglePhoto):
                shutters.append(str(item.speed))
            else:
                shutters.extend(str(view) for view in item.views)
        return shutters

    if regular:
        return _generic_regular_shutters(
            fastest,
            slowest,
            float(step),
        )

    return [str(speed) for speed in (speeds or [])]


def nearest_executable_shutter(
    rig_snapshot: Mapping[str, Any],
    target_seconds: float,
) -> str:
    """Return the camera-supported shutter nearest to an EV target."""

    if (
        isinstance(target_seconds, bool)
        or not isinstance(target_seconds, (int, float))
        or not math.isfinite(float(target_seconds))
        or float(target_seconds) <= 0
    ):
        raise ValueError("target shutter must be positive and finite")

    backend = _camera_backend(rig_snapshot)

    profile_capabilities = exposure_planning_capabilities(backend)
    profile_shutters = profile_capabilities.get("shutter_values") or []

    if profile_shutters:
        supported = [
            (str(speed), parse_speed(str(speed)))
            for speed in profile_shutters
        ]
    elif backend == "sony":
        supported = sony_exposure_planner.SONY_SPEEDS
    elif backend in {"nikon", "nikon-dslr", "nikon-z"}:
        supported = NIKON_SPEEDS
    else:
        supported = [
            (str(speed), parse_speed(str(speed)))
            for speed in DEFAULT_SUPPORTED_SHUTTERS
        ]

    target_ev = math.log2(float(target_seconds))

    return min(
        supported,
        key=lambda item: abs(math.log2(float(item[1])) - target_ev),
    )[0]


__all__ = [
    "expand_executable_shutters",
    "nearest_executable_shutter",
]
