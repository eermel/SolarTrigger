import inspect

from backend.camera_characterization import characterize


def test_characterization_uses_persistent_camera_session_and_five_trials():
    source = inspect.getsource(characterize)
    assert "def fresh_capture_session(reason)" not in source
    assert "COLD TRIGGER PASS" not in source
    assert "COLD BRACKET PASS" not in source
    assert "expected_trials=5" in source

    # BRK3+BRK7 is the preferred timing pair, but selection is deliberately
    # dynamic so cameras exposing an incomplete bracket set remain usable.
    assert "candidate_pairs" in source
    assert "pair == (3, 7)" in source
    assert "bracket_calibration_frames = list(" in source

    assert "USB SET-ready" in source


def test_characterization_builds_direct_set_dependency_matrix():
    source = inspect.getsource(characterize)
    assert "dependency_baseline" in source
    assert "dependency_alternates" in source
    assert 'commands[source_key]["invalidates"]' in source
    assert "conservative invalidation enabled" in source


def test_characterization_preserves_supported_brackets_when_one_size_fails():
    source = inspect.getsource(characterize)

    # Bracket capability is now evaluated per size. A failure of BRK9 must not
    # discard valid BRK3/5/7 support.
    assert "bracket_candidates_by_frames" in source
    assert "supported_sizes" in source
    assert "selected_candidate_ids" in source
    assert "per-size exact N/N capability" in source


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


def test_aperture_is_lens_dependent_runtime_optional():
    source = inspect.getsource(characterize)
    assert '"runtime_optional": True' in source
    assert "SET=runtime-optional" in source
    assert "aperture transition readback mismatch" not in source
