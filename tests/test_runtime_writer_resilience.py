from pathlib import Path

from backend.event_log import EventLog
from scripts.eclipse_calculator_py import default_output_path


ROOT = Path(__file__).resolve().parents[1]
APP_SOURCE = (
    ROOT / "flask_app" / "app.py"
).read_text(encoding="utf-8")


def _function_region(source: str, function_name: str, next_marker: str) -> str:
    start = source.index(f"def {function_name}(")
    end = source.index(next_marker, start)
    return source[start:end]


def test_event_log_append_recreates_missing_parent(tmp_path):
    path = tmp_path / "var" / "logs" / "logs_buffer.jsonl"
    log = EventLog(path)

    assert not path.parent.exists()

    log.append("hello")

    assert path.is_file()
    assert "hello" in path.read_text(encoding="utf-8")


def test_calculator_default_output_is_under_application_var():
    path = default_output_path(
        "2027-08-02",
        23.9,
        35.5,
    )

    expected_root = ROOT / "var"

    assert path.is_relative_to(expected_root)
    assert path == ROOT / "var" / "generated" / "todayeclipse.json"


def test_eclipse_override_recreates_todayeclipse_parent():
    region = _function_region(
        APP_SOURCE,
        "api_eclipse_override",
        '@app.route("/api/',
    )

    assert (
        "JSON_FILE.parent.mkdir(parents=True, exist_ok=True)"
        in region
    )


def test_trigger_select_recreates_todayeclipse_parent():
    start = APP_SOURCE.index("def api_trigger_select(")
    region = APP_SOURCE[start:]

    assert (
        "JSON_FILE.parent.mkdir(parents=True, exist_ok=True)"
        in region
    )
    assert "_shutil.copy2(src, JSON_FILE)" in region
