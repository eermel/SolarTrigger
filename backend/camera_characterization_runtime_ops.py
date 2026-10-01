"""Reusable runtime-equivalent SET/readback operations for characterization."""
from __future__ import annotations

import statistics
import time

from backend.camera_characterization_types import Cancelled
from plugins.camera.profile import (
    prime_single_config,
    write_single_config,
    write_widget,
    widget,
)


class CharacterizationRuntimeOps:
    """Own the mutable Direct-SET cache used during one characterization run."""

    def __init__(self, camera, job, commands, set_samples):
        self.camera = camera
        self.job = job
        self.commands = commands
        self.set_samples = set_samples
        self.direct_nodes = {}

    def prime_runtime_spec(self, spec):
        if spec.get("writer") != "single_config":
            return None
        name = spec["name"]
        if name not in self.direct_nodes:
            self.direct_nodes[name] = prime_single_config(self.camera, spec)
        return self.direct_nodes[name]

    def set(self, key, value):
        spec = self.commands[key]
        if spec.get("writer") == "single_config":
            node = self.prime_runtime_spec(spec)
            write_single_config(self.camera, spec, node, value)
        else:
            # Compatibility only for initialization-only optional commands.
            write_widget(self.camera, spec["path"], value)

    def read(self, key):
        """Fresh GET used only by characterization/preflight evidence."""
        spec = self.commands[key]
        _, node = widget(self.camera, spec["path"])
        return node.get_value()

    def converge_preflight(self):
        """Re-establish invariant state after opening a fresh gphoto session."""
        for key in (
            "manual_mode",
            "capture_target",
            "raw",
            "white_balance",
            "shutter_mode",
            "capture_mode",
            "self_timer",
            "time_lapse",
        ):
            spec = self.commands.get(key)
            if not isinstance(spec, dict) or "value" not in spec:
                continue
            target = spec["value"]
            actual = self.read(key)
            if str(actual) == str(target):
                continue
            if spec.get("set") is False:
                if not self.job.ask(
                    "Fresh-session camera preflight: "
                    f"{key} must be {target!r}, current value is {actual!r}. "
                    "Correct this setting physically on the camera, wait until "
                    "the camera is ready, then click OK. The setting will be "
                    "read again before any capture.",
                    kind="start",
                ):
                    raise Cancelled(
                        f"Fresh-session physical preflight cancelled for {key}"
                    )
                actual = self.read(key)
                if str(actual) != str(target):
                    raise RuntimeError(
                        f"Fresh-session invariant {key} still incorrect after "
                        f"operator confirmation: expected={target!r}, "
                        f"actual={actual!r}"
                    )
                continue
            self.set(key, target)
            actual = self.read(key)
            if str(actual) != str(target):
                raise RuntimeError(
                    f"Fresh-session invariant readback mismatch for {key}: "
                    f"expected={target!r}, actual={actual!r}"
                )

    def measure_set(self, key, values):
        values = list(values)
        if not values:
            raise RuntimeError(f"Cannot measure SET {key}: no values")

        samples = []
        for _ in range(5):
            for value in values:
                self.job.check()
                # Production timing measures one SolarTrigger SET call only.
                # The CameraWidget is primed before timing so no configuration
                # GET is added to the runtime SET path.
                self.prime_runtime_spec(self.commands[key])
                begin = time.monotonic()
                self.set(key, value)
                samples.append((time.monotonic() - begin) * 1000.0)

        self.set_samples[key] = samples
        self.job.log(
            f"TIMING SET {key}: "
            f"max={max(samples):.1f} ms "
            f"median={statistics.median(samples):.1f} ms "
            f"({len(samples)} operations)"
        )
        return samples
