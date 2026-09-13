"""Reusable supervised process boundary for non-camera hardware."""

from __future__ import annotations

import multiprocessing
import threading
import time
from multiprocessing.connection import Connection
from typing import Any

from backend.generic_worker import (
    BusyDeviceError,
    ExpiredJobError,
    WorkerTimeoutError,
    WorkerUnavailableError,
)


READY_TIMEOUT_S = 10.0
KILL_GRACE_S = 0.5
TRANSPORT_GRACE_S = 2.0


def safe_send(conn: Connection, payload: dict[str, Any]) -> None:
    try:
        conn.send(payload)
    except (BrokenPipeError, EOFError, OSError):
        pass


def error_payload(exc: BaseException) -> dict[str, Any]:
    payload = {
        "kind": "error",
        "module": type(exc).__module__,
        "class": type(exc).__name__,
        "code": getattr(exc, "code", None),
        "message": str(exc),
    }

    # Preserve structured INDI information when present without importing
    # mount-specific code into this generic module.
    for name in ("command", "returncode", "stderr"):
        if hasattr(exc, name):
            payload[name] = getattr(exc, name)

    return payload


def serve_worker(
    conn: Connection,
    worker,
    *,
    allowed_operations: set[str] | frozenset[str],
) -> None:
    """Serve serialized calls for one already constructed device worker."""

    worker.start()
    safe_send(conn, {"kind": "ready"})

    try:
        while True:
            try:
                request = conn.recv()
            except (EOFError, OSError):
                break

            if not isinstance(request, dict):
                continue

            operation = request.get("operation")

            if operation == "__shutdown__":
                break

            if operation not in allowed_operations:
                safe_send(
                    conn,
                    error_payload(
                        RuntimeError(
                            f"unsupported device operation: {operation}"
                        )
                    ),
                )
                continue

            args = tuple(request.get("args", ()))
            kwargs = dict(request.get("kwargs", {}))

            try:
                method = getattr(worker, str(operation), None)

                if callable(method):
                    result = method(*args, **kwargs)
                else:
                    # Some service operations are intentionally not explicit
                    # worker methods (e.g. focuser active_step / set_mode).
                    result = worker._call(
                        str(operation),
                        *args,
                        **kwargs,
                    )

                safe_send(
                    conn,
                    {
                        "kind": "result",
                        "value": result,
                    },
                )

            except BaseException as exc:
                safe_send(conn, error_payload(exc))

    finally:
        try:
            worker.shutdown(timeout=0.25)
        except BaseException:
            pass
        try:
            conn.close()
        except OSError:
            pass


