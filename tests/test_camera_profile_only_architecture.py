from backend.camera_candidate_optimizer import CandidateEvidence, select_best

def ev(name, durations, *, ok=True, failures=None, expected=None):
    return CandidateEvidence(name, {"method": name},
        len(durations) if expected is None else expected,
        list(durations), list(failures or []), ok)

def test_fast_but_unreliable_never_wins():
    flaky = ev("flaky", [10,11,9,10], failures=["trial 5 failed"], expected=5)
    reliable = ev("reliable", [80,81,79,82,80])
    assert select_best([flaky, reliable]).candidate_id == "reliable"

def test_worst_case_precedes_median():
    assert select_best([ev("a",[10,10,10,10,100]), ev("b",[20,20,20,20,20])]).candidate_id == "b"

def test_no_candidate_fails_closed():
    import pytest
    with pytest.raises(RuntimeError, match="No reliable candidate"):
        select_best([ev("bad", [], ok=False, expected=5)])

def test_production_loader_does_not_autoselect_reference_plugins():
    import inspect, plugins.camera as camera_plugins
    source = inspect.getsource(camera_plugins.load_plugin)
    assert "_load_reference_plugin_classes" not in source
    assert "profile_for_model" in source
    assert "ProfilePlugin" in source
