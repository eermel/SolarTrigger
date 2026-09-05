from tests.frontend_source import frontend_source
import re
from pathlib import Path


INDEX = frontend_source()


def _function(name):
    match = re.search(
        rf"(?:async )?function {name}\([^)]*\) \{{(.*?)\n\}}",
        INDEX,
        re.DOTALL,
    )
    assert match is not None
    return match.group(1)


def _exposure_page():
    match = re.search(
        r'<div class="page" id="page-exposure-opt">'
        r'(?P<body>.*?)'
        r'</div><!-- /Exposure Optimization -->',
        INDEX,
        re.DOTALL,
    )
    assert match is not None
    return match.group("body")


def test_atmospheric_attenuation_is_inside_exposure_optimization():
    page = _exposure_page()

    assert '<div class="card-title">Atmospheric Attenuation</div>' in page
    assert 'id="cfg-atmo-switch"' in page


def test_atmospheric_attenuation_control_is_a_switch():
    page = _exposure_page()

    section = re.search(
        r'<div class="card-title">Atmospheric Attenuation</div>'
        r'(.*?)</div>\s*</div>',
        page,
        re.DOTALL,
    )
    assert section is not None

    markup = section.group(1)
    assert 'id="cfg-atmo-switch"' in markup
    assert 'role="switch"' in markup


def test_photo_setup_save_contains_no_atmosphere():
    save_logic = _function("saveCameraConfig")

    assert "exposure_correction" not in save_logic
    assert "cfg-atmo-switch" not in save_logic
    assert "save_photo" in save_logic


def test_exposure_opt_save_owns_atmospheric_attenuation():
    read_logic = _function("readExposureOptConfig")

    assert "cfg-atmo-switch" in read_logic
    assert "atmospheric_attenuation_enabled" in read_logic
    assert "config_type: 'exposure_optimization'" in read_logic
