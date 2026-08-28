"""Runtime-only, multi-instance inventory of bindable hardware devices.

Discovery is deliberately explicit: :func:`get_cached_inventory` only returns
the last snapshot, while :func:`refresh_inventory` performs one provider pass.
USB bus/device addresses are retained only as transient transport locators.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import re
import threading
from typing import Any, Iterable, Mapping

from backend.device_identity import is_usb_bus_device


CATEGORIES = ("camera", "mount", "focuser")
SYSFS_USB_DEVICES = Path("/sys/bus/usb/devices")
_USB_LOCATOR = re.compile(r"^usb:(\d+),(\d+)$")
_cache_lock = threading.Lock()
_cache: dict[str, list[dict[str, Any]]] = {name: [] for name in CATEGORIES}


def get_cached_inventory() -> dict[str, list[dict[str, Any]]]:
    """Return an isolated copy of the latest snapshot without probing."""

    with _cache_lock:
        return deepcopy(_cache)


def refresh_inventory() -> dict[str, list[dict[str, Any]]]:
    """Perform one discovery pass and atomically replace the memory cache."""

    discovered = {
        "camera": _discover_cameras(),
        "mount": _discover_mounts(),
        "focuser": _discover_focusers(),
    }
    normalized = {
        category: _normalize_entries(category, discovered.get(category, ()))
        for category in CATEGORIES
    }
    for category in CATEGORIES:
        build_display_labels(normalized[category])
    with _cache_lock:
        _cache.clear()
        _cache.update(deepcopy(normalized))
    return deepcopy(normalized)


def build_display_labels(
    entries: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Add deterministic alias/model labels, disambiguated by stable serial.

    A four-character serial suffix is used where possible and is extended just
    far enough to distinguish otherwise equal labels. Transient USB locators
    are never eligible as label suffixes.
    """

    bases = [_label_base(entry) for entry in entries]
    groups: dict[tuple[str, str], list[int]] = {}
    for index, (entry, base) in enumerate(zip(entries, bases)):
        category = str(entry.get("category") or "")
        groups.setdefault((category, base), []).append(index)

    for indices in groups.values():
        suffixes: dict[int, str] = {}
        serial_indices = [
            index for index in indices if _stable_serial(entries[index])
        ]
        if len(indices) > 1 and serial_indices:
            width = 4
            longest = max(len(_stable_serial(entries[index])) for index in serial_indices)
            while width < longest:
                values = {
                    _stable_serial(entries[index])[-width:] for index in serial_indices
                }
                if len(values) == len(serial_indices):
                    break
                width += 1
            suffixes = {
                index: _stable_serial(entries[index])[-width:]
                for index in serial_indices
            }
        for index in indices:
            suffix = suffixes.get(index)
            entries[index]["display_label"] = (
                f"{bases[index]} · {suffix}" if suffix else bases[index]
            )
    return entries


def _discover_cameras() -> list[dict[str, Any]]:
    try:
        import gphoto2 as gp
    except Exception:
        return []

    try:
        detected = list(gp.Camera.autodetect())
    except Exception:
        return []

    entries = []
    for detected_model, port in detected:
        protocol = _read_gphoto_metadata(gp, port)
        sysfs = _usb_identity(port)
        model = _text(protocol.get("model")) or _text(detected_model)
        manufacturer = _text(protocol.get("manufacturer")) or _manufacturer(model)
        serial = _text(protocol.get("serial")) or sysfs.get("serial")
        entry = {
            "category": "camera",
            "backend": _camera_backend(model),
            "manufacturer": manufacturer,
            "model": model,
            "serial": serial,
            "fallback_physical_path": None if serial else sysfs.get("physical_path"),
            "present": True,
            "transport_locator": _text(port),
        }
        entries.append(entry)
    return entries


def _discover_mounts() -> list[dict[str, Any]]:
    return _discover_legacy_category("mount")


def _discover_focusers() -> list[dict[str, Any]]:
    return _discover_legacy_category("focuser")


