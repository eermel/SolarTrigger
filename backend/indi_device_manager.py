"""Generic discovery of astronomical devices exposed by one INDI server.

INDI is the primary discovery layer for astronomical equipment. DSLR/mirrorless
cameras controlled through the INDI gphoto driver are deliberately excluded:
SolarTrigger keeps those cameras on its characterized gphoto2 pipeline.

The manager is read-only. It enumerates properties advertised by an already
running indiserver and classifies logical INDI devices from the standard
DRIVER_INTERFACE bitmask, with property-signature fallbacks for incomplete
drivers.
"""

from __future__ import annotations

import os
from typing import Any, Mapping

from plugins.mount.indi_client import IndiSubprocessClient


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
    preferred = (
        ("DEVICE_INFO", ("SERIAL", "SERIAL_NUMBER", "SERIALNUMBER", "DEVICE_SERIAL")),
        ("MOUNTINFORMATION", ("MOUNT_SERIAL", "SERIAL", "SERIAL_NUMBER", "SERIALNUMBER")),
        ("VERSION", ("SN", "SERIAL", "SERIAL_NUMBER")),
    )
    for prop_name, elements in preferred:
        prop = properties.get(prop_name, {})
        value = _text(prop, *elements)
        if value and value.casefold() not in {"unknown", "n/a", "none"}:
            return value

    for prop_name, elements in properties.items():
        if not isinstance(elements, Mapping):
            continue
        for element, raw_value in elements.items():
            key = f"{prop_name}.{element}".casefold()
            if "serial" not in key and str(element).casefold() != "sn":
                continue
            value = str(_raw(raw_value) or "").strip()
            if value and value.casefold() not in {"unknown", "n/a", "none"}:
                return value
    return None


def _stable_serial_path(serial_port: Any) -> str | None:
    raw = str(serial_port or "").strip()
    if not raw:
        return None
    prefix = "/dev/serial/by-id/"
    if raw.startswith(prefix):
        return raw

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
    """Read-only catalogue of logical devices advertised by indiserver."""

    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 7624,
        timeout_s: float = 2.0,
        client: IndiSubprocessClient | None = None,
    ) -> None:
        self.host = str(host)
        self.port = int(port)
        self.timeout_s = float(timeout_s)
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
        # A loaded INDI driver advertises a logical device even when no
        # hardware is attached. For serial devices, only treat the device as
        # physically present when the configured port resolves to a currently
        # existing device. Do not assume anything about the USB/serial
        # chipset (FTDI, CH34x, Prolific, ...).
        configured_port = _text(port_prop, "PORT")
        serial_path = _stable_serial_path(configured_port)
        serial_transport_present = bool(
            configured_port
            and os.path.exists(os.path.realpath(configured_port))
        )
        present = connected or serial_transport_present

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

    def discover(self) -> list[dict[str, Any]]:
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
            entry = dict(source)
            entry["category"] = category
            entry["backend"] = "indi"
            entry["pilotable"] = True
            entries.append(entry)
        return entries


__all__ = ["INTERFACE_BITS", "IndiDeviceManager"]
