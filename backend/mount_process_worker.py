"""Supervised process owner for one configured mount."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.device_process_worker import (
    SupervisedDeviceProcess,
    safe_send,
    serve_worker,
)
from backend.mount_worker import MountWorker
from backend.state_store import StateStore
from plugins.mount.indi_client import IndiClientError
from services.mount_service import MountService


_MOUNT_OPERATIONS = frozenset(
    {
        "status",
        "set_tracking_mode",
        "start_tracking",
        "stop_tracking",
        "set_speed",
        "set_location",
        "start_slew",
        "home_start",
        "stop",
        "emergency_stop",
        "warmup",
    }
)


class ReloadingStateStore:
    """Read persisted application state afresh for every snapshot.

    A process-isolated mount may live for hours.  Keeping one ordinary
    StateStore would freeze GPS data at child startup.  MountService only needs
    read access here, so reloading the persisted state prevents stale GPS
    coordinates without sharing locks or Python objects across processes.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def snapshot(self, key: str | None = None):
        return StateStore(self.path).snapshot(key)

    def get(self, key: str, default=None):
        return StateStore(self.path).get(key, default)


def _mount_process_main(
    conn,
    spec: dict[str, Any],
    call_timeout_s: float,
) -> None:
    def log(message) -> None:
        safe_send(
            conn,
            {
                "kind": "log",
                "message": str(message),
            },
        )

    state_store = ReloadingStateStore(spec["state_path"])
    backend = str(spec["backend"])
    config = dict(spec.get("device_config") or {})

    worker = MountWorker(
        rig_id=int(spec["rig_id"]),
        service_factory=lambda: MountService(
            state_store,
            log_fn=log,
            config=config,
            selected_plugin=backend,
        ),
        log_fn=log,
        call_timeout_s=call_timeout_s,
    )

    serve_worker(
        conn,
        worker,
        allowed_operations=_MOUNT_OPERATIONS,
    )


class ProcessMountWorker(SupervisedDeviceProcess):
    """MountWorker-compatible proxy backed by a supervised child."""

    def __init__(
        self,
        *,
        rig_id: int,
        backend: str,
        device_config: dict[str, Any],
        state_path: str | Path,
        log_fn=print,
        call_timeout_s: float = 60.0,
        process_target=None,
    ) -> None:
        super().__init__(
            rig_id=rig_id,
            device_kind="mount",
            process_spec={
                "rig_id": int(rig_id),
                "backend": str(backend),
                "device_config": dict(device_config),
                "state_path": str(state_path),
            },
            process_target=(
                process_target or _mount_process_main
            ),
            call_timeout_s=call_timeout_s,
            log_fn=log_fn,
        )

    def remote_exception(self, response):
        if response.get("class") != "IndiClientError":
            return None

        return IndiClientError(
            str(response.get("code") or "INDI_ERROR"),
            str(response.get("message") or "INDI error"),
            command=response.get("command"),
            returncode=response.get("returncode"),
            stderr=str(response.get("stderr") or ""),
        )

    def status(self):
        return self.call("status")

    def set_tracking_mode(self, mode: str):
        return self.call("set_tracking_mode", mode)

    def start_tracking(self):
        return self.call("start_tracking")

    def stop_tracking(self):
        return self.call("stop_tracking")

    def set_speed(self, value):
        return self.call("set_speed", value)

    def set_location(self, latitude, longitude, elevation):
        return self.call(
            "set_location",
            latitude,
            longitude,
            elevation,
        )

    def start_slew(self, direction: str):
        return self._motion_call("start_slew", direction)

    def home_start(self):
        return self._motion_call("home_start")

    def stop(self):
        # After a timed-out motion command the old child is gone.  A normal
        # MountService.stop() may have no connected plugin in the fresh child,
        # so recovery must force reconnection and send a physical STOP.
        if self.motion_state_unknown:
            result = self.call("emergency_stop")
            self._clear_motion_state_unknown()
            return result

        return self.call("stop")

    def warmup(self):
        return self.call("warmup")


__all__ = [
    "ProcessMountWorker",
    "ReloadingStateStore",
]
