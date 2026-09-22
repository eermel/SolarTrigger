"""Direct in-memory scheduler for real camera validation.

Camera Validation deliberately uses Camera IPC and the real camera worker, but
it does not materialize or parse an intermediate execution-plan file.  The
recipe is a relative diagnostic sequence: preflight is completed first, then
SET/PHOTO commands are dispatched from one monotonic anchor.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import math
import time
from typing import Any, Callable


class CameraValidationScheduleError(RuntimeError):
    """Invalid validation recipe or scheduler state."""


class CameraValidationScheduleCancelled(CameraValidationScheduleError):
    """Validation was cancelled before all commands were dispatched."""


def _utc_text(value: datetime) -> str:
    return (
        value.astimezone(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _stop_requested(stop_event) -> bool:
    return stop_event is not None and stop_event.is_set()


def _wait_until(target_monotonic: float, stop_event) -> None:
    while True:
        if _stop_requested(stop_event):
            raise CameraValidationScheduleCancelled("validation cancelled")
        remaining = target_monotonic - time.monotonic()
        if remaining <= 0:
            return
        delay = min(remaining, 0.02)
        if stop_event is not None:
            if stop_event.wait(delay):
                raise CameraValidationScheduleCancelled("validation cancelled")
        else:
            time.sleep(delay)


def _validated_commands(recipe: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(recipe, dict):
        raise CameraValidationScheduleError("validation recipe must be an object")
    commands = recipe.get("commands")
    if not isinstance(commands, list) or not commands:
        raise CameraValidationScheduleError("validation recipe has no commands")

    previous_offset = -1.0
    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(commands):
        if not isinstance(raw, dict):
            raise CameraValidationScheduleError(
                f"validation command {index} must be an object"
            )
        action = raw.get("action")
        if action not in {"SET", "PHOTO"}:
            raise CameraValidationScheduleError(
                f"validation command {index} has unsupported action {action!r}"
            )
        params = raw.get("params")
        if not isinstance(params, dict):
            raise CameraValidationScheduleError(
                f"validation command {index} params must be an object"
            )
        try:
            offset_ms = float(raw.get("offset_ms"))
            duration_ms = float(raw.get("duration_ms"))
        except (TypeError, ValueError) as exc:
            raise CameraValidationScheduleError(
                f"validation command {index} has invalid timing"
            ) from exc
        if (
            not math.isfinite(offset_ms)
            or offset_ms < 0
            or offset_ms < previous_offset
            or not math.isfinite(duration_ms)
            or duration_ms < 0
        ):
            raise CameraValidationScheduleError(
                f"validation command {index} has invalid timing"
            )
        if action == "SET":
            parameter = params.get("parameter")
            if not isinstance(parameter, str) or not parameter or "value" not in params:
                raise CameraValidationScheduleError(
                    f"validation SET {index} is incomplete"
                )
        normalized.append(
            {
                "index": index,
                "action": action,
                "params": deepcopy(params),
                "offset_ms": offset_ms,
                "duration_ms": duration_ms,
            }
        )
        previous_offset = offset_ms
    return normalized


def run_validation_recipe(
    recipe: dict[str, Any],
    *,
    rig_id: int,
    camera_client,
    stop_event=None,
    log_fn: Callable[[str], None] = print,
) -> None:
    """Run one validation recipe directly against a camera endpoint.

    Preflight is completed before the timed anchor is created.  Consequently a
    slow USB preflight can never make the first diagnostic command expire.  A
    command-level camera error is logged and the following diagnostic commands
    are still attempted; the recording client is responsible for preserving
    the exact error for the final validation verdict.  PHOTO is never replayed.
    """
    if not isinstance(rig_id, int) or isinstance(rig_id, bool) or not 1 <= rig_id <= 4:
        raise CameraValidationScheduleError("rig_id must be in 1..4")

    commands = _validated_commands(recipe)
    if _stop_requested(stop_event):
        raise CameraValidationScheduleCancelled("validation cancelled")

    preflight = getattr(camera_client, "preflight", None)
    if not callable(preflight):
        raise CameraValidationScheduleError("camera endpoint has no preflight operation")

    log_fn(f"CAMERA_VALIDATION preflight rig={rig_id}")
    preflight(rig_id, {})

    if _stop_requested(stop_event):
        raise CameraValidationScheduleCancelled("validation cancelled after preflight")

    anchor_monotonic = time.monotonic()
    anchor_utc = datetime.now(timezone.utc)

    for command in commands:
        target_monotonic = anchor_monotonic + command["offset_ms"] / 1000.0
        _wait_until(target_monotonic, stop_event)

        params = deepcopy(command["params"])
        target_utc = anchor_utc + timedelta(milliseconds=command["offset_ms"])
        if command["action"] == "PHOTO":
            params["validation_target_utc"] = _utc_text(target_utc)

        lateness_ms = (time.monotonic() - target_monotonic) * 1000.0
        log_fn(
            "CAMERA_VALIDATION "
            f"rig={rig_id} action={command['action']} index={command['index']} "
            f"lateness_ms={lateness_ms:+.3f}"
        )

        timeout_s = max(5.0, command["duration_ms"] / 1000.0 + 1.0)
        try:
            if command["action"] == "SET":
                camera_client.set_parameter(
                    rig_id,
                    params["parameter"],
                    params["value"],
                    fallback_parameter=params.get("fallback_parameter"),
                    timeout_s=timeout_s,
                    scheduled=True,
                )
            else:
                camera_client.execute_photo(
                    rig_id,
                    params,
                    timeout_s=timeout_s,
                    scheduled=True,
                )
        except CameraValidationScheduleCancelled:
            raise
        except Exception as exc:
            code = getattr(exc, "code", type(exc).__name__)
            log_fn(
                "WARNING CAMERA_VALIDATION "
                f"rig={rig_id} action={command['action']} index={command['index']} "
                f"code={code}; continuing diagnostic sequence"
            )

        if _stop_requested(stop_event):
            raise CameraValidationScheduleCancelled("validation cancelled")
