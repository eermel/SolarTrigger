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


RIG_CATEGORIES = ("camera", "mount", "focuser")
CATEGORIES = (*RIG_CATEGORIES, "astro")
SYSFS_USB_DEVICES = Path("/sys/bus/usb/devices")
_USB_LOCATOR = re.compile(r"^usb:(\d+),(\d+)$")
_cache_lock = threading.Lock()
_cache: dict[str, list[dict[str, Any]]] = {name: [] for name in CATEGORIES}


def get_cached_inventory() -> dict[str, list[dict[str, Any]]]:
    """Return an isolated copy of the latest snapshot without probing."""

    with _cache_lock:
        return deepcopy(_cache)


def refresh_inventory(
    *,
    reserved_mounts: Iterable[Mapping[str, Any]] | None = None,
    reserved_focusers: Iterable[Mapping[str, Any]] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Perform one explicit discovery pass and atomically replace the cache.

    INDI is queried once and becomes the primary astronomical equipment
    catalogue. DSLR/mirrorless cameras remain on the gphoto2 path.
    """

    indi_catalog = _discover_indi_catalog()
    mount_entries = (
        _discover_mounts()
        if reserved_mounts is None and not indi_catalog
        else _discover_mounts(
            reserved_mounts=reserved_mounts,
            indi_catalog=indi_catalog,
        )
    )
    focuser_entries = (
        _discover_focusers()
        if reserved_focusers is None and not indi_catalog
        else _discover_focusers(
            reserved_focusers=reserved_focusers,
            indi_catalog=indi_catalog,
        )
    )
    discovered = {
        "camera": _discover_cameras(),
        "mount": mount_entries,
        "focuser": focuser_entries,
        "astro": indi_catalog,
    }
    normalized = {
        category: (
            _normalize_astro_entries(discovered.get(category, ()))
            if category == "astro"
            else _normalize_entries(category, discovered.get(category, ()))
        )
        for category in CATEGORIES
    }
    for category in RIG_CATEGORIES:
        build_display_labels(normalized[category])
    with _cache_lock:
        _cache.clear()
        _cache.update(deepcopy(normalized))
    return deepcopy(normalized)


def reclassify_cached_cameras() -> dict[str, list[dict[str, Any]]]:
    """Re-evaluate cached camera backends without probing hardware.

    Characterization publishes a new profile while the physical inventory is
    intentionally frozen.  Reclassifying the existing snapshot makes that
    camera immediately eligible for validation without another USB discovery
    pass.  No gphoto2/INDI/ZWO probe is performed here.
    """

    from backend.camera_profiles import profile_for_model

    with _cache_lock:
        updated = deepcopy(_cache)
        cameras = updated.get("camera", [])
        for entry in cameras:
            if not isinstance(entry, dict):
                continue
            profile = profile_for_model(entry.get("model"))
            backend = profile.get("backend") if isinstance(profile, dict) else None
            entry["backend"] = backend or "gphoto2"
            entry["pilotable"] = bool(backend)
        build_display_labels(cameras)
        _cache.clear()
        _cache.update(deepcopy(updated))
        return deepcopy(updated)


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

    for (category, _base), indices in groups.items():
        suffixes: dict[int, str] = {}
        serials = {
            index: serial
            for index in indices
            if (serial := _stable_serial(entries[index]))
        }
        distinct_serials = set(serials.values())
        distinct_entries = len(distinct_serials) + len(indices) - len(serials)
        force_serial_label = category in {"mount", "focuser"}

        if (distinct_entries > 1 or force_serial_label) and serials:
            width = 4
            longest = max(len(serial) for serial in distinct_serials)
            while width < longest:
                values = {serial[-width:] for serial in distinct_serials}
                if len(values) == len(distinct_serials):
                    break
                width += 1
            suffixes = {
                index: serial[-width:]
                for index, serial in serials.items()
            }

        separator = " - " if force_serial_label else " · "
        for index in indices:
            suffix = suffixes.get(index)
            entries[index]["display_label"] = (
                f"{bases[index]}{separator}{suffix}"
                if suffix
                else bases[index]
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
        sysfs = _usb_identity(port)
        usb_serial = _text(sysfs.get("serial"))

        # The USB/sysfs serial is the canonical physical identity.  Unlike
        # protocol metadata it remains available while a persistent worker
        # already owns the camera.  Avoid opening the camera during refresh
        # when that stable identity is available.
        protocol = (
            {}
            if usb_serial
            else _read_gphoto_metadata(gp, port)
        )

        model = _text(protocol.get("model")) or _text(detected_model)
        manufacturer = (
            _text(protocol.get("manufacturer"))
            or _manufacturer(model)
        )
        protocol_serial = _text(protocol.get("serial"))
        serial = usb_serial or protocol_serial

        backend = _camera_backend(model)
        # A brand-level fallback is not evidence that this model was measured.
        # Legacy models remain eligible through their existing timing document.
        from backend.camera_profiles import is_characterized_model
        if not is_characterized_model(manufacturer, model):
            backend = "gphoto2"
        entry = {
            "category": "camera",
            "backend": backend,
            "pilotable": backend != "gphoto2",
            "manufacturer": manufacturer,
            "model": model,
            "serial": serial,
            "fallback_physical_path": (
                None if serial else sysfs.get("physical_path")
            ),
            "present": True,
            "transport_locator": _text(port),
        }
        entries.append(entry)
    return entries


def _discover_indi_catalog() -> list[dict[str, Any]]:
    """Return all non-gphoto astronomical devices advertised by INDI."""
    try:
        from backend.indi_device_manager import IndiDeviceManager

        return IndiDeviceManager().discover()
    except Exception:
        return []


def _reserved_entries(
    category: str,
    sources: Iterable[Mapping[str, Any]] | None,
) -> tuple[list[dict[str, Any]], set[str], set[str]]:
    entries = []
    physical_paths: set[str] = set()
    device_ids: set[str] = set()

    for source in sources or ():
        if not isinstance(source, Mapping):
            continue
        backend = _text(source.get("backend"))
        if not backend or backend in {"none", "external"}:
            continue

        entry = dict(source)
        entry.setdefault("category", category)

        physical_path = _first_text(
            source,
            "fallback_physical_path",
            "physical_path",
        )
        if physical_path:
            try:
                present = Path(physical_path).exists()
            except OSError:
                present = False
            if backend == "indi":
                # For INDI, the logical device identity remains valid even
                # when a disconnected serial controller is not currently
                # visible. Presence will be reconciled from the INDI catalogue.
                present = True
            if present:
                entry["fallback_physical_path"] = physical_path
                physical_paths.add(physical_path)

        device_id = _first_text(source, "device_id")
        if device_id:
            device_ids.add(device_id)

        entry["present"] = True
        entries.append(entry)

    return entries, physical_paths, device_ids


def _discover_mounts(
    reserved_mounts: Iterable[Mapping[str, Any]] | None = None,
    indi_catalog: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Prefer INDI mounts; direct serial plugins remain migration fallbacks."""

    reserved_entries, reserved_paths, reserved_ids = _reserved_entries(
        "mount",
        reserved_mounts,
    )

    try:
        from backend.indi_device_manager import IndiDeviceManager

        indi_mounts = IndiDeviceManager.inventory_entries(
            indi_catalog or [],
            "mount",
        )
    except Exception:
        indi_mounts = []

    if indi_mounts:
        discovered = [
            entry for entry in indi_mounts
            if _text(entry.get("device_id")) not in reserved_ids
        ]
        return [*reserved_entries, *discovered]

    try:
        from plugins.mount import inventory_mounts

        discovered = list(inventory_mounts(
            log_fn=lambda *_args: None,
            exclude_physical_paths=reserved_paths,
        ))
        if discovered or reserved_entries:
            return [*reserved_entries, *discovered]
    except Exception:
        if reserved_entries:
            return reserved_entries

    return _discover_legacy_category("mount")


def _discover_focusers(
    reserved_focusers: Iterable[Mapping[str, Any]] | None = None,
    indi_catalog: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Prefer INDI focusers; vendor SDK plugins remain migration fallbacks."""

    reserved_entries, _reserved_paths, reserved_ids = _reserved_entries(
        "focuser",
        reserved_focusers,
    )

    try:
        from backend.indi_device_manager import IndiDeviceManager

        indi_focusers = IndiDeviceManager.inventory_entries(
            indi_catalog or [],
            "focuser",
        )
    except Exception:
        indi_focusers = []

    if indi_focusers:
        discovered = [
            entry for entry in indi_focusers
            if _text(entry.get("device_id")) not in reserved_ids
        ]
        return [*reserved_entries, *discovered]

    try:
        from plugins.focuser import inventory_focusers

        discovered = list(inventory_focusers(
            log_fn=lambda *_args: None,
            exclude_device_ids=reserved_ids,
        ))
        if discovered or reserved_entries:
            return [*reserved_entries, *discovered]
    except Exception:
        if reserved_entries:
            return reserved_entries
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
    if not values or all(value in (None, "") for value in values):
        values = [{}]

    entries = []
    for value in values:
        if value in (None, ""):
            continue
        if isinstance(value, Mapping):
            reported_category = _text(value.get("category"))
            if reported_category and reported_category != category:
                continue
            entry = dict(value)
            entry.setdefault("backend", result.get("suggested_plugin"))
        else:
            entry = {
                "backend": result.get("suggested_plugin") or _text(value),
                "model": _text(value),
            }
        entries.append(entry)
    return entries


def _normalize_entries(
    category: str, entries: Iterable[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    normalized = []
    for source in entries or ():
        if not isinstance(source, Mapping):
            continue
        serial = _first_text(source, "serial", "usb_serial")
        if serial and is_usb_bus_device(serial):
            serial = None
        device_id = _first_text(source, "device_id", "sdk_id")
        physical_path = _first_text(
            source, "fallback_physical_path", "physical_path"
        )
        device_name = _first_text(source, "device_name", "indi_device_name")
        entry = {
            "category": category,
            "backend": _text(source.get("backend")) or category,
            "manufacturer": _text(source.get("manufacturer")),
            "model": _text(source.get("model")),
            "serial": serial,
            "device_id": device_id,
            "fallback_physical_path": physical_path,
            "present": source.get("present") is not False,
            "transport_locator": _text(source.get("transport_locator")),
        }
        if device_name:
            entry["device_name"] = device_name

        for field in (
            "host",
            "port",
            "driver_exec",
            "driver_name",
            "driver_version",
            "driver_interface",
            "connected",
        ):
            if field in source and source.get(field) is not None:
                entry[field] = source.get(field)

        categories = source.get("categories")
        if isinstance(categories, (list, tuple)):
            entry["categories"] = [
                str(value) for value in categories if str(value).strip()
            ]

        alias = _text(source.get("alias"))
        if alias:
            entry["alias"] = alias

        entry["bindable"] = (
            serial is not None
            or device_id is not None
            or entry["fallback_physical_path"] is not None
        )
        if "pilotable" in source:
            entry["pilotable"] = source.get("pilotable") is True
        normalized.append(entry)
    return normalized


def _normalize_astro_entries(
    entries: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Normalize the complete INDI catalogue without making it RIG-bindable."""
    result = []
    for source in entries or ():
        if not isinstance(source, Mapping):
            continue
        entry = dict(source)
        entry["category"] = "astro"
        entry["pilotable"] = False
        entry["bindable"] = False
        result.append(entry)
    return result

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


def _first_text(source: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = _text(source.get(key))
        if value:
            return value
    return None


__all__ = [
    "build_display_labels",
    "get_cached_inventory",
    "reclassify_cached_cameras",
    "refresh_inventory",
]
