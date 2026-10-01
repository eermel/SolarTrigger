from pathlib import Path

import backend.camera_characterization as characterization
import backend.camera_characterization_persistence as persistence
import backend.camera_characterization_selection as selection


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "backend" / "camera_characterization.py").read_text(
    encoding="utf-8"
)


def test_characterization_persistence_is_split_without_api_breakage():
    assert characterization.publish is persistence.publish
    assert (
        characterization._persistent_profile_document
        is persistence._persistent_profile_document
    )
    assert (
        characterization._persistent_timing_document
        is persistence._persistent_timing_document
    )
    assert "def publish(" not in SOURCE
    assert "def _persistent_profile_document(" not in SOURCE


def test_characterization_candidate_selection_is_split_without_api_breakage():
    assert (
        characterization.choose_common_bracket_command
        is selection.choose_common_bracket_command
    )
    assert (
        characterization._capture_validation_state
        is selection._capture_validation_state
    )
    assert (
        characterization._select_common_bracket_calibration_candidate
        is selection._select_common_bracket_calibration_candidate
    )
    assert (
        characterization._select_bracket_candidate
        is selection._select_bracket_candidate
    )
    assert "def choose_common_bracket_command(" not in SOURCE
    assert "def _select_bracket_candidate(" not in SOURCE
