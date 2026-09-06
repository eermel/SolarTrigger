"""Camera timing budgets used by characterization and plan compilation.

Contract v3 deliberately stores only operational budgets. Raw observations stay in
configs/camera_characterization/measurements and are never required by Trigger.
"""
from __future__ import annotations

import math
from typing import Iterable, Mapping, Sequence

SAFETY_POLICY = {
    "basis": "maximum_observed",
    "relative_margin_percent": 10,
    "fixed_margin_ms": 50,
    "rounding_ms": 50,
    "order": [
        "maximum_observed",
        "multiply_by_1.10",
        "add_50_ms",
        "round_up_to_50_ms",
    ],
    "formula": "ceil50(max_observed_ms * 1.10 + 50)",
    "version": 3,
}

# Compatibility name used by contract-v2 tests/code.
POLICY = SAFETY_POLICY


def _finite_nonnegative(value, field="timing"):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be numeric")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError(f"{field} must be finite and nonnegative")
    return result


def _round_up_50(value_ms: float) -> int:
    return int(math.ceil(value_ms / 50.0) * 50)


def budget_ms(samples: Iterable[float]) -> int:
    """Apply the agreed safety policy to the maximum observed sample."""
    values = [_finite_nonnegative(value) for value in samples]
    if not values:
        raise ValueError("Timing samples must not be empty")
    peak = max(values)
    return _round_up_50(peak * 1.10 + 50.0)


def single_photo_duration_ms(single_overhead_ms: float, exposure_s: float) -> float:
    """PHOTO(single) = exposure + fixed characterized overhead."""
    overhead = _finite_nonnegative(single_overhead_ms, "single_overhead_ms")
    exposure = _finite_nonnegative(exposure_s, "exposure_s")
    return overhead + exposure * 1000.0


def bracket_photo_duration_ms(
    bracket_overhead_ms: float,
    bracket_inter_image_ms: float,
    exposures_s: Sequence[float],
) -> float:
    """PHOTO(bracket N) = sum(exposures) + fixed overhead + (N-1)*inter-image."""
    fixed = _finite_nonnegative(bracket_overhead_ms, "bracket_overhead_ms")
    inter = _finite_nonnegative(bracket_inter_image_ms, "bracket_inter_image_ms")
    exposures = [_finite_nonnegative(value, "exposure_s") for value in exposures_s]
    if not exposures:
        raise ValueError("bracket exposures must not be empty")
    return sum(exposures) * 1000.0 + fixed + (len(exposures) - 1) * inter


def derive_bracket_components(
    overhead_samples_by_frames: Mapping[int, Iterable[float]],
) -> dict:
    """Derive a conservative fixed/inter-image decomposition from measured brackets.

    For each supported frame count we first retain the maximum measured overhead
    after subtracting the known exposure times. The inter-image component is the
    largest positive slope between any two measured sizes. The fixed component is
    then the largest residual needed to cover every measured size.

    With one bracket size the inter-image component is not identifiable; the raw
    slope is therefore zero and all measured overhead is folded into the fixed
    component. Applying budget_ms() later still gives the inter-image term its
    normal 50 ms engineering guard.
    """
    peaks: dict[int, float] = {}
    for raw_frames, samples in overhead_samples_by_frames.items():
        if isinstance(raw_frames, bool):
            raise ValueError("bracket frame count must be an integer")
        frames = int(raw_frames)
        if frames <= 1:
            raise ValueError("bracket frame count must be > 1")
        values = [_finite_nonnegative(value, f"bracket[{frames}]") for value in samples]
        if not values:
            raise ValueError(f"bracket[{frames}] samples must not be empty")
        peaks[frames] = max(values)

    if not peaks:
        raise ValueError("at least one bracket size is required")

    sizes = sorted(peaks)
    slopes = []
    for index, first in enumerate(sizes):
        for second in sizes[index + 1:]:
            slopes.append(max(0.0, (peaks[second] - peaks[first]) / (second - first)))

    raw_inter = max(slopes, default=0.0)
    raw_fixed = max(
        max(0.0, peaks[frames] - (frames - 1) * raw_inter)
        for frames in sizes
    )

    return {
        "peak_overhead_ms_by_frames": {str(key): peaks[key] for key in sizes},
        "raw_bracket_overhead_ms": raw_fixed,
        "raw_bracket_inter_image_ms": raw_inter,
    }


def _multiple_of_50(value, field, *, allow_zero=False):
    numeric = _finite_nonnegative(value, field)
    if not allow_zero and numeric <= 0:
        raise ValueError(f"{field} must be > 0")
    if abs(numeric / 50.0 - round(numeric / 50.0)) > 1e-9:
        raise ValueError(f"{field} must be a multiple of 50 ms")
    return int(round(numeric))


def validate_timing_contract_v3(contract, *, bracket_frames=None):
    if not isinstance(contract, dict) or contract.get("version") != 3:
        raise ValueError("unsupported camera timing contract")

    policy = contract.get("safety_policy")
    if not isinstance(policy, dict):
        raise ValueError("timing contract safety_policy is required")
    for key, expected in (
        ("basis", "maximum_observed"),
        ("relative_margin_percent", 10),
        ("fixed_margin_ms", 50),
        ("rounding_ms", 50),
        ("formula", "ceil50(max_observed_ms * 1.10 + 50)"),
    ):
        if policy.get(key) != expected:
            raise ValueError(f"invalid safety_policy.{key}")

    _multiple_of_50(contract.get("set_overhead_ms"), "set_overhead_ms")
    _multiple_of_50(contract.get("single_overhead_ms"), "single_overhead_ms")

    raw_supported = contract.get("supported_bracket_frames", [])
    if not isinstance(raw_supported, list):
        raise ValueError("supported_bracket_frames must be an array")
    supported = []
    for raw in raw_supported:
        if isinstance(raw, bool):
            raise ValueError("supported bracket frame count must be an integer")
        frames = int(raw)
        if frames <= 1 or frames % 2 == 0:
            raise ValueError("supported bracket frame count must be odd and > 1")
        supported.append(frames)
    if supported != sorted(set(supported)):
        raise ValueError("supported_bracket_frames must be sorted and unique")

    if bracket_frames is not None:
        expected = sorted(int(value) for value in bracket_frames)
        if supported != expected:
            raise ValueError("timing/profile bracket frame mismatch")

    has_brackets = bool(supported)
    _multiple_of_50(
        contract.get("bracket_overhead_ms", 0),
        "bracket_overhead_ms",
        allow_zero=not has_brackets,
    )
    _multiple_of_50(
        contract.get("bracket_inter_image_ms", 0),
        "bracket_inter_image_ms",
        allow_zero=not has_brackets,
    )
    if not has_brackets and (
        contract.get("bracket_overhead_ms", 0) != 0
        or contract.get("bracket_inter_image_ms", 0) != 0
    ):
        raise ValueError("bracket budgets must be zero when no bracket is supported")
    return contract


# Contract-v2 compatibility. Existing profiles remain readable until they are
# deliberately re-characterized; v3 code never calls this function.
def photo_budget_ms(block, exposure_s):
    """Legacy v2 baseline/excess calculation."""
    extra_ms = max(
        0.0,
        _finite_nonnegative(exposure_s, "exposure_s")
        - _finite_nonnegative(block["reference_exposure_s"], "reference_exposure_s"),
    ) * 1000.0
    return _finite_nonnegative(block["duration_ms"], "duration_ms") + math.ceil(
        extra_ms * 1.10 / 50.0
    ) * 50.0
