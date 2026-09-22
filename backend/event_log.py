from __future__ import annotations
import json, threading, time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

class EventLog:
    def __init__(self, path: Path, size: int = 500, emit_fn=None):
        self.path = Path(path); self.size = size; self.emit_fn = emit_fn
        self.buffer = deque(maxlen=size); self.lock = threading.Lock()

    def reset(self):
        # Buffer and backing file form one logical log.  Keep the same lock
        # across both so a concurrent append cannot be erased after it returns.
        with self.lock:
            self.buffer.clear()
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_text("", encoding="utf-8")
            except Exception:
                pass

    def append(self, text, level="info", source="system", rig_id=None):
        entry = {"text": str(text), "level": level, "source": source,
                 "timestamp": datetime.now(timezone.utc).strftime("%H:%M:%S")}
        if rig_id is not None:
            entry["rig_id"] = int(rig_id)
        with self.lock:
            self.buffer.append(entry)
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            except Exception: pass
        if self.emit_fn:
            try:
                self.emit_fn("log_line", entry)
            except Exception:
                # Logging/UI observability is never authoritative for capture.
                pass
        return entry

    def snapshot(self):
        with self.lock: return list(self.buffer)

    def trim_forever(self, interval=300):
        while True:
            time.sleep(interval)
            # append(), reset() and trim all mutate the same backing file.
            # Serializing them prevents a rewrite from dropping an append that
            # happened between trim's read and write.
            with self.lock:
                try:
                    lines = self.path.read_text(encoding="utf-8").splitlines(True)
                    if len(lines) > self.size:
                        self.path.parent.mkdir(parents=True, exist_ok=True)
                        self.path.write_text(
                            "".join(lines[-self.size:]),
                            encoding="utf-8",
                        )
                except Exception:
                    pass
