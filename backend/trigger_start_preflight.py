"""GPS-first hardware preflight for real Trigger/DEBUG sequences."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timezone
from typing import Callable, Iterable

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


def _log(log_fn, rig_id: int, message: str) -> None:
    if callable(log_fn):
        log_fn(rig_id, message)


def _normalize_rig_ids(rig_ids: Iterable[int]) -> tuple[int, ...]:
    try:
        requested = tuple(rig_ids)
    except TypeError as exc:
        raise TriggerValidationError(
            "RIG ids must be iterable.",
            "RIG_ID_INVALID",
        ) from exc

    if not requested:
        raise TriggerValidationError(
            "Select at least one active RIG.",
            "RIG_ID_INVALID",
        )
    if any(
        not isinstance(rig_id, int)
        or isinstance(rig_id, bool)
        or not 1 <= rig_id <= 4
        for rig_id in requested
    ):
        raise TriggerValidationError(
            "RIG ids must be integers from 1 to 4.",
            "RIG_ID_INVALID",
        )
    return tuple(dict.fromkeys(requested))


def _parallel_by_rig(rig_ids, operation):
    if not rig_ids:
        return {}, []

    results = {}
    failures = []
    with ThreadPoolExecutor(
        max_workers=len(rig_ids),
        thread_name_prefix="trigger-preflight",
    ) as executor:
        futures = {
            rig_id: executor.submit(operation, rig_id)
            for rig_id in rig_ids
        }
        # Collect in deterministic RIG order while work itself runs in parallel.
        for rig_id in rig_ids:
            try:
                results[rig_id] = futures[rig_id].result()
            except Exception as exc:
                failures.append((rig_id, exc))
    return results, failures


def _failure_detail(label, failures) -> str:
    return " | ".join(
        f"RIG {rig_id} {label}: {type(exc).__name__}: {exc}"
        for rig_id, exc in failures
    )


def prepare_trigger_hardware_batch(
    *,
    rig_ids: Iterable[int],
    state_store,
    rig_config_loader: Callable[[], dict],
    camera_runtime,
    mount_runtime,
    camera_required_state_loader: Callable[[], dict] | None = None,
    trigger_active_fn: Callable[[int], bool] | None = None,
    log_fn: Callable[[int, str], None] | None = None,
    now_fn: Callable[[], datetime] | None = None,
    cancel_check: Callable[[], None] | None = None,
) -> dict:
    """Prepare every participating RIG before any sequence is started.

    The phase barriers are deliberate:
      1. GPS synchronization is validated before any other start verification.
      2. Every camera is characterized-preflighted, in parallel.
      3. Only after all cameras pass are all mounts synchronized from GPS.
      4. Only after all mount synchronizations pass is solar tracking enabled.

    A RIG without a pilotable mount participates in the camera phase and then
    cleanly skips the mount phases.
    """

    # GPS is the first verification.  Do not validate RIG ids, read the RIG
    # configuration or touch hardware before this succeeds.
    current = (now_fn or (lambda: datetime.now(timezone.utc)))()
    if current.tzinfo is None or current.utcoffset() is None:
        current = current.replace(tzinfo=timezone.utc)
    else:
        current = current.astimezone(timezone.utc)
    gps = validate_trigger_gps_state(
        state_store.snapshot("gps") or {},
        now_utc=current,
    )

    def check_cancel():
        if callable(cancel_check):
            cancel_check()

    check_cancel()
    normalized_ids = _normalize_rig_ids(rig_ids)

    if callable(trigger_active_fn):
        active = [
            rig_id
            for rig_id in normalized_ids
            if trigger_active_fn(rig_id)
        ]
        if active:
            raise TriggerValidationError(
                "Trigger already running or starting on RIG(s): "
                + ", ".join(str(rig_id) for rig_id in active),
                "TRIGGER_ALREADY_RUNNING",
            )

    check_cancel()
    try:
        config = rig_config_loader()
        for rig_id in normalized_ids:
            validate_execution_rig(config, rig_id)
    except TriggerValidationError:
        raise
    except Exception as exc:
        raise TriggerValidationError(
            f"RIG configuration cannot be loaded: {exc}",
            "RIG_CONFIG_INVALID",
        ) from exc

    check_cancel()
    required_camera_state = {}
    if callable(camera_required_state_loader):
        try:
            required_camera_state = camera_required_state_loader()
        except TriggerValidationError:
            raise
        except Exception as exc:
            raise TriggerValidationError(
                f"Camera preflight configuration is invalid: {exc}",
                "TRIGGER_INPUTS_INVALID",
            ) from exc
        if not isinstance(required_camera_state, dict):
            raise TriggerValidationError(
                "Camera preflight state is invalid.",
                "TRIGGER_INPUTS_INVALID",
            )

    check_cancel()
    # Reconcile ownership once, then test all cameras concurrently.  Slow USB
    # on one camera must not postpone discovery of a problem on another RIG.
    try:
        camera_runtime.reconcile(config)
    except Exception as exc:
        raise TriggerValidationError(
            f"Camera runtime preparation failed: {exc}",
            "CAMERA_PREFLIGHT_FAILED",
        ) from exc

    check_cancel()

    def camera_preflight(rig_id):
        worker = camera_runtime.get_for_rig(rig_id)
        if worker is None:
            raise RuntimeError("camera worker is unavailable")
        return worker.preflight(deepcopy(required_camera_state))

    camera_results, camera_failures = _parallel_by_rig(
        normalized_ids,
        camera_preflight,
    )
    if camera_failures:
        raise TriggerValidationError(
            "Camera preflight failed — "
            + _failure_detail("camera", camera_failures),
            "CAMERA_PREFLIGHT_FAILED",
        )

    check_cancel()
    for rig_id in normalized_ids:
        result = camera_results.get(rig_id)
        model = result.get("model") if isinstance(result, dict) else None
        _log(
            log_fn,
            rig_id,
            f"RIG {rig_id} camera preflight OK"
            + (f": {model}" if model else ""),
        )

    # All cameras are now known-good.  Mount setup can begin.
    check_cancel()
    try:
        mount_runtime.reconcile(config)
        mount_workers = {
            rig_id: mount_runtime.get_for_rig(rig_id)
            for rig_id in normalized_ids
        }
    except Exception as exc:
        raise TriggerValidationError(
            f"Mount preparation failed: {exc}",
            "MOUNT_PREFLIGHT_FAILED",
        ) from exc

    check_cancel()
    mounted_ids = tuple(
        rig_id
        for rig_id in normalized_ids
        if mount_workers.get(rig_id) is not None
    )
    for rig_id in normalized_ids:
        if mount_workers.get(rig_id) is None:
            _log(
                log_fn,
                rig_id,
                f"RIG {rig_id} has no pilotable mount: mount preflight skipped",
            )

    sync_results = {}
    tracking_results = {}
    if mounted_ids:
        latitude = gps.get("lat")
        longitude = gps.get("lon")
        elevation = gps.get("alt")
        utc_offset_minutes = gps.get("utc_offset_minutes")

        if not all(
            _finite_number(value)
            for value in (
                latitude,
                longitude,
                elevation,
                utc_offset_minutes,
            )
        ):
            raise TriggerValidationError(
                "Mount synchronization requires valid GPS latitude, "
                "longitude, altitude and UTC offset.",
                "MOUNT_SYNC_GPS_INVALID",
            )

        offset_minutes = float(utc_offset_minutes)
        if not -1440.0 <= offset_minutes <= 1440.0:
            raise TriggerValidationError(
                "GPS UTC offset is outside the supported range.",
                "MOUNT_SYNC_GPS_INVALID",
            )

        utc_iso = current.strftime("%Y-%m-%dT%H:%M:%S")
        utc_offset_hours = offset_minutes / 60.0

        def sync_mount(rig_id):
            worker = mount_workers[rig_id]
            fast = getattr(worker, "sync_site_time_fast", None)
            operation = fast if callable(fast) else worker.sync_site_time
            return operation(
                float(latitude),
                float(longitude),
                float(elevation),
                utc_iso,
                utc_offset_hours,
            )

        sync_results, sync_failures = _parallel_by_rig(
            mounted_ids,
            sync_mount,
        )
        if sync_failures:
            raise TriggerValidationError(
                "Mount GPS/time synchronization failed — "
                + _failure_detail("mount sync", sync_failures),
                "MOUNT_SYNC_FAILED",
            )

        check_cancel()
        for rig_id in mounted_ids:
            _log(
                log_fn,
                rig_id,
                f"RIG {rig_id} mount GPS/time synchronization OK",
            )

        def enable_tracking(rig_id):
            worker = mount_workers[rig_id]
            set_mode_fast = getattr(
                worker,
                "set_tracking_mode_fast",
                None,
            )
            start_fast = getattr(
                worker,
                "start_tracking_fast",
                None,
            )
            (
                set_mode_fast("solar")
                if callable(set_mode_fast)
                else worker.set_tracking_mode("solar")
            )
            result = (
                start_fast()
                if callable(start_fast)
                else worker.start_tracking()
            )
            if (
                isinstance(result, dict)
                and result.get("tracking_enabled") is False
            ):
                raise RuntimeError(
                    "mount did not confirm tracking activation"
                )
            return result

        tracking_results, tracking_failures = _parallel_by_rig(
            mounted_ids,
            enable_tracking,
        )
        if tracking_failures:
            raise TriggerValidationError(
                "Solar tracking activation failed — "
                + _failure_detail("tracking", tracking_failures),
                "MOUNT_TRACKING_FAILED",
            )

        check_cancel()
        for rig_id in mounted_ids:
            _log(log_fn, rig_id, f"RIG {rig_id} solar tracking ON")

    return {
        "rig_ids": list(normalized_ids),
        "camera": camera_results,
        "mount_sync": sync_results,
        "tracking": tracking_results,
    }


def prepare_trigger_hardware(
    *,
    rig_id: int,
    state_store,
    rig_config_loader: Callable[[], dict],
    camera_runtime,
    mount_runtime,
    camera_required_state_loader: Callable[[], dict] | None = None,
    trigger_active_fn: Callable[[int], bool] | None = None,
    log_fn: Callable[[int, str], None] | None = None,
    now_fn: Callable[[], datetime] | None = None,
    cancel_check: Callable[[], None] | None = None,
) -> dict:
    """Compatibility wrapper for a single-RIG start path."""
    batch = prepare_trigger_hardware_batch(
        rig_ids=(rig_id,),
        state_store=state_store,
        rig_config_loader=rig_config_loader,
        camera_runtime=camera_runtime,
        mount_runtime=mount_runtime,
        camera_required_state_loader=camera_required_state_loader,
        trigger_active_fn=trigger_active_fn,
        log_fn=log_fn,
        now_fn=now_fn,
        cancel_check=cancel_check,
    )
    return {
        "rig_id": rig_id,
        "camera": batch["camera"].get(rig_id),
        "mount": (
            None
            if rig_id not in batch["mount_sync"]
            else {
                "synchronization": batch["mount_sync"].get(rig_id),
                "tracking": batch["tracking"].get(rig_id),
            }
        ),
    }


__all__ = [
    "prepare_trigger_hardware",
    "prepare_trigger_hardware_batch",
]
