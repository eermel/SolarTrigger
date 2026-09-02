import re
from pathlib import Path


INDEX = (
    Path(__file__).resolve().parents[1]
    / "flask_app"
    / "templates"
    / "index.html"
).read_text(encoding="utf-8")


def test_atmospheric_attenuation_card_is_unique():
    title = '<div class="card-title">Atmospheric Attenuation</div>'
    assert INDEX.count(title) == 1


def test_atmospheric_attenuation_is_not_in_photo_setup():
    photo_start = INDEX.index('<div class="page" id="page-2">')
    photo_end = INDEX.index('<!-- /page-2 CFG PHOTO -->', photo_start)
    photo_page = INDEX[photo_start:photo_end]

    assert "Atmospheric Attenuation" not in photo_page
    assert 'id="cfg-atmo-switch"' not in photo_page


def test_atmospheric_attenuation_is_in_exposure_opt():
    start = INDEX.index('<div class="page" id="page-exposure-opt">')
    end = INDEX.index('<!-- /Exposure Optimization -->', start)
    exposure_page = INDEX[start:end]

    assert "Atmospheric Attenuation" in exposure_page
    assert 'id="cfg-atmo-switch"' in exposure_page
    assert 'role="switch"' in exposure_page


def test_photo_setup_json_does_not_include_atmospheric_correction():
    match = re.search(
        r"function _readCameraConfig\(\) \{(?P<body>.*?)\n\}",
        INDEX,
        re.DOTALL,
    )
    assert match is not None

    body = match.group("body")
    assert "exposure_correction" not in body
    assert "atmospheric_attenuation_enabled" not in body
