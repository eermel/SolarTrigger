"""Thread-safe JSON-lines appender for per-rig trace events."""

from __future__ import annotations

import json
import threading
from pathlib import Path

from backend.runtime_paths import RIG_TRACES_FILE


class RigTraceLog:
    """Append structured trace entries to a JSONL file."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def append(self, entry: dict) -> None:
        line = json.dumps(entry, separators=(",", ":"))
        with self._lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.path, "a", encoding="utf-8") as trace_file:
                    trace_file.write(line + "\n")
            except (OSError, IOError):
                pass


_default_log: RigTraceLog | None = None
_default_log_lock = threading.Lock()


def get_default_log() -> RigTraceLog:
    """Return the process-wide trace log for the repository root."""

    global _default_log
    if _default_log is None:
        with _default_log_lock:
            if _default_log is None:
                _default_log = RigTraceLog(RIG_TRACES_FILE)
    return _default_log
