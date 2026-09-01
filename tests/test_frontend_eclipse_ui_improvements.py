from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

HTML = (
    ROOT / "flask_app" / "templates" / "index.html"
).read_text(encoding="utf-8")

APP = (
    ROOT / "flask_app" / "app.py"
).read_text(encoding="utf-8")


def test_supported_eclipse_dates_have_backend_api():
    assert '@app.route("/api/eclipse/supported")' in APP
    assert 'eclipse_loader.list_supported_eclipses()' in APP


def test_eclipse_select_is_dynamic_and_has_chevron():
    assert '<div class="select-chev">' in HTML
    assert 'id="inp-eclipse" onchange="handleEclipseSelectionChange()"' in HTML
    assert "fetch('/api/eclipse/supported')" in HTML
    assert "dates.forEach(date =>" in HTML


def test_static_six_date_list_is_removed():
    assert '2027-08-02 — Egypt / Luxor' not in HTML
    assert '2026-08-12 — Spain' not in HTML


def test_filename_prefix_changes_immediately_with_selected_date():
    assert "function handleEclipseSelectionChange()" in HTML
    assert "updateEclipseSaveFilename({_date: eclipseDate})" in HTML


def test_clean_requires_confirmation():
    start = HTML.index("async function cleanCircumstances()")
    end = HTML.index("async function cleanCameraConfigs()", start)
    body = HTML[start:end]

    assert "confirm(" in body
    assert "Delete all saved circumstances files" in body


def test_occultation_is_rendered_in_circumstances():
    assert 'id="eclipse-obscuration"' in HTML
    assert "data._obscuration_percent" in HTML
    assert "obscuration.toFixed(2)" in HTML


def test_calculator_journal_is_plain_journal_with_real_button():
    assert "<span>Log</span>" in HTML
    assert ">Journal Calculateur<" not in HTML
    assert "onclick=\"clearLog('calculator')\">CLEAR</button>" in HTML


def test_supported_eclipse_list_loaded_at_startup():
    init = HTML[HTML.rindex("// Init") :]
    assert "loadSupportedEclipses();" in init
