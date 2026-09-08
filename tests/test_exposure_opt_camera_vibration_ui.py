from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / "flask_app/templates/index.html").read_text(encoding="utf-8")
JS = (ROOT / "flask_app/static/js/solartrigger.js").read_text(encoding="utf-8")
CSS = (ROOT / "flask_app/static/css/solartrigger.css").read_text(encoding="utf-8")
APP = (ROOT / "flask_app/app.py").read_text(encoding="utf-8")


def test_add_camera_has_one_shared_log_and_clear_button():
    assert 'id="camera-add-log"' in INDEX
    assert 'onclick="clearCameraAddLog()"' in INDEX
    assert 'id="camera-characterization-log"' not in INDEX
    assert 'id="camera-validation-log"' not in INDEX
    assert "=== CAMERA CHARACTERIZATION ===" in JS
    assert "=== CAMERA VALIDATION ===" in JS


def test_sequencer_log_uses_shared_log_visual_contract():
    assert 'id="log-container-sequencer"' in INDEX
    assert '#log-container-sequencer' in CSS
    assert "clearLog('sequencer')" in INDEX


def test_exposure_opt_rigs_use_two_column_layout_and_required_help_text():
    assert 'class="camcfg-rigs-grid"' in INDEX
    assert 'grid-template-columns: repeat(2, minmax(0, 1fr))' in CSS
    assert "Earth's rotation" in INDEX
    assert 'Not applicable when an astronomical tracking mount is used.' in INDEX
    assert 'Camera mechanical vibration' in INDEX
    assert 'DSLR mirror or mechanical shutter' in INDEX
    assert 'Sequential capture only; unavailable with BRACKET capture.' in INDEX


def test_mechanical_vibration_controls_are_per_rig_with_zero_to_five_delay():
    for rig_id in range(1, 5):
        assert f'id="rig-{rig_id}-mechanical-vibration-switch"' in INDEX
        assert f'id="rig-{rig_id}-mechanical-vibration-delay"' in INDEX
    for delay in range(6):
        assert f'<option value="{delay}"' in INDEX
    assert "capabilities.strategy === 'bracket'" in JS


def test_exposure_opt_json_and_backend_include_vibration_fields():
    assert 'mechanical_vibration_enabled: current.photo.mechanical_vibration_enabled' in JS
    assert 'mechanical_vibration_delay_s: current.photo.mechanical_vibration_delay_s' in JS
    assert '"mechanical_vibration_enabled",' in APP
    assert '"mechanical_vibration_delay_s"' in APP
    assert 'exposure_ui_capabilities(backend)' in APP


def test_iso_max_is_populated_from_real_camera_profile_full_ev_values():
    assert 'camera_capabilities' in APP
    assert 'capabilities.iso_values' in JS
    assert 'populateRigIsoMaxSelect' in JS
