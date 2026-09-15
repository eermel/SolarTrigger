from datetime import datetime, timezone

import backend.sequencer_compiler as compiler


def _rig():
    return {
        "rig_id": 1,
        "enabled": True,
        "devices": {
            "camera": {"backend": "sony", "manufacturer": "SONY", "model": "ILCE-7M5"},
            "mount": {"control": "none", "geometry": "fixed", "tracking": False},
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


def _photo_config():
    base = {
        "enabled": True,
        "iso": 100,
        "aperture": "f/8",
        "shutter_min": "1/250",
        "shutter_max": "1/1000",
        "step_ev": 1.0,
    }
    return {
        "phases": {
            "partial": dict(base),
            "diamond_ring": {**base, "duration_s": 30, "interval_s": 4},
            "totality": dict(base),
        }
    }


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


def _materialize(monkeypatch, phase, window, when):
    _patch_atmos_math(monkeypatch)
    target = compiler.CaptureTarget(
        target_time=when,
        phase=phase,
        phase_window=window,
        sequence_index=0,
        deadline=None,
    )
    return compiler.materialize_capture_target_for_rig(
        target,
        _rig(),
        _photo_config(),
        {
            "atmospheric_attenuation_enabled": True,
            "atmospheric_attenuation_replace_exposures": False,
            "rigs": [],
        },
        _ctx(),
    )


def test_atmos_applies_before_c2_diamond_ring(monkeypatch):
    capture = _materialize(
        monkeypatch, "partial", "phase_1a",
        datetime(2027, 8, 2, 10, 29, 29, tzinfo=timezone.utc),
    )
    assert capture.atmos_applied is True
    assert len(capture.final_exposure_plan) == 6


def test_atmos_disabled_during_pre_c2_diamond_ring(monkeypatch):
    capture = _materialize(
        monkeypatch, "diamond_ring", "phase_1b",
        datetime(2027, 8, 2, 10, 29, 30, tzinfo=timezone.utc),
    )
    assert capture.atmos_applied is False
    assert [x["shutter"] for x in capture.final_exposure_plan] == [
        "1/1000", "1/500", "1/250"
    ]


def test_atmos_disabled_during_totality(monkeypatch):
    capture = _materialize(
        monkeypatch, "totality", "phase_2",
        datetime(2027, 8, 2, 10, 31, 0, tzinfo=timezone.utc),
    )
    assert capture.atmos_applied is False
    assert [x["shutter"] for x in capture.final_exposure_plan] == [
        "1/1000", "1/500", "1/250"
    ]


def test_atmos_disabled_during_post_c3_diamond_ring(monkeypatch):
    capture = _materialize(
        monkeypatch, "diamond_ring", "phase_3a",
        datetime(2027, 8, 2, 10, 32, 30, tzinfo=timezone.utc),
    )
    assert capture.atmos_applied is False
    assert [x["shutter"] for x in capture.final_exposure_plan] == [
        "1/1000", "1/500", "1/250"
    ]


def test_atmos_applies_after_c3_diamond_ring(monkeypatch):
    capture = _materialize(
        monkeypatch, "partial", "phase_3b",
        datetime(2027, 8, 2, 10, 32, 31, tzinfo=timezone.utc),
    )
    assert capture.atmos_applied is True
    assert len(capture.final_exposure_plan) == 6
