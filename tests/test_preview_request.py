from datetime import datetime

import pytest

from backend.preview_request import validate_and_normalize


CONFIG = {"sequence": {"common": {"phases": {"partial": {}, "totality": {}}}}}


def intent(**updates):
    value = {
        "phase": "partial",
        "target_time": "2026-08-12T14:30:00+02:00",
        "shutter_min": "1/125",
        "shutter_max": "1/1000",
    }
    value.update(updates)
    return value


def test_valid_intents_are_normalized_in_request_order():
    payload = {
        "intents": [
            intent(iso_target=200, request_id=None),
            intent(
                phase="totality",
                target_time="2026-08-12T12:31:00Z",
                shutter_min=None,
                shutter_max=None,
                speeds=["1/8", "1/4"],
                iso_target="0400",
                origin="manual",
                request_id="req-2",
            ),
        ]
    }

    result = validate_and_normalize(payload, CONFIG)

    assert [item["phase"] for item in result] == ["partial", "totality"]
    assert result[0]["target_time"] == datetime(2026, 8, 12, 12, 30)
    assert result[0]["step_ev"] == 1.0
    assert result[0]["iso_target"] == "200"
    assert result[0]["origin"] == "partial"
    assert result[0]["request_id"] is None
    assert result[1]["iso_target"] == "400"
    assert result[1]["request_id"] == "req-2"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"intents": [], "extra": True},
        {"intents": [intent(extra=True)]},
        {"intents": [intent(speeds=["1/8"])]},
        {"intents": [intent(shutter_min=None)]},
        {"intents": [intent(iso_target=True)]},
        {"intents": [intent(iso_target=1.5)]},
        {"intents": [intent(step_ev=True)]},
        {"intents": [intent(target_time="2026-08-12T12:30:00")]},
        {"intents": [intent(phase="missing")]},
    ],
)
def test_malformed_payloads_raise_only_value_error(payload):
    with pytest.raises(ValueError):
        validate_and_normalize(payload, CONFIG)


def test_deadline_is_normalized_and_inputs_are_not_mutated():
    raw_intent = intent(deadline="2026-08-12T14:29:59+02:00")
    payload = {"intents": [raw_intent]}

    result = validate_and_normalize(payload, CONFIG)

    assert result[0]["deadline"] == datetime(2026, 8, 12, 12, 29, 59)
    assert raw_intent["deadline"] == "2026-08-12T14:29:59+02:00"


def test_preview_payload_accepts_temporary_rig_override():
    from backend.preview_request import validate_payload

    config = {
        "sequence": {
            "common": {
                "phases": {
                    "partial": {},
                }
            }
        }
    }
    payload = {
        "intents": [{
            "phase": "partial",
            "target_time": "2026-08-12T17:30:00Z",
            "deadline": None,
            "shutter_min": "1/125",
            "shutter_max": "1/1000",
            "iso_target": 200,
        }],
        "rig_id": 2,
        "rig_override": {
            "optics": {
                "focal_length_mm": 430.0,
            },
            "photo": {
                "anti_trailing_enabled": True,
                "motion_tolerance_px": 1.0,
                "iso_compensation_enabled": False,
                "iso_max": 3200,
                "atmos_enabled": True,
            },
        },
    }

    intents, rig_id, override = validate_payload(payload, config)

    assert len(intents) == 1
    assert rig_id == 2
    assert override == payload["rig_override"]


def test_preview_payload_rejects_unknown_override_fields():
    from backend.preview_request import validate_payload

    config = {
        "sequence": {
            "common": {
                "phases": {
                    "partial": {},
                }
            }
        }
    }
    payload = {
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
            "optics": {
                "focal_length_mm": 430.0,
            },
            "photo": {
                "anti_trailing_enabled": True,
                "motion_tolerance_px": 1.0,
                "iso_compensation_enabled": True,
                "iso_max": 6400,
                "atmos_enabled": False,
                "unexpected": True,
            },
        },
    }

    with pytest.raises(ValueError, match="invalid or missing fields"):
        validate_payload(payload, config)
