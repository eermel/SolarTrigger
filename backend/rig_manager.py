"""Runtime representation and validation for configured rigs.

This module intentionally does not create or connect to hardware services.
Those services are attached to a :class:`Rig` later in the application
lifecycle.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


MIN_RIG_ID = 1
MAX_RIG_ID = 4


@dataclass
class Rig:
    """Minimal runtime state for one configured rig."""

    rig_id: int
    enabled: bool
    name: str
    devices: dict[str, Any]
    camera_service: Any = field(default=None, init=False)
    mount_service: Any = field(default=None, init=False)
    focuser_service: Any = field(default=None, init=False)


class RigManager:
    """Own the runtime rigs built from a schema v2 configuration."""

    def __init__(self, rigs: dict[int, Rig]) -> None:
        self.rigs = dict(rigs)

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "RigManager":
        """Build runtime rigs and enforce the rig-level business rules."""

        if not isinstance(config, dict):
            raise ValueError("configuration must be an object")

        configured_rigs = config.get("rigs")
        if not isinstance(configured_rigs, list):
            raise ValueError("rigs must be a list")

        rigs: dict[int, Rig] = {}
        for index, configured_rig in enumerate(configured_rigs):
            prefix = f"rigs[{index}]"
            if not isinstance(configured_rig, dict):
                raise ValueError(f"{prefix} must be an object")

            rig_id = configured_rig.get("rig_id")
            if not isinstance(rig_id, int) or isinstance(rig_id, bool):
                raise ValueError(f"{prefix}.rig_id must be an integer")
            if rig_id in rigs:
                raise ValueError(f"duplicate rig_id: {rig_id}")

            enabled = configured_rig.get("enabled")
            if not isinstance(enabled, bool):
                raise ValueError(f"{prefix}.enabled must be a boolean")

            name = configured_rig.get("name")
            if not isinstance(name, str):
                raise ValueError(f"{prefix}.name must be a string")

            devices = configured_rig.get("devices")
            if not isinstance(devices, dict):
                raise ValueError(f"{prefix}.devices must be an object")

            if enabled:
                camera = devices.get("camera")
                backend = camera.get("backend") if isinstance(camera, dict) else None
                if (
                    not isinstance(backend, str)
                    or not backend.strip()
                    or backend == "none"
                ):
                    raise ValueError(
                        f"enabled rig {rig_id} requires a configured camera backend"
                    )

            rigs[rig_id] = Rig(
                rig_id=rig_id,
                enabled=enabled,
                name=name,
                devices=dict(devices),
            )

        return cls(rigs)

    def get_rig(self, rig_id: int) -> Rig:
        """Return a configured rig after validating its public identifier."""

        if (
            not isinstance(rig_id, int)
            or isinstance(rig_id, bool)
            or not MIN_RIG_ID <= rig_id <= MAX_RIG_ID
        ):
            raise ValueError(
                f"rig_id must be an integer between {MIN_RIG_ID} and {MAX_RIG_ID}"
            )

        try:
            return self.rigs[rig_id]
        except KeyError:
            raise ValueError(f"rig_id {rig_id} is not configured") from None
