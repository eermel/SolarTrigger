import re
from pathlib import Path


INDEX = (
    Path(__file__).resolve().parents[1]
    / "flask_app"
    / "templates"
    / "index.html"
).read_text(encoding="utf-8")


def test_load_camera_config_initializes_atmospheric_attenuation_switch():
    function = re.search(
        r"async function loadCameraConfig\(filename\) \{(.*?)\n\}",
        INDEX,
        re.DOTALL,
    )

    assert function is not None

    body = function.group(1)

    assert "data.exposure_correction" in body
    assert "atmospheric_attenuation_enabled" in body

    assert re.search(
        r"document\.getElementById\([\"']cfg-atmo-switch[\"']\)"
        r"\.checked\s*=\s*Boolean\(\s*"
        r"data\.exposure_correction\?\.atmospheric_attenuation_enabled"
        r"\s*\)",
        body,
        re.DOTALL,
    )
