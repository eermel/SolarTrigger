import inspect

from backend.camera_characterization import characterize


def test_characterization_cold_starts_every_capture_candidate():
    source = inspect.getsource(characterize)
    assert "def fresh_capture_session(reason)" in source
    assert "direct_nodes.clear()" in source
    assert "COLD TRIGGER PASS" in source
    assert "COLD BRACKET PASS" in source
    assert "expected_trials=6" in source


def test_characterization_builds_direct_set_dependency_matrix():
    source = inspect.getsource(characterize)
    assert "dependency_baseline" in source
    assert "dependency_alternate" in source
    assert 'commands[source_key]["invalidates"]' in source
    assert "conservative invalidation enabled" in source


def test_characterization_prunes_failed_bracket_methods_for_larger_sizes():
    source = inspect.getsource(characterize)
    assert "rejected_bracket_candidates" in source
    assert "rejected_bracket_candidates[command_id]" in source
    assert "will not be retested" in source


def test_characterization_promotes_optional_shutter_mode_to_direct_set():
    source = inspect.getsource(characterize)
    assert 'shutter_mode_spec["writer"] = "single_config"' in source
    assert "direct shutter-mode target readback mismatch" in source


def test_capture_count_mismatch_is_rejected_without_operator_confirmation():
    source = inspect.getsource(characterize)
    assert "operator_photos" not in source
    assert "Operator physical-card check" not in source
    assert "_ensure_camera_storage" not in source
    assert "CameraStorageCapacityError" not in source
    assert "AUTO REJECT: USB confirmed" in source


def test_readonly_fresh_session_preflight_is_operator_assisted():
    source = inspect.getsource(characterize)
    assert "Fresh-session camera preflight" in source
    assert "Correct this setting physically on the camera" in source
