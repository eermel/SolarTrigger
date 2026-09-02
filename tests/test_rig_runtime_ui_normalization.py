import json

from backend import rig_runtime
from backend.rig_manager import RigManager
from backend.state_store import StateStore


def _write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_missing_v2_config_migrates_legacy_state_and_normalizes_four_slots(
    tmp_path, monkeypatch
):
    circumstances = {
        "_date": "2027-08-02",
        "_circumstances_location": {
            "latitude": 43.6,
            "longitude": 1.44,
            "altitude_m": 150,
        },
        "C1": "2027-08-02T08:00:00Z",
        "C2": "2027-08-02T09:00:00Z",
        "TMAX": "2027-08-02T09:01:00Z",
        "C3": "2027-08-02T09:02:00Z",
        "C4": "2027-08-02T10:00:00Z",
    }
    _write_json(
        tmp_path / "configs" / "circumstances" / "eclipse.json",
        circumstances,
    )

    state_store = StateStore(tmp_path / "flask_app" / "state.json")
    state_store.set(
        "devices",
        {
            "camera": {"plugin": "simulated", "active": True},
            "gps": {"plugin": "none", "active": False},
            "mount": {"plugin": "none", "active": False},
            "focuser": {"plugin": "none", "active": False},
        },
    )
    state_store.set(
        "circumstances",
        {"loaded": True, "active_file": "eclipse.json", "meta": {}},
    )
    state_store.save()

    migration_calls = []
    real_migrate_legacy = rig_runtime.migrate_legacy

    def tracking_migrate_legacy(store, configs_dir):
        migration_calls.append((store, configs_dir))
        return real_migrate_legacy(store, configs_dir)

    monkeypatch.setattr(rig_runtime, "TRIGGER_DIR", tmp_path)
    monkeypatch.setattr(rig_runtime, "migrate_legacy", tracking_migrate_legacy)
    rig_runtime.reset_rig_manager_for_tests()

    manager = rig_runtime.get_rig_manager()

    assert isinstance(manager, RigManager)
    assert len(migration_calls) == 1
    migrated_store, configs_dir = migration_calls[0]
    assert isinstance(migrated_store, StateStore)
    assert migrated_store.path == tmp_path / "flask_app" / "state.json"
    assert configs_dir == tmp_path / "configs"
    config_path = (
        tmp_path
        / "configs"
        / "rig"
        / "default.json"
    )
    assert config_path.exists()
    assert rig_runtime.normalize_rigs_for_ui(manager) == [
        {"rig_id": 1, "name": "RIG 1", "enabled": True},
        {"rig_id": 2, "name": "RIG 2", "enabled": False},
        {"rig_id": 3, "name": "RIG 3", "enabled": False},
        {"rig_id": 4, "name": "RIG 4", "enabled": False},
    ]

    rig_runtime.reset_rig_manager_for_tests()
