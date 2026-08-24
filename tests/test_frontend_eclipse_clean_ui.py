import re
from pathlib import Path


INDEX = (
    Path(__file__).resolve().parents[1] / "flask_app" / "templates" / "index.html"
).read_text(encoding="utf-8")


def test_clean_circumstances_button_is_unique():
    button = (
        '<button class="btn btn-secondary" type="button" '
        'onclick="cleanCircumstances()">🧹 Clean</button>'
    )

    assert INDEX.count(button) == 1
    assert INDEX.count('onclick="cleanCircumstances()"') == 1


def test_clean_circumstances_calls_clean_before_refreshing_list():
    clean_function = re.search(
        r"async function cleanCircumstances\(\) \{(.*?)\n\}",
        INDEX,
        re.DOTALL,
    )

    assert clean_function is not None
    clean_logic = clean_function.group(1)
    clean_call = "fetch('/api/configs/circumstances/clean', { method: 'POST' })"
    refresh_call = "fetch('/api/configs/list_eclipse')"
    assert clean_call in clean_logic
    assert refresh_call in clean_logic
    assert clean_logic.index(clean_call) < clean_logic.index(refresh_call)
