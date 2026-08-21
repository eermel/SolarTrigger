import json
from pathlib import Path

import pytest

from scripts.eclipse_dataset_builder import build_all


DATASETS_DIR = Path(__file__).parents[1] / "data" / "eclipses"
MINIMAL_ECLIPSES = [
    ("2026-08-12", 59),
    ("2027-08-02", 61),
    ("2028-07-22", 63),
    ("2030-11-25", 69),
    ("2034-03-20", 76),
    ("2035-09-02", 79),
]
ELEMENT_KEYS = {
    "julian_day", "t0", "tmin", "tmax", "dUTC", "dT",
    "x0", "x1", "x2", "x3", "y0", "y1", "y2", "y3",
    "d0", "d1", "d2", "m0", "m1", "m2",
    "l10", "l11", "l12", "l20", "l21", "l22", "tan_f1", "tan_f2",
}


def _read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize(("date_iso", "expected_val"), MINIMAL_ECLIPSES)
def test_minimal_eclipse_dataset_structure_and_value(date_iso, expected_val):
    dataset = _read_json(DATASETS_DIR / f"{date_iso}.json")

    assert set(dataset) == {"header", "jubier", "source", "elements"}
    assert set(dataset["header"]) == {"generated_utc", "date_iso"}
    assert dataset["header"]["date_iso"] == date_iso
    assert isinstance(dataset["header"]["generated_utc"], str)
    assert set(dataset["jubier"]) == {"val", "elements_offset"}
    assert dataset["jubier"] == {
        "val": expected_val,
        "elements_offset": 28 * (expected_val + 65),
    }
    assert set(dataset["source"]) == {
        "file", "type", "option_text", "option_index",
    }
    assert dataset["source"]["file"] == "jubier_files/index.html"
    assert dataset["source"]["type"] == "index_option"
    assert dataset["source"]["option_text"]
    assert isinstance(dataset["source"]["option_index"], int)
    assert set(dataset["elements"]) == ELEMENT_KEYS
    assert len(dataset["elements"]) == 28
    assert all(
        isinstance(value, (int, float)) and not isinstance(value, bool)
        for value in dataset["elements"].values()
    )


def _without_generated_utc(document):
    normalized = json.loads(json.dumps(document))
    if "generated_utc" in normalized:
        del normalized["generated_utc"]
    if "header" in normalized:
        del normalized["header"]["generated_utc"]
    return normalized


def _directory_snapshot(directory):
    return {
        path.name: _without_generated_utc(_read_json(path))
        for path in sorted(directory.glob("*.json"))
    }


def test_successive_builds_preserve_structure_and_numeric_values(tmp_path):
    first_generated, first_anomalies = build_all(tmp_path)
    first_snapshot = _directory_snapshot(tmp_path)

    second_generated, second_anomalies = build_all(tmp_path)
    second_snapshot = _directory_snapshot(tmp_path)

    assert first_anomalies == second_anomalies == []
    assert first_generated == second_generated
    assert first_snapshot == second_snapshot
