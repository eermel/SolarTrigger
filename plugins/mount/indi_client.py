"""Subprocess-based client for the INDI command-line tools."""

from __future__ import annotations

import subprocess
from typing import Any


class IndiClientError(Exception):
    """A structured failure reported while invoking an INDI command."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        command: list[str] | None = None,
        returncode: int | None = None,
        stderr: str = "",
    ) -> None:
        self.code = code
        self.command = command
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(message)


class IndiSubprocessClient:
    """Small, testable wrapper around ``indi_getprop`` and ``indi_setprop``."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 7624,
        device: str = "EQMod Mount",
        timeout_s: float = 4.0,
    ) -> None:
        self.host = host
        self.port = port
        self.device = device
        self.timeout_s = timeout_s

    def get_props(self, patterns: list[str] | None = None) -> dict:
        """Return properties belonging to the configured device.

        Each pattern is relative to the device unless it already starts with
        the configured device name.
        """
        filters = None
        if patterns is not None:
            filters = [self._device_pattern(self.device, pattern) for pattern in patterns]
        output = self._run("indi_getprop", filters or [])
        return self._parse_props(output).get(self.device, {})

    def set_props(self, assignments: dict[str, dict[str, Any]]) -> None:
        """Set property elements on the configured device."""
        values = [
            f"{self.device}.{prop}.{element}={value}"
            for prop, elements in assignments.items()
            for element, value in elements.items()
        ]
        self._run("indi_setprop", values)

    def ensure_device_present(self, device_name: str) -> None:
        """Raise ``DEVICE_NOT_FOUND`` unless *device_name* is advertised."""
        output = self._run("indi_getprop", [f"{device_name}.*.*"])
        if device_name not in self._parse_props(output):
            raise IndiClientError(
                "DEVICE_NOT_FOUND",
                f"INDI device not found: {device_name}",
            )

    def _run(self, executable: str, arguments: list[str]) -> str:
        command = [
            executable,
            "-h",
            self.host,
            "-p",
            str(self.port),
            *arguments,
        ]
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=self.timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise IndiClientError(
                "TIMEOUT",
                f"{executable} exceeded the {self.timeout_s}s timeout",
                command=command,
                stderr=self._as_text(exc.stderr),
            ) from exc
        except OSError as exc:
            raise IndiClientError(
                "INDI_UNAVAILABLE",
                f"Unable to start {executable}: {exc}",
                command=command,
                stderr=str(exc),
            ) from exc

        if result.returncode != 0:
            stderr = result.stderr or ""
            code = self._failure_code(executable, stderr)
            detail = stderr.strip() or f"exit code {result.returncode}"
            raise IndiClientError(
                code,
                f"{executable} failed: {detail}",
                command=command,
                returncode=result.returncode,
                stderr=stderr,
            )
        return result.stdout or ""

    @staticmethod
    def _device_pattern(device: str, pattern: str) -> str:
        if pattern == device or pattern.startswith(f"{device}."):
            return pattern
        return f"{device}.{pattern}"

    @staticmethod
    def _parse_props(output: str) -> dict[str, dict[str, dict[str, str]]]:
        devices: dict[str, dict[str, dict[str, str]]] = {}
        for raw_line in output.splitlines():
            line = raw_line.strip()
            if not line or "=" not in line:
                continue
            name, value = line.split("=", 1)
            parts = name.split(".", 2)
            if len(parts) != 3:
                continue
            device, prop, element = parts
            devices.setdefault(device, {}).setdefault(prop, {})[element] = value
        return devices

    @staticmethod
    def _failure_code(executable: str, stderr: str) -> str:
        detail = stderr.casefold()
        if any(
            phrase in detail
            for phrase in (
                "connection refused",
                "cannot connect",
                "could not connect",
                "connection timed out",
                "timed out",
                "timeout",
                "server unavailable",
            )
        ):
            return "INDI_UNAVAILABLE"
        if any(
            phrase in detail
            for phrase in (
                "connection lost",
                "broken pipe",
                "connection reset",
                "server disconnected",
            )
        ):
            return "CONNECTION_LOST"
        if executable == "indi_setprop" and any(
            word in detail for word in ("property", "element", "not found", "unknown")
        ):
            return "PROPERTY_UNSUPPORTED"
        return "CONNECTION_FAILED"

    @staticmethod
    def _as_text(value: str | bytes | None) -> str:
        if isinstance(value, bytes):
            return value.decode(errors="replace")
        return value or ""


__all__ = ["IndiClientError", "IndiSubprocessClient"]
