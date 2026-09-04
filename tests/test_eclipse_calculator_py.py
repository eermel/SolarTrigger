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
    local_type = data["_type"].lower()

    if local_type == "aucune":
        # L'éclipse n'est pas observable depuis cette position.
        assert data["C2"] is None
        assert data["C3"] is None
        assert data["C2_local"] is None
        assert data["C3_local"] is None

        for event in ("C1", "C2", "TMAX", "C3", "C4"):
            assert data[f"{event}_alt_deg"] is None

    elif local_type == "partielle":
        # Une éclipse partielle possède C1, TMAX et C4,
        # mais aucun contact interne C2/C3.
        for field in ("C1", "TMAX", "C4", "TSTART", "TEND"):
            assert TIME_PATTERN.fullmatch(data[field])

        for event in ("C1", "TMAX", "C4"):
            assert TIME_PATTERN.fullmatch(data[f"{event}_local"])
            assert isinstance(data[f"{event}_alt_deg"], float)

        assert data["C2"] is None
        assert data["C3"] is None
        assert data["C2_local"] is None
        assert data["C3_local"] is None
        assert data["C2_alt_deg"] is None
        assert data["C3_alt_deg"] is None

    else:
        # Eclipse centrale locale : tous les contacts existent.
        for field in ("C1", "C2", "TMAX", "C3", "C4", "TSTART", "TEND"):
            assert TIME_PATTERN.fullmatch(data[field])

        for event in ("C1", "C2", "TMAX", "C3", "C4"):
            assert TIME_PATTERN.fullmatch(data[f"{event}_local"])
            assert isinstance(data[f"{event}_alt_deg"], float)


def test_default_output_is_under_application_var():
    path = eclipse_calculator_py.default_output_path(
        "2027-08-02",
        25.5,
        -3.25,
    )

    assert path == (
        eclipse_calculator_py.REPOSITORY_ROOT
        / "var"
        / "generated"
        / "todayeclipse.json"
    )


def test_partial_trigger_config_keeps_missing_internal_contacts_null():
    dataset = {
        "label": "2026 Aug 12 (T)",
    }
    circumstances = {
        "eclipse_type": "Partielle",
        "magnitude": 0.93045,
        "moon_sun_ratio": 1.03295,
        "obscuration_percent": 92.02356,
        "duration_str": "0m 0s",
        "sun_alt_tmax": "7.6°",

        "C1_utc": "17:22:13.056",
        "C2_utc": None,
        "TMAX_utc": "18:17:18.669",
        "C3_utc": None,
        "C4_utc": "19:09:25.098",

        "C1_local": "19:22:13.056",
        "C2_local": None,
        "TMAX_local": "20:17:18.669",
        "C3_local": None,
        "C4_local": "21:09:25.098",

        "C1_alt_deg": 16.5295,
        "C2_alt_deg": None,
        "TMAX_alt_deg": 7.5787,
        "C3_alt_deg": None,

        # 0° est une altitude solaire réelle valide et ne doit pas
        # être confondue avec une donnée absente.
        "C4_alt_deg": 0.0,
    }

    data = eclipse_calculator_py.build_trigger_config(
        dataset,
        circumstances,
        "2026-08-12",
        48.87388,
        2.38058,
        68.0,
        2.0,
    )

    assert data["_type"] == "Partielle"

    assert data["C1"] == "17:22:13.056"
    assert data["C2"] is None
    assert data["TMAX"] == "18:17:18.669"
    assert data["C3"] is None
    assert data["C4"] == "19:09:25.098"

    assert data["C2_local"] is None
    assert data["C3_local"] is None

    assert data["C2_alt_deg"] is None
    assert data["C3_alt_deg"] is None

    assert data["C4_alt_deg"] == 0.0
    assert isinstance(data["C4_alt_deg"], float)
