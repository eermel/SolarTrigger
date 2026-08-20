from __future__ import annotations

from datetime import date, datetime, time as dt_time, timedelta

EVENT_ORDER = ("TSTART", "C1", "C2", "TMAX", "C3", "C4", "TEND")
VALIDATION_ORDER = ("TSTART", "C1", "C2", "C3", "C4", "TEND")


def parse_hms_seconds(value: str | None) -> float | None:
    """Return seconds since midnight for HH:MM:SS[.fraction]."""
    if value is None or str(value).strip() == "":
        return None
    parts = str(value).strip().split(":")
    if len(parts) != 3:
        raise ValueError(f"Heure invalide: {value!r}")
    h, m, sec = int(parts[0]), int(parts[1]), float(parts[2])
    if not (0 <= h <= 23 and 0 <= m <= 59 and 0 <= sec < 60):
        raise ValueError(f"Heure hors limites: {value!r}")
    return h * 3600.0 + m * 60.0 + sec


def format_hms_ms(value: datetime) -> str:
    """Human/JSON representation preserving millisecond precision."""
    return value.strftime("%H:%M:%S.%f")[:-3]


def parse_date_from_config(cfg: dict, fallback: date | None = None) -> date:
    """Canonical circumstances date. `_date` is v7.1; `_date_utc` is legacy."""
    for key in ("_date", "_date_utc"):
        value = cfg.get(key)
        if value:
            try:
                return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
            except ValueError:
                pass
    generated = cfg.get("_generated_utc")
    if generated:
        try:
            return datetime.strptime(str(generated)[:10], "%Y-%m-%d").date()
        except ValueError:
            pass
    if fallback is not None:
        return fallback
    raise ValueError("Date des circonstances absente (_date)")


def datetime_from_hms(day: date, value: str) -> datetime:
    seconds = parse_hms_seconds(value)
    if seconds is None:
        raise ValueError("Heure absente")
    return datetime.combine(day, dt_time()) + timedelta(seconds=seconds)


def unfold_datetimes(values: dict[str, datetime], order=EVENT_ORDER) -> dict[str, datetime]:
    """Unfold a physical sequence over midnight while preserving sub-second offsets."""
    out: dict[str, datetime] = {}
    prev: datetime | None = None
    day_shift = timedelta(0)
    for name in order:
        dt = values.get(name)
        if dt is None:
            continue
        candidate = dt + day_shift
        if prev is not None and candidate < prev:
            day_shift += timedelta(days=1)
            candidate = dt + day_shift
        out[name] = candidate
        prev = candidate
    return out


def build_timeline(cfg: dict, *, fallback_date: date | None = None) -> dict[str, datetime]:
    """Build the real circumstances timeline from `_date` + independent UTC times."""
    day = parse_date_from_config(cfg, fallback=fallback_date)
    raw = {}
    for name in EVENT_ORDER:
        value = cfg.get(name)
        if value:
            raw[name] = datetime_from_hms(day, value)
    return unfold_datetimes(raw)


def rebase_timeline(timeline: dict[str, datetime], new_tstart: datetime) -> dict[str, datetime]:
    """Translate a timeline exactly: every pairwise interval is preserved."""
    origin = timeline["TSTART"]
    return {name: new_tstart + (dt - origin) for name, dt in timeline.items()}


def sequence_seconds(cfg: dict, order=VALIDATION_ORDER) -> list[float | None]:
    """Seconds sequence unfolded over midnight, retaining decimal seconds."""
    out=[]; prev=None; offset=0.0
    for key in order:
        raw=parse_hms_seconds(cfg.get(key))
        if raw is None:
            out.append(None); continue
        current=raw+offset
        if prev is not None and current < prev:
            offset += 86400.0
            current = raw+offset
        prev=current; out.append(current)
    return out
