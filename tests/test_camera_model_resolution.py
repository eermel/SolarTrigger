from pathlib import Path

import pytest

from backend.camera_model_resolution import resolve_sensor_entry
from backend.device_identity import identity_key
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


def test_same_model_resolves_same_sensor_with_distinct_device_identities():
    db = load_sensor_db(FIXTURES / "sensors_valid.json")
    device_a = {"manufacturer": "Nikon", "model": "D850", "serial": "A"}
    device_b = {"manufacturer": "Nikon", "model": "D850", "serial": "B"}

    entry_a = resolve_sensor_entry(device_a["manufacturer"], device_a["model"], db)
    entry_b = resolve_sensor_entry(device_b["manufacturer"], device_b["model"], db)

    assert entry_a["id_key"] == entry_b["id_key"]
    assert entry_a["pixel_pitch_um"] == entry_b["pixel_pitch_um"]
    assert identity_key(device_a) == ("serial", "A")
    assert identity_key(device_b) == ("serial", "B")
    assert identity_key(device_a) != identity_key(device_b)
