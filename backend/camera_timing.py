"""Persistent calibrated camera timing profiles.

These values describe measured hardware/USB behaviour. They are deliberately
separate from the RIG topology and from the Sequencer execution plan.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from backend.sequencer_compiler import CameraTimingProfile


_TIMING_FIELDS = (
    "set_iso_ms",
    "set_capturemode_ms",
    "set_shutter_ms",
    "trigger_single_latency_ms",
    "trigger_single_duration_ms",
    "bracket_press_latency_ms",
    "bracket_release_ms",
    "settle_idle_ms",
)


def _nonnegative_ms(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be numeric")

    result = float(value)

    if not math.isfinite(result) or result < 0:
        raise ValueError(f"{field} must be finite and >= 0")

    return result


def load_camera_timing_profile(path: str | Path) -> CameraTimingProfile:
    path = Path(path)

    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)

    if not isinstance(data, dict):
        raise ValueError("camera timing profile must be an object")

    if data.get("config_type") != "camera_timing":
        raise ValueError("invalid camera timing config_type")

    backend = str(data.get("backend") or "").strip().lower()

    if not backend:
        raise ValueError("camera timing backend is required")

    timing = data.get("timing")

    if not isinstance(timing, dict):
        raise ValueError("camera timing block is required")

    values = {
        field: _nonnegative_ms(
            timing.get(field, 0),
            field,
        )
        for field in _TIMING_FIELDS
    }

    raw_bracket_atomic = timing.get(
        "bracket_atomic_ms_by_frames",
        {},
    )

    if not isinstance(raw_bracket_atomic, dict):
        raise ValueError(
            "bracket_atomic_ms_by_frames must be an object"
        )

    bracket_atomic_ms_by_frames: dict[int, float] = {}

    for raw_frames, raw_duration in raw_bracket_atomic.items():
        try:
            frames = int(raw_frames)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "bracket_atomic_ms_by_frames keys must be integers"
            ) from exc

        if frames <= 0:
            raise ValueError(
                "bracket_atomic_ms_by_frames keys must be > 0"
            )

        bracket_atomic_ms_by_frames[frames] = _nonnegative_ms(
            raw_duration,
            f"bracket_atomic_ms_by_frames[{frames}]",
        )

    return CameraTimingProfile(
        backend=backend,
        bracket_atomic_ms_by_frames=bracket_atomic_ms_by_frames,
        **values,
    )


def load_camera_timing_document(path: str | Path) -> dict[str, Any]:
    """Return and validate the complete persistent document."""

    path = Path(path)

    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)

    # Validation through the canonical loader.
    load_camera_timing_profile(path)

    return data


__all__ = [
    "load_camera_timing_document",
    "load_camera_timing_profile",
]
