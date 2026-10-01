from __future__ import annotations

import os
from pathlib import Path
import threading
from contextlib import contextmanager
from collections.abc import Callable, Iterator

try:
    import fcntl
except ImportError:  # pragma: no cover - SolarTrigger production is Linux.
    fcntl = None


class MaintenanceActiveError(RuntimeError):
    pass


class TriggerActiveError(RuntimeError):
    pass


class AdmissionBusyError(RuntimeError):
    pass


_LOCK = threading.RLock()


@contextmanager
def _admission_lock(*, blocking=True) -> Iterator[None]:
    """Serialize admission across portal processes when configured.

    Non-blocking mode is reserved for the emergency path: it must never queue
    behind a normal START or destructive maintenance operation.
    """
    acquired = _LOCK.acquire(blocking=blocking)
    if not acquired:
        raise AdmissionBusyError("Trigger admission lock is busy")
    try:
        path = os.environ.get("SOLARTRIGGER_ADMISSION_LOCK")
        if not path or fcntl is None:
            yield
            return

        lock_path = Path(path)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o660)
        locked = False
        try:
            flags = fcntl.LOCK_EX
            if not blocking:
                flags |= fcntl.LOCK_NB
            try:
                fcntl.flock(fd, flags)
                locked = True
            except BlockingIOError as exc:
                raise AdmissionBusyError(
                    "Trigger admission lock is busy"
                ) from exc
            yield
        finally:
            if locked:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                finally:
                    os.close(fd)
            else:
                os.close(fd)
    finally:
        _LOCK.release()


@contextmanager
def trigger_start_section(maintenance_running: Callable[[], bool]) -> Iterator[None]:
    """Atomically reject Trigger START while maintenance owns the system."""
    with _admission_lock():
        if maintenance_running():
            raise MaintenanceActiveError("System maintenance is running")
        yield


@contextmanager
def trigger_priority_section(
    maintenance_running: Callable[[], bool],
) -> Iterator[None]:
    """Emergency admission: fail immediately instead of waiting on the lock."""
    with _admission_lock(blocking=False):
        if maintenance_running():
            raise MaintenanceActiveError("System maintenance is running")
        yield


@contextmanager
def trigger_idle_section(trigger_busy: Callable[[], bool]) -> Iterator[None]:
    """Serialize a configuration mutation against Trigger START admission."""
    with _admission_lock():
        if trigger_busy():
            raise TriggerActiveError("Trigger is running or starting")
        yield


def start_maintenance_if_trigger_idle(
    trigger_busy: Callable[[], bool],
    start_fn: Callable[[], object],
):
    """Atomically reject maintenance while a Trigger is running or starting."""
    with _admission_lock():
        if trigger_busy():
            raise TriggerActiveError("Trigger is running or starting")
        return start_fn()
