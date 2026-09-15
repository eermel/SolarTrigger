import types

import pytest

import backend.camera_process_worker as cpw
from backend.camera_process_worker import ProcessCameraWorker
from backend.generic_worker import WorkerUnavailableError


class _FakeProcess:
    def __init__(self):
        self.alive = True
        self.terminated = False
        self.killed = False

    def is_alive(self):
        return self.alive

    def terminate(self):
        self.terminated = True
        self.alive = False

    def kill(self):
        self.killed = True
        self.alive = False

    def join(self, timeout=None):
        return None


class _FakeConn:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def _proxy():
    worker = ProcessCameraWorker(rig_id=1, log_fn=lambda _msg: None)
    worker._process = _FakeProcess()
    worker._conn = _FakeConn()
    return worker


def test_parent_kills_generation_on_fatal_child_error():
    worker = _proxy()

    with pytest.raises(WorkerUnavailableError, match="camera child fatal SystemExit"):
        worker._raise_remote_error_locked(
            "test_photo",
            {
                "kind": "error",
                "class": "SystemExit",
                "code": None,
                "message": "fatal exit",
                "fatal": True,
            },
        )

    assert worker._process is None
    assert worker._conn is None
    assert "SystemExit" in (worker._last_failure or "")


def test_parent_keeps_generation_for_normal_remote_exception():
    worker = _proxy()

    with pytest.raises(RuntimeError, match="camera child ValueError: ordinary"):
        worker._raise_remote_error_locked(
            "test_photo",
            {
                "kind": "error",
                "class": "ValueError",
                "code": None,
                "message": "ordinary",
                "fatal": False,
            },
        )

    assert worker._process is not None
    assert worker._process.is_alive() is True
    assert worker._conn is not None


def test_child_stops_serving_after_fatal_baseexception(monkeypatch):
    events = []

    class FakeWorker:
        def __init__(self, *args, **kwargs):
            events.append("init")

        def configure_camera(self, entry):
            events.append("configure")

        def start(self):
            events.append("start")

        def explode(self):
            events.append("explode")
            raise SystemExit("fatal child exit")

        def should_not_run(self):
            events.append("should_not_run")
            return "bad"

        def stop(self, timeout=None):
            events.append(("stop", timeout))
            return True

    class FakeConn:
        def __init__(self):
            self.requests = iter([
                {"operation": "explode", "args": (), "kwargs": {}},
                {"operation": "should_not_run", "args": (), "kwargs": {}},
            ])
            self.sent = []
            self.closed = False

        def recv(self):
            try:
                return next(self.requests)
            except StopIteration:
                raise EOFError

        def send(self, payload):
            self.sent.append(payload)

        def close(self):
            self.closed = True

    monkeypatch.setattr(cpw, "CameraWorker", FakeWorker)
    conn = FakeConn()

    cpw._camera_process_main(
        conn,
        rig_id=1,
        camera_entry={"backend": "fake"},
        clock_spec=None,
        call_timeout_s=1.0,
    )

    assert "explode" in events
    assert "should_not_run" not in events
    fatal_errors = [
        item for item in conn.sent
        if item.get("kind") == "error" and item.get("fatal") is True
    ]
    assert len(fatal_errors) == 1
    assert fatal_errors[0]["class"] == "SystemExit"
    assert conn.closed is True
