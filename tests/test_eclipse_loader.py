import json
from pathlib import Path

import pytest

from backend.eclipse_engine import loader


DATASETS_DIR = Path(__file__).parents[1] / "data" / "eclipses"
MINIMAL_ECLIPSES = [
    "2026-08-12",
    "2027-08-02",
    "2028-07-22",
    "2030-11-25",
    "2034-03-20",
    "2035-09-02",
]


def test_list_supported_eclipses_matches_registry():
    registry = json.loads(
        (DATASETS_DIR / "registry.json").read_text(encoding="utf-8")
    )

    assert loader.list_supported_eclipses() == [
        entry["date"] for entry in registry["eclipses"]
    ]


@pytest.mark.parametrize("date_iso", MINIMAL_ECLIPSES)
def test_load_eclipse_returns_complete_dataset(date_iso):
    expected = json.loads(
        (DATASETS_DIR / f"{date_iso}.json").read_text(encoding="utf-8")
    )

    assert loader.load_eclipse(date_iso) == expected


def test_load_eclipse_rejects_unsupported_date():
    with pytest.raises(loader.EclipseNotFoundError, match="unsupported eclipse date"):
        loader.load_eclipse("2000-01-01")


def test_missing_dataset_is_reported_cleanly(tmp_path, monkeypatch):
    (tmp_path / "registry.json").write_text(
        json.dumps({"eclipses": [{"date": "2026-08-12", "file": "missing.json"}]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(loader, "DATASETS_DIR", tmp_path)

    with pytest.raises(loader.EclipseNotFoundError, match="data file not found"):
        loader.load_eclipse("2026-08-12")


def test_invalid_json_is_reported_cleanly(tmp_path, monkeypatch):
    (tmp_path / "registry.json").write_text("{not valid json", encoding="utf-8")
    monkeypatch.setattr(loader, "DATASETS_DIR", tmp_path)

    with pytest.raises(loader.EclipseDataFormatError, match="invalid JSON"):
        loader.list_supported_eclipses()
