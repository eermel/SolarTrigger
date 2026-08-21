import json

from scripts.eclipse_dataset_builder import build_all
from scripts.eclipse_dataset_diff import main


def test_diff_detects_element_and_source_changes(tmp_path, capsys):
    build_all(tmp_path)
    dataset_path = tmp_path / "2027-08-02.json"
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    dataset["header"]["generated_utc"] = "deliberately different"
    dataset["source"]["file"] = "jubier_files/index.html"
    dataset["elements"]["dUTC"] = 69.3
    dataset["elements"]["dT"] = 69.3
    dataset_path.write_text(json.dumps(dataset), encoding="utf-8")

    assert main(["--data-dir", str(tmp_path)]) == 1
    reports = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert reports == [
        {
            "date": "2027-08-02",
            "changed_fields": ["elements.dT", "elements.dUTC", "source.file"],
            "numeric_deltas": {
                "elements.dT": {"old": 69.3, "new": 69.25},
                "elements.dUTC": {"old": 69.3, "new": 69.25},
            },
        }
    ]


def test_diff_ignores_generation_timestamp_after_rebuild(tmp_path, capsys):
    build_all(tmp_path)

    assert main(["--data-dir", str(tmp_path)]) == 0
    assert capsys.readouterr().out == ""
