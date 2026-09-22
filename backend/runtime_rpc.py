"""Unix-socket RPC client used by the web portal to reach the standalone runtime."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import socket
from types import SimpleNamespace
from typing import Any


DEFAULT_RUNTIME_SOCKET = "/run/solartrigger/runtime.sock"
_ENV_SOCKET = "SOLARTRIGGER_RUNTIME_SOCKET"
_ENV_CLIENT = "SOLARTRIGGER_RUNTIME_CLIENT"
_MAX_MESSAGE_BYTES = 16 * 1024 * 1024


class RuntimeRpcError(RuntimeError):
    """Base class for standalone-runtime RPC failures."""


class RuntimeUnavailableError(RuntimeRpcError):
    """Raised when the standalone runtime socket cannot be reached."""


class RuntimeRemoteError(RuntimeRpcError):
    """Raised when the runtime reports an application-level exception."""

    def __init__(self, message: str, *, error_type: str | None = None, code: str | None = None):
        super().__init__(message)
        self.error_type = error_type
        self.code = code


def runtime_client_enabled() -> bool:
    value = os.environ.get(_ENV_CLIENT, "")
    return value.strip().lower() in {"1", "true", "yes", "on"}


def runtime_socket_path() -> str:
    return os.environ.get(_ENV_SOCKET, DEFAULT_RUNTIME_SOCKET)


def _to_wire(value: Any):
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, datetime):
        return {
            "__runtime_type__": "datetime",
            "value": value.isoformat(),
        }
    if isinstance(value, Path):
        return {
            "__runtime_type__": "path",
            "value": str(value),
        }
    if is_dataclass(value):
        return {
            "__runtime_type__": "namespace",
            "fields": _to_wire(asdict(value)),
        }
    if isinstance(value, SimpleNamespace):
        return {
            "__runtime_type__": "namespace",
            "fields": _to_wire(vars(value)),
        }
    if isinstance(value, dict):
        return {str(key): _to_wire(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_to_wire(item) for item in value]
    as_dict = getattr(value, "_asdict", None)
    if callable(as_dict):
        return {
            "__runtime_type__": "namespace",
            "fields": _to_wire(as_dict()),
        }
    data = getattr(value, "__dict__", None)
    if isinstance(data, dict):
        return {
            "__runtime_type__": "namespace",
            "fields": _to_wire({
                key: item
                for key, item in data.items()
                if not key.startswith("_")
            }),
        }
    raise TypeError(f"value is not RPC serializable: {type(value).__name__}")


def _from_wire(value: Any):
    if isinstance(value, list):
        return [_from_wire(item) for item in value]
    if not isinstance(value, dict):
        return value

    marker = value.get("__runtime_type__")
    if marker == "datetime":
        return datetime.fromisoformat(str(value["value"]))
    if marker == "path":
        return Path(str(value["value"]))
    if marker == "namespace":
        fields = _from_wire(value.get("fields", {}))
        return SimpleNamespace(**fields)

    return {key: _from_wire(item) for key, item in value.items()}


class RuntimeClient:
    """One-request-per-connection JSON RPC client.

    A fresh AF_UNIX connection for every request deliberately avoids carrying
    portal process state across a Gunicorn restart. The runtime remains the
    authoritative owner of TriggerService and camera workers.
    """

    def __init__(self, socket_path: str | None = None, *, timeout: float = 60.0):
        self.socket_path = socket_path or runtime_socket_path()
        self.timeout = float(timeout)

    def call(self, operation: str, payload: dict | None = None, *, timeout: float | None = None):
        request = {
            "operation": str(operation),
            "payload": _to_wire(payload or {}),
        }
        raw = (
            json.dumps(request, separators=(",", ":"), ensure_ascii=False)
            + "\n"
        ).encode("utf-8")

        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout if timeout is None else float(timeout))
        try:
            sock.connect(self.socket_path)
            sock.sendall(raw)
            chunks = bytearray()
            while True:
                block = sock.recv(65536)
                if not block:
                    break
                chunks.extend(block)
                if len(chunks) > _MAX_MESSAGE_BYTES:
                    raise RuntimeRpcError("runtime response exceeds size limit")
                if b"\n" in block:
                    break
        except (FileNotFoundError, ConnectionRefusedError, socket.timeout, OSError) as exc:
            raise RuntimeUnavailableError(
                f"standalone runtime unavailable at {self.socket_path}: {exc}"
            ) from exc
        finally:
            sock.close()

        if not chunks:
            raise RuntimeUnavailableError("standalone runtime closed the connection without a response")

        line = bytes(chunks).split(b"\n", 1)[0]
        try:
            response = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeRpcError("invalid response from standalone runtime") from exc

        if not isinstance(response, dict):
            raise RuntimeRpcError("invalid response from standalone runtime")

        if response.get("ok") is not True:
            error = response.get("error") or {}
            if not isinstance(error, dict):
                error = {}
            raise RuntimeRemoteError(
                str(error.get("message") or "runtime operation failed"),
                error_type=(
                    str(error["type"])
                    if error.get("type") is not None
                    else None
                ),
                code=(
                    str(error["code"])
                    if error.get("code") is not None
                    else None
                ),
            )

        return _from_wire(response.get("result"))


class RemoteCameraWorker:
    """Camera-worker facade whose operations execute in the runtime service."""

    def __init__(self, runtime: "RemoteCameraWorkerRuntime", rig_id: int):
        self._runtime = runtime
        self.rig_id = int(rig_id)

    def _call(self, method: str, *args, **kwargs):
        try:
            return self._runtime._client.call(
                "camera.worker_call",
                {
                    "rig_id": self.rig_id,
                    "method": method,
                    "args": list(args),
                    "kwargs": kwargs,
                },
                timeout=180.0,
            )
        except RuntimeRemoteError as exc:
            if exc.error_type == "BusyDeviceError":
                from backend.generic_worker import BusyDeviceError

                raise BusyDeviceError(str(exc)) from exc
            raise

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)

        def remote_method(*args, **kwargs):
            return self._call(name, *args, **kwargs)

        return remote_method


class RemoteCameraWorkerRuntime:
    """Portal-side facade for the camera runtime owned by systemd."""

    def __init__(self, socket_path: str | None = None):
        self._client = RuntimeClient(socket_path)

    def reconcile(self, config: dict) -> None:
        self._client.call("camera.reconcile", {"config": config}, timeout=60.0)

    def get_for_rig(self, rig_id: int):
        exists = self._client.call(
            "camera.worker_exists",
            {"rig_id": int(rig_id)},
            timeout=10.0,
        )
        return RemoteCameraWorker(self, rig_id) if exists else None

    def get_policy_config_for_rig(self, rig_id: int):
        return self._client.call(
            "camera.policy_config",
            {"rig_id": int(rig_id)},
            timeout=10.0,
        )

    def active_camera_rig_ids(self) -> tuple[int, ...]:
        result = self._client.call("camera.active_rig_ids", timeout=10.0)
        return tuple(int(item) for item in (result or []))

    def open_ipc_session(self, rig_ids=None):
        result = self._client.call(
            "camera.open_ipc_session",
            {"rig_ids": None if rig_ids is None else list(rig_ids)},
            timeout=30.0,
        )
        from backend.camera_worker_runtime import CameraIpcSession

        return CameraIpcSession(
            socket_path=str(result["socket_path"]),
            session_id=str(result["session_id"]),
        )

    def close_ipc_session(self, session_id: str) -> None:
        self._client.call(
            "camera.close_ipc_session",
            {"session_id": str(session_id)},
            timeout=30.0,
        )

    def release_idle_workers(self) -> None:
        self._client.call("camera.release_idle_workers", timeout=30.0)

    def revoke_portal_sessions(self) -> int:
        result = self._client.call(
            "camera.revoke_portal_sessions",
            timeout=30.0,
        )
        return int(result or 0)

    def shutdown(self) -> None:
        # The portal is not allowed to tear down the autonomous runtime.
        return None


_remote_camera_runtime: RemoteCameraWorkerRuntime | None = None


def get_remote_camera_worker_runtime() -> RemoteCameraWorkerRuntime:
    global _remote_camera_runtime
    if _remote_camera_runtime is None:
        _remote_camera_runtime = RemoteCameraWorkerRuntime()
    return _remote_camera_runtime


class RemoteTriggerService:
    """Drop-in subset of TriggerService used by the Flask adapter."""

    def __init__(self, state_store, *, socket_path: str | None = None):
        self.state = state_store
        self._client = RuntimeClient(socket_path)

    def _trigger_call(self, operation: str, payload: dict, *, timeout: float = 90.0):
        try:
            result = self._client.call(operation, payload, timeout=timeout)
        except RuntimeRemoteError as exc:
            if exc.error_type == "TriggerValidationError":
                from backend.trigger_service import TriggerValidationError

                raise TriggerValidationError(str(exc), exc.code or "TRIGGER_INVALID") from exc
            raise
        self.sync_state(best_effort=True)
        return result

    def start(self, rig_id=1, simulate=False, speed=60.0, dry_run=False, selected=None):
        return self._trigger_call(
            "trigger.start",
            {
                "rig_id": rig_id,
                "simulate": simulate,
                "speed": speed,
                "dry_run": dry_run,
                "selected": selected,
            },
        )

    def start_totality_only(self, rig_id=1):
        return self._trigger_call(
            "trigger.totality_only",
            {"rig_id": rig_id},
        )

    def override_totality(self, rig_id=1):
        return self._trigger_call(
            "trigger.override_totality",
            {"rig_id": rig_id},
            timeout=20.0,
        )

    def stop(self, rig_id=1):
        return self._trigger_call(
            "trigger.stop",
            {"rig_id": rig_id},
            timeout=45.0,
        )

    def is_active_or_starting(self, rig_id: int) -> bool:
        result = self._client.call(
            "trigger.is_active_or_starting",
            {"rig_id": rig_id},
            timeout=5.0,
        )
        return bool(result)

    def any_active_or_starting(self) -> bool:
        return bool(
            self._client.call(
                "trigger.any_active_or_starting",
                timeout=5.0,
            )
        )

    def status(self) -> dict:
        result = self._client.call("trigger.status", timeout=5.0)
        return result if isinstance(result, dict) else {}

    def read_logs(self, after_seq: int = 0, limit: int = 500) -> dict:
        result = self._client.call(
            "logs.read",
            {"after_seq": int(after_seq), "limit": int(limit)},
            timeout=10.0,
        )
        return result if isinstance(result, dict) else {}

    def read_events(self, after_seq: int = 0, limit: int = 500) -> dict:
        result = self._client.call(
            "events.read",
            {"after_seq": int(after_seq), "limit": int(limit)},
            timeout=10.0,
        )
        return result if isinstance(result, dict) else {}

    def read_relay(
        self,
        log_after_seq: int = 0,
        event_after_seq: int = 0,
        limit: int = 500,
    ) -> dict:
        result = self._client.call(
            "relay.read",
            {
                "log_after_seq": int(log_after_seq),
                "event_after_seq": int(event_after_seq),
                "limit": int(limit),
            },
            timeout=10.0,
        )
        return result if isinstance(result, dict) else {}

    def sync_state(self, *, best_effort: bool = False) -> bool:
        try:
            status = self.status()
            trigger = status.get("trigger")
            if not isinstance(trigger, dict):
                return False
            self.state.set("trigger", trigger, persist=False)
            return True
        except RuntimeRpcError:
            if best_effort:
                return False
            raise


__all__ = [
    "DEFAULT_RUNTIME_SOCKET",
    "RemoteCameraWorker",
    "RemoteCameraWorkerRuntime",
    "RemoteTriggerService",
    "RuntimeClient",
    "RuntimeRemoteError",
    "RuntimeRpcError",
    "RuntimeUnavailableError",
    "_from_wire",
    "_to_wire",
    "get_remote_camera_worker_runtime",
    "runtime_client_enabled",
    "runtime_socket_path",
]
