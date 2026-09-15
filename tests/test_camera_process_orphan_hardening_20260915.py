from backend.camera_process_worker import ProcessCameraWorker
from backend.generic_worker import WorkerUnavailableError


class _Conn:
    def __init__(self):
        self.closed = False
        self.sent = []

    def send(self, payload):
        self.sent.append(payload)

    def close(self):
        self.closed = True


class _StubbornProcess:
    def __init__(self):
        self.alive = True
        self.terminate_calls = 0
        self.kill_calls = 0
        self.join_calls = []

    def is_alive(self):
        return self.alive

    def terminate(self):
        self.terminate_calls += 1

    def kill(self):
        self.kill_calls += 1

    def join(self, timeout=None):
        self.join_calls.append(timeout)


class _DiesOnKillProcess(_StubbornProcess):
    def kill(self):
        super().kill()
        self.alive = False


def _worker():
    worker = ProcessCameraWorker(rig_id=1)
    worker._started = True
    return worker


def test_stop_retains_child_reference_when_termination_fails():
    worker = _worker()
    process = _StubbornProcess()
    conn = _Conn()
    worker._process = process
    worker._conn = conn

    assert worker.stop(timeout=0) is False

    assert worker._process is process
    assert worker._conn is None
    assert conn.closed is True
    assert worker._started is False
    assert worker.healthy is False
    assert process.terminate_calls == 1
    assert process.kill_calls == 1
    assert "survived shutdown termination" in worker._last_failure


def test_stop_clears_reference_when_forced_kill_succeeds():
    worker = _worker()
    process = _DiesOnKillProcess()
    conn = _Conn()
    worker._process = process
    worker._conn = conn

    assert worker.stop(timeout=0) is True

    assert worker._process is None
    assert worker._conn is None
    assert conn.closed is True
    assert process.kill_calls == 1


def test_kill_current_retains_unstoppable_generation_and_fails_closed():
    worker = _worker()
    process = _StubbornProcess()
    conn = _Conn()
    worker._process = process
    worker._conn = conn

    worker._kill_current_locked()

    assert worker._process is process
    assert worker._conn is None
    assert worker._started is False
    assert worker.healthy is False
    assert process.terminate_calls == 1
    assert process.kill_calls == 1
    assert "survived forced termination" in worker._last_failure

    try:
        worker._ensure_process_locked()
    except WorkerUnavailableError:
        pass
    else:
        raise AssertionError("unstoppable child generation was reused")


def test_kill_current_clears_generation_when_kill_succeeds():
    worker = _worker()
    process = _DiesOnKillProcess()
    conn = _Conn()
    worker._process = process
    worker._conn = conn

    worker._kill_current_locked()

    assert worker._process is None
    assert worker._conn is None
    assert conn.closed is True
