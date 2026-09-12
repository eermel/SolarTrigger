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
    generated["TSTART"] = target_start.strftime("%H:%M:%S.%f")[:-3]
    generated["TEND"] = (
        timeline["C4"] + delta + timedelta(minutes=margin_min)
    ).strftime("%H:%M:%S.%f")[:-3]
    return generated


__all__ = ["generate_dryrun_now"]
