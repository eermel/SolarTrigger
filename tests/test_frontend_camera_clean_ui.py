import re
from pathlib import Path


INDEX = (
    Path(__file__).resolve().parents[1]
    / "flask_app"
    / "templates"
    / "index.html"
).read_text(encoding="utf-8")


def _clean_photo_function():
    match = re.search(
        r"async function cleanCameraConfigs\(\) \{(.*?)\n\}\n\n// Charger",
        INDEX,
        re.DOTALL,
    )
    assert match is not None
    return match.group(1)


def test_photo_setup_clean_button_is_unique():
    assert INDEX.count('onclick="cleanCameraConfigs()"') == 1
    assert "🧹 CLEAN" in INDEX


def test_photo_setup_clean_uses_only_photo_cfg_namespace():
    logic = _clean_photo_function()

    assert "/api/configs/photo_cfg/clean" in logic
    assert "loadCameraConfigList()" in logic

    assert "/api/configs/camera_cfg/clean" not in logic
    assert "/api/configs/list_camera" not in logic
    assert "trigger-camera-select" not in logic


def test_photo_setup_clean_requires_confirmation():
    logic = _clean_photo_function()

    assert "confirm(" in logic
    assert "Photo Setup" in logic
