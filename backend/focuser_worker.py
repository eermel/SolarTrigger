"""Persistent single-threaded owner for a focuser service."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from backend.generic_worker import (
    PRIORITY_DIAGNOSTIC,
    PRIORITY_MANUAL,
    PRIORITY_STOP,
    GenericWorker,
)

if TYPE_CHECKING:
    from services.focuser_service import FocuserService


class FocuserWorker:
    """Run all operations for one focuser sequentially in its worker thread."""

    def __init__(
        self,
        rig_id: int,
        service_factory: Callable[[], FocuserService],
        log_fn=print,
        shutdown_policy: str = "drain",
        max_queue_size: int | None = None,
    ) -> None:
        self._service_factory = service_factory
        self._service: FocuserService | None = None
        self._worker = GenericWorker(
            rig_id=rig_id,
            device_kind="focuser",
            log_fn=log_fn,
            shutdown_policy=shutdown_policy,
            device_close=self._close_service,
            max_queue_size=max_queue_size,
        )

    @property
    def running(self) -> bool:
        return self._worker.running

    @property
    def last_error(self) -> dict | None:
        return self._worker.last_error

    def start(self) -> None:
        self._worker.start()

    def shutdown(self, timeout: float | None = None) -> None:
        self._worker.stop(timeout=timeout)

    def _ensure_service(self) -> FocuserService:
        if self._service is None:
            self._service = self._service_factory()
        return self._service

    def _close_service(self) -> None:
        service = self._service
        self._service = None
        if service is not None:
            service.close()

    def _call(
        self,
        method_name: str,
        *args,
        priority: int = PRIORITY_MANUAL,
        **kwargs,
    ) -> Any:
        def invoke():
            method = getattr(self._ensure_service(), method_name)
            return method(*args, **kwargs)

        return self._worker.submit_with_priority(priority, invoke).result()

    def status(self):
        return self._call("status", priority=PRIORITY_DIAGNOSTIC)

    def set_step(self, coarse, fine):
        return self._call("set_step", coarse, fine)

    def move_to(self, position, wait=False):
        return self._call("move_to", position, wait=wait)

    def move_relative(self, delta, wait=False):
        return self._call("move_relative", delta, wait=wait)

    def start_jog(self, direction, mode=None):
        return self._call("start_jog", direction, mode=mode)

    def stop_jog(self):
        return self._call("stop_jog", priority=PRIORITY_STOP)

    def stop(self):
        return self._call("stop", priority=PRIORITY_STOP)

    def home(self, wait=False):
        return self._call("home", wait=wait)
