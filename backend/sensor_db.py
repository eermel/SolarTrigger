"""Load and resolve camera sensor specifications.

The on-disk format is schema version 1::

    {"schema_version": 1, "sensors": [{...}]}

Each sensor requires ``manufacturer``, ``model``, ``sensor_width_mm``,
``sensor_height_mm``, ``width_px``, ``height_px``, and ``sources``.  ``aliases``
and ``pixel_pitch_um`` are optional.  Pixel pitch is derived only from the
physical and pixel widths when it is not supplied.
"""

from __future__ import annotations

import json
import math
from copy import deepcopy
from typing import Any


_REQUIRED_ENTRY_FIELDS = (
    "manufacturer",
    "model",
    "sensor_width_mm",
    "sensor_height_mm",
    "width_px",
    "height_px",
    "sources",
)


def _nonempty_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _positive_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a positive number")
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"{field} must be a positive finite number")
    return result


def _positive_integer(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field} must be a positive integer")
    return value


def _entry_key(manufacturer: str, name: str) -> str:
    return f"{manufacturer}::{name}"


def validate_db(doc: dict) -> None:
    """Validate a schema-v1 sensor database document.

    ``ValueError`` is raised with a field-oriented message for malformed
    documents, duplicate models, and ambiguous aliases.
    """

    if not isinstance(doc, dict):
        raise ValueError("sensor database must be a JSON object")
    version = doc.get("schema_version")
    if isinstance(version, bool) or version != 1:
        raise ValueError(f"unsupported sensor database schema_version: {version!r}; expected 1")
    sensors = doc.get("sensors")
    if not isinstance(sensors, list):
        raise ValueError("sensors must be an array")

    claimed_names: dict[str, str] = {}
    for position, entry in enumerate(sensors):
        prefix = f"sensors[{position}]"
        if not isinstance(entry, dict):
            raise ValueError(f"{prefix} must be an object")
        missing = [field for field in _REQUIRED_ENTRY_FIELDS if field not in entry]
        if missing:
            raise ValueError(f"{prefix} is missing required field(s): {', '.join(missing)}")

        manufacturer = _nonempty_string(entry["manufacturer"], f"{prefix}.manufacturer")
        model = _nonempty_string(entry["model"], f"{prefix}.model")
        _positive_number(entry["sensor_width_mm"], f"{prefix}.sensor_width_mm")
        _positive_number(entry["sensor_height_mm"], f"{prefix}.sensor_height_mm")
        _positive_integer(entry["width_px"], f"{prefix}.width_px")
        _positive_integer(entry["height_px"], f"{prefix}.height_px")
        if "pixel_pitch_um" in entry and entry["pixel_pitch_um"] is not None:
            _positive_number(entry["pixel_pitch_um"], f"{prefix}.pixel_pitch_um")

        sources = entry["sources"]
        if not isinstance(sources, list) or not sources:
            raise ValueError(f"{prefix}.sources must be a non-empty array")
        for source_position, source in enumerate(sources):
            _nonempty_string(source, f"{prefix}.sources[{source_position}]")

        aliases = entry.get("aliases", [])
        if not isinstance(aliases, list):
            raise ValueError(f"{prefix}.aliases must be an array")

        canonical_key = _entry_key(manufacturer, model).casefold()
        owner = claimed_names.get(canonical_key)
        if owner is not None:
            raise ValueError(f"duplicate or ambiguous sensor name: {manufacturer}::{model}")
        claimed_names[canonical_key] = canonical_key

        local_aliases: set[str] = set()
        for alias_position, alias_value in enumerate(aliases):
            alias = _nonempty_string(alias_value, f"{prefix}.aliases[{alias_position}]")
            alias_key = _entry_key(manufacturer, alias).casefold()
            if alias_key == canonical_key or alias_key in local_aliases:
                raise ValueError(f"duplicate alias: {manufacturer}::{alias}")
            owner = claimed_names.get(alias_key)
            if owner is not None and owner != canonical_key:
                raise ValueError(f"ambiguous alias: {manufacturer}::{alias}")
            local_aliases.add(alias_key)
        for alias_key in local_aliases:
            claimed_names[alias_key] = canonical_key


def _normalize_entry(entry: dict) -> dict:
    normalized = deepcopy(entry)
    normalized["manufacturer"] = entry["manufacturer"].strip()
    normalized["model"] = entry["model"].strip()
    normalized["aliases"] = [alias.strip() for alias in entry.get("aliases", [])]
    normalized["sources"] = [source.strip() for source in entry["sources"]]
    normalized["id_key"] = _entry_key(normalized["manufacturer"], normalized["model"])
    if normalized.get("pixel_pitch_um") is None:
        normalized["pixel_pitch_um"] = (
            float(normalized["sensor_width_mm"]) * 1000.0 / normalized["width_px"]
        )
    return normalized


def load_sensor_db(path: str) -> dict:
    """Load, validate, and normalize a schema-v1 JSON sensor database."""

    try:
        with open(path, "r", encoding="utf-8") as stream:
            doc = json.load(stream)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot load sensor database {path!r}: {exc}") from exc

    validate_db(doc)
    sensors = [_normalize_entry(entry) for entry in doc["sensors"]]
    models_index = {entry["id_key"]: entry for entry in sensors}
    aliases_index = {
        _entry_key(entry["manufacturer"], alias): entry
        for entry in sensors
        for alias in entry["aliases"]
    }
    return {
        "schema_version": 1,
        "sensors": sensors,
        "models_index": models_index,
        "aliases_index": aliases_index,
    }


def lookup_model(db: dict, manufacturer: str, name_or_alias: str) -> dict:
    """Return the sensor matching a manufacturer and model name or alias.

    Matching ignores surrounding whitespace and letter case.  ``KeyError`` is
    raised when no model can be resolved.
    """

    maker = _nonempty_string(manufacturer, "manufacturer")
    name = _nonempty_string(name_or_alias, "name_or_alias")
    requested = _entry_key(maker, name)
    for index_name in ("models_index", "aliases_index"):
        index = db.get(index_name, {}) if isinstance(db, dict) else {}
        if requested in index:
            return deepcopy(index[requested])
    folded = requested.casefold()
    for index_name in ("models_index", "aliases_index"):
        index = db.get(index_name, {}) if isinstance(db, dict) else {}
        for key, entry in index.items():
            if key.casefold() == folded:
                return deepcopy(entry)
    raise KeyError(f"unknown sensor model: {requested}")


def make_manual_entry(
    manufacturer: str,
    model: str,
    sensor_width_mm: float,
    sensor_height_mm: float,
    width_px: int,
    height_px: int,
    pixel_pitch_um: float | None = None,
) -> dict:
    """Validate and return a normalized manually supplied sensor entry."""

    entry = {
        "manufacturer": manufacturer,
        "model": model,
        "sensor_width_mm": sensor_width_mm,
        "sensor_height_mm": sensor_height_mm,
        "width_px": width_px,
        "height_px": height_px,
        "aliases": [],
        "sources": ["manual"],
    }
    if pixel_pitch_um is not None:
        entry["pixel_pitch_um"] = pixel_pitch_um
    validate_db({"schema_version": 1, "sensors": [entry]})
    return _normalize_entry(entry)

