from pathlib import Path

import pytest

from backend.camera_model_resolution import resolve_sensor_entry
from backend.sensor_db import load_sensor_db


FIXTURES = Path(__file__).parent / "fixtures"


def test_resolve_known_model():
    db = load_sensor_db(FIXTURES / "sensors_valid.json")

    entry = resolve_sensor_entry("Nikon", "D850", db)

    assert entry["id_key"] == "Nikon::D850"
    assert isinstance(entry["pixel_pitch_um"], float)
    assert entry["pixel_pitch_um"] == pytest.approx(35.9 * 1000 / 8256)
    assert entry["camera_type"] is None


def test_resolve_known_alias():
    db = load_sensor_db(FIXTURES / "sensors_valid.json")

    entry = resolve_sensor_entry("Nikon", "Nikon D850", db)

    assert entry["id_key"] == "Nikon::D850"
    assert isinstance(entry["pixel_pitch_um"], float)
    assert entry["pixel_pitch_um"] == pytest.approx(35.9 * 1000 / 8256)
    assert entry["camera_type"] is None
