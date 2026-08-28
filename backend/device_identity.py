"""Pure helpers for validating device identities across rigs."""

from __future__ import annotations

import re
from typing import Any


DEVICE_CATEGORIES = ("camera", "mount", "focuser")
_USB_BUS_DEVICE_PATTERN = re.compile(r"^usb:\d+,\d+$")


def is_usb_bus_device(value: str) -> bool:
    """Return whether *value* is a transient USB bus/device address."""

    return isinstance(value, str) and _USB_BUS_DEVICE_PATTERN.fullmatch(value) is not None


def identity_key(entry: dict[str, Any]) -> tuple[str, str] | None:
    """Return the stable identity key available in a device entry."""

    serial = entry.get("serial")
    if serial and not is_usb_bus_device(serial):
        return "serial", serial

    fallback = entry.get("fallback_physical_path")
    if fallback:
        return "fallback", fallback

    return None


def validate_and_collect_warnings(
    config: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Validate device identities and return the rigs plus fallback warnings."""

    rigs = config.get("rigs", [])

    # USB addresses are rejected before collision detection, regardless of the
    # order in which invalid entries occur in the configuration.
    for rig in rigs:
        devices = rig.get("devices", {})
        for category in DEVICE_CATEGORIES:
            entry = devices.get(category)
            if isinstance(entry, dict) and is_usb_bus_device(entry.get("serial")):
                raise ValueError(
                    f"invalid device serial: {category} serial cannot use "
                    f"usb:bus,device ({entry['serial']})"
                )

    warnings: list[str] = []
    identities: dict[str, set[tuple[str, str]]] = {
        category: set() for category in DEVICE_CATEGORIES
    }
    for rig in rigs:
        rig_id = rig.get("rig_id")
        devices = rig.get("devices", {})
        for category in DEVICE_CATEGORIES:
            entry = devices.get(category)
            if not isinstance(entry, dict):
                continue

            key = identity_key(entry)
            if key is None:
                continue
            identity_type, value = key
            if key in identities[category]:
                raise ValueError(
                    f"duplicate device identity: {category} "
                    f"{identity_type}={value}"
                )
            identities[category].add(key)

            if identity_type == "fallback":
                warnings.append(
                    f"RIG {rig_id} {category}: using fallback physical path as "
                    "identity; prefer a stable serial"
                )

    return rigs, warnings


__all__ = [
    "identity_key",
    "is_usb_bus_device",
    "validate_and_collect_warnings",
]
