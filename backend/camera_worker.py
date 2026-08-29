"""Persistent single-threaded owner for a camera service."""

from __future__ import annotations

from collections.abc import Callable
import time
from typing import Any

from backend.generic_worker import (
    PRIORITY_DIAGNOSTIC,
    PRIORITY_SEQUENCER,
    GenericWorker,
)
from services.camera_service import CameraService


class CameraWorker:
    """Run all operations for one camera sequentially in its worker thread."""

    def __init__(
        self,
        rig_id: int,
        service_factory: Callable[[], CameraService] | None = None,
        log_fn=print,
        clock=None,
        shutdown_policy: str = "drain",
        max_queue_size: int | None = None,
    ) -> None:
        self._clock = clock
        self._camera_entry: dict | None = None
        self._service_factory = service_factory or (
            lambda: CameraService(
                log_fn=log_fn,
                clock=clock,
                camera_identity=self._camera_entry,
            )
        )
        self._service: CameraService | None = None
        self._worker = GenericWorker(
            rig_id=rig_id,
            device_kind="camera",
            log_fn=log_fn,
            shutdown_policy=shutdown_policy,
            device_close=self._close_service,
            max_queue_size=max_queue_size,
        )

    @property
    def running(self) -> bool:
        return self._worker.running

    def configure_camera(self, camera_entry: dict) -> None:
        """Bind this worker to one immutable camera configuration snapshot."""
        if self._service is not None:
            raise RuntimeError("camera worker is already initialized")
        self._camera_entry = dict(camera_entry)

    def start(self) -> None:
        self._worker.start()

    def stop(self, timeout: float | None = None) -> None:
        self._worker.stop(timeout=timeout)

    def _ensure_service(self) -> CameraService:
        if self._service is None:
            self._service = self._service_factory()
        return self._service

    def _close_service(self) -> None:
        if self._service is not None:
            self._service.close()

    def _call(
        self,
        method_name: str,
        *args,
        priority: int | None = None,
        worker_deadline: float | None = None,
        **kwargs,
    ) -> Any:
        def invoke():
            method = getattr(self._ensure_service(), method_name)
            return method(*args, **kwargs)

        if priority is None:
            future = self._worker.submit(invoke)
        else:
            future = self._worker.submit_with_priority(
                priority,
                invoke,
                worker_deadline=worker_deadline,
            )
        return future.result()

    def _capture_deadline(self, deadline) -> float | None:
        """Convert an absolute UTC capture deadline once, before queueing."""
        if deadline is None:
            return None
        if self._clock is None:
            raise RuntimeError("horloge d'exécution non configurée")
        return time.monotonic() + self._clock.remaining(deadline)

    def connect(self):
        return self._call("connect")

    def init_settings(
        self,
        aperture=None,
        iso=None,
        image_format="RAW",
        white_balance="Daylight",
    ):
        return self._call(
            "init_settings",
            aperture=aperture,
            iso=iso,
            image_format=image_format,
            white_balance=white_balance,
        )

    def set_exposure_settings(self, aperture=None, iso=None):
        return self._call(
            "set_exposure_settings", aperture=aperture, iso=iso
        )

    def apply_phase_settings(self, aperture=None, iso=None):
        return self._call(
            "apply_phase_settings", aperture=aperture, iso=iso
        )

    def prepare_capture(self, intent):
        return self._call(
            "prepare_capture", intent, priority=PRIORITY_SEQUENCER
        )

    def trigger_prepared(self, prepared, deadline=None):
        monotonic_deadline = self._capture_deadline(deadline)
        return self._call(
            "trigger_prepared",
            prepared,
            deadline=deadline,
            monotonic_deadline=monotonic_deadline,
            priority=PRIORITY_SEQUENCER,
            worker_deadline=monotonic_deadline,
        )

    def shoot_speed_list(
        self,
        speeds,
        photo_num_start=0,
        deadline=None,
        slowest_override_seconds=None,
    ):
        monotonic_deadline = self._capture_deadline(deadline)
        return self._call(
            "shoot_speed_list",
            speeds,
            photo_num_start=photo_num_start,
            deadline=deadline,
            monotonic_deadline=monotonic_deadline,
            slowest_override_seconds=slowest_override_seconds,
            priority=PRIORITY_SEQUENCER,
            worker_deadline=monotonic_deadline,
        )

    def get_battery_level(self):
        return self._call(
            "get_battery_level", priority=PRIORITY_DIAGNOSTIC
        )

    def read_info(self):
        def invoke():
            return self._ensure_service().read_info()

        return self._worker.submit_with_priority(
            PRIORITY_DIAGNOSTIC, invoke, reject_if_busy=True
        ).result()

    def sync_datetime(self, ref):
        return self._call("sync_datetime", ref)

    def probe_info(self) -> dict[str, str | int | None]:
        def probe():
            service = self._ensure_service()
            if not service.connected:
                service.connect()
            return {
                "model": service.model or None,
                "plugin": getattr(service.plugin, "name", None),
                "battery": service.get_battery_level(),
            }

        return self._worker.submit_with_priority(
            PRIORITY_DIAGNOSTIC, probe, reject_if_busy=True
        ).result()

    def test_photo(
        self,
        speeds,
        photo_num_start=0,
        deadline=None,
        slowest_override_seconds=None,
    ):
        return self._call(
            "shoot_speed_list",
            speeds,
            photo_num_start=photo_num_start,
            deadline=deadline,
            slowest_override_seconds=slowest_override_seconds,
        )

    def test_photo_diagnostic(
        self,
        speeds,
        photo_num_start=0,
        deadline=None,
        slowest_override_seconds=None,
    ):
        def shoot():
            service = self._ensure_service()
            if not service.connected:
                service.connect()
            return service.shoot_speed_list(
                speeds,
                photo_num_start=photo_num_start,
                deadline=deadline,
                slowest_override_seconds=slowest_override_seconds,
            )

        return self._worker.submit_with_priority(
            PRIORITY_DIAGNOSTIC, shoot, reject_if_busy=True
        ).result()
