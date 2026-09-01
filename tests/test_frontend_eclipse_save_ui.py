import re
from pathlib import Path

INDEX = (
    Path(__file__).resolve().parents[1]
    / "flask_app"
    / "templates"
    / "index.html"
).read_text(encoding="utf-8")


def test_save_circumstances_controls_match_camera_configuration_pattern():
    html = INDEX

    save_title = '<div class="card-title">Circumstances configuration</div>'
    observation_title = '<div class="card-title">Observation location</div>'

    start = html.index(save_title)
    end = html.index(observation_title, start)
    block = html[start:end]

    assert start < end

    assert html.count('id="eclipse-circumstances-select"') == 1
    assert html.count('onclick="saveEclipseConfig()"') == 1
    assert html.count('onclick="cleanCircumstances()"') == 1

    assert '<select id="eclipse-circumstances-select"' in block
    assert 'onchange="loadSavedCircumstances(this.value)"' in block
    assert '<option value="">— Circumstances file —</option>' in block

    assert "display:flex;gap:6px" in block
    assert "background:var(--bg3)" in block
    assert "border:1px solid var(--border)" in block
    assert "border-radius:8px;padding:8px 10px" in block
    assert "font-family:var(--mono);font-size:11px" in block
    assert "padding:7px 10px" in block

    assert 'eclipse-save-filename' not in block
    assert 'eclipse-circumstances-list' not in block
    assert '📂 Charger' not in block


def test_circumstances_selection_loads_automatically():
    html = INDEX

    assert "async function loadSavedCircumstances(filename)" in html
    assert "fetch('/api/trigger/select'" in html
    assert "filename: filename" in html
    assert "dir: 'circumstances'" in html


def test_circumstances_list_is_refreshed_from_backend():
    html = INDEX

    assert "async function refreshSavedCircumstances()" in html
    assert "document.getElementById('eclipse-circumstances-select')" in html
    assert "fetch('/api/configs/list_eclipse')" in html


def test_eclipse_save_prefix_uses_backend_eclipse_date():
    html = INDEX

    start = html.index("function updateEclipseSaveFilename(eclipseData)")
    end = html.index("function handleEclipseSelectionChange()", start)
    block = html[start:end]

    assert "eclipseData._date || eclipseData._date_utc" in block
    assert "_Circumstances_" in block
    assert "new Date(" not in block
    assert "Date()" not in block


def test_eclipse_calculated_updates_save_prefix():
    html = INDEX

    calculated_handler = re.search(
        r"socket\.on\('eclipse_calculated', d => \{(.*?)\n\}\);",
        html,
        re.DOTALL,
    )

    assert calculated_handler is not None
    logic = calculated_handler.group(1)

    assert "d.status === 'success' && d.data" in logic
    assert "updateEclipseSaveFilename(d.data)" in logic


def test_save_uses_prefix_and_rejects_bare_prefix_before_fetch():
    html = INDEX

    start = html.index("async function saveEclipseConfig()")
    end = html.index("async function cleanCircumstances()", start)
    block = html[start:end]

    assert r"/^\d{8}_Circumstances_$/" in block
    assert "const filename = prompt(" in block
    assert "filename.trim() === activePrefix" in block

    assert block.index("filename.trim() === activePrefix") < block.index(
        "fetch('/api/configs/save'"
    )


def test_saved_circumstances_becomes_selected_after_save():
    html = INDEX

    start = html.index("async function saveEclipseConfig()")
    end = html.index("async function cleanCircumstances()", start)
    block = html[start:end]

    assert "await refreshSavedCircumstances()" in block
    assert "circumstancesSelect.value = data.filename" in block
