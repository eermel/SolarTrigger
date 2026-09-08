import pytest

from backend.rig_config import canonical_rig_defaults, validate


def _config(photo_patch=None):
    rig = canonical_rig_defaults(1)
    if photo_patch:
        rig["photo"].update(photo_patch)
    return {
        "schema_version": 2,
        "eclipse": None,
        "sequence": {"common": {}},
        "rigs": [rig],
    }


def test_mechanical_vibration_defaults_are_off_and_two_seconds():
    photo = canonical_rig_defaults(1)["photo"]
    assert photo["mechanical_vibration_enabled"] is False
    assert photo["mechanical_vibration_delay_s"] == 2


@pytest.mark.parametrize("delay", [0, 1, 2, 3, 4, 5])
def test_mechanical_vibration_delay_accepts_integer_zero_to_five(delay):
    validate(_config({
        "mechanical_vibration_enabled": True,
        "mechanical_vibration_delay_s": delay,
    }))


@pytest.mark.parametrize("delay", [-1, 6, 1.5, True, "2"])
def test_mechanical_vibration_delay_rejects_outside_contract(delay):
    with pytest.raises(ValueError, match="mechanical_vibration_delay_s"):
        validate(_config({"mechanical_vibration_delay_s": delay}))



@pytest.mark.parametrize("value", [1, 0, "false", None, {}])
def test_mechanical_vibration_enabled_rejects_non_boolean_values(value):
    with pytest.raises(
        ValueError,
        match="mechanical_vibration_enabled",
    ):
        validate(_config({
            "mechanical_vibration_enabled": value,
        }))
