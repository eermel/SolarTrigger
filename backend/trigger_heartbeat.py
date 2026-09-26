"""In-memory heartbeat channel between eclipse_trigger.py and TriggerService."""

from __future__ import annotations

import os
import subprocess
import threading
import time


HEARTBEAT_ENV = "SOLARTRIGGER_HEARTBEAT_FD"
DEFAULT_HEARTBEAT_TIMEOUT_S = 180.0

# Scheduler states that should never remain silent for the generic 180 s
# fallback. Camera operations keep a larger budget because the IPC layer
# legitimately permits long captures.
DEFAULT_STAGE_TIMEOUTS_S = {
    "startup": 30.0,
    "ipc.ready": 20.0,
    "runtime.begin": 20.0,
    "wait": 20.0,
    "phase.setup.begin": 40.0,
    "phase.ready": 20.0,
    "capture.prepare.begin": 40.0,
    "capture.begin": 125.0,
    "capture.error": 20.0,
    "capture.end": 20.0,
    "runtime.end": 20.0,
    "runtime.error": 20.0,
    "shutdown": 40.0,
}


class HeartbeatEmitter:
    """Best-effort child-side heartbeat writer.

    Heartbeat observability must never be able to stop the scheduler.
    """

    def __init__(self, fd: int | None):
        self.fd = fd

    @classmethod
    def from_environment(cls, env=None):
        source = os.environ if env is None else env
        raw = source.get(HEARTBEAT_ENV)
        try:
            fd = int(raw) if raw is not None else None
        except (TypeError, ValueError):
            fd = None
        return cls(fd)

    def pulse(self, stage: str, *, timeout_s: float | None = None) -> None:
        if self.fd is None:
            return
        try:
            text = (
                str(stage).replace("\n", " ").replace("\t", " ").strip()
                or "tick"
            )
            if timeout_s is not None:
                value = float(timeout_s)
                if value > 0.0:
                    text = f"{text}\t{value:.3f}"
            os.write(self.fd, (text + "\n").encode("utf-8", errors="replace"))
        except (BlockingIOError, BrokenPipeError, OSError):
            # Parent supervision/heartbeat failure must not affect capture.
            self.fd = None

    def close(self) -> None:
        fd, self.fd = self.fd, None
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass


class HeartbeatSupervisor:
    """Parent-side reader/watchdog independent from child stdout."""

    def __init__(
        self,
        *,
        read_fd: int,
        proc,
        timeout_s: float = DEFAULT_HEARTBEAT_TIMEOUT_S,
        manual_stop_fn=lambda: False,
        log_fn=lambda _message: None,
        thread_factory=threading.Thread,
    ):
        self.read_fd = read_fd
        self.proc = proc
        self.timeout_s = max(0.05, float(timeout_s))
        self.manual_stop_fn = manual_stop_fn
        self.log_fn = log_fn
        self.thread_factory = thread_factory
        self.last_seen = time.monotonic()
        self.last_stage = None
        self.last_stage_timeout_s = None
        self.timed_out = False
        self._stop = threading.Event()
        self._pulse_event = threading.Event()
        self._lock = threading.Lock()
        self._reader_thread = None
        self._watchdog_thread = None

    def start(self):
        self._reader_thread = self.thread_factory(
            target=self._reader,
            name="trigger-heartbeat-reader",
            daemon=True,
        )
        self._watchdog_thread = self.thread_factory(
            target=self._watchdog,
            name="trigger-heartbeat-watchdog",
            daemon=True,
        )
        self._reader_thread.start()
        self._watchdog_thread.start()
        return self

    def _reader(self):
        buffer = b""
        fd = self.read_fd
        try:
            while not self._stop.is_set():
                try:
                    chunk = os.read(fd, 4096)
                except OSError:
                    break
                if not chunk:
                    break
                buffer += chunk
                while b"\n" in buffer:
                    raw, buffer = buffer.split(b"\n", 1)
                    pulse = raw.decode("utf-8", errors="replace").strip() or "tick"
                    stage, separator, raw_timeout = pulse.partition("\t")
                    stage = stage.strip() or "tick"
                    custom_timeout_s = None
                    if separator:
                        try:
                            parsed_timeout = float(raw_timeout)
                            if parsed_timeout > 0.0:
                                custom_timeout_s = parsed_timeout
                        except (TypeError, ValueError):
                            custom_timeout_s = None
                    with self._lock:
                        self.last_seen = time.monotonic()
                        self.last_stage = stage
                        if custom_timeout_s is not None:
                            # A capture-specific budget may legitimately exceed
                            # the generic watchdog timeout. It is derived from
                            # the characterized PreparedCapture duration.
                            self.last_stage_timeout_s = custom_timeout_s
                        else:
                            stage_timeout = DEFAULT_STAGE_TIMEOUTS_S.get(stage)
                            self.last_stage_timeout_s = (
                                None
                                if stage_timeout is None
                                else min(self.timeout_s, float(stage_timeout))
                            )
                    self._pulse_event.set()
        finally:
            try:
                os.close(fd)
            except OSError:
                pass
            self.read_fd = -1

    def _watchdog(self):
        while not self._stop.is_set():
            with self._lock:
                stage_timeout_s = self.last_stage_timeout_s
            effective_timeout_s = (
                self.timeout_s
                if stage_timeout_s is None
                else stage_timeout_s
            )
            poll_interval_s = min(
                1.0,
                max(0.05, effective_timeout_s / 4.0),
            )

            # A newly received stage may have a much shorter timeout than the
            # previous one. Wake immediately on heartbeat input so the watchdog
            # recomputes its polling cadence instead of sleeping according to
            # stale state.
            self._pulse_event.wait(poll_interval_s)
            self._pulse_event.clear()
            if self._stop.is_set():
                return

            try:
                if self.proc.poll() is not None:
                    return
            except Exception:
                return
            try:
                if self.manual_stop_fn():
                    # Graceful STOP deliberately permits an in-flight atomic
                    # camera capture to finish, regardless of its duration.
                    continue
            except Exception:
                pass

            with self._lock:
                age = time.monotonic() - self.last_seen
                stage = self.last_stage
                stage_timeout_s = self.last_stage_timeout_s
            effective_timeout_s = (
                self.timeout_s
                if stage_timeout_s is None
                else stage_timeout_s
            )
            if age <= effective_timeout_s:
                continue
            self.timed_out = True
            try:
                self.log_fn(
                    "Trigger scheduler heartbeat timeout "
                    f"({age:.1f}s > {effective_timeout_s:.1f}s, "
                    f"last_stage={stage or 'none'})."
                )
            except Exception:
                pass
            self._terminate_child()
            return

    def _terminate_child(self):
        try:
            if self.proc.poll() is not None:
                return
            self.proc.terminate()
            self.proc.wait(timeout=5)
            return
        except subprocess.TimeoutExpired:
            pass
        except Exception:
            pass
        try:
            if self.proc.poll() is None:
                self.proc.kill()
                self.proc.wait(timeout=2)
        except Exception:
            pass

    def snapshot(self) -> tuple[str | None, bool]:
        with self._lock:
            return self.last_stage, bool(self.timed_out)

    def stop(self):
        self._stop.set()
        self._pulse_event.set()
        for thread in (self._watchdog_thread, self._reader_thread):
            if thread is not None and thread is not threading.current_thread():
                thread.join(timeout=1.0)