def _discover_legacy_category(category: str) -> list[dict[str, Any]]:
    """Adapt the current single-instance detectors to the inventory contract."""

    try:
        from backend import devices

        result = getattr(devices, f"detect_{category}")()
    except Exception:
        return []
    if not isinstance(result, Mapping) or not result.get("detected"):
        return []
    info = result.get("detected_info")
    values = info if isinstance(info, list) else [info]
    return [
        value if isinstance(value, Mapping) else {
            "backend": result.get("suggested_plugin") or _text(value),
            "model": _text(value),
        }
        for value in values if value not in (None, "")
    ]


def _normalize_entries(
    category: str, entries: Iterable[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    normalized = []
    for source in entries or ():
        if not isinstance(source, Mapping):
            continue
        serial = _text(source.get("serial"))
        if serial and is_usb_bus_device(serial):
            serial = None
        entry = {
            "category": category,
            "backend": _text(source.get("backend")) or category,
            "manufacturer": _text(source.get("manufacturer")),
            "model": _text(source.get("model")),
            "serial": serial,
            "fallback_physical_path": _text(source.get("fallback_physical_path")),
            "present": True,
            "transport_locator": _text(source.get("transport_locator")),
        }
        alias = _text(source.get("alias"))
        if alias:
            entry["alias"] = alias
        entry["bindable"] = (
            serial is not None or entry["fallback_physical_path"] is not None
        )
        normalized.append(entry)
    return normalized


def _read_gphoto_metadata(gp: Any, port: str) -> dict[str, str | None]:
    camera = None
    try:
        camera = gp.Camera()
        port_list = gp.PortInfoList()
        port_list.load()
        camera.set_port_info(port_list[port_list.lookup_path(port)])
        camera.init()
        config = camera.get_config()
        return {
            "manufacturer": _config_value(config, "manufacturer"),
            "model": _config_value(config, "cameramodel", "model", "modelname"),
            "serial": _config_value(config, "serialnumber", "serial", "serial_number"),
        }
    except Exception:
        return {}
    finally:
        if camera is not None:
            try:
                camera.exit()
            except Exception:
                pass


def _config_value(config: Any, *names: str) -> str | None:
    for name in names:
        try:
            value = _text(config.get_child_by_name(name).get_value())
            if value:
                return value
        except Exception:
            continue
    return None


def _usb_identity(locator: Any) -> dict[str, str | None]:
    match = _USB_LOCATOR.fullmatch(str(locator or ""))
    if not match:
        return {"serial": None, "physical_path": None}
    busnum, devnum = (str(int(value)) for value in match.groups())
    try:
        candidates = sorted(SYSFS_USB_DEVICES.iterdir(), key=lambda path: path.name)
    except OSError:
        return {"serial": None, "physical_path": None}
    for path in candidates:
        if (_read(path / "busnum") == busnum and _read(path / "devnum") == devnum):
            serial = _text(_read(path / "serial"))
            physical = (
                f"sysfs-usb:{path.name}"
                if re.fullmatch(r"\d+-[\d.]+", path.name)
                else None
            )
            return {"serial": serial, "physical_path": physical}
    return {"serial": None, "physical_path": None}


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def _camera_backend(model: str | None) -> str:
    try:
        from backend.devices import suggest_camera_plugin

        return suggest_camera_plugin(model) or "gphoto2"
    except Exception:
        return "gphoto2"


def _manufacturer(model: str | None) -> str | None:
    value = _text(model)
    return value.split(None, 1)[0] if value and " " in value else None


def _label_base(entry: Mapping[str, Any]) -> str:
    alias = _text(entry.get("alias"))
    if alias:
        return alias
    manufacturer = _text(entry.get("manufacturer"))
    model = _text(entry.get("model"))
    if manufacturer and model:
        if model.casefold().startswith(manufacturer.casefold() + " "):
            return model
        return f"{manufacturer} {model}"
    return manufacturer or model or _text(entry.get("backend")) or "Device"


def _stable_serial(entry: Mapping[str, Any]) -> str:
    serial = _text(entry.get("serial"))
    return serial if serial and not is_usb_bus_device(serial) else ""


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


__all__ = ["build_display_labels", "get_cached_inventory", "refresh_inventory"]
