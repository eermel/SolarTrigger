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
