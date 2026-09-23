import os
import time

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
