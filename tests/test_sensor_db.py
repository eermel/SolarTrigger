from pathlib import Path

import pytest

from backend.sensor_db import load_sensor_db, lookup_model, make_manual_entry


FIXTURES = Path(__file__).parent / "fixtures"


def test_load_valid_then_lookup_model():
    db = load_sensor_db(FIXTURES / "sensors_valid.json")

    entry = lookup_model(db, "Nikon", "D850")

    assert entry["pixel_pitch_um"] == pytest.approx(35.9 * 1000 / 8256)
    assert entry["camera_type"] is None


def test_lookup_by_alias():
    db = load_sensor_db(FIXTURES / "sensors_valid.json")

    canonical = lookup_model(db, "Nikon", "D850")
    alias = lookup_model(db, "Nikon", "Nikon D850")

    assert alias["pixel_pitch_um"] == pytest.approx(canonical["pixel_pitch_um"])


def test_invalid_fixture_raises():
    with pytest.raises(ValueError, match="sources"):
        load_sensor_db(FIXTURES / "sensors_invalid.json")


def test_load_normalizes_camera_type():
    db = load_sensor_db(FIXTURES / "sensors_camera_type_valid.json")

    entry = lookup_model(db, "Nikon", "D850")

    assert entry["camera_type"] == "dslr"


def test_invalid_camera_type_raises():
    with pytest.raises(ValueError, match="camera_type"):
        load_sensor_db(FIXTURES / "sensors_camera_type_invalid.json")


def test_manual_fallback():
    db = load_sensor_db(FIXTURES / "sensors_valid.json")
    with pytest.raises(KeyError):
        lookup_model(db, "Unknown", "Custom")

    fallback = make_manual_entry("Unknown", "Custom", 36.0, 24.0, 6000, 4000)

    assert "manual" in fallback["sources"]
    assert fallback["pixel_pitch_um"] == pytest.approx(36.0 * 1000 / 6000)


def test_production_db_resolves_gphoto2_d850_model_without_serial():
    db_path = (
        Path(__file__).resolve().parents[1]
        / "data"
        / "camera_sensors"
        / "camera_sensors_2017plus_zwo.json"
    )
    db = load_sensor_db(db_path)

    canonical = lookup_model(db, "Nikon", "D850")
    gphoto2 = lookup_model(db, "Nikon", "Nikon DSC D850")

    assert gphoto2["model"] == "D850"
    assert gphoto2["pixel_pitch_um"] == pytest.approx(
        canonical["pixel_pitch_um"]
    )
