"""Lifecycle owner for configured camera workers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import secrets
import threading

from backend.camera_ipc_server import CameraIpcServer
from backend.camera_worker import CameraWorker
from backend.trigger_runtime import RuntimeClock


@dataclass(frozen=True)
class CameraIpcSession:
    """Immutable lease granting access to the camera IPC server."""

    socket_path: str
    session_id: str


class CameraWorkerRuntime:
    """Own one persistent camera worker for each eligible rig."""

    def __init__(
        self,
        log_fn=print,
        clock=None,
        worker_factory=CameraWorker,
        ipc_server_factory=None,
    ) -> None:
        self._log = log_fn
        self._clock = clock or RuntimeClock()
        self._worker_factory = worker_factory
        self._ipc_server_factory = ipc_server_factory or CameraIpcServer
        self._ipc_server = None
        self._ipc_session_ids: set[str] = set()
        self._registry: dict[int, CameraWorker] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _eligible_rig_ids(config: dict) -> set[int]:
        rig_ids: set[int] = set()
        for rig in config.get("rigs", []):
            if not isinstance(rig, dict) or rig.get("enabled") is not True:
                continue
            devices = rig.get("devices")
            camera = devices.get("camera") if isinstance(devices, dict) else None
            if not isinstance(camera, dict):
                continue
            raw_backend = camera.get("backend")
            if not isinstance(raw_backend, str):
                continue
            backend = raw_backend.strip().lower()
            if not backend or backend in {"none", "external"}:
                continue
            rig_id = rig.get("rig_id")
            if isinstance(rig_id, int) and not isinstance(rig_id, bool):
                rig_ids.add(rig_id)
        return rig_ids

    def reconcile(self, config: dict) -> None:
        """Reconcile persistent workers against the current rig configuration."""

        desired = self._eligible_rig_ids(config)
        with self._lock:
            created: dict[int, CameraWorker] = {}
            try:
                for rig_id in desired - self._registry.keys():
                    worker = self._worker_factory(
                        rig_id=rig_id,
                        clock=self._clock,
                        log_fn=self._log,
                    )
                    created[rig_id] = worker
                    worker.start()
            except BaseException:
                for worker in created.values():
                    try:
                        worker.stop()
                    except Exception:
                        pass
                raise

            obsolete = [
                worker
                for rig_id, worker in self._registry.items()
                if rig_id not in desired
            ]
            self._registry = {
                rig_id: (
                    self._registry[rig_id]
                    if rig_id in self._registry
                    else created[rig_id]
                )
                for rig_id in desired
            }
            for worker in obsolete:
                worker.stop()

    def get_for_rig(self, rig_id: int) -> CameraWorker | None:
        """Return the persistent worker for *rig_id*, if configured."""

        with self._lock:
            return self._registry.get(rig_id)

    def active_camera_rig_ids(self) -> tuple[int, ...]:
        """Return an ascending snapshot of active camera rig identifiers."""

        with self._lock:
            return tuple(sorted(self._registry))

    def open_ipc_session(self) -> CameraIpcSession:
        """Start IPC lazily and return a lease for the active camera workers."""

        with self._lock:
            if not self._registry:
                raise RuntimeError("cannot open camera IPC without active camera rigs")

            server = self._ipc_server
            if server is None:
                server = self._ipc_server_factory(
                    self,
                    clock=self._clock,
                    log_fn=self._log,
                )
                server.start()
                self._ipc_server = server

            session_id = secrets.token_urlsafe(24)
            try:
                server.activate_session(session_id)
            except BaseException:
                if not self._ipc_session_ids:
                    server.stop()
                    self._ipc_server = None
                raise
            self._ipc_session_ids.add(session_id)
            socket_path = str(Path(server.socket_path).absolute())
            return CameraIpcSession(
                socket_path=socket_path,
                session_id=session_id,
            )

    def close_ipc_session(self, session_id: str) -> None:
        """Revoke an IPC lease and stop the server after its final session."""

        with self._lock:
            if session_id not in self._ipc_session_ids or self._ipc_server is None:
                raise ValueError("camera IPC session is not active")
            server = self._ipc_server
            server.revoke_session(session_id)
            self._ipc_session_ids.remove(session_id)
            if not self._ipc_session_ids:
                server.stop()
                self._ipc_server = None

    def shutdown(self) -> None:
        """Stop IPC, purge its sessions, and then stop every camera worker."""

        with self._lock:
            server, self._ipc_server = self._ipc_server, None
            self._ipc_session_ids.clear()
            if server is not None:
                server.stop()

            workers = tuple(self._registry.values())
            self._registry.clear()
            for worker in workers:
                worker.stop()


_camera_worker_runtime: CameraWorkerRuntime | None = None
_camera_worker_runtime_lock = threading.Lock()


def get_camera_worker_runtime(log_fn=print) -> CameraWorkerRuntime:
    """Return the process-wide camera worker runtime singleton."""

    global _camera_worker_runtime

    with _camera_worker_runtime_lock:
        if _camera_worker_runtime is None:
            _camera_worker_runtime = CameraWorkerRuntime(log_fn=log_fn)
        return _camera_worker_runtime


__all__ = [
    "CameraIpcSession",
    "CameraWorkerRuntime",
    "get_camera_worker_runtime",
]
