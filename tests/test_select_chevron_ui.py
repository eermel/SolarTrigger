from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / "flask_app/templates/index.html").read_text(encoding="utf-8")
CSS = (ROOT / "flask_app/static/css/solartrigger.css").read_text(encoding="utf-8")


def select_tag(select_id):
    start = INDEX.index(f'id="{select_id}"')
    return INDEX[start:INDEX.index(">", start)]


def test_add_camera_selects_use_native_chevron():
    for select_id in (
        "camera-characterization-select",
        "camera-validation-select",
    ):
        tag = select_tag(select_id)
        assert "config-file-select" in tag
        assert 'native-select-chevron' in tag


def test_eclipse_date_uses_same_native_chevron():
    tag = select_tag("inp-eclipse")
    assert 'native-select-chevron' in tag
    assert 'onchange="handleEclipseSelectionChange()"' in tag


def test_circumstances_selector_remains_native_reference():
    tag = select_tag("eclipse-circumstances-select")
    assert "native-select-chevron" not in tag
    assert "select-chev" not in tag


def test_native_chevron_css_overrides_hidden_select_arrow():
    assert ".native-select-chevron {" in CSS
    assert "appearance: auto;" in CSS
    assert "-webkit-appearance: menulist;" in CSS
