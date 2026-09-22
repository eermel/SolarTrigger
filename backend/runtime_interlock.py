from __future__ import annotations

import threading
from contextlib import contextmanager
from collections.abc import Callable, Iterator


class MaintenanceActiveError(RuntimeError):
    pass


class TriggerActiveError(RuntimeError):
    pass


_LOCK = threading.RLock()


@contextmanager
def trigger_start_section(maintenance_running: Callable[[], bool]) -> Iterator[None]:
    """Atomically reject Trigger START while maintenance owns the system."""
    with _LOCK:
        if maintenance_running():
            raise MaintenanceActiveError("System maintenance is running")
        yield


def start_maintenance_if_trigger_idle(
    trigger_busy: Callable[[], bool],
    start_fn: Callable[[], object],
):
    """Atomically reject maintenance while a Trigger is running or starting."""
    with _LOCK:
        if trigger_busy():
            raise TriggerActiveError("Trigger is running or starting")
        return start_fn()
