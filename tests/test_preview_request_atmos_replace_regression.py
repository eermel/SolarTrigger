import pytest

from backend.preview_request import validate_payload


CONFIG = {"sequence": {"common": {"phases": {"partial": {}}}}}


def _payload(*, atmos_replace_marker=...):
    photo = {
        "anti_trailing_enabled": True,
        "motion_tolerance_px": 1.0,
        "mechanical_vibration_enabled": False,
        "mechanical_vibration_delay_s": 2,
        "iso_compensation_enabled": True,
        "iso_max": 6400,
        "atmos_enabled": True,
    }
    if atmos_replace_marker is not ...:
        photo["atmos_replace_enabled"] = atmos_replace_marker

    return {
        "intents": [{
            "phase": "partial",
            "target_time": "2026-08-12T17:30:00Z",
            "deadline": None,
            "shutter_min": "1/125",
            "shutter_max": "1/1000",
            "iso_target": 200,
        }],
        "rig_id": 1,
        "rig_override": {
            "optics": {"focal_length_mm": 430.0},
            "photo": photo,
        },
    }


@pytest.mark.parametrize("replace_enabled", [False, True])
def test_current_expo_opt_payload_accepts_atmos_replace_flag(replace_enabled):
    _intents, _rig_id, override = validate_payload(
        _payload(atmos_replace_marker=replace_enabled),
        CONFIG,
    )

    assert override["photo"]["atmos_replace_enabled"] is replace_enabled


def test_legacy_preview_payload_defaults_atmos_replace_to_false():
    _intents, _rig_id, override = validate_payload(_payload(), CONFIG)

    assert override["photo"]["atmos_replace_enabled"] is False


def test_preview_payload_rejects_non_boolean_atmos_replace_flag():
    with pytest.raises(
        ValueError,
        match="atmos_replace_enabled must be a boolean",
    ):
        validate_payload(
            _payload(atmos_replace_marker="true"),
            CONFIG,
        )


def test_preview_payload_still_rejects_unknown_photo_fields():
    payload = _payload(atmos_replace_marker=False)
    payload["rig_override"]["photo"]["unexpected"] = True

    with pytest.raises(ValueError, match="invalid or missing fields"):
        validate_payload(payload, CONFIG)
