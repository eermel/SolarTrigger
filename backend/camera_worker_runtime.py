"""Lifecycle owner for configured camera workers."""

from __future__ import annotations

import threading

from backend.camera_worker import CameraWorker


class CameraWorkerRuntime:
    """Own one persistent camera worker for each eligible rig."""

    def __init__(self, log_fn=print) -> None:
        self._log = log_fn
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
                    worker = CameraWorker(rig_id=rig_id, log_fn=self._log)
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


_camera_worker_runtime: CameraWorkerRuntime | None = None
_camera_worker_runtime_lock = threading.Lock()


def get_camera_worker_runtime(log_fn=print) -> CameraWorkerRuntime:
    """Return the process-wide camera worker runtime singleton."""

    global _camera_worker_runtime

    with _camera_worker_runtime_lock:
        if _camera_worker_runtime is None:
            _camera_worker_runtime = CameraWorkerRuntime(log_fn=log_fn)
        return _camera_worker_runtime


__all__ = ["CameraWorkerRuntime", "get_camera_worker_runtime"]
