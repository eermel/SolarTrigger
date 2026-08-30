import json
from datetime import datetime, timezone

import pytest

from backend.motion_exposure_policy import (
    compute_motion_exposure_ceiling,
    materialize_exposure_plan,
)


TARGET = datetime(
    2026, 8, 12, 18, 0, 0,
    tzinfo=timezone.utc,
)


def _sensor_db(tmp_path):
    path = tmp_path / "camera_sensors.v1.json"
    path.write_text(
        json.dumps({
            "schema_version": 1,
            "sensors": [{
                "manufacturer": "Test Cameras",
                "model": "Known Model",
                "sensor_width_mm": 30.0,
                "sensor_height_mm": 20.0,
                "width_px": 6000,
                "height_px": 4000,
                "pixel_pitch_um": 5.0,
                "sources": ["test fixture"],
            }],
        }),
        encoding="utf-8",
    )
    return path


def _base_policy():
    return {
        "devices": {
            "camera": {
                "manufacturer": "Test Cameras",
                "model": "Known Model",
            },
            "mount": None,
        },
        "optics": {
            "focal_length_mm": 1000.0,
        },
        "photo": {
            "anti_trailing_enabled": True,
            "motion_tolerance_px": 1.0,
            "iso_max": 6400,
        },
    }


def test_none_constraint_does_not_require_camera_or_optics():
    policy = {
        "photo": {
            "anti_trailing_enabled": False,
        },
    }

    assert compute_motion_exposure_ceiling(
        policy,
        TARGET,
    ) is None


def test_fixed_trailing_uses_sensor_focal_tolerance_and_declination(
    tmp_path,
):
    policy = _base_policy()

    ceiling = compute_motion_exposure_ceiling(
        policy,
        TARGET,
        sensor_db_path=_sensor_db(tmp_path),
        solar_declination_fn=lambda _when: 0.0,
    )

    assert ceiling > 0


def test_altaz_tracking_uses_sensor_corner_for_field_rotation(tmp_path):
    policy = _base_policy()
    policy["devices"]["mount"] = {
        "control": "external",
        "geometry": "altaz",
        "tracking": "solar",
    }
    policy["eclipse"] = {
        "reference_site": {
            "lat": 44.0,
            "lon": 2.0,
        },
    }

    ceiling = compute_motion_exposure_ceiling(
        policy,
        TARGET,
        sensor_db_path=_sensor_db(tmp_path),
        field_rotation_rate_fn=lambda *_args: 0.01,
        solar_position_fn=lambda _when: (100.0, 20.0),
        sidereal_fn=lambda _when: 120.0,
        hour_angle_fn=lambda _ra, _sidereal, _lon: 22.0,
    )

    radius_px = 0.5 * (6000.0 ** 2 + 4000.0 ** 2) ** 0.5
    expected = 1.0 / (
        0.01
        * 3.141592653589793
        / 180.0
        * radius_px
    )

    assert ceiling == pytest.approx(expected)


def test_field_rotation_does_not_require_focal_length(tmp_path):
    policy = _base_policy()
    policy["optics"] = {}
    policy["devices"]["mount"] = {
        "control": "external",
        "geometry": "altaz",
        "tracking": "solar",
    }
    policy["eclipse"] = {
        "reference_site": {
            "lat": 44.0,
            "lon": 2.0,
        },
    }

    ceiling = compute_motion_exposure_ceiling(
        policy,
        TARGET,
        sensor_db_path=_sensor_db(tmp_path),
        field_rotation_rate_fn=lambda *_args: 0.01,
        solar_position_fn=lambda _when: (100.0, 20.0),
        sidereal_fn=lambda _when: 120.0,
        hour_angle_fn=lambda _ra, _sidereal, _lon: 22.0,
    )

    assert ceiling > 0


def test_field_rotation_requires_no_radius_configuration(tmp_path):
    policy = _base_policy()
    policy["devices"]["mount"] = {
        "control": "external",
        "geometry": "altaz",
        "tracking": "solar",
    }
    policy["eclipse"] = {
        "reference_site": {
            "lat": 44.0,
            "lon": 2.0,
        },
    }

    assert "field_rotation_radius_deg" not in policy["photo"]

    ceiling = compute_motion_exposure_ceiling(
        policy,
        TARGET,
        sensor_db_path=_sensor_db(tmp_path),
        field_rotation_rate_fn=lambda *_args: 0.01,
        solar_position_fn=lambda _when: (100.0, 20.0),
        sidereal_fn=lambda _when: 120.0,
        hour_angle_fn=lambda _ra, _sidereal, _lon: 22.0,
    )

    assert ceiling > 0


def test_materializer_never_lengthens_a_regular_bracket():
    result = materialize_exposure_plan(
        speeds=None,
        shutter_min="1/125",
        shutter_max="1/1000",
        step_ev=1.0,
        iso_requested=200,
        iso_max=6400,
        t_max=4.0,
        iso_compensation_enabled=True,
    )

    assert result["shutter_min"] == "1/125"
    assert result["shutter_max"] == "1/1000"
    assert result["iso_applied"] == 200
    assert result["corrections"] == []
    assert result["warnings"] == []


def test_materializer_limits_regular_bracket_with_iso_compensation():
    result = materialize_exposure_plan(
        speeds=None,
        shutter_min="1",
        shutter_max="1/1000",
        step_ev=1.0,
        iso_requested=100,
        iso_max=6400,
        t_max=0.25,
        iso_compensation_enabled=True,
    )

    assert result["shutter_min"] == "1/4"
    assert result["iso_applied"] == 400
    assert result["corrections"] == [
        "shutter_limited",
        "iso_compensated",
    ]


def test_materializer_limits_regular_bracket_without_iso_compensation():
    result = materialize_exposure_plan(
        speeds=None,
        shutter_min="1",
        shutter_max="1/1000",
        step_ev=1.0,
        iso_requested=100,
        iso_max=6400,
        t_max=0.25,
        iso_compensation_enabled=False,
    )

    assert result["shutter_min"] == "1/4"
    assert result["iso_applied"] == 100
    assert result["corrections"] == ["shutter_limited"]


def test_materializer_only_changes_explicit_speeds_above_ceiling():
    result = materialize_exposure_plan(
        speeds=["1/1000", "1/125", "1"],
        shutter_min=None,
        shutter_max=None,
        step_ev=1.0,
        iso_requested=100,
        iso_max=6400,
        t_max=0.25,
        iso_compensation_enabled=False,
    )

    assert result["speeds"] == [
        "1/1000",
        "1/125",
        "1/4",
    ]
    assert result["iso_applied"] == 100
