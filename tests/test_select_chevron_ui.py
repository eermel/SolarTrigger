from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / "flask_app/templates/index.html").read_text(
    encoding="utf-8"
)
CSS = (ROOT / "flask_app/static/css/solartrigger.css").read_text(
    encoding="utf-8"
)


FILE_SELECT_IDS = (
    "camera-characterization-select",
    "camera-validation-select",
    "eclipse-circumstances-select",
    "camera-config-select",
    "exposure-opt-config-select",
    "sequencer-circumstances-select",
    "sequencer-photo-select",
    "sequencer-exposure-opt-select",
    "trigger-circumstances-select",
    "trigger-photo-select",
    "trigger-exposure-opt-select",
)


def select_tag(select_id):
    start = INDEX.index(f'id="{select_id}"')
    return INDEX[start:INDEX.index(">", start)]


def test_all_file_configuration_selects_share_one_chevron_contract():
    for select_id in FILE_SELECT_IDS:
        tag = select_tag(select_id)
        assert "file-select-chevron" in tag, select_id


def test_eclipse_date_uses_same_chevron_contract():
    tag = select_tag("inp-eclipse")

    assert "file-select-chevron" in tag
    assert 'onchange="handleEclipseSelectionChange()"' in tag


def test_file_select_chevron_is_explicit_and_browser_independent():
    assert ".file-select-chevron {" in CSS
    assert "appearance: none !important;" in CSS
    assert "-webkit-appearance: none !important;" in CSS
    assert "var(--bg3) !important;" in CSS
    assert "linear-gradient(45deg," in CSS
    assert "linear-gradient(135deg," in CSS


def test_obsolete_native_chevron_contract_is_removed():
    assert "native-select-chevron" not in INDEX
    assert ".native-select-chevron" not in CSS
