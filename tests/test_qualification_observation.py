from types import SimpleNamespace
import sys
import pytest
from plugins.camera import profile as module
from backend.camera_characterization import Cancelled


def rig(monkeypatch, arrival):
    clock = [0.0]
    active = [False]
    emitted = [False]
    monkeypatch.setitem(sys.modules, 'gphoto2', SimpleNamespace(GP_EVENT_TIMEOUT=0, GP_EVENT_FILE_ADDED=1))
    monkeypatch.setattr(module.time, 'monotonic', lambda: clock[0])
    def event(timeout):
        clock[0] += timeout / 1000
        if active[0] and not emitted[0] and clock[0] >= arrival:
            emitted[0] = True
            return 1, SimpleNamespace(folder='DCIM', name='test.raw')
        return 0, None
    plugin = object.__new__(module.ProfilePlugin)
    plugin.profile = {}
    plugin.commands = {'trigger_single': {'method': 'trigger_capture'}}
    plugin.camera = SimpleNamespace(wait_for_event=event, trigger_capture=lambda: active.__setitem__(0, True))
    return plugin, clock


PARAMS = {'shutter': '1/500', 'frames': 1, 'timing_contract_version': 2, 'duration_ms': 650}


def test_qualification_observes_late_file_without_waiting_entire_timeout(monkeypatch):
    plugin, clock = rig(monkeypatch, 1.2)
    assert plugin.execute_photo(PARAMS, observation_timeout_s=15).frames == 1
    assert 1.2 <= clock[0] < 1.4
    assert clock[0] * 1000 > PARAMS['duration_ms']  # Caller revises budget and restarts.


def test_runtime_has_no_characterization_observation_extension(monkeypatch):
    plugin, clock = rig(monkeypatch, 1.2)
    with pytest.raises(RuntimeError, match='0/1'):
        plugin.execute_photo(PARAMS)
    assert clock[0] < 1.2


def test_absent_file_still_fails_after_observation_deadline(monkeypatch):
    plugin, clock = rig(monkeypatch, 100)
    with pytest.raises(RuntimeError, match='0/1'):
        plugin.execute_photo(PARAMS, observation_timeout_s=2)
    assert 2 <= clock[0] < 2.2


def test_observation_can_be_cancelled(monkeypatch):
    plugin, clock = rig(monkeypatch, 100)
    def check():
        if clock[0] >= .3:
            raise Cancelled('operator cancelled')
    with pytest.raises(Cancelled):
        plugin.execute_photo(PARAMS, observation_timeout_s=15, check=check)
    assert clock[0] < .5
