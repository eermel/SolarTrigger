import os
import time

import pytest

import backend.trigger_heartbeat as trigger_heartbeat
from backend.trigger_heartbeat import HeartbeatEmitter, HeartbeatSupervisor


def test_heartbeat_emitter_uses_dedicated_fd():
    read_fd, write_fd = os.pipe()
    emitter = HeartbeatEmitter(write_fd)
    emitter.pulse("capture.end")
    emitter.close()

    assert os.read(read_fd, 128) == b"capture.end\n"
    os.close(read_fd)


def test_heartbeat_watchdog_terminates_silent_child():
    read_fd, write_fd = os.pipe()

    class Proc:
        def __init__(self):
            self.returncode = None
            self.terminated = False

        def poll(self):
            return self.returncode

        def terminate(self):
            self.terminated = True
            self.returncode = -15

        def wait(self, timeout=None):
            return self.returncode

        def kill(self):
            self.returncode = -9

    proc = Proc()
    supervisor = HeartbeatSupervisor(
        read_fd=read_fd,
        proc=proc,
        timeout_s=0.08,
    ).start()
    time.sleep(0.18)
    os.close(write_fd)
    supervisor.stop()

    assert supervisor.timed_out is True
    assert proc.terminated is True


def test_manual_stop_suspends_heartbeat_timeout():
    read_fd, write_fd = os.pipe()
    stopping = True

    class Proc:
        returncode = None
        terminated = False

        def poll(self):
            return self.returncode

        def terminate(self):
            self.terminated = True
            self.returncode = -15

        def wait(self, timeout=None):
            return self.returncode

        def kill(self):
            self.returncode = -9

    proc = Proc()
    supervisor = HeartbeatSupervisor(
        read_fd=read_fd,
        proc=proc,
        timeout_s=0.06,
        manual_stop_fn=lambda: stopping,
    ).start()
    time.sleep(0.16)

    assert supervisor.timed_out is False
    assert proc.terminated is False

    proc.returncode = 0
    os.close(write_fd)
    supervisor.stop()


def test_stage_specific_timeout_shortens_scheduler_hang_detection(monkeypatch):
    read_fd, write_fd = os.pipe()
    monkeypatch.setitem(trigger_heartbeat.DEFAULT_STAGE_TIMEOUTS_S, "wait", 0.06)

    class Proc:
        returncode = None
        terminated = False

        def poll(self):
            return self.returncode

        def terminate(self):
            self.terminated = True
            self.returncode = -15

        def wait(self, timeout=None):
            return self.returncode

        def kill(self):
            self.returncode = -9

    proc = Proc()
    supervisor = HeartbeatSupervisor(
        read_fd=read_fd,
        proc=proc,
        timeout_s=1.0,
    ).start()
    emitter = HeartbeatEmitter(write_fd)
    emitter.pulse("wait")

    stage_deadline = time.monotonic() + 0.5
    while time.monotonic() < stage_deadline:
        stage, _timed_out = supervisor.snapshot()
        if stage == "wait":
            break
        time.sleep(0.005)
    assert supervisor.snapshot()[0] == "wait"

    timeout_deadline = time.monotonic() + 0.3
    while not supervisor.timed_out and time.monotonic() < timeout_deadline:
        time.sleep(0.005)

    emitter.close()
    supervisor.stop()

    assert supervisor.timed_out is True
    assert proc.terminated is True


def test_stage_specific_timeout_keeps_camera_capture_budget(monkeypatch):
    read_fd, write_fd = os.pipe()
    monkeypatch.setitem(
        trigger_heartbeat.DEFAULT_STAGE_TIMEOUTS_S,
        "capture.begin",
        0.25,
    )

    class Proc:
        returncode = None
        terminated = False

        def poll(self):
            return self.returncode

        def terminate(self):
            self.terminated = True
            self.returncode = -15

        def wait(self, timeout=None):
            return self.returncode

        def kill(self):
            self.returncode = -9

    proc = Proc()
    supervisor = HeartbeatSupervisor(
        read_fd=read_fd,
        proc=proc,
        timeout_s=1.0,
    ).start()
    emitter = HeartbeatEmitter(write_fd)
    emitter.pulse("capture.begin")
    time.sleep(0.12)

    assert supervisor.timed_out is False
    assert proc.terminated is False

    proc.returncode = 0
    emitter.close()
    supervisor.stop()


