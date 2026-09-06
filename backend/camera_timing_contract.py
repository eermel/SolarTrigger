"""Operational budgets, deliberately distinct from physical shutter latency.

These are empirical reservations, not hard real-time guarantees. The policy is
explicit so a later hardware qualification can change it without hiding raw data.
"""
import math

POLICY = {"relative_margin": 0.10, "fixed_margin_ms": 50, "rounding_ms": 50,
          "basis": "maximum observed", "version": 2}


def budget_ms(samples):
    values = list(samples)
    if not values or any(not math.isfinite(v) or v < 0 for v in values):
        raise ValueError("Timing samples must be finite and nonnegative")
    peak = max(values)
    return int(math.ceil((peak * 1.10 + 50) / 50) * 50)


def photo_budget_ms(block, exposure_s):
    """Baseline contains exposure already. Only add positive excess once.

    Never extrapolate downwards from the measured baseline. Longer exposures
    receive the same relative guard as the other occupied intervals.
    """
    extra_ms = max(0, exposure_s - block["reference_exposure_s"]) * 1000
    return block["duration_ms"] + math.ceil(extra_ms * 1.10 / 50) * 50
