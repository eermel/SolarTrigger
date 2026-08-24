import re
from pathlib import Path


INDEX = (
    Path(__file__).resolve().parents[1] / "flask_app" / "templates" / "index.html"
).read_text(encoding="utf-8")


def test_save_circumstances_controls_are_unique_and_before_observation_location():
    html = INDEX

    save_title = '<div class="card-title">Save circumstances</div>'
    observation_title = '<div class="card-title">Observation location</div>'
    filename_label = '<label for="eclipse-save-filename">File name</label>'
    filename_input = 'id="eclipse-save-filename"'
    save_button = (
        '<button class="btn btn-primary" type="button" '
        'onclick="saveEclipseConfig()">💾 Save</button>'
    )
    clean_button = (
        '<button class="btn btn-secondary" type="button" '
        'onclick="cleanCircumstances()">🧹 Clean</button>'
    )

    save_index = html.index(save_title)
    observation_index = html.index(observation_title)
    label_index = html.index(filename_label)
    input_index = html.index(filename_input)
    button_index = html.index(save_button)
    clean_button_index = html.index(clean_button)

    assert save_index < label_index < observation_index
    assert save_index < input_index < observation_index
    assert save_index < button_index < clean_button_index < observation_index
    assert html.count(save_title) == 1
    assert html.count(filename_label) == 1
    assert len(re.findall(r'id=["\']eclipse-save-filename["\']', html)) == 1
    assert html.count('onclick="saveEclipseConfig()"') == 1
    assert html.count('onclick="cleanCircumstances()"') == 1


def test_eclipse_save_prefix_uses_backend_date_and_calculation_event():
    html = INDEX

    prefix_function = re.search(
        r"function updateEclipseSaveFilename\(eclipseData\) \{(.*?)\n\}",
        html,
        re.DOTALL,
    )

    assert prefix_function is not None
    prefix_logic = prefix_function.group(1)
    assert "eclipseData._date || eclipseData._date_utc" in prefix_logic
    assert "_Circonstances_" in prefix_logic
    assert "new Date(" not in prefix_logic
    assert "Date()" not in prefix_logic
    assert "input.value.startsWith(_eclipseSavePrefix)" in prefix_logic
    assert "input.value.slice(_eclipseSavePrefix.length)" in prefix_logic

    calculated_handler = re.search(
        r"socket\.on\('eclipse_calculated', d => \{(.*?)\n\}\);",
        html,
        re.DOTALL,
    )
    assert calculated_handler is not None
    assert "d.status === 'success' && d.data" in calculated_handler.group(1)
    assert "updateEclipseSaveFilename(d.data)" in calculated_handler.group(1)


def test_eclipse_save_rejects_empty_default_prefix_suffix_before_fetch():
    html = INDEX

    save_function = re.search(
        r"async function saveEclipseConfig\(\) \{(.*?)\n\}",
        html,
        re.DOTALL,
    )

    assert save_function is not None
    save_logic = save_function.group(1)
    message = "Please complete the file name after the default prefix."
    assert message in save_logic
    assert "File name is required." not in save_logic
    assert r"/^\d{8}_Circonstances_$/" in save_logic
    assert "filename === activePrefix" in save_logic
    assert "filename.startsWith(activePrefix)" in save_logic
    assert "filename.slice(filename.lastIndexOf('_') + 1) === ''" in save_logic
    assert save_logic.index(message) < save_logic.index("fetch('/api/configs/save'")
