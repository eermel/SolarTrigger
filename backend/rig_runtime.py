"""Canonical, configuration-only access to the runtime rig manager."""

from __future__ import annotations

import threading
from pathlib import Path

from backend.rig_config import load, migrate_legacy
from backend.rig_manager import RigManager
from backend.state_store import StateStore


TRIGGER_DIR = Path(__file__).resolve().parent.parent

_rig_manager: RigManager | None = None
_rig_manager_lock = threading.Lock()


def get_rig_manager() -> RigManager:
    """Load and cache the canonical :class:`RigManager` instance.

    If no schema-v2 configuration has been persisted yet, the legacy state is
    migrated in memory only.  Constructing the manager does not initialize or
    probe any hardware service.
    """

    global _rig_manager

    if _rig_manager is None:
        with _rig_manager_lock:
            if _rig_manager is None:
                rig_config_path = TRIGGER_DIR / "configs" / "rig" / "default.json"
                if rig_config_path.exists():
                    config = load(rig_config_path)
                else:
                    state_store = StateStore(TRIGGER_DIR / "flask_app" / "state.json")
                    config = migrate_legacy(state_store, TRIGGER_DIR / "configs")
                _rig_manager = RigManager.from_config(config)

    return _rig_manager


def normalize_rigs_for_ui(rm: RigManager) -> list[dict]:
    """Return the fixed four-slot, configuration-only RIG UI representation."""

    normalized = []
    for rig_id in range(1, 5):
        rig = rm.rigs.get(rig_id)
        normalized.append(
            {
                "rig_id": rig_id,
                "name": rig.name if rig is not None else f"RIG {rig_id}",
                "enabled": rig.enabled if rig is not None else False,
            }
        )
    return normalized


def reset_rig_manager_for_tests() -> None:
    """Clear the cached manager for test isolation."""

    global _rig_manager

    with _rig_manager_lock:
        _rig_manager = None
