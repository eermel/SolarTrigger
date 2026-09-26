"""Generic discovery of astronomical devices exposed by one INDI server.

INDI is the primary discovery layer for astronomical equipment. DSLR/mirrorless
cameras controlled through the INDI gphoto driver are deliberately excluded:
SolarTrigger keeps those cameras on its characterized gphoto2 pipeline.

The manager enumerates properties advertised by an already running indiserver,
classifies logical INDI devices, and may ask INDI mount drivers to connect to
safe serial candidates. Candidate filtering is deliberately transport-based:
known non-mount resources are excluded without inferring device roles from USB
chipset/vendor names.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import time
from typing import Any, Mapping

from backend.runtime_paths import INDI_MOUNT_BINDINGS_FILE
from plugins.mount.indi_client import IndiSubprocessClient, IndiTcpSession


INTERFACE_BITS = {
    "mount": 1,
    "astro_camera": 2,
    "guider": 4,
    "focuser": 8,
    "filter_wheel": 16,
    "dome": 32,
    "gps": 64,
    "weather": 128,
    "adaptive_optics": 256,
    "dustcap": 512,
    "lightbox": 1024,
    "detector": 2048,
    "rotator": 4096,
    "spectrograph": 8192,
    "correlator": 16384,
    "auxiliary": 32768,
}

_PHOTO_DRIVER_TOKENS = ("gphoto", "dslr")


def _raw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return value.get("value", value.get("state"))
    return value


def _text(prop: Mapping[str, Any], *names: str) -> str | None:
    for name in names:
        value = _raw(prop.get(name))
        if value not in (None, ""):
            text = str(value).strip()
            if text:
                return text
    return None


def _first_serial(properties: Mapping[str, Mapping[str, Any]]) -> str | None:
    """Return only an explicitly documented hardware serial field.

    INDI property names containing words such as SERIAL are not necessarily
    serial numbers: switch/status elements may contain values like On.
    Do not heuristically promote arbitrary properties into physical identity.
    """
    preferred = (
        ("DEVICE_INFO", ("SERIAL", "SERIAL_NUMBER", "SERIALNUMBER", "DEVICE_SERIAL")),
        ("MOUNTINFORMATION", ("MOUNT_SERIAL", "SERIAL", "SERIAL_NUMBER", "SERIALNUMBER")),
        ("VERSION", ("SN", "SERIAL", "SERIAL_NUMBER")),
    )
    invalid = {"unknown", "n/a", "none", "on", "off", "true", "false"}
    for prop_name, elements in preferred:
        prop = properties.get(prop_name, {})
        value = _text(prop, *elements)
        if value and value.casefold() not in invalid:
            return value
    return None


def _stable_serial_path(serial_port: Any) -> str | None:
    raw = str(serial_port or "").strip()
    if not raw:
        return None
    prefix = "/dev/serial/by-id/"
    if raw.startswith(prefix):
        # INDI may keep CONNECT=On and the configured DEVICE_PORT after a
        # USB-serial device has been unplugged. A stable name is physical
        # presence evidence only while the symlink still exists.
        return raw if os.path.exists(raw) else None

    target = os.path.realpath(raw)
    root = "/dev/serial/by-id"
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return None

    for name in names:
        candidate = os.path.join(root, name)
        try:
            if os.path.realpath(candidate) == target:
                return candidate
        except OSError:
            continue
    return None


def _driver_interface(properties: Mapping[str, Mapping[str, Any]]) -> int:
    raw_value = _text(properties.get("DRIVER_INFO", {}), "DRIVER_INTERFACE")
    try:
        return int(float(raw_value)) if raw_value is not None else 0
    except (TypeError, ValueError):
        return 0


def _signature_categories(properties: Mapping[str, Mapping[str, Any]]) -> set[str]:
    names = set(properties)
    categories: set[str] = set()

    if names & {
        "EQUATORIAL_EOD_COORD",
        "EQUATORIAL_COORD",
        "TELESCOPE_MOTION_NS",
        "TELESCOPE_MOTION_WE",
        "TELESCOPE_PARK",
        "TELESCOPE_HOME",
    }:
        categories.add("mount")

    if names & {
        "ABS_FOCUS_POSITION",
        "REL_FOCUS_POSITION",
        "FOCUS_MOTION",
        "FOCUS_ABORT_MOTION",
        "FOCUS_MAX",
        "FOCUS_SYNC",
    }:
        categories.add("focuser")

    if names & {"CCD_EXPOSURE", "CCD_INFO", "CCD_FRAME", "CCD1"}:
        categories.add("astro_camera")
    if "FILTER_SLOT" in names:
        categories.add("filter_wheel")
    if names & {"ABS_ROTATOR_ANGLE", "ROTATOR_ANGLE", "SYNC_ROTATOR_ANGLE"}:
        categories.add("rotator")
    if names & {"DOME_MOTION", "DOME_ABSOLUTE_POSITION", "DOME_PARK"}:
        categories.add("dome")
    if "WEATHER_PARAMETERS" in names:
        categories.add("weather")

    return categories


class IndiDeviceManager:
    """Catalogue and transport probing for logical INDI devices."""

    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 7624,
        timeout_s: float = 2.0,
        client: IndiSubprocessClient | None = None,
        bindings_file: Path | str | None = None,
    ) -> None:
        self.host = str(host)
        self.port = int(port)
        self.timeout_s = float(timeout_s)
        self.bindings_file = Path(bindings_file or INDI_MOUNT_BINDINGS_FILE)
        self.client = client or IndiSubprocessClient(
            host=self.host,
            port=self.port,
            timeout_s=self.timeout_s,
        )

    @staticmethod
    def _is_photo_camera(properties: Mapping[str, Mapping[str, Any]]) -> bool:
        info = properties.get("DRIVER_INFO", {})
        haystack = " ".join(
            value or ""
            for value in (
                _text(info, "DRIVER_EXEC"),
                _text(info, "DRIVER_NAME"),
            )
        ).casefold()
        return any(token in haystack for token in _PHOTO_DRIVER_TOKENS)

    @staticmethod
    def _categories(
        properties: Mapping[str, Mapping[str, Any]],
    ) -> list[str]:
        mask = _driver_interface(properties)
        categories = {
            name for name, bit in INTERFACE_BITS.items()
            if mask & bit
        }
        categories.update(_signature_categories(properties))
        if not categories:
            categories.add("unknown")
        return sorted(categories)

    def _entry(
        self,
        device_name: str,
        properties: Mapping[str, Mapping[str, Any]],
    ) -> dict[str, Any] | None:
        if self._is_photo_camera(properties):
            return None

        info = properties.get("DRIVER_INFO", {})
        device_info = properties.get("DEVICE_INFO", {})
        mount_info = properties.get("MOUNTINFORMATION", {})
        port_prop = properties.get("DEVICE_PORT", {})
        connection = properties.get("CONNECTION", {})

        driver_exec = _text(info, "DRIVER_EXEC")
        driver_name = _text(info, "DRIVER_NAME")
        driver_version = _text(info, "DRIVER_VERSION")
        manufacturer = (
            _text(mount_info, "MANUFACTURER", "MOUNT_MANUFACTURER")
            or _text(device_info, "MANUFACTURER", "DEVICE_MANUFACTURER")
        )
        model = (
            _text(mount_info, "MOUNT_MODEL", "MODEL")
            or _text(device_info, "MODEL", "DEVICE_MODEL")
            or device_name
        )
        serial = _first_serial(properties)
        categories = self._categories(properties)
        connected = str(_raw(connection.get("CONNECT", "Off"))).casefold() in {
            "on", "true", "1"
        }
        # A disconnected driver's DEVICE_PORT is only a configured/default
        # value; it is not proof that the advertised mount owns that serial
        # transport. Another USB serial device may currently occupy the same
        # ttyUSB number. Only a connected INDI device is physical presence
        # evidence. Keep a stable serial path only after INDI has connected.
        configured_port = _text(port_prop, "PORT")
        serial_path = (
            _stable_serial_path(configured_port)
            if connected
            else None
        )
        # For serial INDI devices, CONNECT=On alone is insufficient: drivers
        # can retain stale connection state after hot-unplug. When a serial
        # transport is configured, require its stable by-id transport to
        # still exist before publishing physical presence. Non-serial INDI
        # devices keep the normal connection-state semantics.
        serial_transport = bool(
            configured_port
            and (
                configured_port.startswith("/dev/")
                or configured_port.startswith("/dev/serial/")
            )
        )
        present = connected and (serial_path is not None if serial_transport else True)

        return {
            "backend": "indi",
            "device_name": device_name,
            "device_id": f"indi:{self.host}:{self.port}:{device_name}",
            "manufacturer": manufacturer,
            "model": model,
            "serial": serial,
            "fallback_physical_path": serial_path,
            "host": self.host,
            "port": self.port,
            "driver_exec": driver_exec,
            "driver_name": driver_name,
            "driver_version": driver_version,
            "driver_interface": _driver_interface(properties),
            "categories": categories,
            "connected": connected,
            "present": present,
        }

    @staticmethod
    def _reserved_serial_transports() -> set[str]:
        """Return serial transports owned by known non-mount subsystems.

        /dev/gps0 is the stable OS-level GPS resource created by the SolarTrigger
        udev configuration. Resolve it back to /dev/serial/by-id when possible so
        INDI mount probing never writes to the GPS. No VID/PID or USB chipset is
        used here to guess a device role.
        """
        reserved: set[str] = set()
        gps_alias = "/dev/gps0"
        if os.path.exists(gps_alias):
            stable = _stable_serial_path(gps_alias)
            if stable:
                reserved.add(stable)
            else:
                # Keep the resolved tty as a fallback for systems where a
                # by-id alias is unavailable.
                reserved.add(os.path.realpath(gps_alias))
        return reserved

    @classmethod
    def _serial_candidates(cls) -> list[str]:
        """Return unreserved stable serial transports, chipset-agnostic."""
        root = "/dev/serial/by-id"
        try:
            names = sorted(os.listdir(root))
        except OSError:
            return []

        reserved = cls._reserved_serial_transports()
        reserved_real = {os.path.realpath(path) for path in reserved}
        candidates = []
        for name in names:
            candidate = os.path.join(root, name)
            if not os.path.exists(candidate):
                continue
            if candidate in reserved:
                continue
            if os.path.realpath(candidate) in reserved_real:
                continue
            candidates.append(candidate)
        return candidates

    def _load_mount_bindings(self) -> dict[str, str]:
        """Load learned logical-mount to stable-transport bindings safely."""
        try:
            payload = json.loads(self.bindings_file.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return {}
        if not isinstance(payload, Mapping):
            return {}

        raw_bindings = payload.get("bindings", payload)
        if not isinstance(raw_bindings, Mapping):
            return {}

        bindings: dict[str, str] = {}
        for device_name, transport in raw_bindings.items():
            name = str(device_name or "").strip()
            path = str(transport or "").strip()
            if name and path.startswith("/dev/serial/by-id/"):
                bindings[name] = path
        return bindings

    def _load_reconnect_required(self) -> set[str]:
        """Load mounts whose INDI connection survived a physical USB loss."""
        try:
            payload = json.loads(self.bindings_file.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return set()
        if not isinstance(payload, Mapping):
            return set()
        raw = payload.get("reconnect_required", [])
        if not isinstance(raw, list):
            return set()
        return {
            str(name).strip()
            for name in raw
            if str(name or "").strip()
        }

    def _persist_mount_state(
        self,
        bindings: Mapping[str, str],
        reconnect_required: set[str],
    ) -> None:
        """Persist bindings and hot-plug recovery state atomically."""
        clean = {
            str(name): str(path)
            for name, path in bindings.items()
            if str(name).strip()
            and str(path).startswith("/dev/serial/by-id/")
        }
        reconnect = sorted(
            name for name in reconnect_required if name in clean
        )
        self.bindings_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "bindings": clean,
            "reconnect_required": reconnect,
        }
        fd, temporary = tempfile.mkstemp(
            prefix=self.bindings_file.name + ".",
            dir=str(self.bindings_file.parent),
            text=True,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.bindings_file)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass

    def _save_mount_bindings(self, bindings: Mapping[str, str]) -> None:
        """Atomically persist only stable by-id bindings."""
        clean = {
            str(name): str(path)
            for name, path in bindings.items()
            if str(name).strip()
            and str(path).startswith("/dev/serial/by-id/")
        }
        self.bindings_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": 1,
            "bindings": clean,
            "reconnect_required": sorted(
                name for name in self._load_reconnect_required()
                if name in clean
            ),
        }
        fd, temporary = tempfile.mkstemp(
            prefix=self.bindings_file.name + ".",
            dir=str(self.bindings_file.parent),
            text=True,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.bindings_file)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass

    def _remember_mount_binding(
        self,
        device_name: str,
        transport: str,
        bindings: dict[str, str],
    ) -> bool:
        stable = _stable_serial_path(transport)
        if not stable or not stable.startswith("/dev/serial/by-id/"):
            return False
        if bindings.get(device_name) == stable:
            return False
        bindings[device_name] = stable
        try:
            self._save_mount_bindings(bindings)
        except OSError:
            return False
        return True

    def _probe_mount_transport(
        self,
        device_name: str,
        candidate: str,
        *,
        timeout_s: float = 3.0,
        poll_interval: float = 0.10,
    ) -> bool:
        """Probe one mount using one persistent duplex INDI TCP session."""
        connected = False
        try:
            with IndiTcpSession(
                host=self.host,
                port=self.port,
                device=device_name,
                timeout_s=self.timeout_s,
            ) as session:
                session.set_text("DEVICE_PORT", {"PORT": candidate})
                session.set_switch(
                    "CONNECTION",
                    {"CONNECT": "On", "DISCONNECT": "Off"},
                )
                connected = session.wait_for(
                    "CONNECTION",
                    "CONNECT",
                    {"On", "true", "1"},
                    timeout_s,
                )
                if not connected:
                    try:
                        session.set_switch(
                            "CONNECTION",
                            {"CONNECT": "Off", "DISCONNECT": "On"},
                        )
                    except Exception:
                        pass
        except Exception:
            connected = False
        return connected

    def _reconnect_mount_transport(
        self,
        device_name: str,
        candidate: str,
        *,
        timeout_s: float = 3.0,
    ) -> bool:
        """Recover a stale CONNECT=On after physical serial hot-unplug.

        Some INDI drivers retain CONNECT=On when their USB serial transport
        disappears. A plain CONNECT request can then succeed from stale cached
        state without reopening the serial device. Force a complete
        DISCONNECT -> DEVICE_PORT -> CONNECT cycle on the learned transport.
        """
        try:
            with IndiTcpSession(
                host=self.host,
                port=self.port,
                device=device_name,
                timeout_s=self.timeout_s,
            ) as session:
                session.set_switch(
                    "CONNECTION",
                    {"CONNECT": "Off", "DISCONNECT": "On"},
                )
                if not session.wait_for(
                    "CONNECTION", "CONNECT", {"Off", "false", "0"}, timeout_s
                ):
                    return False
                session.set_text("DEVICE_PORT", {"PORT": candidate})
                session.set_switch(
                    "CONNECTION",
                    {"CONNECT": "On", "DISCONNECT": "Off"},
                )
                return session.wait_for(
                    "CONNECTION",
                    "CONNECT",
                    {"On", "true", "1"},
                    timeout_s,
                )
        except Exception:
            return False

    def _autoconnect_mounts(
        self,
        devices: dict[str, dict[str, dict[str, str]]],
    ) -> bool:
        """Connect disconnected mount drivers to distinct serial transports.

        Returns True when at least one connection was established, so the
        caller can refresh the catalogue and publish the live INDI state.
        """
        candidates = self._serial_candidates()
        bindings = self._load_mount_bindings()
        reconnect_required = self._load_reconnect_required()

        connected_bindings = {
            name: path
            for name, properties in devices.items()
            if not self._is_photo_camera(properties)
            and "mount" in self._categories(properties)
            and str(
                _raw(properties.get("CONNECTION", {}).get("CONNECT", "Off"))
            ).casefold() in {"on", "true", "1"}
            if (path := _stable_serial_path(
                _text(properties.get("DEVICE_PORT", {}), "PORT")
            ))
        }
        # A connected transport is normally claimed. A mount marked for
        # forced recovery is the exception: its CONNECT=On state is stale,
        # so its own learned transport must remain available to the recovery
        # cycle below. Other mounts still cannot claim/reuse it.
        claimed = {
            path
            for name, path in connected_bindings.items()
            if name not in reconnect_required
        }

        # A live INDI connection on an existing stable by-id transport is
        # authoritative ownership evidence. Learn it even when the driver was
        # already connected before SolarTrigger started, so upgrading an
        # existing installation does not require disconnecting hardware.
        binding_owners: dict[str, list[str]] = {}
        for name, path in connected_bindings.items():
            binding_owners.setdefault(path, []).append(name)

        bindings_changed = False
        for name, path in connected_bindings.items():
            # Never learn ambiguous ownership if two logical drivers claim the
            # same live transport. Keep the transport claimed for this scan,
            # but require a later unambiguous observation before persisting it.
            if len(binding_owners[path]) != 1:
                continue
            if bindings.get(name) != path:
                bindings[name] = path
                bindings_changed = True
        if bindings_changed:
            # Persistence is an optimisation/safety memory, not a prerequisite
            # for live mount discovery. A read-only or temporarily unavailable
            # state directory must never suppress INDI probing.
            try:
                self._save_mount_bindings(bindings)
            except OSError:
                pass

        # CONNECT=On is not trustworthy after a serial transport vanishes.
        # Persist that observation so a later discovery (even in a new
        # process/manager instance) knows it must force a full reconnect.
        recovery_changed = False
        for device_name, properties in devices.items():
            if self._is_photo_camera(properties):
                continue
            if "mount" not in self._categories(properties):
                continue
            learned = bindings.get(device_name)
            if not learned:
                continue
            connected = str(
                _raw(properties.get("CONNECTION", {}).get("CONNECT", "Off"))
            ).casefold() in {"on", "true", "1"}
            if connected and not os.path.exists(learned):
                if device_name not in reconnect_required:
                    reconnect_required.add(device_name)
                    recovery_changed = True
        if recovery_changed:
            try:
                self._persist_mount_state(bindings, reconnect_required)
            except OSError:
                pass

        changed = False

        for device_name in sorted(devices):
            properties = devices[device_name]
            if self._is_photo_camera(properties):
                continue
            if "mount" not in self._categories(properties):
                continue
            connection = properties.get("CONNECTION", {})
            connected = str(
                _raw(connection.get("CONNECT", "Off"))
            ).casefold() in {"on", "true", "1"}
            learned = bindings.get(device_name)

            if connected and device_name not in reconnect_required:
                continue

            # A previous discovery observed CONNECT=On while the learned USB
            # transport was physically absent. When that exact transport
            # returns, force a complete driver reconnect instead of trusting
            # the stale INDI switch state.
            if device_name in reconnect_required:
                if learned and learned in candidates and learned not in claimed:
                    if self._reconnect_mount_transport(device_name, learned):
                        claimed.add(learned)
                        reconnect_required.discard(device_name)
                        try:
                            self._persist_mount_state(
                                bindings, reconnect_required
                            )
                        except OSError:
                            pass
                        changed = True
                continue

            # Once a logical mount has a learned stable transport, never probe
            # other serial devices merely because that transport is absent.
            # Hot-unplug therefore means "mount absent", not "search every
            # remaining serial port". A broad safe scan is reserved for mounts
            # which have never been learned.
            if learned:
                if learned in candidates and learned not in claimed:
                    if self._probe_mount_transport(device_name, learned):
                        claimed.add(learned)
                        changed = True
                continue

            for candidate in candidates:
                if candidate in claimed:
                    continue
                if self._probe_mount_transport(device_name, candidate):
                    claimed.add(candidate)
                    self._remember_mount_binding(
                        device_name,
                        candidate,
                        bindings,
                    )
                    changed = True
                    break
        return changed

    def discover(self) -> list[dict[str, Any]]:
        devices = self.client.get_all_devices()
        if self._autoconnect_mounts(devices):
            devices = self.client.get_all_devices()
        result = []
        for device_name in sorted(devices):
            properties = devices.get(device_name)
            if not isinstance(properties, Mapping):
                continue
            entry = self._entry(str(device_name), properties)
            if entry is not None:
                result.append(entry)
        return result

    @staticmethod
    def inventory_entries(
        catalog: list[dict[str, Any]],
        category: str,
    ) -> list[dict[str, Any]]:
        """Project the generic catalogue onto a current RIG device category."""
        if category not in {"mount", "focuser"}:
            return []

        entries = []
        for source in catalog:
            categories = source.get("categories") or []
            if category not in categories:
                continue
            if source.get("present") is not True:
                continue
            entry = dict(source)
            entry["category"] = category
            entry["backend"] = "indi"
            entry["pilotable"] = True
            entries.append(entry)
        return entries


__all__ = ["INTERFACE_BITS", "IndiDeviceManager"]
