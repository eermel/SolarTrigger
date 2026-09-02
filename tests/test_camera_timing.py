import json

import pytest

from backend.camera_timing import load_camera_timing_profile


def _write(tmp_path, payload):
    path = tmp_path / "camera.json"
    path.write_text(
        json.dumps(payload),
        encoding="utf-8",
    )
    return path


def test_load_camera_timing_profile(tmp_path):
    path = _write(
        tmp_path,
        {
            "schema_version": 1,
            "config_type": "camera_timing",
            "backend": "sony",
            "manufacturer": "Sony",
            "model": "ILCE-7M5",
            "timing": {
                "set_iso_ms": 101,
                "set_capturemode_ms": 122,
                "set_shutter_ms": 153,
                "trigger_single_latency_ms": 254,
                "trigger_single_duration_ms": 333,
                "bracket_press_latency_ms": 285,
                "bracket_release_ms": 40,
                "settle_idle_ms": 501,
            },
        },
    )

    profile = load_camera_timing_profile(path)

    assert profile.backend == "sony"
    assert profile.set_iso_ms == 101
    assert profile.set_capturemode_ms == 122
    assert profile.set_shutter_ms == 153
    assert profile.trigger_single_latency_ms == 254
    assert profile.trigger_single_duration_ms == 333
    assert profile.bracket_press_latency_ms == 285
    assert profile.bracket_release_ms == 40
    assert profile.settle_idle_ms == 501


def test_rejects_negative_timing(tmp_path):
    path = _write(
        tmp_path,
        {
            "schema_version": 1,
            "config_type": "camera_timing",
            "backend": "sony",
            "timing": {
                "bracket_press_latency_ms": -1,
            },
        },
    )

    with pytest.raises(ValueError):
        load_camera_timing_profile(path)


def test_missing_values_default_to_zero(tmp_path):
    path = _write(
        tmp_path,
        {
            "schema_version": 1,
            "config_type": "camera_timing",
            "backend": "sony",
            "timing": {},
        },
    )

    profile = load_camera_timing_profile(path)

    assert profile.set_iso_ms == 0
    assert profile.trigger_single_duration_ms == 0
    assert profile.bracket_press_latency_ms == 0


def test_rejects_wrong_config_type(tmp_path):
    path = _write(
        tmp_path,
        {
            "schema_version": 1,
            "config_type": "camera",
            "backend": "sony",
            "timing": {},
        },
    )

    with pytest.raises(
        ValueError,
        match="config_type",
    ):
        load_camera_timing_profile(path)
