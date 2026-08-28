"""Load, validate, and save version 2 multi-rig configuration files.

The module deliberately validates only the minimum shape required by schema v2;
additional keys are preserved by :func:`load` and :func:`save`.
"""

from __future__ import annotations

import json
from os import PathLike
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 2
CIRCUMSTANCE_KEYS = ("C1", "C2", "TMAX", "C3", "C4")
REFERENCE_SITE_KEYS = ("lat", "lon", "alt_m")
DEVICE_KEYS = ("camera", "mount", "focuser")
MIN_RIGS = 1
MAX_RIGS = 4


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _is_number(value: Any) -> bool:
    """Return whether *value* is a JSON-style number (excluding booleans)."""

    return isinstance(value, (int, float)) and not isinstance(value, bool)


def validate(obj: Any) -> None:
    """Validate the minimum required shape of a schema v2 configuration.

    ``ValueError`` is raised at the first missing or incorrectly typed field.
    Extra fields are accepted. A successful validation returns ``None``.
    """

    _require(isinstance(obj, dict), "configuration must be an object")
    _require(obj.get("schema_version") == SCHEMA_VERSION, "schema_version must be 2")

    eclipse = obj.get("eclipse")
    _require(isinstance(eclipse, dict), "eclipse must be an object")
    _require(isinstance(eclipse.get("date"), str), "eclipse.date must be a string")

    reference_site = eclipse.get("reference_site")
    _require(
        isinstance(reference_site, dict),
        "eclipse.reference_site must be an object",
    )
    for key in REFERENCE_SITE_KEYS:
        _require(
            _is_number(reference_site.get(key)),
            f"eclipse.reference_site.{key} must be a number",
        )

    circumstances = eclipse.get("circumstances")
    _require(
        isinstance(circumstances, dict),
        "eclipse.circumstances must be an object",
    )
    for key in CIRCUMSTANCE_KEYS:
        _require(
            isinstance(circumstances.get(key), str),
            f"eclipse.circumstances.{key} must be a string",
        )

    sequence = obj.get("sequence")
    _require(isinstance(sequence, dict), "sequence must be an object")
    _require(isinstance(sequence.get("common"), dict), "sequence.common must be an object")

    rigs = obj.get("rigs")
    _require(isinstance(rigs, list), "rigs must be a list")
    _require(MIN_RIGS <= len(rigs) <= MAX_RIGS, "rigs must contain between 1 and 4 items")
    for index, rig in enumerate(rigs):
        prefix = f"rigs[{index}]"
        _require(isinstance(rig, dict), f"{prefix} must be an object")
        _require(
            isinstance(rig.get("rig_id"), int) and not isinstance(rig.get("rig_id"), bool),
            f"{prefix}.rig_id must be an integer",
        )
        _require(isinstance(rig.get("enabled"), bool), f"{prefix}.enabled must be a boolean")
        _require(isinstance(rig.get("name"), str), f"{prefix}.name must be a string")

        devices = rig.get("devices")
        _require(isinstance(devices, dict), f"{prefix}.devices must be an object")
        for key in DEVICE_KEYS:
            _require(
                isinstance(devices.get(key), dict),
                f"{prefix}.devices.{key} must be an object",
            )

        _require(isinstance(rig.get("optics"), dict), f"{prefix}.optics must be an object")
        _require(isinstance(rig.get("photo"), dict), f"{prefix}.photo must be an object")


def load(path: str | PathLike[str]) -> Any:
    """Read UTF-8 JSON from *path*, validate it, and return the decoded object."""

    with Path(path).open("r", encoding="utf-8") as stream:
        obj = json.load(stream)
    validate(obj)
    return obj


def save(path: str | PathLike[str], obj: Any) -> None:
    """Validate *obj* and write it as indented UTF-8 JSON to *path*."""

    validate(obj)
    with Path(path).open("w", encoding="utf-8") as stream:
        json.dump(obj, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def migrate_legacy(state_store: Any, configs_dir: str | PathLike[str]) -> dict[str, Any]:
    """Build a schema v2 configuration from the legacy single-rig state."""

    configs_path = Path(configs_dir)
    devices = state_store.snapshot("devices") or {}
    circumstances_state = state_store.snapshot("circumstances") or {}
    # Reading this snapshot is part of the legacy-state contract even though the
    # capture document itself remains the authoritative source for its content.
    state_store.snapshot("capture")

    circumstances_file = configs_path / "circumstances" / circumstances_state["active_file"]
    with circumstances_file.open("r", encoding="utf-8") as stream:
        circumstances_source = json.load(stream)

    capture_source: dict[str, Any] = {}
    camera_config_file = state_store.get("camera_config_file")
    if camera_config_file:
        filename = Path(camera_config_file).name
        for subdir in ("camera_cfg", "capture"):
            candidate = configs_path / subdir / filename
            if candidate.exists():
                with candidate.open("r", encoding="utf-8") as stream:
                    loaded_capture = json.load(stream)
                if isinstance(loaded_capture, dict):
                    capture_source = loaded_capture
                break

    common = {
        key: capture_source[key]
        for key in ("phases", "exposure_correction")
        if key in capture_source
    }
    exposure_correction = capture_source.get("exposure_correction")
    atmos_enabled = bool(
        exposure_correction.get("atmospheric_attenuation_enabled", False)
        if isinstance(exposure_correction, dict)
        else False
    )

    def plugin_id(device_name: str) -> Any:
        device = devices.get(device_name)
        return device.get("plugin") if isinstance(device, dict) else None

    def active_device(device_name: str) -> dict[str, Any] | None:
        device = devices.get(device_name)
        plugin = plugin_id(device_name)
        if not isinstance(device, dict) or device.get("active") is not True:
            return None
        if not plugin or plugin == "none":
            return None
        return {
            "backend": plugin,
            "manufacturer": None,
            "model": None,
            "serial": None,
        }

    camera_plugin = plugin_id("camera")
    camera_known = bool(camera_plugin and camera_plugin != "none")
    camera = {
        "backend": camera_plugin if camera_known else "none",
        "manufacturer": None,
        "model": None,
        "serial": None,
    }
    camera_state = devices.get("camera")
    enabled = bool(
        isinstance(camera_state, dict)
        and camera_state.get("active") is True
        and camera_known
    )

    location = circumstances_source["_circumstances_location"]
    eclipse_date = circumstances_source.get("_date")
    if eclipse_date is None:
        eclipse_date = circumstances_source["_date_utc"]

    return {
        "schema_version": SCHEMA_VERSION,
        "eclipse": {
            "date": eclipse_date,
            "reference_site": {
                "lat": location["latitude"],
                "lon": location["longitude"],
                "alt_m": location["altitude_m"],
            },
            "circumstances": {
                key: circumstances_source[key] for key in CIRCUMSTANCE_KEYS
            },
        },
        "sequence": {"common": common},
        "rigs": [
            {
                "rig_id": 1,
                "name": "RIG 1",
                "enabled": enabled,
                "devices": {
                    "camera": camera,
                    "mount": active_device("mount"),
                    "focuser": active_device("focuser"),
                },
                "optics": {"focal_length_mm": None},
                "photo": {"atmos_enabled": atmos_enabled},
            }
        ],
    }
