import re
from pathlib import Path

INDEX = (
    Path(__file__).resolve().parents[1]
    / "flask_app"
    / "templates"
    / "index.html"
).read_text(encoding="utf-8")


def test_clean_circumstances_button_is_unique():
    assert INDEX.count('onclick="cleanCircumstances()"') == 1


def test_clean_circumstances_calls_clean_before_refreshing_saved_list():
    start = INDEX.index("async function cleanCircumstances()")
    end = INDEX.index("async function cleanCameraConfigs()", start)
    block = INDEX[start:end]

    clean_call = "fetch('/api/configs/circumstances/clean'"
    refresh_call = "refreshSavedCircumstances()"

    assert clean_call in block
    assert refresh_call in block
    assert block.index(clean_call) < block.index(refresh_call)
