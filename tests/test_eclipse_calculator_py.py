import json
import re

import pytest

from scripts import eclipse_calculator_py


MINIMAL_ECLIPSES = (
    "2026-08-12",
    "2027-08-02",
    "2028-07-22",
    "2030-11-25",
    "2034-03-20",
    "2035-09-02",
)
TIME_PATTERN = re.compile(r"^\d{2}:\d{2}:\d{2}\.\d{3}$")


@pytest.mark.parametrize("date_iso", MINIMAL_ECLIPSES)
def test_cli_generates_trigger_json_for_minimal_eclipses(tmp_path, date_iso):
    output = tmp_path / f"{date_iso}.json"

    assert eclipse_calculator_py.main(
        [
            "--lat", "25.2854", "--lon", "32.5907", "--alt", "76",
            "--tz", "2", "--date", date_iso, "--output", str(output),
        ]
    ) == 0

    data = json.loads(output.read_text(encoding="utf-8"))
    assert data["_date"] == date_iso
    assert data["_timezone"] == "UTC+2"
    assert data["_circumstances_location"] == {
        "latitude": 25.2854,
        "longitude": 32.5907,
        "altitude_m": 76.0,
        "comment": "Circonstances calculées pour cette position GPS et cette altitude.",
    }
    for field in ("C1", "C2", "TMAX", "C3", "C4", "TSTART", "TEND"):
        assert TIME_PATTERN.fullmatch(data[field])
    for event in ("C1", "C2", "TMAX", "C3", "C4"):
        assert TIME_PATTERN.fullmatch(data[f"{event}_local"])
        assert isinstance(data[f"{event}_alt_deg"], float)


def test_default_output_is_under_dataset_out_directory():
    path = eclipse_calculator_py.default_output_path("2027-08-02", 25.5, -3.25)

    assert path == (
        eclipse_calculator_py.REPOSITORY_ROOT
        / "data/eclipses/out/2027-08-02_25.5_-3.25.json"
    )
