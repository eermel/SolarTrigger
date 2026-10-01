"""Backend-owned coordination for portal Trigger commands.

The browser sends intent only. This module owns cancellation/preemption state
for the short portal-side admission/preflight/start transaction. The dedicated
runtime remains authoritative for an already-started photographic sequence.
"""
from __future__ import annotations

import os
import threading


class TriggerCommandBusyError(RuntimeError):
    pass


class TriggerBackendCommand:
    def __init__(self, rig_ids, mode):
        self.command_id = os.urandom(12).hex()
        self.mode = str(mode)
        self.rig_ids = tuple(rig_ids)
        self.abort_event = threading.Event()
        self.done_event = threading.Event()
        self._lock = threading.RLock()
        self._cancelled_rig_ids = set()

    @property
    def aborted(self):
        return self.abort_event.is_set()

    def abort(self):
        self.abort_event.set()

    def cancel_rig(self, rig_id):
        if rig_id not in self.rig_ids:
            return False
        with self._lock:
            self._cancelled_rig_ids.add(rig_id)
        return True

    def is_rig_cancelled(self, rig_id):
        with self._lock:
            return rig_id in self._cancelled_rig_ids

    def cancelled_rig_ids(self):
        with self._lock:
            return tuple(sorted(self._cancelled_rig_ids))


class TriggerCommandCoordinator:
    """Serialize portal START/DEBUG intents and reserve emergency priority."""

    def __init__(self):
        self._lock = threading.RLock()
        self._active = None
        self._emergency_active = False

    def begin(self, rig_ids, mode):
        with self._lock:
            if self._emergency_active:
                raise TriggerCommandBusyError(
                    "Emergency Totality admission is in progress"
                )
            if self._active is not None and not self._active.done_event.is_set():
                raise TriggerCommandBusyError(
                    "Another Trigger start command is already in progress"
                )
            command = TriggerBackendCommand(rig_ids, mode)
            self._active = command
            return command

    def finish(self, command):
        if command is None:
            return
        command.done_event.set()
        with self._lock:
            if self._active is command:
                self._active = None

    def cancel_rig(self, rig_id):
        with self._lock:
            command = self._active
            if command is None or command.done_event.is_set():
                return False
            return command.cancel_rig(rig_id)

    def begin_emergency(self):
        """Reserve priority and abort any portal start/preflight transaction."""
        with self._lock:
            if self._emergency_active:
                raise TriggerCommandBusyError(
                    "Emergency Totality admission is already in progress"
                )
            self._emergency_active = True
            command = self._active
            if command is not None and not command.done_event.is_set():
                command.abort()
                return command
            return None

    def finish_emergency(self):
        with self._lock:
            self._emergency_active = False

    def busy(self):
        with self._lock:
            return bool(
                self._emergency_active
                or (
                    self._active is not None
                    and not self._active.done_event.is_set()
                )
            )


__all__ = [
    "TriggerBackendCommand",
    "TriggerCommandBusyError",
    "TriggerCommandCoordinator",
]
