"""Subprocess-based client for the INDI command-line tools."""

from __future__ import annotations

import fnmatch
import subprocess
import threading
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
    """Small, testable wrapper around ``indi_getprop`` and ``indi_setprop``.

    Normal discovery/probe calls keep using short-lived ``indi_getprop``
    subprocesses.

    Runtime users may call :meth:`start_monitor` to keep one
    ``indi_getprop -m`` process alive. Once active, ``get_props`` reads the
    in-memory cache populated by that monitor instead of starting a new
    ``indi_getprop`` process for every request.
    """

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

        self._monitor_lock = threading.RLock()
        self._monitor_process = None
        self._monitor_thread: threading.Thread | None = None
        self._monitor_cache: dict[str, dict[str, dict[str, str]]] = {}

    @property
    def monitor_active(self) -> bool:
        with self._monitor_lock:
            return self._monitor_process is not None

    def get_props(self, patterns: list[str] | None = None) -> dict:
        """Return properties belonging to the configured device.

        When the persistent monitor is active this is a cache-only operation.
        Otherwise the historical one-shot ``indi_getprop`` behaviour is kept.

        Each pattern is relative to the device unless it already starts with
        the configured device name.
        """
        with self._monitor_lock:
            if self._monitor_process is not None:
                return self._cached_props_locked(patterns)

        filters = None
        if patterns is not None:
            filters = [
                self._device_pattern(self.device, pattern)
                for pattern in patterns
            ]

        output = self._getprop_snapshot(filters or [])
        parsed = self._parse_props(output)

        # Preserve the last known values so a subsequently started monitor
        # has a usable snapshot immediately.
        self._merge_cache(parsed)

        return parsed.get(self.device, {})

    def get_all_devices(self) -> dict[str, dict[str, dict[str, str]]]:
        """Return discovery properties without an unscoped INDI read."""
        patterns = [
            "*.DRIVER_INFO.*", "*.CONNECTION.*", "*.DEVICE_PORT.*",
            "*.DEVICE_INFO.*", "*.MOUNTINFORMATION.*", "*.VERSION.*",
            "*.TELESCOPE_MOTION_NS.*", "*.TELESCOPE_MOTION_WE.*",
            "*.TELESCOPE_PARK.*", "*.TELESCOPE_HOME.*",
            "*.EQUATORIAL_EOD_COORD.*", "*.EQUATORIAL_COORD.*",
            "*.CCD_EXPOSURE.*", "*.CCD_INFO.*", "*.FILTER_SLOT.*",
            "*.ABS_ROTATOR_ANGLE.*", "*.ROTATOR_ANGLE.*",
            "*.DOME_MOTION.*", "*.DOME_PARK.*", "*.WEATHER_PARAMETERS.*",
        ]
        parsed: dict[str, dict[str, dict[str, str]]] = {}
        # One indi_getprop invocation containing many wildcard filters can
        # remain open until the subprocess timeout on real indiserver builds.
        # Query each bounded property family independently instead. This also
        # lets unsupported/absent optional families fail without losing the
        # essential DRIVER_INFO/CONNECTION catalogue.
        for pattern in patterns:
            try:
                output = self._getprop_snapshot([pattern])
            except IndiClientError as exc:
                if pattern in {"*.DRIVER_INFO.*", "*.CONNECTION.*"}:
                    raise
                if exc.code in {"INDI_UNAVAILABLE", "CONNECTION_LOST"}:
                    raise
                continue
            current = self._parse_props(output)
            for device, properties in current.items():
                target = parsed.setdefault(device, {})
                for prop, elements in properties.items():
                    target.setdefault(prop, {}).update(elements)
        self._merge_cache(parsed)
        return parsed

    def set_props(self, assignments: dict[str, dict[str, Any]]) -> None:
        """Set property elements on the configured device."""
        values = [
            f"{self.device}.{prop}.{element}={value}"
            for prop, elements in assignments.items()
            for element, value in elements.items()
        ]
        self._run("indi_setprop", values)

    def ensure_device_present(self, device_name: str) -> None:
        """Raise ``DEVICE_NOT_FOUND`` unless *device_name* is advertised.

        This intentionally remains a one-shot query so probes and inventory
        discovery never leave persistent monitor processes behind.
        """
        output = self._getprop_snapshot([f"{device_name}.*.*"])
        if device_name not in self._parse_props(output):
            raise IndiClientError(
                "DEVICE_NOT_FOUND",
                f"INDI device not found: {device_name}",
            )

    def start_monitor(self) -> None:
        """Start one persistent ``indi_getprop -m`` process for this device."""
        with self._monitor_lock:
            if self._monitor_process is not None:
                return

            command = [
                "stdbuf",
                "-oL",
                "indi_getprop",
                "-m",
                "-t",
                "0",
                "-h",
                self.host,
                "-p",
                str(self.port),
                f"{self.device}.*.*",
            ]

            try:
                process = subprocess.Popen(
                    command,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    bufsize=1,
                )
            except OSError as exc:
                raise IndiClientError(
                    "INDI_UNAVAILABLE",
                    f"Unable to start persistent INDI monitor: {exc}",
                    command=command,
                    stderr=str(exc),
                ) from exc

            self._monitor_process = process
            thread = threading.Thread(
                target=self._monitor_reader,
                args=(process,),
                name=f"indi-monitor-{self.device}",
                daemon=True,
            )
            self._monitor_thread = thread
            thread.start()

    def stop_monitor(self) -> None:
        """Stop the persistent monitor if one is active."""
        with self._monitor_lock:
            process = self._monitor_process
            thread = self._monitor_thread
            self._monitor_process = None
            self._monitor_thread = None

        if process is None:
            return

        try:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=1.0)
        except Exception:
            pass

        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.0)

        for stream_name in ("stdout", "stderr"):
            stream = getattr(process, stream_name, None)
            if stream is not None:
                try:
                    stream.close()
                except Exception:
                    pass

    def _monitor_reader(self, process) -> None:
        stdout = getattr(process, "stdout", None)
        if stdout is None:
            return

        try:
            for raw_line in stdout:
                parsed = self._parse_props(raw_line)
                if parsed:
                    self._merge_cache(parsed)
        finally:
            # A real monitor unexpectedly exiting must not leave the client
            # permanently serving stale data. Fake test processes that merely
            # reach EOF while still reporting poll() == None intentionally
            # remain active.
            try:
                exited = process.poll() is not None
            except Exception:
                exited = False

            if exited:
                with self._monitor_lock:
                    if self._monitor_process is process:
                        self._monitor_process = None
                        self._monitor_thread = None

    def _merge_cache(
        self,
        parsed: dict[str, dict[str, dict[str, str]]],
    ) -> None:
        with self._monitor_lock:
            for device, properties in parsed.items():
                device_cache = self._monitor_cache.setdefault(device, {})
                for prop, elements in properties.items():
                    prop_cache = device_cache.setdefault(prop, {})
                    prop_cache.update(elements)

    def _cached_props_locked(
        self,
        patterns: list[str] | None,
    ) -> dict[str, dict[str, str]]:
        source = self._monitor_cache.get(self.device, {})

        if patterns is None:
            return {
                prop: dict(elements)
                for prop, elements in source.items()
            }

        qualified_patterns = [
            self._device_pattern(self.device, pattern)
            for pattern in patterns
        ]

        result: dict[str, dict[str, str]] = {}

        for prop, elements in source.items():
            for element, value in elements.items():
                qualified_name = f"{self.device}.{prop}.{element}"
                if any(
                    fnmatch.fnmatchcase(qualified_name, pattern)
                    for pattern in qualified_patterns
                ):
                    result.setdefault(prop, {})[element] = value

        return result

    def _getprop_snapshot(self, arguments: list[str]) -> str:
        """Capture the initial indi_getprop snapshot and stop the client.

        Some INDI builds keep indi_getprop attached after emitting matching
        properties. A subprocess timeout is therefore not itself a discovery
        failure when stdout already contains a valid snapshot.
        """
        command = [
            "indi_getprop",
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
            stdout = self._as_text(exc.stdout)
            stderr = self._as_text(exc.stderr)
            if stdout.strip():
                return stdout
            raise IndiClientError(
                "TIMEOUT",
                f"indi_getprop produced no snapshot within {self.timeout_s}s",
                command=command,
                stderr=stderr,
            ) from exc
        except OSError as exc:
            raise IndiClientError(
                "INDI_UNAVAILABLE",
                f"Unable to start indi_getprop: {exc}",
                command=command,
                stderr=str(exc),
            ) from exc

        if result.returncode != 0:
            stderr = result.stderr or ""
            code = self._failure_code("indi_getprop", stderr)
            detail = stderr.strip() or f"exit code {result.returncode}"
            raise IndiClientError(
                code,
                f"indi_getprop failed: {detail}",
                command=command,
                returncode=result.returncode,
                stderr=stderr,
            )
        return result.stdout or ""

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
            word in detail
            for word in ("property", "element", "not found", "unknown")
        ):
            return "PROPERTY_UNSUPPORTED"
        return "CONNECTION_FAILED"

    @staticmethod
    def _as_text(value: str | bytes | None) -> str:
        if isinstance(value, bytes):
            return value.decode(errors="replace")
        return value or ""


__all__ = ["IndiClientError", "IndiSubprocessClient"]
