from backend import rig_runtime


def _config():
    return {
        "schema_version": 2,
        "eclipse": {
            "date": "2027-08-02",
            "reference_site": {"lat": 43.6, "lon": 1.44, "alt_m": 150},
            "circumstances": {
                "C1": "2027-08-02T08:00:00Z",
                "C2": "2027-08-02T09:00:00Z",
                "TMAX": "2027-08-02T09:01:00Z",
                "C3": "2027-08-02T09:02:00Z",
                "C4": "2027-08-02T10:00:00Z",
            },
        },
        "sequence": {"common": {}},
        "rigs": [
            {
                "rig_id": 1,
                "enabled": False,
                "name": "RIG 1",
                "devices": {"camera": {"backend": "none"}},
                "optics": {},
                "photo": {},
            }
        ],
    }


def test_get_rig_manager_is_cached_and_reset_reloads_canonical_config(
    tmp_path, monkeypatch
):
    migration_calls = []

    def migrate_legacy(state_store, configs_dir):
        migration_calls.append((state_store, configs_dir))
        return _config()

    monkeypatch.setattr(rig_runtime, "TRIGGER_DIR", tmp_path)
    monkeypatch.setattr(rig_runtime, "migrate_legacy", migrate_legacy)
    rig_runtime.reset_rig_manager_for_tests()

    first = rig_runtime.get_rig_manager()
    second = rig_runtime.get_rig_manager()

    assert second is first
    assert len(migration_calls) == 1

    config_path = (
        tmp_path
        / "var"
        / "generated"
        / "rig"
        / "default.json"
    )

    assert config_path.exists()

    rig_runtime.reset_rig_manager_for_tests()
    after_reset = rig_runtime.get_rig_manager()

    assert after_reset is not first
    assert len(migration_calls) == 1

    rig_runtime.reset_rig_manager_for_tests()
