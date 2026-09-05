from tests.frontend_source import frontend_source
import re
from pathlib import Path


INDEX = frontend_source()


def _function(name):
    match = re.search(
        rf"async function {name}\([^)]*\) \{{(.*?)\n\}}",
        INDEX,
        re.DOTALL,
    )
    assert match is not None
    return match.group(1)


def test_photo_setup_load_does_not_touch_atmospheric_attenuation():
    body = _function("loadCameraConfig")

    assert "load_photo" in body
    assert "exposure_correction" not in body
    assert "atmospheric_attenuation_enabled" not in body
    assert "cfg-atmo-switch" not in body
    assert "persistGlobalAtmos" not in body


def test_exposure_opt_load_restores_atmospheric_attenuation():
    body = _function("loadExposureOptConfig")

    assert "load_exposure_opt" in body
    assert "atmospheric_attenuation_enabled" in body
    assert "atmos_enabled: atmos" in body
    assert "/api/rigs/photo" in body
    assert "await loadRigPhotoConfig()" in body
