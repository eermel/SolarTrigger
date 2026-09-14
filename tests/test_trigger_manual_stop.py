import signal
import subprocess
import threading

from backend.trigger_service import TriggerService


class FakeProc:
    def __init__(self, timeout_once=False, returncode=0):
        self._running = True
        self.timeout_once = timeout_once
        self.returncode = returncode
        self.wait_timeouts = []
        self.terminated = False
        self.killed = False

    def poll(self):
        return None if self._running else self.returncode

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True
        self._running = False
        self.returncode = -signal.SIGKILL

    def wait(self, timeout=None):
        self.wait_timeouts.append(timeout)
        if self.timeout_once and not self.killed:
            self.timeout_once = False
            raise subprocess.TimeoutExpired('trigger', timeout)
        self._running = False
        return self.returncode


def make_service(proc):
    svc = TriggerService.__new__(TriggerService)
    svc._lock = threading.RLock()
    svc._procs = {1: proc, 2: None, 3: None, 4: None}
    svc._analysis_suppressed_by_rig = {1: False, 2: False, 3: False, 4: False}
    svc._manual_stop_requested_by_rig = {1: False, 2: False, 3: False, 4: False}
    svc.log_lines = []
    svc.log = lambda text, level, source: svc.log_lines.append((text, level, source))
    return svc


def test_manual_stop_allows_atomic_camera_group_to_finish():
    proc = FakeProc(returncode=0)
    svc = make_service(proc)
    result = svc.stop(1)
    assert proc.terminated is True
    assert proc.killed is False
    assert proc.wait_timeouts == [30]
    assert result['forced'] is False
    assert result['still_running'] is False
    assert svc._manual_stop_requested_by_rig[1] is True


def test_manual_stop_kills_only_after_graceful_timeout():
    proc = FakeProc(timeout_once=True)
    svc = make_service(proc)
    result = svc.stop(1)
    assert proc.terminated is True
    assert proc.killed is True
    assert proc.wait_timeouts == [30, 2]
    assert result['forced'] is True
    assert result['still_running'] is False
    assert any('30 s graceful-stop timeout' in text for text, _, _ in svc.log_lines)
