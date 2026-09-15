from datetime import datetime, timezone

import pytest

import backend.sequencer_compiler as compiler


def _rig():
    return {
        "rig_id": 1,
        "enabled": True,
        "devices": {
            "camera": {
                "backend": "sony",
                "manufacturer": "SONY",
                "model": "ILCE-7M5",
            },
            "mount": {
                "control": "none",
                "geometry": "fixed",
                "tracking": False,
            },
        },
        "optics": {"focal_length_mm": 430},
        "photo": {
            "atmos_enabled": False,
            "atmos_replace_enabled": False,
            "anti_trailing_enabled": False,
            "motion_tolerance_px": 1.0,
            "iso_compensation_enabled": True,
            "iso_max": 6400,
        },
    }


def _photo_config(speeds=None):
    phase = {
        "enabled": True,
        "iso": 100,
        "aperture": "f/8",
        "shutter_min": "1/250",
        "shutter_max": "1/1000",
        "step_ev": 1.0,
    }
    if speeds is not None:
        phase["speeds"] = speeds
        phase["shutter_min"] = None
        phase["shutter_max"] = None
    return {"phases": {"partial": phase}}


def _target():
    return compiler.CaptureTarget(
        target_time=datetime(2027, 8, 2, 10, 5, tzinfo=timezone.utc),
        phase="partial",
        phase_window="phase_1a",
        sequence_index=0,
        deadline=datetime(2027, 8, 2, 10, 6, tzinfo=timezone.utc),
    )


def _ctx():
    return {
        "timeline": {
            "C1": "2027-08-02T10:00:00+00:00",
            "C2": "2027-08-02T10:30:00+00:00",
            "TMAX": "2027-08-02T10:31:00+00:00",
            "C3": "2027-08-02T10:32:00+00:00",
            "C4": "2027-08-02T11:00:00+00:00",
        },
        "altitudes": {
            "C1_alt_deg": 10.0,
            "C2_alt_deg": 10.0,
            "TMAX_alt_deg": 10.0,
            "C3_alt_deg": 10.0,
            "C4_alt_deg": 10.0,
        },
        "location": {"altitude_m": 0.0},
    }


def _patch_atmos_math(monkeypatch):
    monkeypatch.setattr(
        "backend.preview_materializer.validate_atmospheric_timeline",
        lambda *_a, **_k: None,
    )
    monkeypatch.setattr(
        "backend.preview_materializer.interpolate_altitude",
        lambda *_a, **_k: 10.0,
    )
    monkeypatch.setattr(
        "backend.preview_materializer.facteur_atmospherique",
        lambda *_a, **_k: 2.0,
    )
    monkeypatch.setattr(
        "backend.preview_materializer.nearest_executable_shutter",
        lambda _rig, seconds: {
            0.002: "1/500",
            0.004: "1/250",
            0.008: "1/125",
        }[round(seconds, 6)],
    )


def test_apply_exposure_optimization_maps_atmos_replace_flag():
    result = compiler.apply_exposure_optimization(
        _rig(),
        {
            "atmospheric_attenuation_enabled": True,
            "atmospheric_attenuation_replace_exposures": True,
            "rigs": [],
        },
    )
    assert result["photo"]["atmos_enabled"] is True
    assert result["photo"]["atmos_replace_enabled"] is True


def test_compiler_append_mode_keeps_every_original_and_compensated_view(monkeypatch):
    _patch_atmos_math(monkeypatch)

    result = compiler.materialize_capture_target_for_rig(
        _target(),
        _rig(),
        _photo_config(),
        {
            "atmospheric_attenuation_enabled": True,
            "atmospheric_attenuation_replace_exposures": False,
            "rigs": [],
        },
        _ctx(),
    )

    assert [item["shutter"] for item in result.final_exposure_plan] == [
        "1/1000", "1/500", "1/250",
        "1/500", "1/250", "1/125",
    ]
    assert [
        item.get("sequence_group")
        for item in result.final_exposure_plan
    ] == [
        None, None, None,
        compiler.ATMOS_EXPOSURE_GROUP,
        compiler.ATMOS_EXPOSURE_GROUP,
        compiler.ATMOS_EXPOSURE_GROUP,
    ]


def test_compiler_replace_mode_uses_every_compensated_view_only(monkeypatch):
    _patch_atmos_math(monkeypatch)

    result = compiler.materialize_capture_target_for_rig(
        _target(),
        _rig(),
        _photo_config(),
        {
            "atmospheric_attenuation_enabled": True,
            "atmospheric_attenuation_replace_exposures": True,
            "rigs": [],
        },
        _ctx(),
    )

    assert [item["shutter"] for item in result.final_exposure_plan] == [
        "1/500", "1/250", "1/125",
    ]
    assert all(
        item.get("sequence_group") == compiler.ATMOS_EXPOSURE_GROUP
        for item in result.final_exposure_plan
    )


def test_compiler_rejects_non_boolean_global_replace_flag():
    with pytest.raises(
        ValueError,
        match="atmospheric_attenuation_replace_exposures must be boolean",
    ):
        compiler.apply_exposure_optimization(
            _rig(),
            {
                "atmospheric_attenuation_replace_exposures": "false",
                "rigs": [],
            },
        )
