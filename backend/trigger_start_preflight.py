"""Start-time hardware preflight for real Trigger/DEBUG sequences."""

from __future__ import annotations

from datetime import datetime, timezone
from numbers import Real
from typing import Callable

from backend.trigger_service import (
    TriggerValidationError,
    validate_execution_rig,
    validate_trigger_gps_state,
)


def _finite_number(value) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return (
        not isinstance(value, bool)
        and number == number
        and number not in (float("inf"), float("-inf"))
    )


def _log(log_fn, message: str) -> None:
    if callable(log_fn):
        log_fn(message)


def prepare_trigger_hardware(
    *,
    rig_id: int,
    state_store,
    rig_config_loader: Callable[[], dict],
    camera_runtime,
    mount_runtime,
    trigger_active_fn: Callable[[int], bool] | None = None,
    log_fn: Callable[[str], None] | None = None,
    now_fn: Callable[[], datetime] | None = None,
) -> dict:
    """Prepare one RIG before a real Trigger/DEBUG child is started.

    Safety order is intentional and tested:
      1. GPS synchronization validity.
      2. RIG/camera configuration and immediate camera probe.
      3. Mount site/time synchronization from GPS, when a mount exists.
      4. Solar tracking activation.

    No mount worker is created for a RIG without a configured pilotable mount.
    """

    # Contract: GPS is always the first start-time verification.  Do not
    # reconcile or touch any hardware before this succeeds.
    gps = validate_trigger_gps_state(state_store.snapshot("gps") or {})

    if callable(trigger_active_fn) and trigger_active_fn(rig_id):
        raise TriggerValidationError(
            f"Trigger RIG {rig_id} is already running or starting.",
            "TRIGGER_ALREADY_RUNNING",
        )

    try:
        config = rig_config_loader()
        validate_execution_rig(config, rig_id)
    except TriggerValidationError:
        raise
    except Exception as exc:
        raise TriggerValidationError(
            f"RIG {rig_id} configuration cannot be loaded: {exc}",
            "RIG_CONFIG_INVALID",
        ) from exc

    # Camera probe is intentionally before every mount operation.  It opens
    # the authoritative camera worker and proves that the configured USB body
    # is reachable, without taking an extra exposure.
    try:
        camera_runtime.reconcile(config)
        camera_worker = camera_runtime.get_for_rig(rig_id)
        if camera_worker is None:
            raise RuntimeError("camera worker is unavailable")
        camera_info = camera_worker.probe_info()
    except Exception as exc:
        raise TriggerValidationError(
            f"RIG {rig_id} camera preflight failed: {exc}",
            "CAMERA_PREFLIGHT_FAILED",
        ) from exc

    model = (
        camera_info.get("model")
        if isinstance(camera_info, dict)
        else None
    )
    _log(
        log_fn,
        f"RIG {rig_id} camera preflight OK"
        + (f": {model}" if model else ""),
    )

    try:
        mount_runtime.reconcile(config)
        mount_worker = mount_runtime.get_for_rig(rig_id)
    except Exception as exc:
        raise TriggerValidationError(
            f"RIG {rig_id} mount preparation failed: {exc}",
            "MOUNT_PREFLIGHT_FAILED",
        ) from exc

    if mount_worker is None:
        _log(log_fn, f"RIG {rig_id} has no pilotable mount: mount preflight skipped")
        return {
            "rig_id": rig_id,
            "camera": camera_info,
            "mount": None,
        }

    latitude = gps.get("lat")
    longitude = gps.get("lon")
    elevation = gps.get("alt")
    utc_offset_minutes = gps.get("utc_offset_minutes")

    if not all(
        _finite_number(value)
        for value in (latitude, longitude, elevation, utc_offset_minutes)
    ):
        raise TriggerValidationError(
            f"RIG {rig_id} mount synchronization requires valid GPS "
            "latitude, longitude, altitude and UTC offset.",
            "MOUNT_SYNC_GPS_INVALID",
        )

    offset_minutes = float(utc_offset_minutes)
    if not -1440.0 <= offset_minutes <= 1440.0:
        raise TriggerValidationError(
            f"RIG {rig_id} GPS UTC offset is outside the supported range.",
            "MOUNT_SYNC_GPS_INVALID",
        )

    current = (now_fn or (lambda: datetime.now(timezone.utc)))()
    if current.tzinfo is None or current.utcoffset() is None:
        current = current.replace(tzinfo=timezone.utc)
    else:
        current = current.astimezone(timezone.utc)
    utc_iso = current.strftime("%Y-%m-%dT%H:%M:%S")
    utc_offset_hours = offset_minutes / 60.0

    try:
        sync_result = mount_worker.sync_site_time(
            float(latitude),
            float(longitude),
            float(elevation),
            utc_iso,
            utc_offset_hours,
        )
    except Exception as exc:
        raise TriggerValidationError(
            f"RIG {rig_id} mount GPS/time synchronization failed: {exc}",
            "MOUNT_SYNC_FAILED",
        ) from exc

    _log(log_fn, f"RIG {rig_id} mount GPS/time synchronization OK")

    try:
        mount_worker.set_tracking_mode("solar")
        tracking_result = mount_worker.start_tracking()
    except Exception as exc:
        raise TriggerValidationError(
            f"RIG {rig_id} solar tracking activation failed: {exc}",
            "MOUNT_TRACKING_FAILED",
        ) from exc

    if (
        isinstance(tracking_result, dict)
        and tracking_result.get("tracking_enabled") is False
    ):
        raise TriggerValidationError(
            f"RIG {rig_id} mount did not confirm tracking activation.",
            "MOUNT_TRACKING_FAILED",
        )

    _log(log_fn, f"RIG {rig_id} solar tracking ON")

    return {
        "rig_id": rig_id,
        "camera": camera_info,
        "mount": {
            "synchronization": sync_result,
            "tracking": tracking_result,
        },
    }


__all__ = ["prepare_trigger_hardware"]
