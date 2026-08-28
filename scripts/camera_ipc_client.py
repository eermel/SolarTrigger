"""Client for the local camera worker JSON-lines IPC protocol."""

from __future__ import annotations

import dataclasses
import json
import socket
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from backend.camera_ipc_server import MAX_MESSAGE_BYTES


def _log(message: str) -> None:
    """Emit a client diagnostic without buffering it behind eclipse output."""

    print(message, flush=True)


class CameraIpcError(Exception):
    """A stable, operation-scoped failure returned by camera IPC."""

    def __init__(self, code: str, operation: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.operation = operation
        self.message = message


class CameraIpcClient:
    """Make isolated request/response calls to a camera IPC server."""

    MAX_MESSAGE_BYTES = MAX_MESSAGE_BYTES
    DEFAULT_TIMEOUT_S = 5.0

    def __init__(
        self,
        socket_path: str | Path,
        session_id: str,
        log_fn: Callable[[str], None] = _log,
    ) -> None:
        self.socket_path = str(socket_path)
        self.session_id = session_id
        self._log = log_fn
        self._log_lock = threading.Lock()

    def ping(self, *, timeout_s: float = DEFAULT_TIMEOUT_S) -> dict[str, Any]:
        return self._call("ping", {}, timeout_s=timeout_s)

    def list_active_camera_rigs(
        self, *, timeout_s: float = DEFAULT_TIMEOUT_S
    ) -> dict[str, list[int]]:
        result = self._call("list_active_camera_rigs", {}, timeout_s=timeout_s)
        rig_ids = result.get("rig_ids") if isinstance(result, dict) else None
        if not isinstance(rig_ids, list) or any(
            not isinstance(rig_id, int) or isinstance(rig_id, bool)
            for rig_id in rig_ids
        ):
            self._fail("INVALID_RESPONSE", "list_active_camera_rigs", "invalid rig list")
        return result

    def list_rigs(
        self, *, timeout_s: float = DEFAULT_TIMEOUT_S
    ) -> dict[str, list[int]]:
        """Compatibility shorthand for the protocol's rig-list operation."""

        return self.list_active_camera_rigs(timeout_s=timeout_s)

    def initialize(
        self,
        rig_id: int,
        *,
        aperture: str | None = None,
        iso: str | None = None,
        image_format: str = "RAW",
        white_balance: str = "Daylight",
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> Any:
        return self._call(
            "camera.initialize",
            {
                "rig_id": rig_id,
                "aperture": aperture,
                "iso": iso,
                "image_format": image_format,
                "white_balance": white_balance,
            },
            timeout_s=timeout_s,
        )

    def initialize_camera(self, rig_id: int, **kwargs: Any) -> Any:
        """Compatibility name matching the dotted ``camera.initialize`` op."""

        return self.initialize(rig_id, **kwargs)

    def apply_phase_settings(
        self,
        rig_id: int,
        *,
        aperture: str | None = None,
        iso: str | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> Any:
        return self._call(
            "apply_phase_settings",
            {"rig_id": rig_id, "aperture": aperture, "iso": iso},
            timeout_s=timeout_s,
        )

    def prepare_capture(
        self,
        rig_id: int,
        intent: Any,
        *,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> Any:
        return self._call(
            "prepare_capture",
            {"rig_id": rig_id, "intent": self._json_value(intent)},
            timeout_s=timeout_s,
        )

    def trigger_prepared(
        self,
        rig_id: int,
        token_id: str,
        *,
        deadline: datetime | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> Any:
        return self._call(
            "trigger_prepared",
            {
                "rig_id": rig_id,
                "token_id": token_id,
                "deadline": self._deadline_value(deadline, "trigger_prepared"),
            },
            timeout_s=timeout_s,
            deadline=deadline,
        )

    def shoot_speed_list(
        self,
        rig_id: int,
        speeds: list[str],
        *,
        photo_num_start: int = 0,
        deadline: datetime | None = None,
        slowest_override_seconds: float | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> Any:
        return self._call(
            "shoot_speed_list",
            {
                "rig_id": rig_id,
                "speeds": speeds,
                "photo_num_start": photo_num_start,
                "deadline": self._deadline_value(deadline, "shoot_speed_list"),
                "slowest_override_seconds": slowest_override_seconds,
            },
            timeout_s=timeout_s,
            deadline=deadline,
        )

    def _call(
        self,
        operation: str,
        params: dict[str, Any],
        *,
        timeout_s: float,
        deadline: datetime | None = None,
    ) -> Any:
        try:
            timeout = self._effective_timeout(timeout_s, deadline)
            request = json.dumps(
                {
                    "operation": operation,
                    "params": params,
                    "session_id": self.session_id,
                },
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
            if len(request) > MAX_MESSAGE_BYTES:
                self._raise("MESSAGE_TOO_LARGE", operation, "request exceeds size limit")

            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(timeout)
                connection.connect(self.socket_path)
                connection.sendall(request + b"\n")
                response = self._read_response(connection, operation)
        except CameraIpcError:
            raise
        except ValueError as exc:
            self._fail("INVALID_REQUEST", operation, str(exc), exc)
        except (socket.timeout, TimeoutError) as exc:
            self._fail("TIMEOUT", operation, "camera IPC request timed out", exc)
        except (OSError, EOFError) as exc:
            self._fail("IPC_UNAVAILABLE", operation, "camera IPC is unavailable", exc)

        if not isinstance(response, dict) or not isinstance(response.get("ok"), bool):
            self._fail("INVALID_RESPONSE", operation, "invalid camera IPC response")
        if response["ok"]:
            if "result" not in response:
                self._fail("INVALID_RESPONSE", operation, "camera IPC result is missing")
            return response["result"]

        error = response.get("error")
        if not isinstance(error, dict):
            self._fail("INVALID_RESPONSE", operation, "camera IPC error is invalid")
        code, message = error.get("code"), error.get("message")
        if not isinstance(code, str) or not code or not isinstance(message, str):
            self._fail("INVALID_RESPONSE", operation, "camera IPC error is invalid")
        self._fail(code, operation, message)

    def _read_response(self, connection: socket.socket, operation: str) -> Any:
        data = bytearray()
        while True:
            chunk = connection.recv(min(4096, MAX_MESSAGE_BYTES + 1 - len(data)))
            if not chunk:
                raise EOFError("connection closed before a complete response")
            data.extend(chunk)
            newline = data.find(b"\n")
            if newline >= 0:
                if newline > MAX_MESSAGE_BYTES:
                    self._raise(
                        "MESSAGE_TOO_LARGE", operation, "response exceeds size limit"
                    )
                if data[newline + 1 :]:
                    self._raise("INVALID_RESPONSE", operation, "multiple responses received")
                payload = bytes(data[:newline])
                break
            if len(data) > MAX_MESSAGE_BYTES:
                self._raise("MESSAGE_TOO_LARGE", operation, "response exceeds size limit")
        try:
            return json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._fail("INVALID_RESPONSE", operation, "response is not valid JSON", exc)

    @staticmethod
    def _effective_timeout(timeout_s: float, deadline: datetime | None) -> float:
        if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)):
            raise ValueError("timeout_s must be a positive number")
        timeout = float(timeout_s)
        if timeout <= 0:
            raise ValueError("timeout_s must be a positive number")
        if deadline is not None:
            if (
                not isinstance(deadline, datetime)
                or deadline.tzinfo is None
                or deadline.utcoffset() is None
            ):
                raise ValueError("deadline must be an aware datetime")
            remaining = (
                deadline.astimezone(timezone.utc) - datetime.now(timezone.utc)
            ).total_seconds()
            if remaining <= 0:
                raise socket.timeout("deadline has passed")
            timeout = min(timeout, remaining)
        return timeout

    def _deadline_value(self, deadline: datetime | None, operation: str) -> str | None:
        if deadline is None:
            return None
        if (
            not isinstance(deadline, datetime)
            or deadline.tzinfo is None
            or deadline.utcoffset() is None
        ):
            self._fail("INVALID_DEADLINE", operation, "deadline must be an aware datetime")
        return deadline.astimezone(timezone.utc).isoformat()

    @classmethod
    def _json_value(cls, value: Any) -> Any:
        if dataclasses.is_dataclass(value):
            return cls._json_value(dataclasses.asdict(value))
        if isinstance(value, dict):
            return {str(key): cls._json_value(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [cls._json_value(item) for item in value]
        if isinstance(value, datetime):
            return value.isoformat()
        return value

    def _raise(self, code: str, operation: str, message: str) -> None:
        self._fail(code, operation, message)

    def _fail(
        self,
        code: str,
        operation: str,
        message: str,
        cause: BaseException | None = None,
    ) -> None:
        # Deliberately omit paths, session identifiers, payloads, and server text.
        try:
            with self._log_lock:
                self._log(f"CAMERA_IPC_ERROR code={code} operation={operation}")
        except Exception:
            pass
        error = CameraIpcError(code, operation, message)
        if cause is None:
            raise error
        raise error from cause


__all__ = ["CameraIpcClient", "CameraIpcError", "MAX_MESSAGE_BYTES"]
