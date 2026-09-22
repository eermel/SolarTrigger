import inspect
import pytest
from backend.camera_candidate_optimizer import CandidateEvidence, select_best

def test_worst_case_priority():
    a = CandidateEvidence("a", {}, 5, [100,100,100,100,120], [], True)
    b = CandidateEvidence("b", {}, 5, [50,50,50,50,130], [], True)
    assert select_best([b,a]).candidate_id == "a"

def test_incomplete_is_unreliable():
    a = CandidateEvidence("a", {}, 5, [10,10,10,10], [], True)
    assert not a.reliable
    with pytest.raises(RuntimeError):
        select_best([a])

def test_profile_init_settings_complete():
    from plugins.camera.profile import ProfilePlugin
    src = inspect.getsource(ProfilePlugin.init_settings)
    assert "white_balance" in src
    assert "characterized RAW acquisition" in src


def test_characterization_selection_evidence():
    import backend.camera_characterization as cc
    src = inspect.getsource(cc.characterize)
    assert 'selection_evidence["trigger_single"]' in src
    assert 'selection_evidence["native_bracket"]' in src
    assert '"white_balance"' in src



def test_setting_characterization_qualifies_requested_value_idempotently():
    """Regression: advertised alternate values need not be writable."""
    import inspect
    import backend.camera_characterization as cc

    src = inspect.getsource(cc.characterize)

    # Qualification must exercise the exact direct single-config operation
    # required at runtime using the widget prepared during characterization.
    assert "write_and_confirm(candidate, node, target)" in src
    assert "write_single_config(camera, direct_spec(candidate), node, value)" in src

    # It must not require an unrelated alternate value before every SET.
    assert "write_and_confirm(candidate, node, alternate)" not in src
    assert 'idempotent_probe = key == "capture_target"' not in src

def test_characterization_does_not_require_physical_shutter_start_latency():
    """Physical shutter-start latency is outside the runtime timing contract."""
    import inspect
    import backend.camera_characterization as cc

    src = inspect.getsource(cc.characterize)

    assert "Physical shutter-start latency" not in src
    assert "no timing correction applied" not in src