class SupervisedDeviceProcess:
    """Parent-side bounded process supervisor.

    A timed-out command is never replayed.  The failing child is killed, and
    only a later command may start a fresh generation.
    """

    def __init__(
        self,
        *,
        rig_id: int,
        device_kind: str,
        process_spec: dict[str, Any],
        process_target,
        call_timeout_s: float,
        log_fn=print,
    ) -> None:
        self.rig_id = int(rig_id)
        self.device_kind = str(device_kind)
        self._process_spec = dict(process_spec)
        self._process_target = process_target
        self._call_timeout_s = max(0.001, float(call_timeout_s))
        self._log = log_fn

        self._ctx = multiprocessing.get_context("spawn")
        self._process = None
        self._conn: Connection | None = None
        self._started = False
        self._generation = 0
        self._last_error: dict[str, Any] | None = None
        self._lock = threading.RLock()

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
            # A killed generation is recoverable: next command creates a new
            # process. "healthy" therefore means the supervisor can continue.
            return self._started

    @property
    def generation(self) -> int:
        with self._lock:
            return self._generation

    @property
    def last_error(self) -> dict[str, Any] | None:
        with self._lock:
            return (
                None
                if self._last_error is None
                else dict(self._last_error)
            )

    def start(self) -> None:
        with self._lock:
            self._started = True
            self._ensure_process_locked()

    def shutdown(self, timeout: float | None = 2.0) -> bool:
        effective_timeout = (
            2.0
            if timeout is None
            else max(0.0, float(timeout))
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
                            "operation": "__shutdown__",
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

    def call(self, operation: str, *args, **kwargs):
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
                message = (
                    f"{self.device_kind} child IPC send failed "
                    f"during {operation}"
                )
                self._record_error(
                    "DEVICE_UNAVAILABLE",
                    message,
                    operation,
                )
                self._kill_current_locked()
                raise WorkerUnavailableError(message) from exc

            response_timeout = (
                self._call_timeout_s + TRANSPORT_GRACE_S
            )
            deadline = time.monotonic() + response_timeout

            while time.monotonic() < deadline:
                if not process.is_alive():
                    message = (
                        f"{self.device_kind} child exited during "
                        f"{operation} (exitcode={process.exitcode})"
                    )
                    self._record_error(
                        "DEVICE_UNAVAILABLE",
                        message,
                        operation,
                    )
                    self._discard_dead_process_locked()
                    raise WorkerUnavailableError(message)

                remaining = max(
                    0.0,
                    deadline - time.monotonic(),
                )

                if not conn.poll(min(0.1, remaining)):
                    continue

                try:
                    response = conn.recv()
                except (EOFError, OSError) as exc:
                    message = (
                        f"{self.device_kind} child IPC closed "
                        f"during {operation}"
                    )
                    self._record_error(
                        "DEVICE_UNAVAILABLE",
                        message,
                        operation,
                    )
                    self._kill_current_locked()
                    raise WorkerUnavailableError(message) from exc

                if not isinstance(response, dict):
                    continue

                kind = response.get("kind")

                if kind == "log":
                    self._emit_log(response.get("message"))
                    continue

                if kind == "result":
                    self._last_error = None
                    return response.get("value")

                if kind == "error":
                    self._raise_remote_error_locked(
                        operation,
                        response,
                    )

            message = (
                f"{self.device_kind} process for rig {self.rig_id} "
                f"timed out during {operation}"
            )
            self._record_error(
                "WORKER_TIMEOUT",
                message,
                operation,
            )
            self._kill_current_locked()

            raise WorkerTimeoutError(
                self.device_kind,
                self.rig_id,
                operation,
                response_timeout,
            )

    def _ensure_process_locked(self) -> None:
        if not self._started:
            raise WorkerUnavailableError(
                f"{self.device_kind} process worker for "
                f"rig {self.rig_id} is stopped"
            )

        if (
            self._process is not None
            and self._process.is_alive()
        ):
            return

        self._discard_dead_process_locked()

        parent_conn, child_conn = self._ctx.Pipe(duplex=True)

        process = self._ctx.Process(
            target=self._process_target,
            args=(
                child_conn,
                dict(self._process_spec),
                self._call_timeout_s,
            ),
            name=f"{self.device_kind}-rig-{self.rig_id}",
            daemon=True,
        )

        process.start()
        child_conn.close()

        self._process = process
        self._conn = parent_conn
        self._generation += 1

        deadline = time.monotonic() + READY_TIMEOUT_S

        while time.monotonic() < deadline:
            if not process.is_alive():
                message = (
                    f"{self.device_kind} child exited during startup "
                    f"(exitcode={process.exitcode})"
                )
                self._record_error(
                    "DEVICE_UNAVAILABLE",
                    message,
                    "startup",
                )
                self._discard_dead_process_locked()
                raise WorkerUnavailableError(message)

            remaining = max(
                0.0,
                deadline - time.monotonic(),
            )

            if not parent_conn.poll(min(0.1, remaining)):
                continue

            try:
                response = parent_conn.recv()
            except (EOFError, OSError) as exc:
                self._discard_dead_process_locked()
                raise WorkerUnavailableError(
                    f"{self.device_kind} child IPC closed "
                    "during startup"
                ) from exc

            if not isinstance(response, dict):
                continue

            if response.get("kind") == "log":
                self._emit_log(response.get("message"))
                continue

            if response.get("kind") == "ready":
                self._last_error = None
                return

            if response.get("kind") == "error":
                self._raise_remote_error_locked(
                    "startup",
                    response,
                )

        message = (
            f"{self.device_kind} child startup timed out"
        )
        self._record_error(
            "DEVICE_UNAVAILABLE",
            message,
            "startup",
        )
        self._kill_current_locked()
        raise WorkerUnavailableError(message)

    def _raise_remote_error_locked(
        self,
        operation: str,
        response: dict[str, Any],
    ) -> None:
        class_name = str(response.get("class") or "")
        code = response.get("code")
        message = str(
            response.get("message")
            or f"{self.device_kind} child error"
        )

        self._record_error(
            code or class_name or "REMOTE_ERROR",
            message,
            operation,
        )

        if code == "WORKER_TIMEOUT":
            self._kill_current_locked()
            raise WorkerTimeoutError(
                self.device_kind,
                self.rig_id,
                operation,
                self._call_timeout_s,
            )

        if code == "WORKER_UNAVAILABLE":
            self._kill_current_locked()
            raise WorkerUnavailableError(message)

        if code == "EXPIRED" or class_name == "ExpiredJobError":
            raise ExpiredJobError(message)

        if class_name == "BusyDeviceError":
            raise BusyDeviceError(message)

        mapped = self.remote_exception(response)
        if mapped is not None:
            raise mapped

        if class_name == "ValueError":
            raise ValueError(message)

        raise RuntimeError(message)

    def remote_exception(
        self,
        response: dict[str, Any],
    ) -> BaseException | None:
        """Subclass hook for device-specific structured errors."""

        return None

    def _record_error(
        self,
        code: str,
        message: str,
        operation: str,
    ) -> None:
        self._last_error = {
            "code": str(code),
            "message": str(message),
            "operation": str(operation),
        }

    def _emit_log(self, message) -> None:
        try:
            self._log(str(message))
        except Exception:
            pass

    def _terminate_process_locked(self, process) -> None:
        if process.is_alive():
            process.terminate()
            process.join(KILL_GRACE_S)

        if process.is_alive():
            process.kill()
            process.join(KILL_GRACE_S)

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
