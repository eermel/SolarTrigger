"""Reusable single-threaded worker with explicit shutdown semantics."""

from __future__ import annotations

import queue
import threading
import time
from concurrent.futures import Future
from datetime import datetime, timezone
from itertools import count
from typing import Callable


_STOP = object()

PRIORITY_STOP = 0
PRIORITY_SEQUENCER = 10
PRIORITY_MANUAL = 30
PRIORITY_DIAGNOSTIC = 90


class BusyDeviceError(RuntimeError):
    """Raised when diagnostic work is rejected by a busy worker."""


class ExpiredJobError(RuntimeError):
    """Raised when a job reaches its deadline before execution."""

    code = "EXPIRED"


class WorkerTimeoutError(RuntimeError):
    """Raised when a hardware worker call exceeds its bounded wait."""

    code = "WORKER_TIMEOUT"

    def __init__(
        self,
        device_kind: str,
        rig_id: int,
        operation: str,
        timeout_s: float,
    ) -> None:
        self.device_kind = device_kind
        self.rig_id = rig_id
        self.operation = operation
        self.timeout_s = float(timeout_s)
        super().__init__(
            f"{device_kind} worker for rig {rig_id} timed out during "
            f"{operation} after {self.timeout_s:.3f}s"
        )


class WorkerUnavailableError(RuntimeError):
    """Raised after a worker has been isolated following a hang."""

    code = "WORKER_UNAVAILABLE"


