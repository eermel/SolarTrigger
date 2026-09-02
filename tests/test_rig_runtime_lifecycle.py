import json

import pytest

from backend import rig_runtime
from backend.state_store import StateStore
from services import camera_service, focuser_service, mount_service


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
                "devices": {
                    "camera": {"backend": "none"},
                    "mount": None,
                    "focuser": None,
                },
                "optics": {},
                "photo": {},
            }
        ],
    }


def _write_canonical_config(root, config):
    path = root / "configs" / "rig" / "default.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(config), encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def _isolated_rig_manager():
    rig_runtime.reset_rig_manager_for_tests()
    yield
    rig_runtime.reset_rig_manager_for_tests()


def test_get_rig_manager_reuses_one_instance_across_calls(tmp_path, monkeypatch):
    _write_canonical_config(tmp_path, _config())
    monkeypatch.setattr(rig_runtime, "TRIGGER_DIR", tmp_path)

    first = rig_runtime.get_rig_manager()

    assert rig_runtime.get_rig_manager() is first
    assert rig_runtime.get_rig_manager() is first


def test_missing_canonical_config_persists_legacy_migration(
    tmp_path,
    monkeypatch,
):
    calls = []

    def migrate(store, configs_dir):
        calls.append((store.path, configs_dir))
        return _config()

    monkeypatch.setattr(
        rig_runtime,
        "TRIGGER_DIR",
        tmp_path,
    )
    monkeypatch.setattr(
        rig_runtime,
        "migrate_legacy",
        migrate,
    )

    manager = rig_runtime.get_rig_manager()

    assert manager.get_rig(1).name == "RIG 1"

    assert calls == [
        (
            tmp_path / "flask_app" / "state.json",
            tmp_path / "configs",
        )
    ]

    config_path = (
        tmp_path
        / "configs"
        / "rig"
        / "default.json"
    )

    assert config_path.exists()

    persisted = json.loads(
        config_path.read_text(encoding="utf-8")
    )

    assert persisted == _config()

    # Once canonical persistence exists, legacy migration is no
    # longer consulted after a runtime-manager reset.
    rig_runtime.reset_rig_manager_for_tests()

    def unexpected_migration(*_args, **_kwargs):
        pytest.fail(
            "persisted canonical config must replace legacy migration"
        )

    monkeypatch.setattr(
        rig_runtime,
        "migrate_legacy",
        unexpected_migration,
    )

    reloaded = rig_runtime.get_rig_manager()

    assert reloaded.get_rig(1).name == "RIG 1"


def test_invalid_canonical_config_propagates_without_migration(tmp_path, monkeypatch):
    _write_canonical_config(tmp_path, {"schema_version": 2})

    def unexpected_migration(*_args, **_kwargs):
        pytest.fail("an existing invalid canonical config must not be replaced")

    monkeypatch.setattr(rig_runtime, "TRIGGER_DIR", tmp_path)
    monkeypatch.setattr(rig_runtime, "migrate_legacy", unexpected_migration)

    with pytest.raises(ValueError, match="eclipse must be an object"):
        rig_runtime.get_rig_manager()


def test_preparing_rigs_for_ui_creates_no_services_or_legacy_rig_state(
    tmp_path, monkeypatch
):
    _write_canonical_config(tmp_path, _config())
    legacy_path = tmp_path / "flask_app" / "state.json"
    legacy_path.parent.mkdir(parents=True)
    legacy_path.write_text(json.dumps({"gps": {"connected": False}}), encoding="utf-8")
    original_legacy = legacy_path.read_bytes()

    def unexpected_service(*_args, **_kwargs):
        pytest.fail("preparing rigs for UI must not instantiate hardware services")

    def unexpected_state_write(*_args, **_kwargs):
        pytest.fail("preparing rigs for UI must not update legacy state.json")

    monkeypatch.setattr(camera_service, "CameraService", unexpected_service)
    monkeypatch.setattr(mount_service, "MountService", unexpected_service)
    monkeypatch.setattr(focuser_service, "FocuserService", unexpected_service)
    monkeypatch.setattr(StateStore, "set", unexpected_state_write)
    monkeypatch.setattr(StateStore, "update_section", unexpected_state_write)
    monkeypatch.setattr(StateStore, "save", unexpected_state_write)
    monkeypatch.setattr(rig_runtime, "TRIGGER_DIR", tmp_path)

    rigs = rig_runtime.normalize_rigs_for_ui(rig_runtime.get_rig_manager())

    assert rigs == [
        {"rig_id": 1, "name": "RIG 1", "enabled": False},
        {"rig_id": 2, "name": "RIG 2", "enabled": False},
        {"rig_id": 3, "name": "RIG 3", "enabled": False},
        {"rig_id": 4, "name": "RIG 4", "enabled": False},
    ]
    assert legacy_path.read_bytes() == original_legacy
