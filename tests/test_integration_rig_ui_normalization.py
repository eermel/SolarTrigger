import pytest

from backend import rig_runtime
from backend.rig_config import validate
from backend.rig_manager import RigManager


@pytest.fixture
def v2_config_factory():
    def build(rig_count):
        config = {
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
                    "rig_id": rig_id,
                    "enabled": True,
                    "name": f"Configured RIG {rig_id}",
                    "devices": {
                        "camera": {"backend": "simulated"},
                        "mount": None,
                        "focuser": None,
                    },
                    "optics": {},
                    "photo": {},
                }
                for rig_id in range(1, rig_count + 1)
            ],
        }
        validate(config)
        return config

    return build


@pytest.fixture(autouse=True)
def isolated_rig_manager():
    rig_runtime.reset_rig_manager_for_tests()
    yield
    rig_runtime.reset_rig_manager_for_tests()


@pytest.mark.parametrize("rig_count", [1, 2, 4])
def test_configured_rigs_are_normalized_and_installed(
    rig_count, v2_config_factory
):
    config = v2_config_factory(rig_count)
    manager = RigManager.from_config(config)

    slots = rig_runtime.normalize_rigs_for_ui(manager)

    assert len(slots) == 4
    assert slots == [
        {
            "rig_id": rig_id,
            "name": (
                f"Configured RIG {rig_id}"
                if rig_id <= rig_count
                else f"RIG {rig_id}"
            ),
            "enabled": rig_id <= rig_count,
        }
        for rig_id in range(1, 5)
    ]

    for rig_id in range(1, rig_count + 1):
        assert manager.get_rig(rig_id).rig_id == rig_id
    for rig_id in range(rig_count + 1, 5):
        with pytest.raises(ValueError, match=f"rig_id {rig_id} is not configured"):
            manager.get_rig(rig_id)

    reloaded = rig_runtime.reload_rig_manager(config)
    assert rig_runtime.get_rig_manager() is reloaded
    assert rig_runtime.normalize_rigs_for_ui(reloaded) == slots
