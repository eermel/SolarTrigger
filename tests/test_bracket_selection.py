from types import SimpleNamespace
import pytest
from backend.camera_characterization import (
    choose_common_bracket_command,
    _select_common_bracket_calibration_candidate,
    check_qualification_margins,
    QualificationOverrun,
)
from backend.camera_candidate_optimizer import CandidateEvidence


def candidate(peaks, setup):
    return {str(n): {'spec': {'peak_capture_ms': p, 'total_ms': p + setup}}
            for n, p in peaks.items()}


def test_common_command_ignores_preparation_noise():
    candidates = {'widget-capture': candidate({3: 2500, 5: 2700}, 9000),
                  'widget-bulb': candidate({3: 2600, 5: 2800}, 1000)}
    assert choose_common_bracket_command(candidates, {}, [3, 5]) == 'widget-capture'


def test_rejected_command_cannot_return_using_earlier_successes():
    candidates = {'capture': candidate({3: 100, 5: 100}, 0),
                  'bulb': candidate({3: 200, 5: 200}, 0)}
    assert choose_common_bracket_command(candidates, {'capture': 'wrong count'}, [3, 5]) == 'bulb'


def test_never_mix_partial_candidates():
    candidates = {'capture': candidate({3: 100}, 0), 'bulb': candidate({5: 100}, 0)}
    assert choose_common_bracket_command(candidates, {}, [3, 5]) is None


def _combined_calibration_candidate(command_id, durations):
    evidence = CandidateEvidence(
        candidate_id=command_id,
        recipe={"method": command_id},
        expected_trials=len(durations),
        durations_ms=list(durations),
        functional_ok=True,
    )
    return {
        "command_id": command_id,
        "evidence": evidence,
        "calibration_total_capture_ms": sum(durations),
        "calibration_worst_capture_ms": max(durations),
        "calibration_total_prepare_to_first_file_ms": sum(durations),
    }


def test_common_calibration_primitive_uses_aggregate_cost_not_largest_bracket_only():
    # A6600-style case: bulb is only 98 ms faster on BRK9 but more than
    # 2.3 s slower on BRK3.  The common primitive must minimize total cost
    # across the calibration pair instead of letting BRK9 dominate.
    capture = _combined_calibration_candidate(
        "capture",
        [3223.1, 8516.6],
    )
    bulb = _combined_calibration_candidate(
        "bulb",
        [5574.2, 8418.6],
    )

    selected = _select_common_bracket_calibration_candidate(
        [bulb, capture]
    )

    assert selected["command_id"] == "capture"


def test_common_calibration_primitive_uses_worst_cost_as_tie_breaker():
    first = _combined_calibration_candidate(
        "first",
        [3000.0, 7000.0],
    )
    second = _combined_calibration_candidate(
        "second",
        [4000.0, 6000.0],
    )

    selected = _select_common_bracket_calibration_candidate(
        [first, second]
    )

    assert selected["command_id"] == "second"


def test_late_maximum_requires_margin_even_below_budget():
    with pytest.raises(QualificationOverrun, match='required 4650'):
        check_qualification_margins({'duration_ms': 4181.039686}, {}, {'duration_ms': 4450})
    check_qualification_margins({'duration_ms': 4181.039686}, {}, {'duration_ms': 4650})


def test_measurement_checkpoint_is_not_discovered_as_profile(tmp_path):
    import json
    from backend.camera_characterization import CharacterizationJob
    from backend.camera_profiles import discover_profiles
    job = CharacterizationJob()
    job.measurement_path = tmp_path / 'configs/camera_characterization/measurements/run.json'
    trials = []
    job.checkpoint(timing_trials=trials)
    trials.append({'status': 'rejected', 'reason': 'wrong frame count'})
    job.checkpoint(outcome={'status': 'FAILED'})
    saved = json.loads(job.measurement_path.read_text())
    assert saved['timing_trials'][0]['reason'] == 'wrong frame count'
    assert saved['outcome']['status'] == 'FAILED'
    assert discover_profiles(tmp_path / 'configs/camera_profiles') == {}
