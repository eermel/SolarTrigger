"""Thread-safe JSONL tracing for rig events."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock


TRIGGER_DIR = Path(__file__).resolve().parent.parent
_PATH = TRIGGER_DIR / "rig_traces.jsonl"
_LOCK = Lock()


def append(event: dict) -> dict:
    """Append an event to the rig trace and return that event."""

    event.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
    line = json.dumps(event, separators=(",", ":"))
    with _LOCK:
        with _PATH.open("a", encoding="utf-8") as trace_file:
            trace_file.write(f"{line}\n")
    return event


def trace_event(kind: str, payload: dict) -> dict:
    """Trace a payload with its event kind."""

    if not isinstance(payload, dict):
        raise TypeError("payload must be a dict")

    event = dict(payload)
    event["kind"] = kind
    return append(event)
