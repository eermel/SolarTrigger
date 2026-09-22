import signal
import threading

from backend.trigger_service import TriggerService


class FakeProc:
    def __init__(self, returncode=0):
        self._running = True
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
        if self._running:
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


def test_graceful_stop_returns_without_timing_out_atomic_camera_group():
    proc = FakeProc(returncode=0)
    svc = make_service(proc)

    result = svc.stop(1)

    assert proc.terminated is True
    assert proc.killed is False
    assert proc.wait_timeouts == []
    assert result == {
        "status": "stopping",
        "rig_id": 1,
        "forced": False,
        "still_running": True,
    }
    assert svc._manual_stop_requested_by_rig[1] is True
    assert any("Graceful STOP requested" in text for text, _, _ in svc.log_lines)


def test_explicit_force_stop_is_the_only_operator_sigkill_path():
    proc = FakeProc(returncode=0)
    svc = make_service(proc)

    graceful = svc.stop(1)
    forced = svc.stop(1, force=True)

    assert graceful["status"] == "stopping"
    assert proc.terminated is True
    assert proc.killed is True
    assert proc.wait_timeouts == [2.0]
    assert forced == {
        "status": "stopped",
        "rig_id": 1,
        "forced": True,
        "still_running": False,
    }
    assert any("FORCE STOP requested" in text for text, _, _ in svc.log_lines)


def test_duplicate_graceful_stop_request_is_coalesced():
    proc = FakeProc(returncode=0)
    svc = make_service(proc)
    svc._stopping_by_rig = {1: True, 2: False, 3: False, 4: False}

    result = svc.stop(1)

    assert result == {
        "status": "stopping",
        "rig_id": 1,
        "forced": False,
        "still_running": True,
    }
    assert proc.terminated is False
    assert proc.killed is False
    assert proc.wait_timeouts == []
    assert svc.log_lines == []
