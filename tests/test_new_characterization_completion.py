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

def test_production_dispatch_profile_only():
    from backend.sequencer_compiler import audit_materialized_capture
    src = inspect.getsource(audit_materialized_capture)
    assert 'capture.backend == "sony"' not in src
    assert '"nikon-dslr"' not in src
    assert "audit_materialized_sony_capture(capture)" not in src
    assert "audit_materialized_nikon_capture(capture)" not in src
    assert 'capture.backend.startswith("profile-")' in src

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

    # Qualification must exercise the exact operation required at runtime.
    assert "write_and_confirm(path, target)" in src

    # It must not require an unrelated alternate value before every SET.
    assert "write_and_confirm(path, alternate)" not in src
    assert 'idempotent_probe = key == "capture_target"' not in src
