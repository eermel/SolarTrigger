"""Canonical lifecycle owner for configured mount workers."""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from backend.device_identity import identity_key
from backend.mount_process_worker import ProcessMountWorker
from backend.mount_worker import MountWorker

if TYPE_CHECKING:
    from services.mount_service import MountService


def _freeze(value: Any) -> Any:
    """Return a recursively immutable copy of JSON-like configuration data."""

    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    """Return a process-serializable mutable copy of frozen config."""

    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    if isinstance(value, frozenset):
        return [_thaw(item) for item in value]
    return value


@dataclass(frozen=True)
class MountBinding:
    """Immutable description of one configured mount worker."""

    rig_id: int
    backend: str
    identity: tuple[str, str]
    mount_entry: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "backend", self.backend.strip().lower())
        object.__setattr__(self, "identity", tuple(self.identity))
        object.__setattr__(self, "mount_entry", _freeze(self.mount_entry))


MountServiceFactory = Callable[[], "MountService"]
MountServiceFactoryProvider = Callable[[MountBinding], MountServiceFactory]


@dataclass(frozen=True)
class _WorkerEntry:
    binding: MountBinding
    worker: Any


class MountWorkerRuntime:
    """Reconcile configured mounts with their persistent worker owners."""

    def __init__(
        self,
        service_factory_provider: MountServiceFactoryProvider | None = None,
        log_fn=print,
        *,
        state_path: str | Path | None = None,
        process_worker_factory=ProcessMountWorker,
    ) -> None:
        if service_factory_provider is not None and not callable(
            service_factory_provider
        ):
            raise TypeError("service_factory_provider must be callable")
        if not callable(process_worker_factory):
            raise TypeError("process_worker_factory must be callable")

        self._service_factory_provider = service_factory_provider
        self._state_path = (
            None if state_path is None else Path(state_path)
        )
        self._process_worker_factory = process_worker_factory
        self._log = log_fn
        self._registry: dict[tuple[str, tuple[str, str]], _WorkerEntry] = {}
        self._lock = threading.RLock()

    def set_service_factory_provider(
        self, provider: MountServiceFactoryProvider
    ) -> None:
        """Set the provider once, allowing repeated use of the same object."""

        if not callable(provider):
            raise TypeError("service factory provider must be callable")
        with self._lock:
            current = self._service_factory_provider
            if current is provider:
                return
            if current is not None or self._registry:
                raise RuntimeError("mount service factory provider is already set")
            self._service_factory_provider = provider

    def set_process_state_path(
        self,
        state_path: str | Path,
    ) -> None:
        """Configure process mode once before workers are created."""

        resolved = Path(state_path)

        with self._lock:
            if self._service_factory_provider is not None:
                raise RuntimeError(
                    "mount runtime is configured for injected thread workers"
                )

            if self._state_path == resolved:
                return

            if self._state_path is not None or self._registry:
                raise RuntimeError(
                    "mount process state path is already set"
                )

            self._state_path = resolved

    @staticmethod
    def _desired_bindings(
        config: dict,
    ) -> dict[tuple[str, tuple[str, str]], MountBinding]:
        desired: dict[tuple[str, tuple[str, str]], MountBinding] = {}
        for rig in config.get("rigs", []):
            if not isinstance(rig, dict):
                continue
            devices = rig.get("devices")
            mount = devices.get("mount") if isinstance(devices, dict) else None
            if not isinstance(mount, dict):
                continue
            raw_backend = mount.get("backend")
            if not isinstance(raw_backend, str):
                continue
            backend = raw_backend.strip().lower()
            if not backend or backend in {"none", "external"}:
                continue

            identity = identity_key(mount)
            if identity is None:
                raise ValueError(
                    f"RIG {rig.get('rig_id')} mount has no stable device identity"
                )
            if not (
                isinstance(identity[0], str) and isinstance(identity[1], str)
            ):
                raise ValueError(
                    f"RIG {rig.get('rig_id')} mount identity must contain strings"
                )
            rig_id = rig.get("rig_id")
            if not isinstance(rig_id, int) or isinstance(rig_id, bool):
                raise ValueError("pilotable mount requires an integer rig_id")

            binding = MountBinding(rig_id, backend, identity, mount)
            key = (backend, identity)
            if key in desired:
                raise ValueError(
                    f"duplicate mount identity: {identity[0]}={identity[1]}"
                )
            desired[key] = binding
        return desired

    def reconcile(self, config: dict) -> None:
        """Atomically reconcile workers against the pilotable mounts in *config*."""

        desired = self._desired_bindings(config)
        with self._lock:
            provider = self._service_factory_provider
            state_path = self._state_path

            if desired and provider is None and state_path is None:
                raise RuntimeError(
                    "mount service factory provider is not set and no "
                    "process state path is configured"
                )

            unchanged = {
                key
                for key, binding in desired.items()
                if key in self._registry
                and self._registry[key].binding == binding
            }
            new_keys = [key for key in desired if key not in unchanged]
            created: dict[tuple[str, tuple[str, str]], _WorkerEntry] = {}
            try:
                for key in new_keys:
                    binding = desired[key]

                    if provider is not None:
                        factory = provider(binding)
                        if not callable(factory):
                            raise TypeError(
                                "mount service factory must be callable"
                            )

                        worker = MountWorker(
                            rig_id=binding.rig_id,
                            service_factory=factory,
                            log_fn=self._log,
                        )
                    else:
                        assert state_path is not None

                        worker = self._process_worker_factory(
                            rig_id=binding.rig_id,
                            backend=binding.backend,
                            device_config=_thaw(
                                binding.mount_entry
                            ),
                            state_path=state_path,
                            log_fn=self._log,
                        )

                    created[key] = _WorkerEntry(
                        binding,
                        worker,
                    )
                    worker.start()
            except BaseException:
                for entry in created.values():
                    try:
                        entry.worker.shutdown()
                    except Exception:
                        pass
                raise

            previous = self._registry
            self._registry = {
                key: previous[key] if key in unchanged else created[key]
                for key in desired
            }
            obsolete = [entry for key, entry in previous.items() if key not in unchanged]

        for entry in obsolete:
            entry.worker.shutdown(timeout=2.0)

    def stop_all(self, timeout: float | None = None) -> None:
        """Clear the registry and shut down every worker."""

        with self._lock:
            entries = list(self._registry.values())
            self._registry = {}

        effective_timeout = 2.0 if timeout is None else timeout
        first_error: BaseException | None = None
        for entry in entries:
            try:
                entry.worker.shutdown(timeout=effective_timeout)
            except BaseException as exc:
                if first_error is None:
                    first_error = exc
        if first_error is not None:
            raise first_error

    def get_for_rig(self, rig_id: int) -> Any | None:
        """Return the persistent worker bound to *rig_id*, if configured."""

        with self._lock:
            for entry in self._registry.values():
                if entry.binding.rig_id == rig_id:
                    return entry.worker
        return None


