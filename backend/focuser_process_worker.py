"""Supervised process owner for one configured focuser."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.device_process_worker import (
    SupervisedDeviceProcess,
    safe_send,
    serve_worker,
)
from backend.focuser_worker import FocuserWorker
from backend.state_store import StateStore
from services.focuser_service import FocuserService


_FOCUSER_OPERATIONS = frozenset(
    {
        "status",
        "set_step",
        "move_to",
        "move_relative",
        "start_jog",
        "stop_jog",
        "stop",
        "home",
        "set_mode",
        "active_step",
    }
)


def _focuser_process_main(
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

    state_store = StateStore(Path(spec["state_path"]))
    backend = str(spec["backend"])
    config = dict(spec.get("device_config") or {})

    worker = FocuserWorker(
        rig_id=int(spec["rig_id"]),
        service_factory=lambda: FocuserService(
            state_store,
            log_fn=log,
            config=config,
            selected_plugin=backend,
            persist_policy="volatile",
        ),
        log_fn=log,
        call_timeout_s=call_timeout_s,
    )

    serve_worker(
        conn,
        worker,
        allowed_operations=_FOCUSER_OPERATIONS,
    )


class ProcessFocuserWorker(SupervisedDeviceProcess):
    """FocuserWorker-compatible proxy backed by a supervised child."""

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
            device_kind="focuser",
            process_spec={
                "rig_id": int(rig_id),
                "backend": str(backend),
                "device_config": dict(device_config),
                "state_path": str(state_path),
            },
            process_target=(
                process_target or _focuser_process_main
            ),
            call_timeout_s=call_timeout_s,
            log_fn=log_fn,
        )

    # Compatibility helper used by existing Flask code.
    def _call(self, method_name: str, *args, **kwargs):
        return self.call(method_name, *args, **kwargs)

    def status(self):
        return self.call("status")

    def set_step(self, coarse=None, fine=None):
        return self.call(
            "set_step",
            coarse,
            fine,
        )

    def move_to(self, position, wait=False):
        return self._motion_call(
            "move_to",
            position,
            wait=wait,
        )

    def move_relative(self, delta, wait=False):
        return self._motion_call(
            "move_relative",
            delta,
            wait=wait,
        )

    def start_jog(self, direction, mode=None):
        return self._motion_call(
            "start_jog",
            direction,
            mode=mode,
        )

    def stop_jog(self):
        return self.call("stop_jog")

    def stop(self):
        result = self.call("stop")
        # FocuserService.stop() reconnects when necessary and calls the
        # plugin's physical stop(), so a successful result clears ambiguity.
        self._clear_motion_state_unknown()
        return result

    def home(self, wait=False):
        return self._motion_call("home", wait=wait)

    def set_mode(self, mode: str):
        return self.call("set_mode", mode)

    def active_step(self):
        return self.call("active_step")


__all__ = ["ProcessFocuserWorker"]
