from types import SimpleNamespace

import pytest

from backend.camera_characterization import QualificationOverrun, refine_qualification


def test_observed_sony_overrun_restarts_full_validation():
    block = {'setup_ms': 4600}
    calls, completed = [], []
    def run():
        calls.append(1)
        for repetition in range(5):
            if len(calls) == 1 and repetition == 0:
                raise QualificationOverrun('setup_ms', 4668.5, block['setup_ms'])
            completed.append(repetition)
        return {'repetitions': 5}
    result = refine_qualification(run, {'iso_ms': 1000}, block, SimpleNamespace(log=lambda _: None, check=lambda: None))
    assert block['setup_ms'] == 5200
    assert completed == list(range(5))
    assert result['attempts'] == 2
    assert result['budget_adjustments'][0]['observed_ms'] == 4668.5


def test_repeated_overruns_continue_until_operator_cancels():
    from backend.camera_characterization import Cancelled
    block = {'setup_ms': 1000}
    calls = []
    def run():
        calls.append(1)
        raise QualificationOverrun('setup_ms', block['setup_ms'] + 500, block['setup_ms'])
    def check():
        if len(calls) == 12:
            raise Cancelled('operator cancelled')
    with pytest.raises(Cancelled):
        refine_qualification(run, {}, block, SimpleNamespace(log=lambda _: None, check=check))
    assert len(calls) == 12


def test_hardware_error_is_not_treated_as_budget_adjustment():
    calls = []
    def run():
        calls.append(1)
        raise RuntimeError('Capture not confirmed: 0/3')
    with pytest.raises(RuntimeError, match='0/3'):
        refine_qualification(run, {}, {}, SimpleNamespace(log=lambda _: None, check=lambda: None))
    assert len(calls) == 1


def test_iso_refinement_updates_shared_budget():
    contract = {'iso_ms': 400}
    calls = []
    def run():
        calls.append(1)
        if len(calls) == 1:
            raise QualificationOverrun('iso_ms', 450, 400)
        return {}
    refine_qualification(run, contract, {}, SimpleNamespace(log=lambda _: None, check=lambda: None))
    assert contract['iso_ms'] == 550


def test_three_independent_sony_overruns_allow_fourth_complete_run():
    contract, block = {'iso_ms': 1850}, {'duration_ms': 650, 'setup_ms': 4500}
    observations = [('iso_ms', 1858.0), ('duration_ms', 654.4), ('setup_ms', 4678.6)]
    calls, completed = [], []
    def run():
        calls.append(1)
        if observations:
            field, observed = observations.pop(0)
            owner = contract if field == 'iso_ms' else block
            raise QualificationOverrun(field, observed, owner[field])
        completed.extend(range(5))
        return {'repetitions': 5}
    result = refine_qualification(run, contract, block, SimpleNamespace(log=lambda _: None, check=lambda: None))
    assert len(calls) == result['attempts'] == 4
    assert completed == list(range(5))
    assert contract == {'iso_ms': 2100}
    assert block == {'duration_ms': 800, 'setup_ms': 5200}
    assert [a['field_revision'] for a in result['budget_adjustments']] == [1, 1, 1]


def test_many_independent_revisions_can_eventually_validate():
    contract, block = {'iso_ms': 100}, {'setup_ms': 100, 'duration_ms': 100}
    fields = iter(['iso_ms', 'setup_ms', 'duration_ms'] * 5)
    calls = []
    def run():
        calls.append(1)
        field = next(fields, None)
        if field:
            owner = contract if field == 'iso_ms' else block
            raise QualificationOverrun(field, owner[field]+100, owner[field])
        return {}
    result = refine_qualification(run, contract, block, SimpleNamespace(log=lambda _: None, check=lambda: None))
    assert result['attempts'] == len(calls) == 16
