"""Deterministic plan versioning and per-RIG materialization caching."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Hashable


_VERSION_FIELDS = {
    "optics": ("focal_length_mm",),
    "photo": (
        "motion_tolerance_px",
        "iso_max",
        "anti_trailing_enabled",
        "atmos_enabled",
    ),
    "devices": {
        "mount": ("control", "geometry", "tracking"),
    },
}


def _select_fields(source: dict, fields: dict) -> dict:
    selected = {}
    for section, section_fields in fields.items():
        value = source.get(section)
        if not isinstance(value, dict):
            continue
        if isinstance(section_fields, dict):
            nested = _select_fields(value, section_fields)
            if nested:
                selected[section] = nested
        else:
            present = {key: value[key] for key in section_fields if key in value}
            if present:
                selected[section] = present
    return selected


def rig_plan_version(policy: dict) -> str:
    """Return a deterministic short digest of plan-relevant policy fields."""
    selected = _select_fields(policy, _VERSION_FIELDS)
    canonical = json.dumps(selected, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


class RigPlanCache:
    """Store plan versions and materialized augmentations independently by RIG."""

    def __init__(self) -> None:
        self._versions: dict[Hashable, str] = {}
        self._entries: dict[Hashable, dict[Hashable, Any]] = {}

    def get_version(self, rig_id: Hashable) -> str | None:
        return self._versions.get(rig_id)

    def set_version_and_clear_if_changed(
        self, rig_id: Hashable, version: str
    ) -> bool:
        if self._versions.get(rig_id) == version:
            return False
        self._versions[rig_id] = version
        self._entries.pop(rig_id, None)
        return True

    def get(self, rig_id: Hashable, logical_key: Hashable) -> Any | None:
        return self._entries.get(rig_id, {}).get(logical_key)

    def put(self, rig_id: Hashable, logical_key: Hashable, value: Any) -> None:
        self._entries.setdefault(rig_id, {})[logical_key] = value

    def clear(self, rig_id: Hashable) -> None:
        self._versions.pop(rig_id, None)
        self._entries.pop(rig_id, None)
