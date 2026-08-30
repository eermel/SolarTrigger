import pytest

from backend.rig_config import (
    canonical_rig_defaults,
    normalize_rig_defaults,
    validate,
)


def test_new_rig_defaults_mechanical_vibration_to_false():
    rig = canonical_rig_defaults(1)

    assert rig["photo"]["mechanical_vibration_enabled"] is False


def test_normalize_old_rig_adds_mechanical_vibration_false():
    config = {
        "rigs": [
            {
                "rig_id": 1,
                "photo": {
                    "anti_trailing_enabled": True,
                },
                "optics": {},
            }
        ]
    }

    normalize_rig_defaults(config)

    assert (
        config["rigs"][0]["photo"]["mechanical_vibration_enabled"]
        is False
    )


def test_normalize_preserves_existing_mechanical_vibration_value():
    config = {
        "rigs": [
            {
                "rig_id": 1,
                "photo": {
                    "mechanical_vibration_enabled": True,
                },
                "optics": {},
            }
        ]
    }

    normalize_rig_defaults(config)

    assert (
        config["rigs"][0]["photo"]["mechanical_vibration_enabled"]
        is True
    )


def _valid_config():
    return {
        "schema_version": 2,
        "eclipse": {
            "date": "2027-08-02",
            "reference_site": {
                "lat": 24.38268,
                "lon": 35.38335,
                "alt_m": 4.0,
            },
            "circumstances": {
                "C1": "08:47:53.110",
                "C2": "10:09:55.484",
                "TMAX": "10:12:58.158",
                "C3": "10:16:00.276",
                "C4": "11:33:09.902",
            },
        },
        "sequence": {"common": {}},
        "rigs": [canonical_rig_defaults(1)],
    }


def test_validate_accepts_mechanical_vibration_true():
    config = _valid_config()
    config["rigs"][0]["photo"]["mechanical_vibration_enabled"] = True

    assert validate(config) is None


@pytest.mark.parametrize("value", [1, 0, "false", None, {}])
def test_validate_rejects_non_boolean_mechanical_vibration(value):
    config = _valid_config()
    config["rigs"][0]["photo"]["mechanical_vibration_enabled"] = value

    with pytest.raises(
        ValueError,
        match=r"photo\.mechanical_vibration_enabled must be a boolean",
    ):
        validate(config)
