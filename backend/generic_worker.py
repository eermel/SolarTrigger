"""Reusable single-threaded worker with explicit shutdown semantics."""

from __future__ import annotations

import queue
import threading
from concurrent.futures import Future
from datetime import datetime, timezone
from typing import Callable


_STOP = object()


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
        self._queue: queue.Queue = queue.Queue(maxsize=max_queue_size or 0)
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._accepting = True
        self._close_called = False
        self._last_error: dict | None = None

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

    def submit(self, callable, *args, **kwargs) -> Future:
        """Queue a callable and return a Future representing its execution."""

        future = Future()
        with self._lock:
            if not self._accepting:
                raise RuntimeError("worker is stopping and no longer accepts jobs")
            self._queue.put_nowait((future, callable, args, kwargs))
        return future

    def stop(self, timeout: float | None = None) -> None:
        """Stop accepting work and wait for the configured shutdown behavior."""

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
        else:
            if wake_worker:
                self._queue.put(_STOP)
            if thread is threading.current_thread():
                return
            thread.join(timeout)

    def _run(self) -> None:
        try:
            while True:
                job = self._queue.get()
                try:
                    if job is _STOP:
                        return
                    future, func, args, kwargs = job
                    if not future.set_running_or_notify_cancel():
                        continue
                    try:
                        future.set_result(func(*args, **kwargs))
                    except BaseException as exc:
                        future.set_exception(exc)
                        self._record_error(exc)
                finally:
                    self._queue.task_done()
        finally:
            self._close_device_once()

    def _cancel_queued_jobs(self) -> None:
        while True:
            try:
                job = self._queue.get_nowait()
            except queue.Empty:
                return
            try:
                if job is not _STOP:
                    job[0].cancel()
            finally:
                self._queue.task_done()

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
