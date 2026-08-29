import json
from datetime import datetime

from backend.preview_context import load_eclipse_context


def test_load_eclipse_context_extracts_timeline_and_atmospheric_inputs(tmp_path):
    path = tmp_path / "todayeclipse.json"
    path.write_text(
        json.dumps(
            {
                "_date": "2026-08-12",
                "C1": "17:34:12.500",
                "TMAX": "18:46:00",
                "C4": "19:57:10",
                "C1_alt_deg": 31.2,
                "TMAX_alt_deg": 40.5,
                "C4_alt_deg": 28.1,
                "_circumstances_location": {"altitude_m": 145},
            }
        ),
        encoding="utf-8",
    )

    assert load_eclipse_context(path) == {
        "timeline": {
            "C1": datetime(2026, 8, 12, 17, 34, 12, 500000),
            "TMAX": datetime(2026, 8, 12, 18, 46),
            "C4": datetime(2026, 8, 12, 19, 57, 10),
        },
        "altitudes": {
            "C1_alt_deg": 31.2,
            "TMAX_alt_deg": 40.5,
            "C4_alt_deg": 28.1,
        },
        "observer_alt_m": 145.0,
    }


def test_load_eclipse_context_does_not_synthesize_optional_values(tmp_path):
    path = tmp_path / "todayeclipse.json"
    path.write_text(json.dumps({"_date": "2026-08-12", "C1": "17:34:12"}), encoding="utf-8")

    assert load_eclipse_context(path) == {
        "timeline": {"C1": datetime(2026, 8, 12, 17, 34, 12)},
        "altitudes": {},
        "observer_alt_m": None,
    }


def test_load_eclipse_context_handles_missing_or_invalid_file(tmp_path):
    expected = {"timeline": {}, "altitudes": {}, "observer_alt_m": None}
    assert load_eclipse_context(tmp_path / "missing.json") == expected

    invalid = tmp_path / "todayeclipse.json"
    invalid.write_text("not json", encoding="utf-8")
    assert load_eclipse_context(invalid) == expected
