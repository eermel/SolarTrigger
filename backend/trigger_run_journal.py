"""Minimal persistent journal for recovering an active Trigger run.

The journal is deliberately low-write: begin/recovery/final state transitions only.
Scheduler heartbeat/progress is kept in memory and never written here.
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import threading
import uuid


SCHEMA_VERSION = 1
ACTIVE_STATUS = "active"
FINAL_STATUSES = frozenset({"completed", "stopped", "failed"})


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def current_boot_id() -> str | None:
    """Return the Linux boot ID when available.

    A missing boot ID is intentionally represented as None. Automatic runtime
    recovery requires an exact, non-empty boot-ID match and therefore fails
    closed on unsupported/unreadable systems.
    """
    try:
        value = Path("/proc/sys/kernel/random/boot_id").read_text(
            encoding="ascii"
        ).strip()
    except (OSError, UnicodeError):
        return None
    return value or None


class TriggerRunJournal:
    def __init__(self, path: Path, *, boot_id_fn=current_boot_id):
        self.path = Path(path)
        self._boot_id_fn = boot_id_fn
        self._lock = threading.RLock()

    @property
    def boot_id(self) -> str | None:
        return self._boot_id_fn()

    def _empty(self) -> dict:
        return {"schema_version": SCHEMA_VERSION, "rigs": {}}

    def _read_unlocked(self) -> dict:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return self._empty()
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            # Corrupt recovery metadata must never be interpreted as a live run.
            return self._empty()
        if not isinstance(raw, dict) or raw.get("schema_version") != SCHEMA_VERSION:
            return self._empty()
        rigs = raw.get("rigs")
        if not isinstance(rigs, dict):
            return self._empty()
        return raw

    def _write_unlocked(self, payload: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        tmp.replace(self.path)

    def snapshot(self) -> dict:
        with self._lock:
            return copy.deepcopy(self._read_unlocked())

    def begin_run(
        self,
        *,
        rig_id: int,
        mode: str,
        selected: dict | None,
        speed: float = 1.0,
        totality_only: bool = False,
    ) -> dict:
        now = utc_now_iso()
        entry = {
            "run_id": uuid.uuid4().hex,
            "rig_id": int(rig_id),
            "status": ACTIVE_STATUS,
            "boot_id": self.boot_id,
            "mode": str(mode),
            "speed": float(speed),
            "totality_only": bool(totality_only),
            "selected": copy.deepcopy(selected or {}),
            "started_utc": now,
            "updated_utc": now,
            "runtime_recovery_count": 0,
            "child_recovery_count": 0,
        }
        with self._lock:
            payload = self._read_unlocked()
            payload["rigs"][str(int(rig_id))] = entry
            self._write_unlocked(payload)
        return copy.deepcopy(entry)

    def active_entries(self) -> tuple[dict, ...]:
        with self._lock:
            payload = self._read_unlocked()
            entries = []
            for value in payload["rigs"].values():
                if isinstance(value, dict) and value.get("status") == ACTIVE_STATUS:
                    entries.append(copy.deepcopy(value))
        return tuple(sorted(entries, key=lambda item: int(item.get("rig_id", 0))))

    def _mutate_active(self, rig_id: int, run_id: str, fn) -> dict | None:
        with self._lock:
            payload = self._read_unlocked()
            key = str(int(rig_id))
            entry = payload["rigs"].get(key)
            if (
                not isinstance(entry, dict)
                or entry.get("status") != ACTIVE_STATUS
                or entry.get("run_id") != run_id
            ):
                return None
            fn(entry)
            entry["updated_utc"] = utc_now_iso()
            self._write_unlocked(payload)
            return copy.deepcopy(entry)

    def claim_runtime_recovery(
        self,
        *,
        rig_id: int,
        run_id: str,
        max_recoveries: int = 1,
    ) -> dict | None:
        def mutate(entry):
            count = int(entry.get("runtime_recovery_count", 0) or 0)
            if count >= int(max_recoveries):
                raise RuntimeError("runtime recovery already consumed")
            entry["runtime_recovery_count"] = count + 1

        try:
            return self._mutate_active(rig_id, run_id, mutate)
        except RuntimeError:
            return None

    def note_child_recovery(
        self,
        *,
        rig_id: int,
        run_id: str,
        max_recoveries: int = 1,
    ) -> dict | None:
        def mutate(entry):
            count = int(entry.get("child_recovery_count", 0) or 0)
            if count >= int(max_recoveries):
                raise RuntimeError("child recovery already consumed")
            entry["child_recovery_count"] = count + 1

        try:
            return self._mutate_active(rig_id, run_id, mutate)
        except RuntimeError:
            return None

    def finish(
        self,
        *,
        rig_id: int,
        run_id: str | None,
        status: str,
        failure_code: str | None = None,
        detail: str | None = None,
        exit_code=None,
    ) -> dict | None:
        if status not in FINAL_STATUSES:
            raise ValueError(f"invalid final trigger status: {status}")
        with self._lock:
            payload = self._read_unlocked()
            key = str(int(rig_id))
            entry = payload["rigs"].get(key)
            if not isinstance(entry, dict):
                return None
            if run_id is not None and entry.get("run_id") != run_id:
                return None
            entry["status"] = status
            entry["updated_utc"] = utc_now_iso()
            entry["finished_utc"] = entry["updated_utc"]
            if failure_code is not None:
                entry["failure_code"] = str(failure_code)
            if detail is not None:
                entry["detail"] = str(detail)
            if exit_code is not None:
                entry["exit_code"] = exit_code
            self._write_unlocked(payload)
            return copy.deepcopy(entry)
