"""Pure per-RIG astronomical motion exposure ceiling calculation."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Callable

from backend.camera_model_resolution import resolve_sensor_entry
from backend.exposure_selection import (
    DEFAULT_SUPPORTED_ISOS,
    DEFAULT_SUPPORTED_SHUTTERS,
    parse_speed,
    safe_shutter_and_iso,
    select_supported_shutter_at_or_below,
)
from backend.field_rotation import field_rotation_rate_deg_s
from backend.motion_constraint_resolver import resolve_motion_constraint
from backend.sensor_db import load_sensor_db
from backend.solar_position import (
    greenwich_sidereal_deg_utc,
    local_hour_angle_deg,
    solar_apparent_ra_dec_deg_utc,
    solar_declination_deg_utc,
)
from backend.solar_trailing import max_exposure_time_fixed_mount


DEFAULT_SENSOR_DB_PATH = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "camera_sensors"
    / "camera_sensors_2017plus_zwo.json"
)


def _positive_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a positive number")
    converted = float(value)
    if not math.isfinite(converted) or converted <= 0:
        raise ValueError(f"{field} must be a positive number")
    return converted


def _coordinate(
    value: Any,
    field: str,
    *,
    minimum: float,
    maximum: float,
    minimum_inclusive: bool = True,
    maximum_inclusive: bool = True,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a finite number")

    converted = float(value)
    lower_valid = (
        converted >= minimum if minimum_inclusive else converted > minimum
    )
    upper_valid = (
        converted <= maximum if maximum_inclusive else converted < maximum
    )

    if not math.isfinite(converted) or not lower_valid or not upper_valid:
        left = "[" if minimum_inclusive else "("
        right = "]" if maximum_inclusive else ")"
        raise ValueError(
            f"{field} must be in {left}{minimum}, {maximum}{right}"
        )

    return converted


def _camera_sensor(policy: dict, sensor_db_path: str | Path) -> dict:
    devices = policy.get("devices")
    camera = devices.get("camera") if isinstance(devices, dict) else None

    manufacturer = (
        camera.get("manufacturer")
        if isinstance(camera, dict)
        else None
    )
    model = camera.get("model") if isinstance(camera, dict) else None
    alias = camera.get("alias") if isinstance(camera, dict) else None
    model_or_alias = (
        model
        if isinstance(model, str) and model.strip()
        else alias
    )

    if not isinstance(manufacturer, str) or not manufacturer.strip():
        raise ValueError("camera manufacturer is missing")
    if not isinstance(model_or_alias, str) or not model_or_alias.strip():
        raise ValueError("camera model or alias is missing")

    sensor_db = load_sensor_db(str(sensor_db_path))
    return resolve_sensor_entry(
        manufacturer,
        model_or_alias,
        sensor_db,
    )


_CORRECTION_ORDER = (
    "shutter_limited",
    "iso_compensated",
    "iso_rounded",
)
_WARNING_ORDER = ("iso_capped",)


def materialize_exposure_plan(
    *,
    speeds: list[str] | None,
    shutter_min: str | None,
    shutter_max: str | None,
    step_ev: float | None,
    iso_requested: int,
    iso_max: int,
    t_max: float,
    iso_compensation_enabled: bool = True,
) -> dict:
    """Apply one motion ceiling without ever lengthening the exposure plan.

    ``shutter_min`` is the slowest bound and ``shutter_max`` the fastest.
    Explicit speed lists preserve their order and length.
    """

    if isinstance(t_max, bool):
        raise ValueError("motion exposure ceiling must be numeric")
    try:
        ceiling = float(t_max)
    except (TypeError, ValueError) as exc:
        raise ValueError("motion exposure ceiling must be numeric") from exc
    if not math.isfinite(ceiling) or ceiling <= 0:
        raise ValueError(
            "motion exposure ceiling must be finite and positive"
        )

    if isinstance(iso_requested, bool) or not isinstance(iso_requested, int):
        raise ValueError("requested ISO must be a positive integer")
    if iso_requested <= 0:
        raise ValueError("requested ISO must be a positive integer")

    if isinstance(iso_max, bool) or not isinstance(iso_max, int):
        raise ValueError("maximum ISO must be a positive integer")
    if iso_max <= 0:
        raise ValueError("maximum ISO must be a positive integer")

    if not isinstance(iso_compensation_enabled, bool):
        raise ValueError(
            "iso_compensation_enabled must be a boolean"
        )

    results = []

    if speeds is not None:
        if not speeds:
            raise ValueError(
                "explicit shutter list must not be empty"
            )

        applied_speeds = []
        for speed in [str(value) for value in speeds]:
            requested_seconds = parse_speed(speed)

            # Anti-blur is a ceiling only. A speed already short enough
            # must remain exactly as requested.
            if requested_seconds <= ceiling:
                applied_speeds.append(speed)
                results.append({
                    "shutter": speed,
                    "iso": iso_requested,
                    "corrections": [],
                    "warnings": [],
                })
                continue

            result = safe_shutter_and_iso(
                t_requested=speed,
                iso_requested=iso_requested,
                t_max=str(ceiling),
                supported_shutters=DEFAULT_SUPPORTED_SHUTTERS,
                supported_isos=DEFAULT_SUPPORTED_ISOS,
                iso_max=iso_max,
                iso_compensation_enabled=iso_compensation_enabled,
            )
            applied_speeds.append(result["shutter"])
            results.append(result)

        output = {
            "speeds": applied_speeds,
            "shutter_min": None,
            "shutter_max": None,
            "step_ev": step_ev,
        }

    else:
        if shutter_min is None or shutter_max is None:
            raise ValueError(
                "shutter bounds are incomplete"
            )

        requested_slowest = str(shutter_min)
        requested_seconds = parse_speed(requested_slowest)

        # Crucial rule: Anti-blur never extends a bracket.
        if requested_seconds <= ceiling:
            return {
                "speeds": None,
                "shutter_min": requested_slowest,
                "shutter_max": str(shutter_max),
                "step_ev": (
                    float(step_ev)
                    if step_ev is not None
                    else 1.0
                ),
                "iso_applied": iso_requested,
                "corrections": [],
                "warnings": [],
            }

        applied_slowest = select_supported_shutter_at_or_below(
            ceiling,
            DEFAULT_SUPPORTED_SHUTTERS,
        )

        result = safe_shutter_and_iso(
            t_requested=requested_slowest,
            iso_requested=iso_requested,
            t_max=applied_slowest,
            supported_shutters=DEFAULT_SUPPORTED_SHUTTERS,
            supported_isos=DEFAULT_SUPPORTED_ISOS,
            iso_max=iso_max,
            iso_compensation_enabled=iso_compensation_enabled,
        )
        results.append(result)

        output = {
            "speeds": None,
            "shutter_min": applied_slowest,
            "shutter_max": str(shutter_max),
            "step_ev": (
                float(step_ev)
                if step_ev is not None
                else 1.0
            ),
        }

    output["iso_applied"] = max(
        result["iso"] for result in results
    )
    output["corrections"] = [
        item
        for item in _CORRECTION_ORDER
        if any(
            item in result["corrections"]
            for result in results
        )
    ]
    output["warnings"] = [
        item
        for item in _WARNING_ORDER
        if any(
            item in result["warnings"]
            for result in results
        )
    ]

    return output


def compute_motion_exposure_ceiling(
    policy: dict,
    target_time: Any,
    *,
    sensor_db_path: str | Path = DEFAULT_SENSOR_DB_PATH,
    field_rotation_rate_fn: Callable[..., float] = field_rotation_rate_deg_s,
    solar_declination_fn: Callable[..., float] = solar_declination_deg_utc,
    solar_position_fn: Callable[..., tuple[float, float]] = (
        solar_apparent_ra_dec_deg_utc
    ),
    sidereal_fn: Callable[..., float] = greenwich_sidereal_deg_utc,
    hour_angle_fn: Callable[..., float] = local_hour_angle_deg,
) -> float | None:
    """Return the astronomical Anti-blur exposure ceiling in seconds.

    ``None`` means that the current RIG geometry does not impose an
    astronomical motion constraint.
    """

    if not isinstance(policy, dict):
        raise ValueError("RIG policy snapshot must be an object")

    constraint = resolve_motion_constraint(policy)
    if constraint == "none":
        return None

    optics = policy.get("optics")
    photo = policy.get("photo")

    if not isinstance(photo, dict):
        raise ValueError("RIG policy snapshot is incomplete")

    tolerance = _positive_number(
        photo.get("motion_tolerance_px"),
        "motion_tolerance_px",
    )

    if constraint == "fixed_trailing":
        if not isinstance(optics, dict):
            raise ValueError("RIG optics configuration is incomplete")

        focal_length = _positive_number(
            optics.get("focal_length_mm"),
            "focal_length_mm",
        )

        sensor = _camera_sensor(policy, sensor_db_path)
        pixel_pitch = _positive_number(
            sensor.get("pixel_pitch_um"),
            "pixel_pitch_um",
        )

        declination = solar_declination_fn(target_time)

        return max_exposure_time_fixed_mount(
            pixel_pitch,
            focal_length,
            tolerance,
            declination,
        )

    if constraint != "field_rotation":
        raise ValueError(f"unsupported motion constraint: {constraint}")

    # Field rotation is image rotation around the optical axis.
    # At the sensor corner, the displacement in pixels depends only on
    # angular rotation and the pixel radius from the image centre.
    # Focal length and an artificial field-angle setting are unnecessary.
    sensor = _camera_sensor(policy, sensor_db_path)

    width_px = _positive_number(
        sensor.get("width_px"),
        "width_px",
    )
    height_px = _positive_number(
        sensor.get("height_px"),
        "height_px",
    )

    radius_px = 0.5 * math.hypot(width_px, height_px)


    eclipse = policy.get("eclipse")
    reference_site = (
        eclipse.get("reference_site")
        if isinstance(eclipse, dict)
        else None
    )

    latitude = _coordinate(
        (
            reference_site.get("lat")
            if isinstance(reference_site, dict)
            else None
        ),
        "reference_site.lat",
        minimum=-90.0,
        maximum=90.0,
        minimum_inclusive=False,
        maximum_inclusive=False,
    )

    longitude = _coordinate(
        (
            reference_site.get("lon")
            if isinstance(reference_site, dict)
            else None
        ),
        "reference_site.lon",
        minimum=-180.0,
        maximum=180.0,
    )

    alpha, declination = solar_position_fn(target_time)
    sidereal = sidereal_fn(target_time)
    hour_angle = hour_angle_fn(alpha, sidereal, longitude)
    omega = field_rotation_rate_fn(
        latitude,
        declination,
        hour_angle,
    )

    if not all(
        math.isfinite(value)
        for value in (
            alpha,
            declination,
            sidereal,
            hour_angle,
            omega,
        )
    ):
        raise ValueError(
            "field-rotation calculation must be finite"
        )

    if omega == 0.0:
        return None

    t_max = tolerance / (
        abs(omega)
        * math.pi
        / 180.0
        * radius_px
    )

    if not all(
        math.isfinite(value) and value > 0
        for value in (
            radius_px,
            t_max,
        )
    ):
        raise ValueError(
            "field-rotation exposure ceiling must be finite and positive"
        )

    return float(t_max)


__all__ = [
    "DEFAULT_SENSOR_DB_PATH",
    "compute_motion_exposure_ceiling",
    "materialize_exposure_plan",
]
