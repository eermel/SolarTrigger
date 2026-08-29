import pytest

from backend.rig_manager import Rig, RigManager


def _config(*, enabled_rig_ids=(), camera_backend="simulated", rig_count=4):
    enabled_rig_ids = set(enabled_rig_ids)
    return {
        "schema_version": 2,
        "eclipse": {
            "date": "2027-08-02",
            "reference_site": {"lat": 43.6, "lon": 1.44, "alt_m": 150},
            "circumstances": {
                "C1": "2027-08-02T08:00:00Z",
                "C2": "2027-08-02T09:00:00Z",
                "C3": "2027-08-02T09:02:00Z",
                "C4": "2027-08-02T10:00:00Z",
            },
        },
        "sequence": {"common": {}},
        "rigs": [
            {
                "rig_id": rig_id,
                "enabled": rig_id in enabled_rig_ids,
                "name": f"Rig {rig_id}",
                "devices": {
                    "camera": {
                        "backend": camera_backend
                        if rig_id in enabled_rig_ids
                        else "none"
                    }
                },
            }
            for rig_id in range(1, rig_count + 1)
        ],
    }


def _assert_no_hardware_services(rig):
    assert rig.camera_service is None
    assert rig.mount_service is None
    assert rig.focuser_service is None


def test_all_disabled_rigs_construct_without_hardware_services():
    manager = RigManager.from_config(_config())

    assert len(manager.rigs) == 4
    for rig_id in range(1, 5):
        rig = manager.get_rig(rig_id)
        assert rig.enabled is False
        _assert_no_hardware_services(rig)


def test_one_enabled_rig_constructs_with_empty_service_placeholders():
    manager = RigManager.from_config(
        _config(enabled_rig_ids={1}, rig_count=1, camera_backend="simulated")
    )

    rig = manager.get_rig(1)
    assert isinstance(rig, Rig)
    assert rig.enabled is True
    _assert_no_hardware_services(rig)


def test_four_enabled_rigs_construct_successfully():
    manager = RigManager.from_config(
        _config(enabled_rig_ids={1, 2, 3, 4}, camera_backend="simulated")
    )

    for rig_id in range(1, 5):
        rig = manager.get_rig(rig_id)
        assert isinstance(rig, Rig)
        assert rig.rig_id == rig_id
        _assert_no_hardware_services(rig)


@pytest.mark.parametrize("camera_backend", ["none", ""])
def test_enabled_rig_may_be_configured_without_pilotable_camera(camera_backend):
    config = _config(
        enabled_rig_ids={1}, rig_count=1, camera_backend=camera_backend
    )

    manager = RigManager.from_config(config)

    assert manager.get_rig(1).devices["camera"]["backend"] == camera_backend


@pytest.mark.parametrize("rig_id", [0, 5, "x"])
def test_get_rig_rejects_invalid_identifier(rig_id):
    manager = RigManager.from_config(_config(rig_count=1))

    with pytest.raises(ValueError, match="rig_id"):
        manager.get_rig(rig_id)
