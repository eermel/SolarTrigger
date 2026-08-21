import json

from scripts.eclipse_dataset_builder import (
    DEFAULT_ELEMENTS_PATH,
    DEFAULT_INDEX_PATH,
    add_elements_with_anomalies,
    build_all,
    build_one,
    discover_eclipses,
    discover_eclipses_with_anomalies,
    extract_eclipse_elements,
    parse_elements_array,
)

import pytest


EXPECTED_DATE_VALUES = [
    ("2025-03-29", 56), ("2025-09-21", 57),
    ("2026-02-17", 58), ("2026-08-12", 59),
    ("2027-02-06", 60), ("2027-08-02", 61),
    ("2028-01-26", 62), ("2028-07-22", 63),
    ("2029-01-14", 64), ("2029-06-12", 65),
    ("2029-07-11", 66), ("2029-12-05", 67),
    ("2030-06-01", 68), ("2030-11-25", 69),
    ("2031-05-21", 70), ("2031-11-14", 71),
    ("2032-05-09", 72), ("2032-11-03", 73),
    ("2033-03-30", 74), ("2033-09-23", 75),
    ("2034-03-20", 76), ("2034-09-12", 77),
    ("2035-03-09", 78), ("2035-09-02", 79),
    ("2036-02-27", 80), ("2036-07-23", 81),
    ("2036-08-21", 82), ("2037-01-16", 83),
    ("2037-07-13", 84), ("2038-01-05", 85),
    ("2038-07-02", 86), ("2038-12-26", 87),
    ("2039-06-21", 88), ("2039-12-15", 89),
]


def test_discovers_every_local_eclipse_in_order():
    eclipses = discover_eclipses(DEFAULT_INDEX_PATH)

    assert len(eclipses) == 155
    assert [(item["date"], item["val"]) for item in eclipses[-34:]] == EXPECTED_DATE_VALUES
    assert [item["option_index"] for item in eclipses] == list(range(155))
    assert all(item["type"] in {"T", "A", "P", "H"} for item in eclipses)
    assert eclipses[124]["label"] == "2026 Aug 12 (T)"


def test_invalid_date_is_an_anomaly_and_not_an_eclipse(tmp_path):
    index_path = tmp_path / "index.html"
    index_path.write_text(
        '<select id="other"><option value="1">2025 Jan 01 (T)</option></select>'
        '<select id="eclipse_index">'
        '<option value="56">2025 Feb 30 (T)</option>'
        '<option value="59">2026 Aug 12 (T)</option>'
        "</select>",
        encoding="utf-8",
    )

    eclipses, anomalies = discover_eclipses_with_anomalies(index_path)

    assert [(item["date"], item["val"]) for item in eclipses] == [("2026-08-12", 59)]
    assert anomalies == [{
        "value": "56",
        "label": "2025 Feb 30 (T)",
        "option_index": 0,
        "error": "option date is invalid",
    }]


@pytest.mark.parametrize("val", [59, 61, 63, 69, 76, 79])
def test_extracts_28_elements_at_the_expected_offset(val):
    elements_offset, elements = extract_eclipse_elements(val, DEFAULT_ELEMENTS_PATH)

    assert elements_offset == 28 * (val + 65)
    assert len(elements) == 28
    assert all(isinstance(item, float) for item in elements)


def test_non_numeric_elements_value_is_rejected():
    with pytest.raises(ValueError, match="index 1 is not numeric"):
        parse_elements_array("var elements = new Array(1, nope, 3);")


def test_out_of_range_slice_is_an_anomaly_and_excludes_eclipse(tmp_path):
    elements_path = tmp_path / "elements.js"
    elements_path.write_text(
        "var elements = new Array(" + ",".join(["1"] * 28) + ");",
        encoding="utf-8",
    )

    eclipses, anomalies = add_elements_with_anomalies(
        [{"val": 59, "date": "2026-08-12"}], elements_path
    )

    assert eclipses == []
    assert len(anomalies) == 1
    assert anomalies[0]["eclipse"]["val"] == 59
    assert "exceeds array length" in anomalies[0]["error"]


MINIMAL_ECLIPSE_DATES = [
    "2026-08-12",
    "2027-08-02",
    "2028-07-22",
    "2030-11-25",
    "2034-03-20",
    "2035-09-02",
]


def test_build_all_writes_structured_datasets_and_registry(tmp_path):
    generated, anomalies = build_all(tmp_path)

    assert len(generated) == 155
    assert anomalies == []
    registry = json.loads((tmp_path / "registry.json").read_text(encoding="utf-8"))
    assert len(registry["eclipses"]) == 155

    registry_dates = {item["date"] for item in registry["eclipses"]}
    for date_iso in MINIMAL_ECLIPSE_DATES:
        dataset_path = tmp_path / f"{date_iso}.json"
        assert dataset_path.is_file()
        assert date_iso in registry_dates
        dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
        assert dataset["header"]["date_iso"] == date_iso
        assert dataset["header"]["generated_utc"]
        assert set(dataset["jubier"]) == {"val", "elements_offset"}
        assert (
            dataset["source"]["file"]
            == "jubier_files/SolarEclipseCalc_Diagram.html"
        )
        assert dataset["source"]["type"] == "index_option"
        assert dataset["source"]["option_text"]
        assert isinstance(dataset["source"]["option_index"], int)
        assert len(dataset["elements"]) == 28


def test_build_one_only_writes_requested_dataset(tmp_path):
    generated, anomalies = build_one("2026-08-12", tmp_path)

    assert [item["date"] for item in generated] == ["2026-08-12"]
    assert anomalies == []
    assert sorted(path.name for path in tmp_path.iterdir()) == ["2026-08-12.json"]


def test_build_one_uses_canonical_2027_element_times(tmp_path):
    generated, anomalies = build_one("2027-08-02", tmp_path)

    assert [item["date"] for item in generated] == ["2027-08-02"]
    assert anomalies == []
    dataset = json.loads(
        (tmp_path / "2027-08-02.json").read_text(encoding="utf-8")
    )
    assert dataset["elements"]["dUTC"] == 69.25
    assert dataset["elements"]["dT"] == 69.25
