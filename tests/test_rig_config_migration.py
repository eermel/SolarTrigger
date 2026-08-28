import pytest

from backend.rig_config import load, save, validate


def _minimal_config():
    return {
        "schema_version": 2,
        "eclipse": {
            "date": "2027-08-02",
            "reference_site": {
                "lat": 43.6,
                "lon": 1.44,
                "alt_m": 150,
            },
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
                "name": "Primary rig",
                "devices": {
                    "camera": {},
                    "mount": {},
                    "focuser": {},
                },
                "optics": {},
                "photo": {},
            }
        ],
    }


def test_minimal_v2_config_validates_and_round_trips(tmp_path):
    config = _minimal_config()

    assert validate(config) is None

    path = tmp_path / "rig-config.json"
    save(path, config)

    assert load(path) == config


@pytest.mark.parametrize("device_key", ("camera", "mount", "focuser"))
def test_validate_rejects_non_object_device_config(device_key):
    config = _minimal_config()
    config["rigs"][0]["devices"][device_key] = None

    with pytest.raises(ValueError, match=rf"devices\.{device_key} must be an object"):
        validate(config)
