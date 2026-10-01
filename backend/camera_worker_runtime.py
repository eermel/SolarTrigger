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

    @staticmethod
    def _stop_owned_worker(worker, timeout: float = 2.0) -> tuple[bool, str | None]:
        """Stop one owned worker without ever treating an explicit False as success."""

        try:
            result = _stop_worker(worker, timeout=timeout)
        except Exception as exc:
            return False, f"{type(exc).__name__}: {exc}"
        if result is False:
            return False, "worker reported that shutdown did not complete"
        return True, None


    def _refresh_stopped_ipc_server_locked(self):
        server = self._ipc_server
        if server is not None and bool(getattr(server, "stopped", False)):
            self._ipc_server = None
            return None
        return server

    def _guard_ipc_shutdown_locked(self):
        server = self._refresh_stopped_ipc_server_locked()
        if server is not None and bool(getattr(server, "stopping", False)):
            raise RuntimeError(
                "camera IPC shutdown is still in progress; "
                "camera ownership remains frozen"
            )
        return server

    def reconcile(self, config: dict) -> None:
        """Reconcile persistent workers against the current rig configuration.

        Camera ownership is exclusive.  A replacement worker is never started
        until the previous owner for that RIG has confirmed shutdown.
        """

        desired_entries = self._eligible_camera_entries(config)
        desired = set(desired_entries)

        with self._lock:
            self._guard_ipc_shutdown_locked()
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

            previous = dict(self._registry)
            unchanged = {
                rig_id
                for rig_id in desired
                if (
                    rig_id in previous
                    and self._camera_entries.get(rig_id) == desired_entries[rig_id]
                )
            }
            obsolete = {
                rig_id: worker
                for rig_id, worker in previous.items()
                if rig_id not in unchanged
            }

            # Stop old owners first.  Starting their replacements before this
            # point can create two gphoto2 processes competing for the same USB
            # camera.  Keep every failed owner registered so later code cannot
            # mistake the USB device for released.
            stop_failures: dict[int, str] = {}
            for rig_id, worker in obsolete.items():
                stopped, detail = self._stop_owned_worker(worker, timeout=2.0)
                if not stopped:
                    stop_failures[rig_id] = detail or "shutdown failed"

            if stop_failures:
                detail = "; ".join(
                    f"RIG {rig_id}: {stop_failures[rig_id]}"
                    for rig_id in sorted(stop_failures)
                )
                raise RuntimeError(
                    "cannot reconfigure camera runtime because existing "
                    f"worker ownership was not released ({detail})"
                )

            # Every obsolete owner is now confirmed stopped.  Remove it before
            # constructing a replacement so a failed startup leaves the
            # runtime fail-closed rather than retaining a stale active binding.
            self._registry = {
                rig_id: previous[rig_id]
                for rig_id in unchanged
            }
            self._camera_entries = {
                rig_id: deepcopy(self._camera_entries[rig_id])
                for rig_id in unchanged
            }

            created: dict[int, CameraWorker] = {}
            try:
                for rig_id in sorted(desired - unchanged):
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
            except BaseException as start_exc:
                cleanup_failures: dict[int, str] = {}
                for rig_id, worker in created.items():
                    stopped, detail = self._stop_owned_worker(
                        worker,
                        timeout=2.0,
                    )
                    if not stopped:
                        cleanup_failures[rig_id] = detail or "shutdown failed"
                        # Never orphan a child which may still own the camera.
                        self._registry[rig_id] = worker
                        self._camera_entries[rig_id] = deepcopy(
                            desired_entries[rig_id]
                        )

                if cleanup_failures:
                    detail = "; ".join(
                        f"RIG {rig_id}: {cleanup_failures[rig_id]}"
                        for rig_id in sorted(cleanup_failures)
                    )
                    raise RuntimeError(
                        "camera worker startup failed and cleanup could not "
                        f"release worker ownership ({detail})"
                    ) from start_exc
                raise

            self._registry.update(created)
            self._camera_entries = deepcopy(desired_entries)
            self._config = config

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
            server = self._guard_ipc_shutdown_locked()
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

            # Freeze the policy snapshot before creating or mutating any
            # IPC lease.  Policy materialization is pure in-memory work; if it
            # fails, admission must fail without leaving a server-side session
            # that the runtime never records.
            leased_ids = set(available) if rig_ids is None else set(allowed)
            frozen_policies: dict[int, dict] = {}
            for rig_id in leased_ids:
                policy = self.get_policy_config_for_rig(rig_id)
                if policy is not None:
                    frozen_policies[rig_id] = deepcopy(policy)

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
            except BaseException as activation_exc:
                if not self._ipc_session_ids:
                    cleanup_error = None
                    try:
                        stopped = _stop_ipc_server(server, timeout=2.0)
                        if stopped is False:
                            cleanup_error = RuntimeError(
                                "camera IPC server cleanup did not drain "
                                "after session activation failed"
                            )
                    except BaseException as exc:
                        cleanup_error = exc

                    if cleanup_error is None:
                        if self._ipc_server is server:
                            self._ipc_server = None
                    else:
                        # Fail closed: keep the server authoritative until a
                        # later shutdown retry confirms that all IPC ownership
                        # has drained.  Never lose a partially stopping server.
                        self._ipc_server = server
                        raise RuntimeError(
                            "camera IPC session activation failed and server "
                            "cleanup did not complete: "
                            f"{type(cleanup_error).__name__}: {cleanup_error}"
                        ) from activation_exc
                raise

            self._leased_policy_configs.update(frozen_policies)
            self._ipc_session_ids.add(session_id)
            self._ipc_session_rigs[session_id] = requested_scope
            socket_path = str(Path(server.socket_path).absolute())
            return CameraIpcSession(
                socket_path=socket_path,
                session_id=session_id,
            )

    def mark_ipc_session_priority(
        self,
        session_id: str,
        priority: bool = True,
    ) -> None:
        """Promote an active lease into the camera IPC priority reserve."""
        with self._lock:
            if (
                session_id not in self._ipc_session_ids
                or self._ipc_server is None
            ):
                # Priority is a resilience property, not lease validity.
                # Injected/legacy session factories used by diagnostics/tests
                # may return a synthetic lease which is not registered here.
                return False
            server = self._ipc_server
        setter = getattr(server, "set_session_priority", None)
        if not callable(setter):
            # Backward compatibility for injected/legacy IPC server doubles.
            # Production CameraIpcServer always exposes this capability.
            return False
        setter(session_id, priority)
        return True

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
            # runtime leases remain.  Keep the server reference until its
            # admitted handlers have actually drained.
            if self._ipc_server is server and not self._ipc_session_ids:
                stop_server = True

        stop_error = None
        if stop_server:
            try:
                stopped = _stop_ipc_server(server, timeout=2.0)
                if stopped is False:
                    stop_error = RuntimeError(
                        "camera IPC server shutdown did not drain active handlers"
                    )
                else:
                    with self._lock:
                        if self._ipc_server is server and not self._ipc_session_ids:
                            self._ipc_server = None
            except BaseException as exc:
                stop_error = exc

        if stop_error is not None:
            if revoke_error is not None:
                raise RuntimeError(
                    f"{stop_error}; session revoke also failed: {revoke_error}"
                ) from revoke_error
            raise stop_error

        if revoke_error is not None:
            raise revoke_error

    def release_idle_workers(self) -> None:
        """Release camera USB ownership when no Trigger IPC lease exists.

        Characterization deliberately opens gphoto2 directly. Before doing so,
        the authoritative runtime must relinquish every persistent camera
        process. An active Trigger lease makes that unsafe and is rejected.
        """

        with self._lock:
            server = self._guard_ipc_shutdown_locked()
            if self._ipc_session_ids or self._ipc_closing_session_ids:
                raise RuntimeError(
                    "camera runtime cannot be released while a trigger IPC session is active"
                )
            if server is not None:
                raise RuntimeError(
                    "camera runtime cannot be released while camera IPC "
                    "ownership has not fully shut down"
                )

            workers = dict(self._registry)
            failures: dict[int, str] = {}
            for rig_id, worker in workers.items():
                stopped, detail = self._stop_owned_worker(worker, timeout=2.0)
                if not stopped:
                    failures[rig_id] = detail or "shutdown failed"

            # Remove only workers whose shutdown was confirmed.  A failed
            # worker may still own the USB device and must remain referenced so
            # characterization cannot open a second gphoto2 owner.
            self._registry = {
                rig_id: workers[rig_id]
                for rig_id in failures
            }
            self._camera_entries = {
                rig_id: deepcopy(self._camera_entries[rig_id])
                for rig_id in failures
                if rig_id in self._camera_entries
            }
            self._leased_policy_configs.clear()

            if failures:
                detail = "; ".join(
                    f"RIG {rig_id}: {failures[rig_id]}"
                    for rig_id in sorted(failures)
                )
                raise RuntimeError(
                    "camera runtime could not release USB ownership; "
                    f"direct camera access refused ({detail})"
                )

            self._config = None

    def shutdown(self) -> None:
        """Stop IPC and workers without forgetting surviving camera owners.

        Shutdown is retryable.  Workers whose bounded stop does not complete
        remain registered so a caller can retry cleanup and so ownership is
        never reported as released while a child may still hold the USB
        device.
        """

        with self._lock:
            server = self._refresh_stopped_ipc_server_locked()
            workers = dict(self._registry)

        if server is not None:
            try:
                result = _stop_ipc_server(server, timeout=2.0)
            except Exception as exc:
                raise RuntimeError(
                    "camera runtime shutdown could not release ownership "
                    f"(IPC server: {type(exc).__name__}: {exc})"
                ) from exc
            if result is False:
                raise RuntimeError(
                    "camera runtime shutdown could not release ownership "
                    "(IPC server: active handlers did not drain)"
                )

            with self._lock:
                if self._ipc_server is server:
                    self._ipc_server = None
                    self._ipc_session_ids.clear()
                    self._ipc_session_rigs.clear()
                    self._ipc_closing_session_ids.clear()
                    self._leased_policy_configs.clear()

        worker_failures: dict[int, str] = {}
        for rig_id, worker in workers.items():
            stopped, detail = self._stop_owned_worker(worker, timeout=2.0)
            if not stopped:
                worker_failures[rig_id] = detail or "shutdown failed"

        with self._lock:
            # Remove only workers whose shutdown was confirmed.  Failed owners
            # remain authoritative and keep their binding metadata for retry.
            for rig_id, worker in workers.items():
                if rig_id in worker_failures:
                    continue
                if self._registry.get(rig_id) is worker:
                    self._registry.pop(rig_id, None)
                    self._camera_entries.pop(rig_id, None)

            if not self._registry:
                self._config = None

        failures = [
            f"RIG {rig_id}: {worker_failures[rig_id]}"
            for rig_id in sorted(worker_failures)
        ]
        if failures:
            raise RuntimeError(
                "camera runtime shutdown could not release ownership ("
                + "; ".join(failures)
                + ")"
            )


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
