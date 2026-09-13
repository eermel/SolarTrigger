"""Timeout policy shared by camera worker and process supervisor."""

from __future__ import annotations

from typing import Any


_CAPTURE_MARGIN_FACTOR = 1.25
_MIN_CAPTURE_MARGIN_S = 0.25


def _speed_seconds(value: Any) -> float | None:
    try:
        text = str(value).strip()
        if not text:
            return None
        if "/" in text:
            numerator, denominator = text.split("/", 1)
            result = float(numerator) / float(denominator)
        else:
            result = float(text)
        if result < 0:
            return None
        return result
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _capture_timeout(
    expected_s: float | None,
    frame_count: int | None,
    base_timeout_s: float,
) -> float:
    base = max(0.001, float(base_timeout_s))

    if expected_s is None:
        return base

    expected = max(0.0, float(expected_s))

    # The base timeout already provides the large safety allowance for short
    # camera operations (30 s in production).  Only genuinely long captures
    # need to extend it.  Keeping the additional margin proportional avoids
    # turning a millisecond exposure into a many-second watchdog window.
    proportional_margin = max(
        _MIN_CAPTURE_MARGIN_S,
        expected * (_CAPTURE_MARGIN_FACTOR - 1.0),
    )
    guarded = expected + proportional_margin

    return max(base, guarded)


def _prepared_estimate(prepared: Any) -> tuple[float | None, int | None]:
    estimate = getattr(prepared, "estimated_total_s", None)
    count = getattr(prepared, "planned_count", None)

    try:
        estimate_value = (
            None if estimate is None else max(0.0, float(estimate))
        )
    except (TypeError, ValueError):
        estimate_value = None

    try:
        count_value = None if count is None else max(1, int(count))
    except (TypeError, ValueError):
        count_value = None

    return estimate_value, count_value


def _speed_list_estimate(
    speeds: Any,
    slowest_override_seconds: Any = None,
) -> tuple[float | None, int | None]:
    try:
        values = list(speeds)
    except TypeError:
        return None, None

    if not values:
        return 0.0, 1

    durations = [_speed_seconds(value) for value in values]
    known = [value for value in durations if value is not None]

    if len(known) != len(values):
        return None, len(values)

    total = sum(known)

    if slowest_override_seconds is not None:
        try:
            override = float(slowest_override_seconds)
        except (TypeError, ValueError):
            override = None

        if override is not None and override > 0:
            # Conservative upper bound.  The optimized regular bracket may
            # extend its slow end beyond the explicit input list.
            total = max(total, override * len(values))

    return total, len(values)


def _execute_photo_estimate(params: Any) -> tuple[float | None, int | None]:
    if not isinstance(params, dict):
        return None, None

    exposure_plan = params.get("exposure_plan")
    if isinstance(exposure_plan, (list, tuple)) and exposure_plan:
        total = 0.0
        count = 0

        for item in exposure_plan:
            if not isinstance(item, dict):
                return None, len(exposure_plan)

            speed = (
                item.get("shutter")
                or item.get("speed")
                or item.get("shutter_speed")
            )
            seconds = _speed_seconds(speed)
            if seconds is None:
                return None, len(exposure_plan)

            frames = item.get("frames", item.get("expected_frames", 1))
            try:
                frame_count = max(1, int(frames))
            except (TypeError, ValueError):
                frame_count = 1

            total += seconds * frame_count
            count += frame_count

        return total, max(1, count)

    speeds = params.get("speeds")
    if isinstance(speeds, (list, tuple)) and speeds:
        return _speed_list_estimate(
            speeds,
            params.get("slowest_override_seconds"),
        )

    speed = (
        params.get("shutter")
        or params.get("speed")
        or params.get("shutter_speed")
    )
    seconds = _speed_seconds(speed)

    if seconds is None:
        return None, None

    frames = params.get(
        "expected_frames",
        params.get("frames", 1),
    )

    try:
        frame_count = max(1, int(frames))
    except (TypeError, ValueError):
        frame_count = 1

    return seconds * frame_count, frame_count


def camera_operation_timeout_s(
    operation: str,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    base_timeout_s: float,
) -> float:
    """Return the bounded wait appropriate for one camera operation."""

    base = max(0.001, float(base_timeout_s))

    if operation == "trigger_prepared" and args:
        estimate, count = _prepared_estimate(args[0])
        return _capture_timeout(estimate, count, base)

    if operation in {
        "shoot_speed_list",
        "test_photo",
        "test_photo_diagnostic",
    } and args:
        estimate, count = _speed_list_estimate(
            args[0],
            kwargs.get("slowest_override_seconds"),
        )
        return _capture_timeout(estimate, count, base)

    if operation in {
        "execute_photo",
        "test_photo_fast",
    } and args:
        params = args[0]

        # test_photo_fast receives a shutter string rather than a params dict.
        if operation == "test_photo_fast" and not isinstance(params, dict):
            params = {
                "shutter": params,
                "expected_frames": 1,
            }

        estimate, count = _execute_photo_estimate(params)
        return _capture_timeout(estimate, count, base)

    return base


__all__ = ["camera_operation_timeout_s"]
