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


_LOCK = threading.RLock()


@contextmanager
def _admission_lock() -> Iterator[None]:
    """Serialize admission across portal processes when configured.

    Unit tests and development default to the historical in-process RLock.
    Production sets SOLARTRIGGER_ADMISSION_LOCK inside the systemd unit so a
    portal restart or a second client process cannot race START against GPS or
    destructive maintenance admission.
    """
    with _LOCK:
        path = os.environ.get("SOLARTRIGGER_ADMISSION_LOCK")
        if not path or fcntl is None:
            yield
            return

        lock_path = Path(path)
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o660)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)


@contextmanager
def trigger_start_section(maintenance_running: Callable[[], bool]) -> Iterator[None]:
    """Atomically reject Trigger START while maintenance owns the system."""
    with _admission_lock():
        if maintenance_running():
            raise MaintenanceActiveError("System maintenance is running")
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
