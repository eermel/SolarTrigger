import threading

from backend.trigger_service import TriggerService


class _FakeSupervisor:
    def __init__(self, service):
        self.service = service
        self.joined = False

    def join(self, timeout=None):
        self.joined = True
        with self.service._lock:
            self.service._starting_by_rig[1] = False


class _FakeProc:
    def __init__(self):
        self.returncode = None
        self.terminated = False
        self.killed = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True

    def wait(self, timeout=None):
        self.returncode = 0
        return 0

    def kill(self):
        self.killed = True
        self.returncode = -9


def _service_shell():
    service = TriggerService.__new__(TriggerService)
    service._lock = threading.RLock()
    service._procs = {rig_id: None for rig_id in range(1, 5)}
    service._starting_by_rig = {rig_id: False for rig_id in range(1, 5)}
    service._analysis_suppressed_by_rig = {rig_id: False for rig_id in range(1, 5)}
    service._manual_stop_requested_by_rig = {rig_id: False for rig_id in range(1, 5)}
    service._cancel_start_requested_by_rig = {rig_id: False for rig_id in range(1, 5)}
    service._supervisor_threads = {rig_id: None for rig_id in range(1, 5)}
    service.log = lambda *args, **kwargs: None
    return service


def test_stop_cancels_start_before_popen_is_published():
    service = _service_shell()
    service._starting_by_rig[1] = True
    supervisor = _FakeSupervisor(service)
    service._supervisor_threads[1] = supervisor

    result = service.stop(1)

    assert supervisor.joined is True
    assert service._cancel_start_requested_by_rig[1] is True
    assert service._manual_stop_requested_by_rig[1] is True
    assert result["status"] == "stopped"
    assert result["still_running"] is False


def test_graceful_stop_does_not_wait_for_supervisor_or_atomic_photo():
    service = _service_shell()
    proc = _FakeProc()
    service._procs[1] = proc
    supervisor = _FakeSupervisor(service)
    service._supervisor_threads[1] = supervisor

    result = service.stop(1)

    assert proc.terminated is True
    assert supervisor.joined is False
    assert result["status"] == "stopping"
    assert result["still_running"] is True
