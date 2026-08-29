"""Canonical lifecycle owner for configured focuser workers."""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from backend.device_identity import identity_key
from backend.focuser_worker import FocuserWorker

if TYPE_CHECKING:
    from services.focuser_service import FocuserService


def _freeze(value: Any) -> Any:
    """Return a recursively immutable copy of JSON-like configuration data."""

    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze(item) for item in value)
    return value


@dataclass(frozen=True)
class FocuserBinding:
    """Immutable description of one configured focuser worker."""

    rig_id: int
    backend: str
    identity: tuple[str, str]
    focuser_entry: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "backend", self.backend.strip().lower())
        object.__setattr__(self, "identity", tuple(self.identity))
        object.__setattr__(self, "focuser_entry", _freeze(self.focuser_entry))


FocuserServiceFactory = Callable[[], "FocuserService"]
FocuserServiceFactoryProvider = Callable[[FocuserBinding], FocuserServiceFactory]


@dataclass(frozen=True)
class _WorkerEntry:
    binding: FocuserBinding
    worker: FocuserWorker


class FocuserWorkerRuntime:
    """Reconcile configured focusers with their persistent worker owners."""

    def __init__(
        self,
        service_factory_provider: FocuserServiceFactoryProvider | None = None,
        log_fn=print,
    ) -> None:
        if service_factory_provider is not None and not callable(
            service_factory_provider
        ):
            raise TypeError("service_factory_provider must be callable")
        self._service_factory_provider = service_factory_provider
        self._log = log_fn
        self._registry: dict[tuple[str, tuple[str, str]], _WorkerEntry] = {}
        self._lock = threading.RLock()

    def set_service_factory_provider(
        self, provider: FocuserServiceFactoryProvider
    ) -> None:
        """Set the provider once, allowing repeated use of the same object."""

        if not callable(provider):
            raise TypeError("service factory provider must be callable")
        with self._lock:
            current = self._service_factory_provider
            if current is provider:
                return
            if current is not None or self._registry:
                raise RuntimeError("focuser service factory provider is already set")
            self._service_factory_provider = provider

    @staticmethod
    def _desired_bindings(
        config: dict,
    ) -> dict[tuple[str, tuple[str, str]], FocuserBinding]:
        desired: dict[tuple[str, tuple[str, str]], FocuserBinding] = {}
        for rig in config.get("rigs", []):
            if not isinstance(rig, dict):
                continue
            devices = rig.get("devices")
            focuser = devices.get("focuser") if isinstance(devices, dict) else None
            if not isinstance(focuser, dict):
                continue
            raw_backend = focuser.get("backend")
            if not isinstance(raw_backend, str):
                continue
            backend = raw_backend.strip().lower()
            if not backend or backend in {"none", "external"}:
                continue

            identity = identity_key(focuser)
            if identity is None:
                raise ValueError(
                    f"RIG {rig.get('rig_id')} focuser has no stable device identity"
                )
            if not (
                isinstance(identity[0], str) and isinstance(identity[1], str)
            ):
                raise ValueError(
                    f"RIG {rig.get('rig_id')} focuser identity must contain strings"
                )
            rig_id = rig.get("rig_id")
            if not isinstance(rig_id, int) or isinstance(rig_id, bool):
                raise ValueError("eligible focuser requires an integer rig_id")

            binding = FocuserBinding(rig_id, backend, identity, focuser)
            key = (backend, identity)
            if key in desired:
                raise ValueError(
                    f"duplicate focuser identity: {identity[0]}={identity[1]}"
                )
            desired[key] = binding
        return desired

    def reconcile(self, config: dict) -> None:
        """Atomically reconcile workers against the eligible focusers in *config*."""

        desired = self._desired_bindings(config)
        with self._lock:
            provider = self._service_factory_provider
            if desired and provider is None:
                raise RuntimeError("focuser service factory provider is not set")

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
                    factory = provider(binding)  # type: ignore[misc]
                    if not callable(factory):
                        raise TypeError("focuser service factory must be callable")
                    worker = FocuserWorker(
                        rig_id=binding.rig_id,
                        service_factory=factory,
                        log_fn=self._log,
                    )
                    created[key] = _WorkerEntry(binding, worker)
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
                entry.worker.shutdown()

    def get_for_rig(self, rig_id: int) -> FocuserWorker | None:
        """Return the persistent worker bound to *rig_id*, if configured."""

        with self._lock:
            for entry in self._registry.values():
                if entry.binding.rig_id == rig_id:
                    return entry.worker
        return None

    def stop_all(self, timeout: float | None = None) -> None:
        """Clear the registry and shut down every worker."""

        with self._lock:
            entries = list(self._registry.values())
            self._registry = {}
            first_error: BaseException | None = None
            for entry in entries:
                try:
                    entry.worker.shutdown(timeout=timeout)
                except BaseException as exc:
                    if first_error is None:
                        first_error = exc
            if first_error is not None:
                raise first_error


_focuser_worker_runtime: FocuserWorkerRuntime | None = None
_focuser_worker_runtime_lock = threading.Lock()


def get_focuser_worker_runtime(
    service_factory_provider: FocuserServiceFactoryProvider | None = None,
    log_fn=print,
) -> FocuserWorkerRuntime:
    """Return the process-wide focuser worker runtime singleton."""

    global _focuser_worker_runtime

    with _focuser_worker_runtime_lock:
        if _focuser_worker_runtime is None:
            _focuser_worker_runtime = FocuserWorkerRuntime(
                service_factory_provider=service_factory_provider,
                log_fn=log_fn,
            )
        elif service_factory_provider is not None:
            _focuser_worker_runtime.set_service_factory_provider(
                service_factory_provider
            )
        return _focuser_worker_runtime


def reset_focuser_worker_runtime_for_tests() -> None:
    """Stop and clear the singleton for test isolation."""

    global _focuser_worker_runtime

    with _focuser_worker_runtime_lock:
        runtime = _focuser_worker_runtime
        if runtime is not None:
            runtime.stop_all()
        _focuser_worker_runtime = None


__all__ = [
    "FocuserBinding",
    "FocuserServiceFactory",
    "FocuserServiceFactoryProvider",
    "FocuserWorkerRuntime",
    "get_focuser_worker_runtime",
]
