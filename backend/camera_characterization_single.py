"""Single-photo characterization selection and rearm helpers."""
from __future__ import annotations

from copy import deepcopy
import math
import time

from backend.camera_characterization_types import Cancelled
from plugins.camera.profile import ProfilePlugin


SINGLE_TRIGGER_CAPTURE_TIE_MS = 50
SINGLE_REARM_STEP_MS = 50
SINGLE_REARM_REPETITIONS = 5
SINGLE_REARM_MAX_MS = 5000
SINGLE_REARM_SUSTAINED_FRAMES = 15
SINGLE_REARM_SUSTAINED_REPETITIONS = 3
SINGLE_REARM_EXPOSURE_REPETITIONS = 5
SINGLE_REARM_EXPOSURE_REGIMES = (
    ("fast", "1/1000", "1/500"),
    ("medium", "1/30", "1/15"),
    ("long", "1", "2"),
    ("very_long", "2", "4"),
)


def _select_single_trigger_candidate(evidence):
    """Select a stable single trigger without reacting to sub-grid timing noise.

    The capture method is operationally simpler than trigger_capture because it
    does not require a separate trigger-return -> shutter-SET rearm contract.
    If a reliable capture candidate is within one 50 ms safety grid step of
    the fastest reliable candidate, prefer it. Larger measured advantages
    remain authoritative, so cameras such as the D850 can still select
    trigger_capture when it is materially faster.
    """
    from backend.camera_candidate_optimizer import select_best

    fastest = select_best(evidence)
    capture_candidates = [
        item
        for item in evidence
        if item.reliable
        and isinstance(item.recipe, dict)
        and item.recipe.get("method") == "capture"
    ]
    if not capture_candidates:
        return fastest

    capture = min(
        capture_candidates,
        key=lambda item: (item.peak_ms, item.median_ms, item.candidate_id),
    )
    if capture.peak_ms <= fastest.peak_ms + SINGLE_TRIGGER_CAPTURE_TIE_MS:
        return capture
    return fastest

def _operational_ready_timings(
    operation_begin,
    ready_origin,
    successful_ready_started,
    successful_ready_verified,
):
    """Separate runtime readiness from characterization-only proof latency."""
    values = [
        float(operation_begin),
        float(ready_origin),
        float(successful_ready_started),
        float(successful_ready_verified),
    ]
    if not all(math.isfinite(value) for value in values):
        raise ValueError("camera readiness timestamps must be finite")
    if not (
        operation_begin
        <= ready_origin
        <= successful_ready_started
        <= successful_ready_verified
    ):
        raise ValueError("camera readiness timestamps are not monotonic")
    return {
        "usb_return_ms": (
            successful_ready_started - ready_origin
        ) * 1000.0,
        "runtime_total_ms": (
            successful_ready_started - operation_begin
        ) * 1000.0,
        "verification_ms": (
            successful_ready_verified - successful_ready_started
        ) * 1000.0,
        "wall_ms": (
            successful_ready_verified - operation_begin
        ) * 1000.0,
    }


def _ceil_rearm_step_ms(value_ms, step_ms=SINGLE_REARM_STEP_MS):
    if isinstance(value_ms, bool) or not isinstance(value_ms, (int, float)):
        raise ValueError("single rearm delay must be numeric")
    if not math.isfinite(float(value_ms)) or float(value_ms) < 0:
        raise ValueError("single rearm delay must be finite and nonnegative")
    if not isinstance(step_ms, int) or isinstance(step_ms, bool) or step_ms <= 0:
        raise ValueError("single rearm step must be a positive integer")
    return int(math.ceil(float(value_ms) / step_ms) * step_ms)


def _settle_characterization_readback(
    read_value,
    target,
    *,
    check=None,
    sleep_fn=time.sleep,
    delays_s=None,
):
    """Wait for an untimed characterization SET readback to converge.

    Sony bodies can acknowledge set_single_config() before a fresh get_config()
    reflects the new value. This helper is only for pre-probe/baseline evidence;
    it must never be inserted between the measured rearm SET and trigger.
    """
    if not callable(read_value):
        raise ValueError("characterization readback requires a callable reader")
    if delays_s is None:
        delays_s = ProfilePlugin.PREFLIGHT_SETTLE_DELAYS_S

    last = None
    last_error = None
    attempts = [0.0, *tuple(float(value) for value in delays_s)]
    for index, delay_s in enumerate(attempts):
        if check is not None:
            check()
        if index and delay_s > 0:
            sleep_fn(delay_s)
        try:
            last = read_value()
            last_error = None
        except Exception as exc:
            last_error = exc
            continue
        if str(last) == str(target):
            return last

    if last is None and last_error is not None:
        raise RuntimeError(
            "characterization readback unavailable after settling: "
            f"{type(last_error).__name__}: {last_error}"
        ) from last_error
    return last


