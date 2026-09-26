from tests.frontend_source import frontend_source
import re
from pathlib import Path


INDEX = frontend_source()


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


def test_system_camera_ui_is_unified_characterize_validate_workflow():
    assert "Characterize &amp; Validate" in INDEX
    assert 'class="card-title">Re-characterize Camera' not in INDEX
    assert 'class="card-title">Camera Validation' not in INDEX
    assert "qualification_candidates" in INDEX
    assert "startAutomaticCameraValidation" in INDEX
    assert "NEW ·" in INDEX


def test_system_log_has_semantic_success_and_error_coloring():
    assert "camera-log-success" in INDEX
    assert "camera-log-error" in INDEX
