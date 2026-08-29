"""Canonical, configuration-only access to the runtime rig manager."""

from __future__ import annotations

import threading
from pathlib import Path

from backend.rig_config import load, migrate_legacy
from backend.rig_manager import RigManager
from backend.state_store import StateStore


TRIGGER_DIR = Path(__file__).resolve().parent.parent

def _resolve_state_file(trigger_dir: Path) -> Path:
    """Resolve StateStore path for source and installed PROD layouts."""
    root_app = trigger_dir / "app.py"
    source_app = trigger_dir / "flask_app" / "app.py"

    if root_app.is_file() and not source_app.is_file():
        return trigger_dir / "state.json"

    return trigger_dir / "flask_app" / "state.json"

_rig_manager: RigManager | None = None
_rig_manager_lock = threading.Lock()


def load_rig_configuration() -> dict:
    """Load the persisted rig configuration, or migrate legacy state in memory."""

    rig_config_path = TRIGGER_DIR / "configs" / "rig" / "default.json"
    if rig_config_path.exists():
        return load(rig_config_path)

    state_store = StateStore(_resolve_state_file(TRIGGER_DIR))
    return migrate_legacy(state_store, TRIGGER_DIR / "configs")


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
                _rig_manager = RigManager.from_config(load_rig_configuration())

    return _rig_manager


def reload_rig_manager(config: dict | None = None) -> RigManager:
    """Build and install a fresh canonical manager without probing hardware."""

    global _rig_manager

    replacement = RigManager.from_config(
        load_rig_configuration() if config is None else config
    )
    with _rig_manager_lock:
        _rig_manager = replacement
    return replacement


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
