"""Minimal persistent journal for recovering an active Trigger run.

The journal is deliberately low-write: begin/recovery/final state transitions only.
Scheduler heartbeat/progress is kept in memory and never written here.
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import threading
import uuid


SCHEMA_VERSION = 1
ACTIVE_STATUS = "active"
FINAL_STATUSES = frozenset({"completed", "stopped", "failed"})


class TriggerRunJournalInvalid(RuntimeError):
    """Recovery journal exists but cannot be trusted or decoded."""


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

    def _invalid(self, reason: str, exc: Exception | None = None):
        error = TriggerRunJournalInvalid(
            f"{self.path}: {reason}"
        )
        if exc is not None:
            raise error from exc
        raise error

    def _read_unlocked(self) -> dict:
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return self._empty()
        except (OSError, UnicodeError) as exc:
            self._invalid(
                f"recovery journal is unreadable ({type(exc).__name__}: {exc})",
                exc,
            )

        try:
            raw = json.loads(text)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            self._invalid(
                f"recovery journal contains invalid JSON ({exc})",
                exc,
            )

        if not isinstance(raw, dict):
            self._invalid("recovery journal root must be an object")
        if raw.get("schema_version") != SCHEMA_VERSION:
            self._invalid(
                "unsupported or missing recovery journal schema_version"
            )
        rigs = raw.get("rigs")
        if not isinstance(rigs, dict):
            self._invalid("recovery journal rigs must be an object")
        return raw

    def _write_unlocked(self, payload: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        encoded = json.dumps(
            payload,
            indent=2,
            ensure_ascii=False,
            sort_keys=True,
        )
        try:
            with tmp.open("w", encoding="utf-8") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())

            os.replace(tmp, self.path)

            flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            directory_fd = os.open(self.path.parent, flags)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            try:
                tmp.unlink()
            except FileNotFoundError:
                pass

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

    def _entries_with_status(self, status: str) -> tuple[dict, ...]:
        with self._lock:
            payload = self._read_unlocked()
            entries = []
            for value in payload["rigs"].values():
                if isinstance(value, dict) and value.get("status") == status:
                    entries.append(copy.deepcopy(value))
        return tuple(
            sorted(entries, key=lambda item: int(item.get("rig_id", 0)))
        )

    def active_entries(self) -> tuple[dict, ...]:
        return self._entries_with_status(ACTIVE_STATUS)

    def failed_entries(self) -> tuple[dict, ...]:
        return self._entries_with_status("failed")

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
