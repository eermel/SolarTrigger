"""Supervised process boundary for one physical camera.

The child process owns CameraWorker, CameraService and every camera/plugin
object.  If a low-level USB/PTP call hangs, the parent can terminate/kill the
entire child without blocking Flask or another RIG.

Timed-out commands are never retried automatically.  The following command
starts a fresh child process.
"""

from __future__ import annotations

import multiprocessing
import secrets
import threading
import time
from datetime import datetime
from multiprocessing.connection import Connection
from typing import Any

from backend.camera_timeout_policy import camera_operation_timeout_s
from backend.camera_worker import CameraWorker
from backend.generic_worker import (
    BusyDeviceError,
    ExpiredJobError,
    WorkerTimeoutError,
    WorkerUnavailableError,
)
from backend.trigger_runtime import RuntimeClock
from services.camera_service import PreparedCapture


_READY_TIMEOUT_S = 10.0
_KILL_GRACE_S = 0.5


def _clock_snapshot(clock) -> dict[str, Any] | None:
    if clock is None:
        return None

    now = clock.now()
    return {
        "anchor_utc": now.isoformat(),
        "anchor_mono": time.monotonic(),
        "sim_mode": bool(getattr(clock, "sim_mode", False)),
        "speed": float(getattr(clock, "speed", 1.0)),
    }


def _restore_clock(spec: dict[str, Any] | None):
    if spec is None:
        return None

    clock = RuntimeClock()
    clock.sim_mode = bool(spec.get("sim_mode", False))
    clock.speed = float(spec.get("speed", 1.0))
    clock._anchor_mono = float(spec["anchor_mono"])
    clock._anchor_utc = datetime.fromisoformat(str(spec["anchor_utc"]))
    if clock.sim_mode:
        clock.virt_start = clock._anchor_utc
    return clock


def _safe_send(conn: Connection, payload: dict[str, Any]) -> None:
    try:
        conn.send(payload)
    except (BrokenPipeError, EOFError, OSError):
        pass


def _camera_process_main(
    conn: Connection,
    rig_id: int,
    camera_entry: dict[str, Any],
    clock_spec: dict[str, Any] | None,
    call_timeout_s: float,
) -> None:
    """Own the real CameraWorker and its hardware objects in the child."""

    prepared: dict[str, PreparedCapture] = {}

    def log(message) -> None:
        _safe_send(
            conn,
            {
                "kind": "log",
                "message": str(message),
            },
        )

    worker = CameraWorker(
        rig_id=rig_id,
        clock=_restore_clock(clock_spec),
        log_fn=log,
        call_timeout_s=call_timeout_s,
    )
    worker.configure_camera(camera_entry)
    worker.start()

    _safe_send(conn, {"kind": "ready"})

    try:
        while True:
            try:
                request = conn.recv()
            except (EOFError, OSError):
                break

            if not isinstance(request, dict):
                continue

            operation = request.get("operation")
            if operation == "__stop__":
                break

            args = tuple(request.get("args", ()))
            kwargs = dict(request.get("kwargs", {}))

            try:
                if operation == "prepare_capture":
                    result = worker.prepare_capture(*args, **kwargs)

                    token_id = secrets.token_urlsafe(24)
                    prepared[token_id] = result

                    result = PreparedCapture(
                        token=token_id,
                        estimated_total_s=result.estimated_total_s,
                        exposures_s=result.exposures_s,
                        planned_count=result.planned_count,
                        plugin_name=result.plugin_name,
                        materialized=result.materialized,
                    )

                elif operation == "trigger_prepared":
                    if not args:
                        raise WorkerUnavailableError(
                            "prepared capture token is missing"
                        )

                    proxy_prepared = args[0]
                    token_id = getattr(proxy_prepared, "token", None)

                    real_prepared = prepared.pop(str(token_id), None)
                    if real_prepared is None:
                        raise WorkerUnavailableError(
                            "prepared capture belongs to a previous "
                            "camera worker generation"
                        )

                    result = worker.trigger_prepared(
                        real_prepared,
                        *args[1:],
                        **kwargs,
                    )

                elif operation == "discard_prepared":
                    if not args:
                        raise WorkerUnavailableError(
                            "prepared capture token is missing"
                        )
                    proxy_prepared = args[0]
                    token_id = getattr(proxy_prepared, "token", None)
                    result = prepared.pop(str(token_id), None) is not None

                else:
                    method = getattr(worker, str(operation))
                    result = method(*args, **kwargs)

                _safe_send(
                    conn,
                    {
                        "kind": "result",
                        "value": result,
                    },
                )

            except BaseException as exc:
                _safe_send(
                    conn,
                    {
                        "kind": "error",
                        "class": type(exc).__name__,
                        "code": getattr(exc, "code", None),
                        "message": str(exc),
                    },
                )

    finally:
        # Never allow graceful child cleanup to become another unbounded wait.
        try:
            worker.stop(timeout=0.25)
        except BaseException:
            pass
        try:
            conn.close()
        except OSError:
            pass


