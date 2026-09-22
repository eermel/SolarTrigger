"""Lifecycle owner for configured camera workers."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
import inspect
import secrets
import threading

from backend.camera_ipc_server import CameraIpcServer
from backend.camera_process_worker import ProcessCameraWorker
from backend.camera_worker import CameraWorker
from backend.trigger_runtime import RuntimeClock


@dataclass(frozen=True)
class CameraIpcSession:
    """Immutable lease granting access to the camera IPC server."""

    socket_path: str
    session_id: str


def _stop_ipc_server(server, timeout: float = 2.0):
    """Stop an IPC server while preserving injected legacy/test contracts."""

    stop = server.stop
    try:
        parameters = inspect.signature(stop).parameters.values()
    except (TypeError, ValueError):
        return stop(timeout=timeout)

    accepts_timeout = any(
        parameter.name == "timeout"
        or parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters
    )

    if accepts_timeout:
        return stop(timeout=timeout)
    return stop()


def _stop_worker(worker, timeout: float = 2.0):
    """Stop a worker while preserving compatibility with legacy/test doubles.

    Production CameraWorker.stop() accepts ``timeout``. Some injected workers
    used by tests or integrations still expose the historical ``stop()``
    signature. Inspecting the signature avoids masking a TypeError raised from
    inside the worker itself.
    """

    stop = worker.stop
    try:
        parameters = inspect.signature(stop).parameters.values()
    except (TypeError, ValueError):
        return stop(timeout=timeout)

    accepts_timeout = any(
        parameter.name == "timeout"
        or parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters
    )

    if accepts_timeout:
        return stop(timeout=timeout)
    return stop()


class CameraWorkerRuntime:
    """Own one persistent camera worker for each eligible rig."""

    def __init__(
        self,
        log_fn=print,
        clock=None,
        worker_factory=ProcessCameraWorker,
        ipc_server_factory=None,
    ) -> None:
        self._log = log_fn
        self._clock = clock or RuntimeClock()
        self._worker_factory = worker_factory
        self._ipc_server_factory = ipc_server_factory or CameraIpcServer
        self._ipc_server = None
        self._ipc_session_ids: set[str] = set()
        self._ipc_session_rigs: dict[
            str, frozenset[int] | None
        ] = {}
        # A session being revoked must remain runtime-owned until the first
        # closer has finished server-side prepared-token cleanup.  Without a
        # separate closing marker, a concurrent duplicate close can observe
        # the still-active runtime lease, fail server-side after the first
        # revoke removed the session, then prematurely drop runtime ownership.
        self._ipc_closing_session_ids: set[str] = set()
        self._leased_policy_configs: dict[int, dict] = {}
        self._registry: dict[int, CameraWorker] = {}
        self._camera_entries: dict[int, dict] = {}
        self._config: dict | None = None
        self._lock = threading.RLock()

    @staticmethod
    def _eligible_camera_entries(config: dict) -> dict[int, dict]:
        entries: dict[int, dict] = {}
        for rig in config.get("rigs", []):
            if not isinstance(rig, dict):
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
                entries[rig_id] = deepcopy(camera)
        return entries

    @classmethod
    def _eligible_rig_ids(cls, config: dict) -> set[int]:
        return set(cls._eligible_camera_entries(config))

    def reconcile(self, config: dict) -> None:
        """Reconcile persistent workers against the current rig configuration."""

        desired_entries = self._eligible_camera_entries(config)
        desired = set(desired_entries)

        with self._lock:
            leased_rigs: set[int] = set()
            for scope in self._ipc_session_rigs.values():
                if scope is None:
                    leased_rigs.update(self._registry)
                else:
                    leased_rigs.update(scope)

            # A Trigger IPC lease owns the camera binding for its lifetime.
            # Reconciliation from Controls/UI must never replace or remove
            # that worker while the real-time child still holds the lease.
            for rig_id in leased_rigs:
                if self._camera_entries.get(rig_id) != desired_entries.get(rig_id):
                    raise RuntimeError(
                        f"cannot reconfigure camera for RIG {rig_id} "
                        "while a trigger IPC session is active"
                    )

            unchanged = {
                rig_id
                for rig_id in desired
                if (
                    rig_id in self._registry
                    and self._camera_entries.get(rig_id) == desired_entries[rig_id]
                )
            }

            created: dict[int, CameraWorker] = {}
            try:
                for rig_id in desired - unchanged:
                    worker = self._worker_factory(
                        rig_id=rig_id,
                        clock=self._clock,
                        log_fn=self._log,
                    )
                    configure_camera = getattr(worker, "configure_camera", None)
                    if callable(configure_camera):
                        configure_camera(desired_entries[rig_id])
                    created[rig_id] = worker
                    worker.start()
            except BaseException:
                for worker in created.values():
                    try:
                        worker.stop()
                    except Exception:
                        pass
                raise

            previous = self._registry
            obsolete = [
                worker
                for rig_id, worker in previous.items()
                if rig_id not in unchanged
            ]

            self._registry = {
                rig_id: (
                    previous[rig_id]
                    if rig_id in unchanged
                    else created[rig_id]
                )
                for rig_id in desired
            }
            self._camera_entries = deepcopy(desired_entries)
            self._config = config

        for worker in obsolete:
            _stop_worker(worker, timeout=2.0)

    def get_for_rig(self, rig_id: int) -> CameraWorker | None:
        """Return the persistent worker for *rig_id*, if configured."""

        with self._lock:
            return self._registry.get(rig_id)

    def get_policy_config_for_rig(self, rig_id: int) -> dict | None:
        """Return a policy-only configuration snapshot for an active rig."""

        with self._lock:
            frozen = self._leased_policy_configs.get(rig_id)
            if frozen is not None:
                return deepcopy(frozen)

            if rig_id not in self._registry or self._config is None:
                return None

            for rig in self._config.get("rigs", []):
                if not isinstance(rig, dict) or rig.get("rig_id") != rig_id:
                    continue

                devices = rig.get("devices")
                camera = devices.get("camera") if isinstance(devices, dict) else None
                mount = devices.get("mount") if isinstance(devices, dict) else None
                optics = rig.get("optics")
                photo = rig.get("photo")
                eclipse = self._config.get("eclipse")
                reference_site = (
                    eclipse.get("reference_site")
                    if isinstance(eclipse, dict)
                    else None
                )
                return {
                    "eclipse": {
                        "reference_site": {
                            key: (
                                reference_site.get(key)
                                if isinstance(reference_site, dict)
                                else None
                            )
                            for key in ("lat", "lon")
                        }
                    },
                    "devices": {
                        "camera": {
                            key: camera.get(key) if isinstance(camera, dict) else None
                            for key in ("backend", "manufacturer", "model", "alias")
                        },
                        "mount": {
                            key: mount.get(key) if isinstance(mount, dict) else None
                            for key in ("control", "geometry", "tracking")
                        },
                    },
                    "optics": {
                        "focal_length_mm": (
                            optics.get("focal_length_mm")
                            if isinstance(optics, dict)
                            else None
                        )
                    },
                    "photo": {
                        **{
                            key: photo.get(key) if isinstance(photo, dict) else None
                            for key in (
                                "atmos_enabled",
                                "anti_trailing_enabled",
                                "motion_tolerance_px",
                                "iso_max",
                            )
                        },
                        "iso_compensation_enabled": (
                            photo.get("iso_compensation_enabled", True)
                            if isinstance(photo, dict)
                            else True
                        ),
                    },
                }
            return None

    def active_camera_rig_ids(self) -> tuple[int, ...]:
        """Return an ascending snapshot of active camera rig identifiers."""

        with self._lock:
            return tuple(sorted(self._registry))

    def open_ipc_session(self, rig_ids=None) -> CameraIpcSession:
        """Start IPC for an explicit subset of configured camera workers.

        Without ``rig_ids`` the historical behaviour is preserved and every
        configured camera worker is exposed. Trigger execution passes an
        explicit allowlist so disabled secondary RIGs remain Controls-only.
        """

        with self._lock:
            available = set(self._registry)
            if not available:
                raise RuntimeError("cannot open camera IPC without active camera rigs")

            if rig_ids is None:
                allowed = tuple(sorted(available))
            else:
                try:
                    requested = tuple(rig_ids)
                except TypeError as exc:
                    raise ValueError("rig_ids must be iterable") from exc
                if any(
                    not isinstance(rig_id, int)
                    or isinstance(rig_id, bool)
                    or not 1 <= rig_id <= 4
                    for rig_id in requested
                ):
                    raise ValueError("rig_ids must contain integers from 1 to 4")
                allowed = tuple(sorted(set(requested)))
                if not allowed:
                    raise ValueError("rig_ids must not be empty")
                missing = set(allowed) - available
                if missing:
                    raise RuntimeError(
                        "camera worker unavailable for RIG(s): "
                        + ", ".join(str(rig_id) for rig_id in sorted(missing))
                    )

            # Keep runtime-side ownership authoritative as well as the IPC
            # server's ownership. During close_ipc_session(), revoke_session()
            # may spend time cleaning prepared state after it has already
            # removed the server-side session. A new overlapping lease must not
            # enter during that cleanup window.
            requested_scope = (
                None
                if rig_ids is None
                else frozenset(allowed)
            )
            for active_scope in self._ipc_session_rigs.values():
                if requested_scope is None or active_scope is None:
                    raise RuntimeError(
                        "another camera IPC session already owns this camera scope"
                    )
                if requested_scope & active_scope:
                    raise RuntimeError(
                        "another camera IPC session already owns one of these RIGs"
                    )

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
                if rig_ids is None:
                    server.activate_session(session_id)
                else:
                    server.activate_session(session_id, allowed)
            except BaseException:
                if not self._ipc_session_ids:
                    server.stop()
                    self._ipc_server = None
                raise
            # Freeze the policy visible to this leased RIG before any later
            # Controls/UI reconcile can replace the runtime-wide config.
            leased_ids = set(available) if rig_ids is None else set(allowed)
            for rig_id in leased_ids:
                policy = self.get_policy_config_for_rig(rig_id)
                if policy is not None:
                    self._leased_policy_configs[rig_id] = deepcopy(policy)

            self._ipc_session_ids.add(session_id)
            self._ipc_session_rigs[session_id] = requested_scope
            socket_path = str(Path(server.socket_path).absolute())
            return CameraIpcSession(
                socket_path=socket_path,
                session_id=session_id,
            )

    def close_ipc_session(self, session_id: str) -> None:
        """Revoke an IPC lease and stop the server after its final session."""

        # Keep the runtime lease registered until revoke_session() has finished
        # its best-effort prepared-token cleanup. The server removes its own
        # session before that cleanup runs, so dropping runtime ownership early
        # would let reconcile() replace the worker while stale prepared state is
        # still being discarded.
        with self._lock:
            if session_id not in self._ipc_session_ids or self._ipc_server is None:
                raise ValueError("camera IPC session is not active")
            if session_id in self._ipc_closing_session_ids:
                raise RuntimeError(
                    "camera IPC session close is already in progress"
                )
            self._ipc_closing_session_ids.add(session_id)
            server = self._ipc_server
            scope = self._ipc_session_rigs.get(session_id)

        revoke_error = None
        try:
            server.revoke_session(session_id)
        except BaseException as exc:
            revoke_error = exc

        stop_server = False
        with self._lock:
            # Runtime ownership ends only after revocation/cleanup completed.
            self._ipc_session_ids.discard(session_id)
            self._ipc_session_rigs.pop(session_id, None)
            self._ipc_closing_session_ids.discard(session_id)
            if scope is None:
                self._leased_policy_configs.clear()
            else:
                for rig_id in scope:
                    self._leased_policy_configs.pop(rig_id, None)

            # Another disjoint RIG may have opened a lease while revocation was
            # in progress. Stop only if this is still the same server and no
            # runtime leases remain.
            if self._ipc_server is server and not self._ipc_session_ids:
                self._ipc_server = None
                stop_server = True

        if stop_server:
            _stop_ipc_server(server, timeout=2.0)

        if revoke_error is not None:
            raise revoke_error

    def release_idle_workers(self) -> None:
        """Release camera USB ownership when no Trigger IPC lease exists.

        Characterization deliberately opens gphoto2 directly. Before doing so,
        the authoritative runtime must relinquish every persistent camera
        process. An active Trigger lease makes that unsafe and is rejected.
        """

        with self._lock:
            if self._ipc_session_ids or self._ipc_closing_session_ids:
                raise RuntimeError(
                    "camera runtime cannot be released while a trigger IPC session is active"
                )
            workers = tuple(self._registry.values())
            self._registry.clear()
            self._camera_entries.clear()
            self._leased_policy_configs.clear()
            self._config = None

        for worker in workers:
            _stop_worker(worker, timeout=2.0)

    def shutdown(self) -> None:
        """Stop IPC and workers without waiting under the runtime lock."""

        with self._lock:
            server, self._ipc_server = self._ipc_server, None
            self._ipc_session_ids.clear()
            self._ipc_session_rigs.clear()
            self._ipc_closing_session_ids.clear()
            self._leased_policy_configs.clear()
            workers = tuple(self._registry.values())
            self._registry.clear()

        if server is not None:
            _stop_ipc_server(server, timeout=2.0)
        for worker in workers:
            _stop_worker(worker, timeout=2.0)


_camera_worker_runtime: CameraWorkerRuntime | None = None
_camera_worker_runtime_lock = threading.Lock()


def get_camera_worker_runtime(log_fn=print):
    """Return the authoritative camera runtime for this process role.

    The systemd runtime service owns the real CameraWorkerRuntime.  Portal
    processes opt into the RPC facade with SOLARTRIGGER_RUNTIME_CLIENT=1, which
    prevents Gunicorn restarts from ever creating a second USB camera owner.
    """

    from backend.runtime_rpc import (
        get_remote_camera_worker_runtime,
        runtime_client_enabled,
    )

    if runtime_client_enabled():
        return get_remote_camera_worker_runtime()

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
