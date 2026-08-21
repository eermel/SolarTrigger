"""Load the generated eclipse datasets without modifying them."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


DATASETS_DIR = Path(__file__).resolve().parents[2] / "data" / "eclipses"


class EclipseDataError(Exception):
    """Base error raised while reading eclipse datasets."""


class EclipseNotFoundError(EclipseDataError, FileNotFoundError):
    """Raised when a registry or registered eclipse dataset is missing."""


class EclipseDataFormatError(EclipseDataError, ValueError):
    """Raised when a registry or eclipse dataset is not valid JSON data."""


def _read_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as dataset_file:
            return json.load(dataset_file)
    except FileNotFoundError as exc:
        raise EclipseNotFoundError(f"eclipse data file not found: {path}") from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise EclipseDataFormatError(
            f"invalid JSON in eclipse data file: {path}"
        ) from exc


def _registry_entries() -> list[dict[str, Any]]:
    registry = _read_json(DATASETS_DIR / "registry.json")
    if not isinstance(registry, dict) or not isinstance(registry.get("eclipses"), list):
        raise EclipseDataFormatError(
            "eclipse registry must contain an 'eclipses' list"
        )

    entries = registry["eclipses"]
    if any(
        not isinstance(entry, dict)
        or not isinstance(entry.get("date"), str)
        or not isinstance(entry.get("file"), str)
        for entry in entries
    ):
        raise EclipseDataFormatError("eclipse registry contains an invalid entry")
    return entries


def list_supported_eclipses() -> list[str]:
    """Return the ISO dates declared by the registry, in registry order."""

    return [entry["date"] for entry in _registry_entries()]


def load_eclipse(date_iso: str) -> dict[str, Any]:
    """Return the complete dataset registered for ``date_iso``.

    The returned mapping contains the dataset fields unchanged. Only files named
    by the registry can be loaded.
    """

    entry = next(
        (item for item in _registry_entries() if item["date"] == date_iso),
        None,
    )
    if entry is None:
        raise EclipseNotFoundError(f"unsupported eclipse date: {date_iso}")

    file_name = entry["file"]
    if Path(file_name).name != file_name:
        raise EclipseDataFormatError(
            f"eclipse registry contains an invalid file name: {file_name!r}"
        )

    dataset = _read_json(DATASETS_DIR / file_name)
    if not isinstance(dataset, dict):
        raise EclipseDataFormatError(
            f"eclipse dataset must contain a JSON object: {file_name}"
        )
    return dataset
