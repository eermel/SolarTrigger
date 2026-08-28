from copy import deepcopy

import pytest

from backend.rig_config import validate
from backend.rig_manager import RigManager


def _minimal_v2_config(*, rig_count=1):
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
                "rig_id": 1,
                "enabled": True,
                "name": "RIG 1",
                "devices": {
                    "camera": {"backend": "simulated", "serial": None},
                    "mount": None,
                    "focuser": None,
                },
                "optics": {},
                "photo": {},
            }
        ],
    }

    for rig_id in range(2, rig_count + 1):
        rig = deepcopy(config["rigs"][0])
        rig["rig_id"] = rig_id
        rig["name"] = f"RIG {rig_id}"
        config["rigs"].append(rig)

    assert validate(config) is None
    return config


def test_distinct_camera_serials_are_accepted_across_rigs():
    config = _minimal_v2_config(rig_count=2)
    config["rigs"][0]["devices"]["camera"]["serial"] = "123"
    config["rigs"][1]["devices"]["camera"]["serial"] = "456"

    manager = RigManager.from_config(config)

    assert set(manager.rigs) == {1, 2}


def test_duplicate_camera_serials_are_rejected_across_rigs():
    config = _minimal_v2_config(rig_count=2)
    for rig in config["rigs"]:
        rig["devices"]["camera"]["serial"] = "123"

    with pytest.raises(ValueError, match="duplicate device identity"):
        RigManager.from_config(config)


def test_usb_bus_device_camera_serial_is_rejected():
    config = _minimal_v2_config()
    config["rigs"][0]["devices"]["camera"]["serial"] = "usb:001,006"

    with pytest.raises(ValueError, match=r"cannot use usb:bus,device"):
        RigManager.from_config(config)


def test_fallback_physical_path_is_accepted_with_identity_warning():
    config = _minimal_v2_config()
    camera = config["rigs"][0]["devices"]["camera"]
    camera["fallback_physical_path"] = "/dev/serial/by-path/pci-..."

    manager = RigManager.from_config(config)

    assert len(manager.identity_warnings) == 1
    assert "RIG 1" in manager.identity_warnings[0]
    assert "camera" in manager.identity_warnings[0]


def test_minimal_single_rig_without_alias_or_fallback_has_no_warning():
    config = _minimal_v2_config()

    manager = RigManager.from_config(config)

    assert set(manager.rigs) == {1}
    assert manager.identity_warnings == []
