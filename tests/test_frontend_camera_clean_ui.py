import re
from pathlib import Path


INDEX = (
    Path(__file__).resolve().parents[1] / "flask_app" / "templates" / "index.html"
).read_text(encoding="utf-8")


def _clean_camera_function():
    match = re.search(
        r"async function cleanCameraConfigs\(\) \{(.*?)\n\}\n\n// Charger",
        INDEX,
        re.DOTALL,
    )
    assert match is not None
    return match.group(1)


def test_clean_camera_configs_button_is_unique_and_in_camera_configuration():
    camera_card = re.search(
        r'<div class="card-title">Camera configuration</div>(.*?)</div>\n\s*</div>',
        INDEX,
        re.DOTALL,
    )

    assert camera_card is not None
    assert camera_card.group(1).count('onclick="cleanCameraConfigs()"') == 1
    assert "🧹 CLEAN" in camera_card.group(1)
    assert INDEX.count('onclick="cleanCameraConfigs()"') == 1


def test_clean_camera_configs_posts_before_refreshing_camera_lists():
    clean_logic = _clean_camera_function()
    clean_call = "fetch('/api/configs/camera_cfg/clean', { method: 'POST' })"
    refresh_call = "fetch('/api/configs/list_camera')"

    assert clean_call in clean_logic
    assert refresh_call in clean_logic
    assert clean_logic.index(clean_call) < clean_logic.index(refresh_call)


def test_clean_camera_configs_refreshes_both_selects_and_accepts_empty_list():
    clean_logic = _clean_camera_function()

    assert "const files = data.files || [];" in clean_logic
    assert "document.getElementById('camera-config-select')" in clean_logic
    assert "document.getElementById('trigger-camera-select')" in clean_logic
    assert clean_logic.count("files.forEach(file =>") == 2
    assert (
        "cameraSelect.innerHTML = "
        "'<option value=\"\">— Camera config file —</option>';"
    ) in clean_logic
    assert (
        "triggerCameraSelect.innerHTML = "
        "'<option value=\"\">— Camera config —</option>';"
    ) in clean_logic
