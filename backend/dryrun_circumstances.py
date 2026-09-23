"""Generate translated circumstances for a real-time dry run."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from backend.timeline import build_timeline


def generate_dryrun_now(
    circumstances: Mapping[str, Any],
    photo_setup: Mapping[str, Any],
    now_utc: datetime,
    *,
    delay_s: float = 300.0,
) -> dict[str, Any]:
    if now_utc.tzinfo is None:
        raise ValueError("now_utc must be timezone-aware")
    now_naive = now_utc.astimezone(timezone.utc).replace(tzinfo=None)
    timeline = build_timeline(circumstances, fallback_date=now_naive.date())
    margin_min = float(photo_setup.get("sequence_margin_min", 60))
    if margin_min < 0:
        raise ValueError("sequence_margin_min must be nonnegative")

    source_start = timeline["C1"] - timedelta(minutes=margin_min)
    target_start = now_naive + timedelta(seconds=delay_s)
    delta = target_start - source_start

    generated = deepcopy(dict(circumstances))
    generated["_date"] = target_start.date().isoformat()
    generated["_date_utc"] = target_start.date().isoformat()
    generated["_generated_utc"] = now_utc.astimezone(timezone.utc).isoformat()
    generated["_comment"] = "Temporary DRY-RUN NOW circumstances"
    for name in ("C1", "C2", "TMAX", "C3", "C4"):
        if timeline.get(name) is not None:
            generated[name] = (timeline[name] + delta).strftime("%H:%M:%S.%f")[:-3]
        # Local values belong to the source eclipse and become false after
        # translating UTC contacts.  The UI derives local display values from
        # the translated UTC time and the current configured timezone.
        generated.pop(f"{name}_local", None)
    generated["TSTART"] = target_start.strftime("%H:%M:%S.%f")[:-3]
    generated["TEND"] = (
        timeline["C4"] + delta + timedelta(minutes=margin_min)
    ).strftime("%H:%M:%S.%f")[:-3]
    return generated


DEBUG_TSTART_DELAY_S = 60
DEBUG_C1_AFTER_TSTART_S = 5 * 60 + 12
DEBUG_C2_AFTER_C1_S = 6 * 60 + 6
DEBUG_C3_AFTER_C2_S = 3 * 60 + 24
DEBUG_C4_AFTER_C3_S = 5 * 60 + 12
DEBUG_TEND_AFTER_C4_S = 3 * 60 + 18


def generate_debug_now(now_utc: datetime) -> dict[str, Any]:
    """Build the short real-time DEBUG circumstances scenario."""
    if now_utc.tzinfo is None:
        raise ValueError("now_utc must be timezone-aware")
    now_naive = now_utc.astimezone(timezone.utc).replace(tzinfo=None)
    tstart = now_naive + timedelta(seconds=DEBUG_TSTART_DELAY_S)
    c1 = tstart + timedelta(seconds=DEBUG_C1_AFTER_TSTART_S)
    c2 = c1 + timedelta(seconds=DEBUG_C2_AFTER_C1_S)
    c3 = c2 + timedelta(seconds=DEBUG_C3_AFTER_C2_S)
    c4 = c3 + timedelta(seconds=DEBUG_C4_AFTER_C3_S)
    tend = c4 + timedelta(seconds=DEBUG_TEND_AFTER_C4_S)
    tmax = c2 + (c3 - c2) / 2
    def hms(value: datetime) -> str:
        return value.strftime("%H:%M:%S.%f")[:-3]
    return {
        "_date": tstart.date().isoformat(),
        "_date_utc": tstart.date().isoformat(),
        "_generated_utc": now_utc.astimezone(timezone.utc).isoformat(),
        "_comment": "Temporary DEBUG circumstances",
        "_debug_scenario": True,
        "_type": "Total",
        "_type_global": "Total",
        "title": "DEBUG scenario",

        # Synthetic but complete atmospheric context.
        #
        # DEBUG is a functional scenario, not an astronomical prediction.
        # Keep the Sun below the 30-degree atmospheric-compensation threshold
        # so an enabled Exposure Optimization actually exercises that path.
        "_circumstances_location": {
            "latitude_deg": 0.0,
            "longitude_deg": 0.0,
            "altitude_m": 0.0,
            "source": "debug_synthetic",
        },
        "C1_alt_deg": 20.0,
        "C2_alt_deg": 18.0,
        "TMAX_alt_deg": 17.0,
        "C3_alt_deg": 16.0,
        "C4_alt_deg": 14.0,

        "TSTART": hms(tstart), "C1": hms(c1), "C2": hms(c2),
        "TMAX": hms(tmax), "C3": hms(c3), "C4": hms(c4), "TEND": hms(tend),
    }


__all__ = ["generate_dryrun_now", "generate_debug_now"]
