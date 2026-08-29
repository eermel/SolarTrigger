from __future__ import annotations

import json
from os import PathLike

from backend.timeline import build_timeline


ALTITUDE_KEYS = (
    "C1_alt_deg",
    "C2_alt_deg",
    "TMAX_alt_deg",
    "C3_alt_deg",
    "C4_alt_deg",
)


def load_eclipse_context(json_path: str | PathLike[str]) -> dict:
    """Load the eclipse timeline and atmospheric inputs used by preview."""
    empty = {"timeline": {}, "altitudes": {}, "observer_alt_m": None}
    try:
        with open(json_path, encoding="utf-8") as stream:
            config = json.load(stream)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return empty

    if not isinstance(config, dict):
        return empty

    try:
        timeline = build_timeline(config)
    except (TypeError, ValueError):
        timeline = {}

    altitudes = {key: config[key] for key in ALTITUDE_KEYS if key in config}
    location = config.get("_circumstances_location")
    raw_observer_alt_m = location.get("altitude_m") if isinstance(location, dict) else None
    try:
        observer_alt_m = float(raw_observer_alt_m) if raw_observer_alt_m is not None else None
    except (TypeError, ValueError):
        observer_alt_m = None

    return {
        "timeline": timeline,
        "altitudes": altitudes,
        "observer_alt_m": observer_alt_m,
    }
