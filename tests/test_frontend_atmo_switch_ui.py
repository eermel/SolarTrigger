import re
from pathlib import Path


INDEX = (
    Path(__file__).resolve().parents[1] / "flask_app" / "templates" / "index.html"
).read_text(encoding="utf-8")


def _camera_config_function(name):
    match = re.search(
        rf"(?:async )?function {name}\([^)]*\) \{{(.*?)\n\}}",
        INDEX,
        re.DOTALL,
    )
    assert match is not None
    return match.group(1)


def test_atmospheric_attenuation_section_is_between_camera_and_partial_phase():
    section_order = re.search(
        r'<div class="card-title">Camera configuration</div>'
        r'.*?<div class="card-title">Atmospheric Attenuation</div>'
        r'.*?<div class="card-title"[^>]*>[^<]*PARTIAL PHASE</div>',
        INDEX,
        re.DOTALL,
    )

    assert section_order is not None


def test_atmospheric_attenuation_control_is_a_labeled_switch():
    section = re.search(
        r'<div class="card-title">Atmospheric Attenuation</div>(.*?)</div>\s*</div>',
        INDEX,
        re.DOTALL,
    )

    assert section is not None
    markup = section.group(1)
    assert 'id="cfg-atmo-switch"' in markup
    assert 'role="switch"' in markup
    assert re.search(r'<label for="cfg-atmo-switch">OFF</label>', markup)
    assert re.search(r'<label for="cfg-atmo-switch">ON</label>', markup)


def test_save_camera_config_includes_atmospheric_attenuation_in_payload():
    save_logic = _camera_config_function("saveCameraConfig")

    assert re.search(
        r"data\.exposure_correction\s*=\s*\{\s*"
        r"atmospheric_attenuation_enabled:\s*Boolean\("
        r"document\.getElementById\('cfg-atmo-switch'\)\.checked"
        r"\)\s*\}",
        save_logic,
        re.DOTALL,
    )
    assert "body: JSON.stringify({ filename: name, data })" in save_logic


def test_load_camera_config_uses_atmospheric_attenuation_and_defaults_false():
    load_logic = _camera_config_function("loadCameraConfig")

    assert re.search(
        r"const\s+atmosEnabled\s*=\s*Boolean\(\s*"
        r"data\.exposure_correction\?\.atmospheric_attenuation_enabled"
        r"\s*\)",
        load_logic,
        re.DOTALL,
    )

    assert re.search(
        r"document\.getElementById\('cfg-atmo-switch'\)"
        r"\.checked\s*=\s*atmosEnabled",
        load_logic,
    )

    assert "await persistGlobalAtmos(atmosEnabled, false)" in load_logic
