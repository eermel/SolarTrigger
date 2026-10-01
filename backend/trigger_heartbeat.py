"""In-memory heartbeat channel between eclipse_trigger.py and TriggerService."""

from __future__ import annotations

import os
from math import isfinite
import subprocess
import threading
import time


HEARTBEAT_ENV = "SOLARTRIGGER_HEARTBEAT_FD"
DEFAULT_HEARTBEAT_TIMEOUT_S = 180.0
MANUAL_STOP_NON_CAPTURE_TIMEOUT_S = 10.0

# Scheduler states that should never remain silent for the generic 180 s
# fallback. Camera operations keep a larger budget because the IPC layer
# legitimately permits long captures.
DEFAULT_STAGE_TIMEOUTS_S = {
    "startup": 30.0,
    "ipc.ready": 20.0,
    "runtime.begin": 20.0,
    "wait": 20.0,
    # Camera fan-out may spend up to 5 s discovering active RIGs and 30 s
    # in one control RPC. Keep margin for USB/scheduling jitter on the Pi.
    "phase.setup.begin": 60.0,
    "phase.ready": 20.0,
    "capture.prepare.begin": 60.0,
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
                if isfinite(value) and value > 0.0:
                    text = f"{text}\t{value:.3f}"
            os.write(self.fd, (text + "\n").encode("utf-8", errors="replace"))
        except BlockingIOError:
            # A non-blocking heartbeat pipe can be temporarily full if the
            # parent reader is delayed. Dropping one observational pulse is
            # safe; closing the fd would permanently disable supervision and
            # can make a healthy scheduler look dead later.
            return
        except (BrokenPipeError, OSError):
            # A genuinely broken writer is no longer usable. Capture remains
            # authoritative; close only this observability channel.
            fd, self.fd = self.fd, None
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass

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
        self.manual_stop_escalated = False
        self._manual_stop_started_at = None
        self._stop = threading.Event()
        self._pulse_event = threading.Event()
        self._lock = threading.Lock()
        self._reader_thread = None
        self._watchdog_thread = None

    def start(self):
        reader = self.thread_factory(
            target=self._reader,
            name="trigger-heartbeat-reader",
            daemon=True,
        )
        watchdog = self.thread_factory(
            target=self._watchdog,
            name="trigger-heartbeat-watchdog",
            daemon=True,
        )

        # Start the watchdog first because it can be stopped without touching
        # the heartbeat FD.  If the reader then fails to start, rollback can
        # wake/join the watchdog while the caller still owns read_fd and may
        # close it in its outer startup cleanup.
        try:
            watchdog.start()
        except BaseException:
            self._watchdog_thread = None
            self._reader_thread = None
            raise

        self._watchdog_thread = watchdog
        try:
            reader.start()
        except BaseException:
            self._stop.set()
            self._pulse_event.set()
            if watchdog is not threading.current_thread():
                try:
                    watchdog.join(timeout=1.0)
                except BaseException:
                    pass
            self._watchdog_thread = None
            self._reader_thread = None
            raise

        self._reader_thread = reader
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
                            if isfinite(parsed_timeout) and parsed_timeout > 0.0:
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
                manual_stop = bool(self.manual_stop_fn())
            except Exception:
                manual_stop = False

            now = time.monotonic()
            if manual_stop:
                if self._manual_stop_started_at is None:
                    self._manual_stop_started_at = now
            else:
                self._manual_stop_started_at = None

            with self._lock:
                age = now - self.last_seen
                stage = self.last_stage
                stage_timeout_s = self.last_stage_timeout_s
            effective_timeout_s = (
                self.timeout_s
                if stage_timeout_s is None
                else stage_timeout_s
            )

            if manual_stop and stage != "capture.begin":
                stop_age = now - self._manual_stop_started_at
                stop_timeout_s = MANUAL_STOP_NON_CAPTURE_TIMEOUT_S
                if stop_age <= stop_timeout_s:
                    continue
                self.manual_stop_escalated = True
                try:
                    self.log_fn(
                        "Graceful STOP exceeded bounded non-capture timeout "
                        f"({stop_age:.1f}s > {stop_timeout_s:.1f}s, "
                        f"last_stage={stage or 'none'}); escalating."
                    )
                except Exception:
                    pass
                self._terminate_child()
                return

            # During capture.begin, graceful STOP preserves the complete atomic
            # PHOTO group but only up to the characterized dynamic capture
            # watchdog budget. A truly hung capture is then terminated.
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