class ProcessCameraWorker:
    """Parent-side CameraWorker-compatible supervised process proxy."""

    _REMOTE_METHODS = frozenset(
        {
            "connect",
            "init_settings",
            "set_exposure_settings",
            "apply_phase_settings",
            "prepare_capture",
            "trigger_prepared",
            "discard_prepared",
            "shoot_speed_list",
            "preflight",
            "get_parameter",
            "set_parameter",
            "execute_photo",
            "get_battery_level",
            "read_info",
            "sync_datetime",
            "probe_info",
            "test_photo",
            "test_photo_fast",
            "test_photo_diagnostic",
        }
    )

    def __init__(
        self,
        rig_id: int,
        service_factory=None,
        log_fn=print,
        clock=None,
        shutdown_policy: str = "drain",
        max_queue_size: int | None = None,
        call_timeout_s: float = 30.0,
        process_target=None,
    ) -> None:
        if service_factory is not None:
            raise ValueError(
                "ProcessCameraWorker reconstructs CameraService in the child; "
                "service_factory injection is not supported"
            )

        self.rig_id = int(rig_id)
        self._log = log_fn
        self._clock = clock
        self._call_timeout_s = max(0.001, float(call_timeout_s))
        self._transport_grace_s = 2.0
        self._camera_entry: dict[str, Any] | None = None

        self._ctx = multiprocessing.get_context("spawn")
        self._process_target = process_target or _camera_process_main

        self._process = None
        self._conn: Connection | None = None
        self._started = False
        self._lock = threading.RLock()
        self._generation = 0
        self._last_failure: str | None = None

    @property
    def running(self) -> bool:
        with self._lock:
            return bool(
                self._process is not None
                and self._process.is_alive()
            )

    @property
    def healthy(self) -> bool:
        with self._lock:
            if not self._started:
                return True
            if self._process is None:
                # A killed generation is recoverable on the next command.
                return True
            return self._process.is_alive()

    @property
    def generation(self) -> int:
        with self._lock:
            return self._generation

    def configure_camera(self, camera_entry: dict) -> None:
        with self._lock:
            if self._process is not None:
                raise RuntimeError(
                    "camera process worker is already initialized"
                )
            self._camera_entry = dict(camera_entry)

    def start(self) -> None:
        with self._lock:
            self._started = True
            self._ensure_process_locked()

    def stop(self, timeout: float | None = 2.0) -> bool:
        effective_timeout = 2.0 if timeout is None else max(
            0.0, float(timeout)
        )

        with self._lock:
            self._started = False
            process = self._process
            conn = self._conn

            if process is None:
                self._close_connection_locked()
                return True

            if process.is_alive() and conn is not None:
                try:
                    conn.send(
                        {
                            "operation": "__stop__",
                            "args": (),
                            "kwargs": {},
                        }
                    )
                except (BrokenPipeError, EOFError, OSError):
                    pass

            process.join(effective_timeout)

            if process.is_alive():
                self._terminate_process_locked(process)

            stopped = not process.is_alive()
            self._process = None
            self._close_connection_locked()
            return stopped

    def _ensure_process_locked(self) -> None:
        if not self._started:
            raise WorkerUnavailableError(
                f"camera process worker for rig {self.rig_id} is stopped"
            )

        if self._camera_entry is None:
            raise RuntimeError("camera configuration is not set")

        if self._process is not None and self._process.is_alive():
            return

        self._discard_dead_process_locked()

        parent_conn, child_conn = self._ctx.Pipe(duplex=True)

        process = self._ctx.Process(
            target=self._process_target,
            args=(
                child_conn,
                self.rig_id,
                dict(self._camera_entry),
                _clock_snapshot(self._clock),
                self._call_timeout_s,
            ),
            name=f"camera-rig-{self.rig_id}",
            daemon=True,
        )

        process.start()
        child_conn.close()

        self._process = process
        self._conn = parent_conn
        self._generation += 1

        deadline = time.monotonic() + _READY_TIMEOUT_S

        while time.monotonic() < deadline:
            if not process.is_alive():
                self._last_failure = (
                    f"camera child exited during startup "
                    f"(exitcode={process.exitcode})"
                )
                self._discard_dead_process_locked()
                raise WorkerUnavailableError(self._last_failure)

            remaining = max(0.0, deadline - time.monotonic())
            if not parent_conn.poll(min(0.1, remaining)):
                continue

            try:
                message = parent_conn.recv()
            except (EOFError, OSError) as exc:
                self._discard_dead_process_locked()
                raise WorkerUnavailableError(
                    "camera child IPC closed during startup"
                ) from exc

            if not isinstance(message, dict):
                continue

            if message.get("kind") == "log":
                self._emit_log(message.get("message"))
                continue

            if message.get("kind") == "ready":
                self._last_failure = None
                return

        self._last_failure = "camera child startup timed out"
        self._kill_current_locked()
        raise WorkerUnavailableError(self._last_failure)

    def _remote_call(self, operation: str, *args, **kwargs):
        with self._lock:
            self._ensure_process_locked()

            process = self._process
            conn = self._conn
            assert process is not None
            assert conn is not None

            try:
                conn.send(
                    {
                        "operation": operation,
                        "args": args,
                        "kwargs": kwargs,
                    }
                )
            except (BrokenPipeError, EOFError, OSError) as exc:
                self._last_failure = (
                    f"camera child IPC send failed during {operation}"
                )
                self._kill_current_locked()
                raise WorkerUnavailableError(self._last_failure) from exc

            operation_timeout_s = camera_operation_timeout_s(
                operation,
                args,
                kwargs,
                self._call_timeout_s,
            )
            response_timeout_s = (
                operation_timeout_s + self._transport_grace_s
            )
            deadline = time.monotonic() + response_timeout_s

            while time.monotonic() < deadline:
                if not process.is_alive():
                    self._last_failure = (
                        f"camera child exited during {operation} "
                        f"(exitcode={process.exitcode})"
                    )
                    self._discard_dead_process_locked()
                    raise WorkerUnavailableError(self._last_failure)

                remaining = max(0.0, deadline - time.monotonic())
                if not conn.poll(min(0.1, remaining)):
                    continue

                try:
                    message = conn.recv()
                except (EOFError, OSError) as exc:
                    self._last_failure = (
                        f"camera child IPC closed during {operation}"
                    )
                    self._kill_current_locked()
                    raise WorkerUnavailableError(
                        self._last_failure
                    ) from exc

                if not isinstance(message, dict):
                    continue

                kind = message.get("kind")

                if kind == "log":
                    self._emit_log(message.get("message"))
                    continue

                if kind == "result":
                    return message.get("value")

                if kind == "error":
                    self._raise_remote_error_locked(
                        operation,
                        message,
                    )

            # Parent watchdog: the child did not even return its own worker
            # timeout.  Kill it.  The command is NOT replayed.
            self._last_failure = (
                f"camera process for rig {self.rig_id} timed out during "
                f"{operation}"
            )
            self._kill_current_locked()
            raise WorkerTimeoutError(
                "camera",
                self.rig_id,
                operation,
                response_timeout_s,
            )

    def _raise_remote_error_locked(
        self,
        operation: str,
        message: dict[str, Any],
    ) -> None:
        code = message.get("code")
        class_name = str(message.get("class") or "")
        text = str(message.get("message") or "camera child error")

        if code == "WORKER_TIMEOUT":
            # The child GenericWorker timed out while its hardware thread may
            # still be blocked.  Kill the entire process immediately.
            self._last_failure = text
            self._kill_current_locked()
            raise WorkerTimeoutError(
                "camera",
                self.rig_id,
                operation,
                self._call_timeout_s,
            )

        if code == "WORKER_UNAVAILABLE":
            self._last_failure = text
            self._kill_current_locked()
            raise WorkerUnavailableError(text)

        if code == "EXPIRED" or class_name == "ExpiredJobError":
            raise ExpiredJobError(text)

        if class_name == "BusyDeviceError":
            raise BusyDeviceError(text)

        raise RuntimeError(
            f"camera child {class_name or 'error'}: {text}"
        )

    def _emit_log(self, message) -> None:
        try:
            self._log(str(message))
        except Exception:
            pass

    def _terminate_process_locked(self, process) -> None:
        if process.is_alive():
            process.terminate()
            process.join(_KILL_GRACE_S)

        if process.is_alive():
            process.kill()
            process.join(_KILL_GRACE_S)

    def _kill_current_locked(self) -> None:
        process = self._process
        if process is not None:
            self._terminate_process_locked(process)
        self._process = None
        self._close_connection_locked()

    def _discard_dead_process_locked(self) -> None:
        process = self._process
        if process is not None:
            try:
                process.join(timeout=0)
            except Exception:
                pass
        self._process = None
        self._close_connection_locked()

    def _close_connection_locked(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            try:
                conn.close()
            except OSError:
                pass

    def __getattr__(self, name: str):
        if name not in self._REMOTE_METHODS:
            raise AttributeError(name)

        def remote(*args, **kwargs):
            return self._remote_call(name, *args, **kwargs)

        return remote


__all__ = [
    "ProcessCameraWorker",
]