def _search_single_rearm_ms(
    probe_candidate,
    recover_after_failure,
    *,
    start_ms,
    step_ms=SINGLE_REARM_STEP_MS,
    max_ms=SINGLE_REARM_MAX_MS,
):
    """Find the lowest stable trigger-return -> next-SET delay on a fixed grid.

    The hardware probe owns the repetition count and must fail closed when any
    SET, immediate following trigger, readback, or file-count check fails.
    Search is monotonic: once a delay is stable, longer delays are assumed safe.
    A final forced verification is always performed after the binary search so
    the published threshold is backed by a fresh hardware pass.
    """
    if not callable(probe_candidate) or not callable(recover_after_failure):
        raise ValueError("single rearm search requires probe and recovery callbacks")
    if not isinstance(step_ms, int) or isinstance(step_ms, bool) or step_ms <= 0:
        raise ValueError("single rearm step must be a positive integer")
    if not isinstance(max_ms, int) or isinstance(max_ms, bool) or max_ms < step_ms:
        raise ValueError("single rearm maximum must be >= one step")

    ceiling_ms = _ceil_rearm_step_ms(max_ms, step_ms)
    start_ms = min(
        ceiling_ms,
        max(step_ms, _ceil_rearm_step_ms(start_ms, step_ms)),
    )
    outcomes = {}
    evidence = []

    def run(delay_ms, *, verification=False):
        if not verification and delay_ms in outcomes:
            return outcomes[delay_ms]
        try:
            detail = probe_candidate(delay_ms)
        except Cancelled:
            raise
        except Exception as exc:
            evidence.append(
                {
                    "delay_ms": int(delay_ms),
                    "passed": False,
                    "verification": bool(verification),
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            outcomes[delay_ms] = False
            recover_after_failure(delay_ms, exc)
            return False

        evidence.append(
            {
                "delay_ms": int(delay_ms),
                "passed": True,
                "verification": bool(verification),
                "detail": deepcopy(detail),
            }
        )
        outcomes[delay_ms] = True
        return True

    high_ms = start_ms
    while not run(high_ms):
        if high_ms >= ceiling_ms:
            raise RuntimeError(
                "No stable single-photo rearm delay found within "
                f"{ceiling_ms} ms"
            )
        high_ms = min(ceiling_ms, high_ms + max(step_ms, 250))

    if run(0):
        minimum_ms = 0
    else:
        low_units = 1
        high_units = high_ms // step_ms
        while low_units < high_units:
            mid_units = (low_units + high_units) // 2
            candidate_ms = mid_units * step_ms
            if run(candidate_ms):
                high_units = mid_units
            else:
                low_units = mid_units + 1
        minimum_ms = low_units * step_ms

    verified_ms = minimum_ms
    while not run(verified_ms, verification=True):
        verified_ms += step_ms
        if verified_ms > ceiling_ms:
            raise RuntimeError(
                "Single-photo rearm threshold was not repeatable within "
                f"{ceiling_ms} ms"
            )

    return {
        "step_ms": step_ms,
        "minimum_stable_ms": int(verified_ms),
        "tested": evidence,
    }


def _qualify_guarded_single_rearm_ms(
    probe_candidate,
    recover_after_failure,
    *,
    start_ms,
    step_ms=SINGLE_REARM_STEP_MS,
    max_ms=SINGLE_REARM_MAX_MS,
):
    """Find the first guarded rearm delay accepted by a hardware qualification.

    The supplied probe owns the qualification policy (sustained burst or
    multi-exposure transitions). A candidate is publishable only when the full
    probe passes. Failed candidates recover the camera session and advance by
    one fixed grid step.
    """
    if not callable(probe_candidate) or not callable(recover_after_failure):
        raise ValueError(
            "sustained single rearm qualification requires probe and recovery callbacks"
        )
    if not isinstance(step_ms, int) or isinstance(step_ms, bool) or step_ms <= 0:
        raise ValueError("single rearm step must be a positive integer")
    if not isinstance(max_ms, int) or isinstance(max_ms, bool) or max_ms < step_ms:
        raise ValueError("single rearm maximum must be >= one step")

    ceiling_ms = _ceil_rearm_step_ms(max_ms, step_ms)
    candidate_ms = _ceil_rearm_step_ms(start_ms, step_ms)
    if candidate_ms > ceiling_ms:
        raise RuntimeError(
            "Guarded single-photo rearm delay exceeds sustained qualification ceiling"
        )

    evidence = []
    while candidate_ms <= ceiling_ms:
        try:
            detail = probe_candidate(candidate_ms)
        except Cancelled:
            raise
        except Exception as exc:
            evidence.append(
                {
                    "delay_ms": int(candidate_ms),
                    "passed": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            )
            recover_after_failure(candidate_ms, exc)
            candidate_ms += step_ms
            continue

        evidence.append(
            {
                "delay_ms": int(candidate_ms),
                "passed": True,
                "detail": deepcopy(detail),
            }
        )
        return {
            "step_ms": step_ms,
            "stable_ms": int(candidate_ms),
            "tested": evidence,
        }

    raise RuntimeError(
        "No sustained single-photo rearm delay passed within "
        f"{ceiling_ms} ms"
    )


