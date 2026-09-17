import pytest

from backend.camera_profiles import exposure_planning_capabilities
from backend.executable_exposure_plan import (
    expand_executable_shutters,
    nearest_executable_shutter,
)
from backend.motion_exposure_policy import materialize_exposure_plan
from backend.preview_request import validate_payload


D850_BACKEND = "profile-nikon_nikon_dsc_d850_8769b3e7"


def _preview_config():
    return {
        "sequence": {
            "common": {
                "phases": {
                    "partial": {},
                    "diamond_ring": {},
                    "totality": {},
                }
            }
        }
    }


def _preview_payload(iso_max):
    return {
        "intents": [{
            "phase": "partial",
            "target_time": "2026-08-12T17:35:00Z",
            "deadline": None,
            "shutter_min": "1/500",
            "shutter_max": "1/500",
            "iso_target": 100,
            "request_id": "profile-iso",
        }],
        "rig_id": 2,
        "rig_override": {
            "optics": {
                "focal_length_mm": 800.0,
            },
            "photo": {
                "anti_trailing_enabled": True,
                "motion_tolerance_px": 1.0,
                "mechanical_vibration_enabled": False,
                "mechanical_vibration_delay_s": 2,
                "iso_compensation_enabled": True,
                "iso_max": iso_max,
                "atmos_enabled": False,
                "atmos_replace_enabled": False,
            },
        },
    }


def test_d850_planning_uses_full_characterized_profile():
    capabilities = exposure_planning_capabilities(D850_BACKEND)

    assert capabilities["strategy"] == "sequential"
    assert 6400 in capabilities["iso_values"]
    assert 12800 in capabilities["iso_values"]
    assert 25600 in capabilities["iso_values"]
    assert 51200 in capabilities["iso_values"]

    assert "1/13" in capabilities["shutter_values"]
    assert "1/8000" in capabilities["shutter_values"]
    assert "30" in capabilities["shutter_values"]


def test_d850_profile_grid_avoids_legacy_6400_range_error():
    capabilities = exposure_planning_capabilities(D850_BACKEND)

    result = materialize_exposure_plan(
        speeds=["8"],
        shutter_min=None,
        shutter_max=None,
        step_ev=1.0,
        iso_requested=100,
        iso_max=6400,
        t_max=1.0 / 13.0,
        iso_compensation_enabled=True,
        supported_shutters=capabilities["shutter_values"],
        supported_isos=capabilities["iso_values"],
    )

    assert result["exposure_plan"] == [
        {
            "shutter": "1/13",
            "iso": 6400,
        }
    ]
    assert "shutter_limited" in result["corrections"]
    assert "iso_compensated" in result["corrections"]
    assert "iso_capped" in result["warnings"]


def test_profile_backed_executable_plan_uses_d850_shutter_grid():
    rig = {
        "devices": {
            "camera": {
                "backend": D850_BACKEND,
            }
        }
    }

    assert nearest_executable_shutter(
        rig,
        1.0 / 13.0,
    ) == "1/13"

    shutters = expand_executable_shutters(
        rig,
        (
            True,
            "1/8000",
            "1/8",
            1.0,
            None,
        ),
    )

    assert shutters[0] == "1/8000"
    assert shutters[-1] == "1/8"


@pytest.mark.parametrize("iso_max", [12800, 25600])
def test_preview_request_accepts_profile_iso_max_above_legacy_6400(
    iso_max,
):
    _intents, rig_id, override = validate_payload(
        _preview_payload(iso_max),
        _preview_config(),
    )

    assert rig_id == 2
    assert override["photo"]["iso_max"] == iso_max