def test_capture_custom_timeout_can_exceed_generic_watchdog():
    read_fd, write_fd = os.pipe()

    class Proc:
        returncode = None
        terminated = False

        def poll(self):
            return self.returncode

        def terminate(self):
            self.terminated = True
            self.returncode = -15

        def wait(self, timeout=None):
            return self.returncode

        def kill(self):
            self.returncode = -9

    proc = Proc()
    supervisor = HeartbeatSupervisor(
        read_fd=read_fd,
        proc=proc,
        timeout_s=0.08,
    ).start()
    emitter = HeartbeatEmitter(write_fd)
    emitter.pulse("capture.begin", timeout_s=0.25)

    stage_deadline = time.monotonic() + 0.3
    while time.monotonic() < stage_deadline:
        if supervisor.snapshot()[0] == "capture.begin":
            break
        time.sleep(0.005)

    time.sleep(0.12)
    assert supervisor.timed_out is False
    assert proc.terminated is False

    # The semantic timeout remains 250 ms.  Give the watchdog thread enough
    # wall-clock scheduling margin when this test runs inside the full suite
    # on a loaded/virtualized CI or development host.
    timeout_deadline = time.monotonic() + 1.0
    while not supervisor.timed_out and time.monotonic() < timeout_deadline:
        time.sleep(0.005)

    emitter.close()
    supervisor.stop()

    assert supervisor.timed_out is True
    assert proc.terminated is True


def test_invalid_custom_timeout_falls_back_to_stage_budget(monkeypatch):
    read_fd, write_fd = os.pipe()
    monkeypatch.setitem(
        trigger_heartbeat.DEFAULT_STAGE_TIMEOUTS_S,
        "capture.begin",
        0.06,
    )

    class Proc:
        returncode = None
        terminated = False

        def poll(self):
            return self.returncode

        def terminate(self):
            self.terminated = True
            self.returncode = -15

        def wait(self, timeout=None):
            return self.returncode

        def kill(self):
            self.returncode = -9

    proc = Proc()
    supervisor = HeartbeatSupervisor(
        read_fd=read_fd,
        proc=proc,
        timeout_s=1.0,
    ).start()
    os.write(write_fd, b"capture.begin\tnot-a-number\n")

    timeout_deadline = time.monotonic() + 0.3
    while not supervisor.timed_out and time.monotonic() < timeout_deadline:
        time.sleep(0.005)

    os.close(write_fd)
    supervisor.stop()

    assert supervisor.timed_out is True
    assert proc.terminated is True


def test_heartbeat_emitter_never_blocks_when_pipe_is_full():
    read_fd, write_fd = os.pipe()
    os.set_blocking(write_fd, False)
    try:
        while True:
            try:
                os.write(write_fd, b"x" * 4096)
            except BlockingIOError:
                break

        emitter = HeartbeatEmitter(write_fd)
        before = time.monotonic()
        emitter.pulse("capture.end")
        elapsed = time.monotonic() - before

        assert elapsed < 0.1
        assert emitter.fd == write_fd

        # Once the parent drains capacity, a later pulse must still be
        # deliverable through the same heartbeat channel.
        os.read(read_fd, 4096)
        emitter.pulse("capture.end")
        emitter.close()
        remaining = bytearray()
        while True:
            chunk = os.read(read_fd, 4096)
            if not chunk:
                break
            remaining.extend(chunk)
        assert b"capture.end\n" in remaining
    finally:
        try:
            os.close(write_fd)
        except OSError:
            pass
        os.close(read_fd)


def test_heartbeat_reader_start_failure_rolls_back_watchdog_without_closing_fd():
    read_fd, write_fd = os.pipe()
    created = []

    class FakeThread:
        def __init__(self, *, target, name, daemon):
            self.target = target
            self.name = name
            self.daemon = daemon
            self.started = False
            self.joined = False
            created.append(self)

        def start(self):
            if self.name == "trigger-heartbeat-reader":
                raise RuntimeError("synthetic reader start failure")
            self.started = True

        def join(self, timeout=None):
            self.joined = True

    class Proc:
        def poll(self):
            return None

    supervisor = HeartbeatSupervisor(
        read_fd=read_fd,
        proc=Proc(),
        timeout_s=1.0,
        thread_factory=FakeThread,
    )

    with pytest.raises(
        RuntimeError,
        match="synthetic reader start failure",
    ):
        supervisor.start()

    by_name = {thread.name: thread for thread in created}
    assert by_name["trigger-heartbeat-watchdog"].started is True
    assert by_name["trigger-heartbeat-watchdog"].joined is True
    assert by_name["trigger-heartbeat-reader"].started is False
    assert supervisor._watchdog_thread is None
    assert supervisor._reader_thread is None
    assert supervisor._stop.is_set() is True

    # Startup rollback deliberately leaves read_fd to the caller.
    os.write(write_fd, b"x")
    assert os.read(read_fd, 1) == b"x"

    os.close(write_fd)
    os.close(read_fd)



def test_heartbeat_emitter_closes_only_on_broken_pipe():
    read_fd, write_fd = os.pipe()
    emitter = HeartbeatEmitter(write_fd)
    os.close(read_fd)

    emitter.pulse("wait")

    assert emitter.fd is None



def test_camera_control_watchdog_stages_keep_pi_safety_margin():
    assert trigger_heartbeat.DEFAULT_STAGE_TIMEOUTS_S["phase.setup.begin"] == 60.0
    assert (
        trigger_heartbeat.DEFAULT_STAGE_TIMEOUTS_S["capture.prepare.begin"]
        == 60.0
    )
