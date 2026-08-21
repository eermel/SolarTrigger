import json
from pathlib import Path

import pytest

from backend.eclipse_engine.compute import compute_local_circumstances
from backend.eclipse_engine.loader import load_eclipse


FIXTURES = json.loads(
    (Path(__file__).parent / "fixtures" / "eclipse_circumstances_js.json").read_text(
        encoding="utf-8"
    )
)
EVENTS = ("C1", "C2", "TMAX", "C3", "C4")


@pytest.mark.parametrize("snapshot", FIXTURES, ids=lambda item: item["source_type"])
def test_local_circumstances_match_javascript_snapshots(snapshot):
    result = compute_local_circumstances(
        load_eclipse(snapshot["date"]), *snapshot["observer"]
    )
    expected = snapshot["expected"]

    assert result["eclipse_type"] == expected["eclipse_type"]
    assert result["magnitude"] == expected["magnitude"]
    assert result["moon_sun_ratio"] == expected["moon_sun_ratio"]
    for event, expected_altitude in zip(EVENTS, expected["altitudes"]):
        assert result[f"{event}_utc"] == expected[f"{event}_utc"]
        if expected_altitude is None:
            assert result[f"{event}_alt_deg"] is None
        else:
            assert result[f"{event}_alt_deg"] == pytest.approx(expected_altitude, abs=1e-12)


def test_rejects_anything_other_than_the_exact_dataset_element_slice():
    elements = dict(load_eclipse("2027-08-02")["elements"])
    elements.pop("x3")

    with pytest.raises(ValueError, match="exact 28-value"):
        compute_local_circumstances(elements, 0.0, 0.0)