class GenericWorker:
    """Execute submitted callables serially on one persistent daemon thread."""

    def __init__(
        self,
        rig_id: int,
        device_kind: str,
        log_fn=print,
        shutdown_policy: str = "drain",
        device_close: Callable[[], None] | None = None,
        max_queue_size: int | None = None,
    ) -> None:
        if shutdown_policy not in {"drain", "cancel_pending"}:
            raise ValueError(
                "shutdown_policy must be 'drain' or 'cancel_pending'"
            )
        if max_queue_size is not None and max_queue_size < 1:
            raise ValueError("max_queue_size must be positive")

        self.rig_id = rig_id
        self.device_kind = device_kind
        self._log_fn = log_fn
        self._shutdown_policy = shutdown_policy
        self._device_close = device_close
        self._queue: queue.PriorityQueue = queue.PriorityQueue(
            maxsize=max_queue_size or 0
        )
        self._sequence = count()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._accepting = True
        self._executing_priority: int | None = None
        self._close_called = False
        self._last_error: dict | None = None
        self._unhealthy_reason: str | None = None

    @property
    def running(self) -> bool:
        """Whether the worker thread is currently alive."""

        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    @property
    def last_error(self) -> dict | None:
        """Return a snapshot of the most recently observed job error."""

        with self._lock:
            return None if self._last_error is None else dict(self._last_error)

    @property
    def healthy(self) -> bool:
        """Whether this worker may still accept new device operations."""

        with self._lock:
            return self._unhealthy_reason is None

    @property
    def unhealthy_reason(self) -> str | None:
        with self._lock:
            return self._unhealthy_reason

    def mark_unhealthy(self, reason: str) -> None:
        """Fail closed after a timed-out hardware operation."""

        text = str(reason).strip() or "worker unavailable"
        with self._lock:
            if self._unhealthy_reason is None:
                self._unhealthy_reason = text
            self._accepting = False
            self._cancel_queued_jobs(preserve_stop=True)
        try:
            self._log_fn(
                f"{self.device_kind} worker for rig {self.rig_id} "
                f"marked unavailable: {text}"
            )
        except Exception:
            pass

    def start(self) -> None:
        """Create and start the worker thread once."""

        with self._lock:
            if self._thread is not None:
                if self._thread.is_alive():
                    return
                raise RuntimeError("worker cannot be restarted after it has stopped")
            if not self._accepting:
                raise RuntimeError("worker has already been stopped")

            self._thread = threading.Thread(
                target=self._run,
                name=f"{self.device_kind}-worker-r{self.rig_id}",
                daemon=True,
            )
            self._thread.start()

    def submit(
        self,
        callable,
        *args,
        worker_deadline: float | None = None,
        purpose: str | None = None,
        **kwargs,
    ) -> Future:
        """Queue a callable and return a Future representing its execution."""

        return self.submit_with_priority(
            PRIORITY_MANUAL,
            callable,
            *args,
            worker_deadline=worker_deadline,
            purpose=purpose,
            **kwargs,
        )

    def submit_with_priority(
        self,
        priority: int,
        callable,
        *args,
        reject_if_busy: bool = False,
        worker_deadline: float | None = None,
        purpose: str | None = None,
        **kwargs,
    ) -> Future:
        """Queue a callable at a priority, optionally rejecting busy work."""

        if not isinstance(priority, int):
            raise TypeError("priority must be an int")
        future = Future()
        with self._lock:
            if self._unhealthy_reason is not None:
                raise WorkerUnavailableError(
                    f"{self.device_kind} worker for rig {self.rig_id} is unavailable: "
                    f"{self._unhealthy_reason}"
                )
            if not self._accepting:
                raise RuntimeError("worker is stopping and no longer accepts jobs")
            if reject_if_busy and (
                self._executing_priority is not None
                or self._has_queued_higher_priority_work()
            ):
                raise BusyDeviceError(
                    f"{self.device_kind} worker for rig {self.rig_id} is busy"
                )
            job = (future, callable, args, kwargs, worker_deadline, purpose)
            self._queue.put_nowait((priority, next(self._sequence), job))
        return future

    def stop(self, timeout: float | None = 2.0) -> bool:
        """Stop accepting work without ever waiting indefinitely.

        Returns ``True`` when the worker thread stopped. ``False`` means an
        in-flight hardware call is still stuck and has been isolated.
        """

        if timeout is None:
            timeout = 2.0
        timeout = max(0.0, float(timeout))

        wake_worker = False
        with self._lock:
            if self._accepting:
                self._accepting = False
                if self._shutdown_policy == "cancel_pending":
                    self._cancel_queued_jobs()
                wake_worker = self._thread is not None
            thread = self._thread

        if thread is None:
            self._cancel_jobs_without_worker()
            self._close_device_once()
            return True

        if wake_worker:
            try:
                self._queue.put_nowait(
                    (PRIORITY_STOP, next(self._sequence), _STOP)
                )
            except queue.Full:
                # Shutdown itself must never become another unbounded wait.
                pass

        if thread is threading.current_thread():
            return False

        thread.join(timeout)
        stopped = not thread.is_alive()
        if not stopped:
            self.mark_unhealthy(
                f"shutdown timed out after {timeout:.3f}s"
            )
        return stopped

    def _run(self) -> None:
        try:
            stop_requested = False
            while True:
                priority, _sequence, job = self._queue.get()
                try:
                    if job is _STOP:
                        stop_requested = True
                    else:
                        future, func, args, kwargs, worker_deadline, purpose = job
                        with self._lock:
                            self._executing_priority = priority
                        if future.set_running_or_notify_cancel():
                            if (
                                worker_deadline is not None
                                and time.monotonic() >= worker_deadline
                            ):
                                future.set_exception(ExpiredJobError())
                                try:
                                    self._log_fn(
                                        f"{self.device_kind} worker for rig "
                                        f"{self.rig_id} skipped expired job: "
                                        f"{purpose or 'unspecified'}"
                                    )
                                except Exception:
                                    pass
                            else:
                                try:
                                    future.set_result(func(*args, **kwargs))
                                except BaseException as exc:
                                    future.set_exception(exc)
                                    self._record_error(exc)
                finally:
                    with self._lock:
                        self._executing_priority = None
                    self._queue.task_done()
                with self._lock:
                    stopping = not self._accepting
                if (stop_requested or stopping) and self._queue.empty():
                    return
        finally:
            self._close_device_once()

    def _cancel_queued_jobs(self, *, preserve_stop: bool = False) -> None:
        preserved_stop = []
        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                break
            _priority, _sequence, job = item
            try:
                if job is _STOP:
                    if preserve_stop:
                        preserved_stop.append(item)
                else:
                    job[0].cancel()
            finally:
                self._queue.task_done()

        for item in preserved_stop:
            self._queue.put_nowait(item)

    def _cancel_jobs_without_worker(self) -> None:
        with self._lock:
            self._cancel_queued_jobs()

    def _record_error(self, exc: BaseException) -> None:
        error = {
            "rig_id": self.rig_id,
            "device_kind": self.device_kind,
            "message": str(exc),
            "when": datetime.now(timezone.utc).isoformat(),
        }
        with self._lock:
            self._last_error = error
        try:
            self._log_fn(
                f"{self.device_kind} worker for rig {self.rig_id} failed: {exc}"
            )
        except Exception:
            pass

    def _has_queued_higher_priority_work(self) -> bool:
        with self._queue.mutex:
            return any(
                priority < PRIORITY_DIAGNOSTIC and job is not _STOP
                for priority, _sequence, job in self._queue.queue
            )

    def _close_device_once(self) -> None:
        with self._lock:
            if self._close_called:
                return
            self._close_called = True
        if self._device_close is None:
            return
        try:
            self._device_close()
        except Exception as exc:
            try:
                self._log_fn(
                    f"Failed to close {self.device_kind} for rig {self.rig_id}: {exc}"
                )
            except Exception:
                pass
