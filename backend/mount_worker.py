"""Persistent single-threaded owner for a mount service."""

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
    from services.mount_service import MountService


class MountWorker:
    """Run all operations for one mount sequentially in its worker thread."""

    def __init__(
        self,
        rig_id: int,
        service_factory: Callable[[], MountService],
        log_fn=print,
        shutdown_policy: str = "drain",
        max_queue_size: int | None = None,
    ) -> None:
        self._service_factory = service_factory
        self._service: MountService | None = None
        self._worker = GenericWorker(
            rig_id=rig_id,
            device_kind="mount",
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

    def _ensure_service(self) -> MountService:
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

    def set_tracking_mode(self, mode: str):
        return self._call("set_tracking_mode", mode)

    def start_tracking(self):
        return self._call("start_tracking")

    def stop_tracking(self):
        return self._call("stop_tracking")

    def set_speed(self, value):
        return self._call("set_speed", value)

    def set_location(self, latitude, longitude, elevation):
        return self._call("set_location", latitude, longitude, elevation)

    def start_slew(self, direction: str):
        return self._call("start_slew", direction)

    def home_start(self):
        return self._call("home_start")

    def stop(self):
        return self._call("stop", priority=PRIORITY_STOP)

    def warmup(self):
        return self._call("warmup")
