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


def test_capture_target_characterization_is_session_safe():
    """Regression: Sony PC Control capturetarget may not be reversible."""
    import backend.camera_characterization as cc

    src = inspect.getsource(cc.characterize)

    # capture_target must not be qualified by bouncing through other storage
    # destinations: some Sony bodies cannot reverse that transition during
    # the same PTP/PC-Control session.
    assert 'idempotent_probe = key == "capture_target"' in src
    assert "if not idempotent_probe:" in src

    # A failed best-effort restore rejects/logs the candidate instead of
    # aborting the whole characterization.
    assert 'ev.failures.append(' in src
    assert '"restore failed: {restore_exc}"' in src
    assert 'raise RuntimeError(\\n                                    f"Cannot restore' not in src