_mount_worker_runtime: MountWorkerRuntime | None = None
_mount_worker_runtime_lock = threading.Lock()


def get_mount_worker_runtime(
    service_factory_provider: MountServiceFactoryProvider | None = None,
    log_fn=print,
    *,
    state_path: str | Path | None = None,
) -> MountWorkerRuntime:
    """Return the process-wide mount worker runtime singleton."""

    global _mount_worker_runtime

    with _mount_worker_runtime_lock:
        if _mount_worker_runtime is None:
            _mount_worker_runtime = MountWorkerRuntime(
                service_factory_provider=service_factory_provider,
                log_fn=log_fn,
                state_path=state_path,
            )
        else:
            if service_factory_provider is not None:
                _mount_worker_runtime.set_service_factory_provider(
                    service_factory_provider
                )
            if state_path is not None:
                _mount_worker_runtime.set_process_state_path(
                    state_path
                )

        return _mount_worker_runtime


def reset_mount_worker_runtime_for_tests() -> None:
    """Stop and clear the singleton for test isolation."""

    global _mount_worker_runtime

    with _mount_worker_runtime_lock:
        runtime = _mount_worker_runtime
        if runtime is not None:
            runtime.stop_all()
        _mount_worker_runtime = None


__all__ = [
    "MountBinding",
    "MountServiceFactory",
    "MountServiceFactoryProvider",
    "MountWorkerRuntime",
    "get_mount_worker_runtime",
]
