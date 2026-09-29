"""Optional camera capabilities discovered outside the timed exposure contract.

This module is deliberately conservative:
- absence/failure never makes camera characterization fail;
- a SET capability is only persisted after a real transition plus readback;
- EFCS is not treated as a fully electronic shutter;
- local camera time and UTC camera time are kept semantically distinct.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import re
import time
import unicodedata


_LOCAL_DATETIME_NAMES = {
    "datetime",
    "cameradatetime",
    "dateandtime",
    "cameratime",
}
_UTC_DATETIME_NAMES = {
    "datetimeutc",
    "utcdatetime",
    "utcdatetime",
}
_TIMEZONE_NAMES = {
    "timezone",
    "timezoneoffset",
    "utcoffset",
    "timezonecode",
    "timezonename",
}
_SHUTTER_MODE_NAMES = {
    "shuttertype",
    "shuttermode",
    "shuttermechanism",
    "shutterselection",
    "electronicshutter",
}


def _storage_scalar(item, name):
    """Read one libgphoto2 storage field from a SWIG object or test mapping."""
    try:
        if isinstance(item, dict):
            return item.get(name)
        return getattr(item, name)
    except Exception:
        return None


def _storage_number(value, *, allow_zero=True):
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if number < 0 or (number == 0 and not allow_zero):
        return None
    return number


def read_camera_storage(camera):
    """Return a normalized, best-effort snapshot of camera storage media.

    libgphoto2 exposes storage capacity in KiB through get_storageinfo().
    This capability is optional: unsupported cameras and transient read errors
    return a structured non-fatal result instead of raising.
    """
    getter = getattr(camera, "get_storageinfo", None)
    if not callable(getter):
        return {
            "supported": False,
            "status": "unsupported",
            "media": [],
            "media_count": 0,
        }

    try:
        raw_media = list(getter() or [])
    except Exception as exc:
        return {
            "supported": False,
            "status": "error",
            "media": [],
            "media_count": 0,
            "error": f"{type(exc).__name__}: {exc}",
        }

    media = []
    for index, item in enumerate(raw_media):
        capacity_kib = _storage_number(
            _storage_scalar(item, "capacitykbytes"),
            allow_zero=False,
        )
        free_kib = _storage_number(
            _storage_scalar(item, "freekbytes"),
            allow_zero=True,
        )
        # A zero free-space value is meaningful when total capacity is known:
        # it is exactly the full-card condition that this diagnostic must expose.
        if capacity_kib is None and free_kib == 0:
            free_kib = None
        if (
            capacity_kib is not None
            and free_kib is not None
            and free_kib > capacity_kib
        ):
            free_kib = None

        used_kib = (
            capacity_kib - free_kib
            if capacity_kib is not None and free_kib is not None
            else None
        )
        free_percent = (
            round((free_kib / capacity_kib) * 100.0, 2)
            if capacity_kib and free_kib is not None
            else None
        )

        entry = {
            "index": index,
            "basedir": _storage_scalar(item, "basedir"),
            "label": _storage_scalar(item, "label"),
            "description": _storage_scalar(item, "description"),
            "capacity_kib": capacity_kib,
            "free_kib": free_kib,
            "used_kib": used_kib,
            "free_percent": free_percent,
            "free_images": _storage_number(
                _storage_scalar(item, "freeimages"),
                allow_zero=True,
            ),
        }
        media.append(entry)

    capacities = [
        item["capacity_kib"]
        for item in media
        if item["capacity_kib"] is not None
    ]
    free_values = [
        item["free_kib"]
        for item in media
        if item["free_kib"] is not None
    ]
    complete_capacity = len(capacities) == len(media) and bool(media)
    complete_free = len(free_values) == len(media) and bool(media)

    total_capacity_kib = sum(capacities) if complete_capacity else None
    total_free_kib = sum(free_values) if complete_free else None
    total_used_kib = (
        total_capacity_kib - total_free_kib
        if total_capacity_kib is not None and total_free_kib is not None
        else None
    )
    total_free_percent = (
        round((total_free_kib / total_capacity_kib) * 100.0, 2)
        if total_capacity_kib and total_free_kib is not None
        else None
    )

    return {
        "supported": True,
        "status": "ok" if media else "no_media",
        "media": media,
        "media_count": len(media),
        "total_capacity_kib": total_capacity_kib,
        "total_free_kib": total_free_kib,
        "total_used_kib": total_used_kib,
        "total_free_percent": total_free_percent,
        "total_free_images": (
            sum(
                item["free_images"]
                for item in media
                if item["free_images"] is not None
            )
            if media
            and all(item["free_images"] is not None for item in media)
            else None
        ),
    }


def storage_capability_from_snapshot(snapshot):
    """Persist only stable support metadata, never stale free-space values."""
    media = snapshot.get("media") if isinstance(snapshot, dict) else []
    media = media if isinstance(media, list) else []
    return {
        "query_supported": bool(
            isinstance(snapshot, dict) and snapshot.get("supported") is True
        ),
        "media_count_at_characterization": len(media),
        "capacity_reported": any(
            item.get("capacity_kib") is not None
            for item in media
            if isinstance(item, dict)
        ),
        "free_space_reported": any(
            item.get("free_kib") is not None
            for item in media
            if isinstance(item, dict)
        ),
        "free_images_reported": any(
            item.get("free_images") is not None
            for item in media
            if isinstance(item, dict)
        ),
        **(
            {"probe_error": snapshot.get("error")}
            if isinstance(snapshot, dict) and snapshot.get("error")
            else {}
        ),
    }


def format_storage_snapshot(snapshot):
    """Short operator-facing storage summary for characterization logs."""
    if not isinstance(snapshot, dict) or snapshot.get("supported") is not True:
        error = snapshot.get("error") if isinstance(snapshot, dict) else None
        return f"unavailable{': ' + error if error else ''}"

    media = snapshot.get("media") or []
    if not media:
        return "supported; no storage media reported"

    def gib(kib):
        return f"{float(kib) / (1024.0 * 1024.0):.2f} GiB"

    parts = []
    for item in media:
        label = item.get("label") or item.get("description") or f"media {item.get('index', 0) + 1}"
        capacity = item.get("capacity_kib")
        free = item.get("free_kib")
        free_images = item.get("free_images")
        details = []
        if capacity is not None:
            details.append(f"capacity={gib(capacity)}")
        if free is not None:
            details.append(f"free={gib(free)}")
        if free_images is not None:
            details.append(f"free_images={free_images}")
        parts.append(f"{label}: " + (", ".join(details) if details else "size unavailable"))
    return "; ".join(parts)


def _norm(value):
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", "", text.casefold())


def _children(node):
    try:
        return list(node.get_children())
    except Exception:
        return []


def _choices(node):
    try:
        return list(node.get_choices())
    except Exception:
        return []


def _widget_type(node):
    try:
        value = node.get_type()
    except Exception:
        return None
    try:
        return int(value)
    except Exception:
        return str(value)


def _is_date_widget(node):
    value = _widget_type(node)
    if value == 8:  # libgphoto2 GP_WIDGET_DATE
        return True
    try:
        from backend.gphoto_runtime import import_gphoto2
        gp = import_gphoto2()
        return value == int(gp.GP_WIDGET_DATE)
    except Exception:
        return False


def _walk(camera):
    result = []

    def visit(node, prefix=""):
        name = str(node.get_name())
        path = f"{prefix}/{name}" if prefix else f"/{name}"
        children = _children(node)
        if children:
            for child in children:
                visit(child, path)
            return
        label = ""
        try:
            label = str(node.get_label())
        except Exception:
            pass
        try:
            current = node.get_value()
        except Exception:
            current = None
        try:
            readonly = bool(node.get_readonly())
        except Exception:
            readonly = True
        result.append(
            {
                "path": path,
                "name": _norm(name),
                "label": label,
                "label_norm": _norm(label),
                "value": current,
                "choices": _choices(node),
                "readonly": readonly,
                "widget_type": _widget_type(node),
                "date_widget": _is_date_widget(node),
            }
        )

    visit(camera.get_config())
    return result


def _node(camera, path):
    config = camera.get_config()
    node = config
    parts = path.strip("/").split("/")
    if parts and parts[0] == config.get_name():
        parts.pop(0)
    for part in parts:
        node = node.get_child_by_name(part)
    return config, node


def _read(camera, path):
    return _node(camera, path)[1].get_value()


def _write(camera, path, value):
    config, node = _node(camera, path)
    current = node.get_value()
    if isinstance(current, bool):
        value = bool(value)
    elif isinstance(current, int):
        value = int(value)
    elif isinstance(current, float):
        value = float(value)
    node.set_value(value)
    camera.set_config(config)


def _same(actual, target):
    return str(actual) == str(target)


def _write_confirm(camera, path, value, *, tolerance_s=None, timeout_s=5.0):
    _write(camera, path, value)
    deadline = time.monotonic() + timeout_s
    last = None
    for attempt in range(21):
        last = _read(camera, path)
        if tolerance_s is None:
            if _same(last, value):
                return last
        else:
            try:
                if abs(float(last) - float(value)) <= float(tolerance_s):
                    return last
            except (TypeError, ValueError):
                pass
        if time.monotonic() >= deadline or attempt == 20:
            break
        time.sleep(0.05)
    raise RuntimeError(
        f"readback mismatch for {path}: requested={value!r}, actual={last!r}"
    )


def _prove_transition(camera, path, current, alternate, *, tolerance_s=None):
    """Prove SET with a real transition and restore the original value."""
    changed = False
    try:
        _write_confirm(camera, path, alternate, tolerance_s=tolerance_s)
        changed = True
        if _same(current, alternate):
            raise RuntimeError("probe did not create a real value transition")
        return True
    finally:
        if changed or not _same(_read(camera, path), current):
            _write_confirm(camera, path, current, tolerance_s=tolerance_s)


def _parse_offset_minutes(value):
    text = str(value).strip().upper().replace(" ", "")
    match = re.fullmatch(r"(?:UTC|GMT)?([+-])(\d{1,2})(?::?(\d{2}))?", text)
    if not match:
        return None
    hours = int(match.group(2))
    minutes = int(match.group(3) or 0)
    if hours > 14 or minutes > 59:
        return None
    total = hours * 60 + minutes
    return total if match.group(1) == "+" else -total


def _clock_capability(camera, items, log):
    result = {
        "local_datetime": {
            "detected": False,
            "set_proven": False,
        },
        "utc_datetime": {
            "detected": False,
            "set_proven": False,
        },
        "timezone": {
            "detected": False,
            "set_proven": False,
            "offset_values": {},
        },
        "local_sync_supported": False,
    }

    local = next((item for item in items if item["name"] in _LOCAL_DATETIME_NAMES), None)
    utc = next((item for item in items if item["name"] in _UTC_DATETIME_NAMES), None)

    for semantic, item in (("local_datetime", local), ("utc_datetime", utc)):
        if item is None:
            continue

        widget_type = item.get("widget_type")
        date_widget = item.get("date_widget")
        if widget_type is None or date_widget is None:
            try:
                _config, live_node = _node(camera, item["path"])
                widget_type = _widget_type(live_node)
                date_widget = _is_date_widget(live_node)
            except Exception:
                # Optional capability only: missing metadata must degrade to
                # unsupported rather than fail the whole auxiliary probe.
                date_widget = False if date_widget is None else date_widget

        cap = {
            "detected": True,
            "path": item["path"],
            "readonly": item["readonly"],
            "widget_type": widget_type,
            "date_widget": bool(date_widget),
            "set_proven": False,
        }
        result[semantic] = cap
        if item["readonly"] or not cap["date_widget"]:
            continue
        current = item["value"]
        if isinstance(current, bool) or not isinstance(current, (int, float)):
            continue
        probe = int(current) + 60
        try:
            _prove_transition(camera, item["path"], current, probe, tolerance_s=2.0)
            cap["set_proven"] = True
            log(f"OPTIONAL CLOCK {semantic}: SET proven at {item['path']}")
        except Exception as exc:
            cap["set_error"] = str(exc)
            log(f"OPTIONAL CLOCK {semantic}: SET not proven at {item['path']}: {exc}")

    timezone = next((item for item in items if item["name"] in _TIMEZONE_NAMES), None)
    if timezone is not None:
        offsets = {}
        for choice in timezone["choices"]:
            parsed = _parse_offset_minutes(choice)
            if parsed is not None:
                offsets[str(parsed)] = choice
        tzcap = {
            "detected": True,
            "path": timezone["path"],
            "readonly": timezone["readonly"],
            "choices": deepcopy(timezone["choices"]),
            "offset_values": offsets,
            "set_proven": False,
        }
        result["timezone"] = tzcap
        if not timezone["readonly"] and len(timezone["choices"]) >= 2:
            current = timezone["value"]
            alternate = next(
                (choice for choice in timezone["choices"] if not _same(choice, current)),
                None,
            )
            if alternate is not None:
                try:
                    _prove_transition(camera, timezone["path"], current, alternate)
                    tzcap["set_proven"] = True
                    log(f"OPTIONAL CLOCK timezone: SET proven at {timezone['path']}")
                except Exception as exc:
                    tzcap["set_error"] = str(exc)
                    log(f"OPTIONAL CLOCK timezone: SET not proven at {timezone['path']}: {exc}")

    result["local_sync_supported"] = bool(
        result["local_datetime"].get("set_proven")
    )
    return result


def _classify_shutter_choice(value):
    text = _norm(value)
    if any(token in text for token in ("efcs", "electronicfrontcurtain", "frontcurtain", "firstcurtain")):
        return "efcs"
    if "mechanical" in text:
        return "mechanical"
    if "electronic" in text:
        return "electronic"
    return None


def _looks_like_shutter_control(item):
    if item["name"] in _SHUTTER_MODE_NAMES:
        return True
    # Characterization's initial discovery intentionally stores only the
    # fields needed by the main qualifier and may not include label_norm.
    # Normalize the label lazily so that auxiliary probes can safely reuse it.
    label_norm = item.get("label_norm")
    if label_norm is None:
        label_norm = _norm(item.get("label", ""))
    combined = f"{item['name']} {label_norm}"
    if "shutterspeed" in combined or "shuttercount" in combined:
        return False
    return any(
        token in combined
        for token in ("shuttertype", "shuttermode", "shuttermechanism", "electronicshutter")
    )


def _shutter_capability(camera, items, log):
    empty = {
        "control_detected": False,
        "mechanical_supported": None,
        "electronic_supported": None,
        "efcs_supported": None,
        "electronic_only_selectable": False,
    }
    candidates = [item for item in items if _looks_like_shutter_control(item)]
    if not candidates:
        return empty, {}

    # Prefer an enumerated mode selector because it can distinguish mechanical,
    # EFCS and fully electronic modes without inference.
    candidates.sort(key=lambda item: (not bool(item["choices"]), item["path"]))
    item = candidates[0]
    classified = {}
    for choice in item["choices"]:
        kind = _classify_shutter_choice(choice)
        if kind and kind not in classified:
            classified[kind] = choice

    result = {
        "control_detected": True,
        "path": item["path"],
        "readonly": item["readonly"],
        "choices": deepcopy(item["choices"]),
        "mechanical_supported": True if "mechanical" in classified else None,
        "electronic_supported": True if "electronic" in classified else None,
        "efcs_supported": True if "efcs" in classified else None,
        "electronic_only_selectable": False,
    }

    electronic = classified.get("electronic")

    # Some drivers expose an explicit Electronic Shutter TOGGLE rather than a
    # mode menu.  This is sufficiently explicit to qualify electronic-only SET,
    # but it does not prove that a mechanical shutter is physically present.
    explicit_toggle = (
        electronic is None
        and item["name"] == "electronicshutter"
        and not item["choices"]
        and isinstance(item["value"], (bool, int))
        and not item["readonly"]
    )
    if explicit_toggle:
        electronic = type(item["value"])(1)
        result["electronic_supported"] = True

    if electronic is None or item["readonly"]:
        return result, {}

    current = item["value"]
    alternate = (
        type(current)(0)
        if explicit_toggle
        else next(
            (
                value
                for kind, value in classified.items()
                if kind != "electronic" and not _same(value, electronic)
            ),
            None,
        )
    )
    if alternate is None and not _same(current, electronic):
        alternate = current
    if alternate is None:
        result["electronic_set_error"] = "no alternate value available for transition proof"
        return result, {}

    try:
        # If electronic is already selected, prove a round-trip via a different
        # mechanism. Otherwise prove electronic then restore the original state.
        if _same(current, electronic):
            _write_confirm(camera, item["path"], alternate)
            _write_confirm(camera, item["path"], electronic)
            _write_confirm(camera, item["path"], current)
        else:
            _write_confirm(camera, item["path"], electronic)
            _write_confirm(camera, item["path"], current)
        result["electronic_only_selectable"] = True
        result["electronic_value"] = electronic
        log(f"OPTIONAL SHUTTER: fully electronic SET proven at {item['path']}={electronic!r}")
        return result, {
            "shutter_mode": {
                "path": item["path"],
                "value": electronic,
                "get": True,
                "set": True,
            }
        }
    except Exception as exc:
        try:
            if not _same(_read(camera, item["path"]), current):
                _write_confirm(camera, item["path"], current)
        except Exception as restore_exc:
            result["restore_error"] = str(restore_exc)
        result["electronic_set_error"] = str(exc)
        log(f"OPTIONAL SHUTTER: electronic SET not proven at {item['path']}: {exc}")
        return result, {}


def characterize_auxiliary_capabilities(camera, job=None, *, items=None):
    """Characterize optional clock/shutter capabilities without fatal policy."""
    def log(message):
        if job is not None:
            try:
                job.log(message)
            except Exception:
                pass

    try:
        items = _walk(camera) if items is None else items
    except Exception as exc:
        log(f"OPTIONAL CAPABILITIES unavailable: {exc}")
        return {
            "clock": {"local_sync_supported": False, "probe_error": str(exc)},
            "shutter": {"control_detected": False, "probe_error": str(exc)},
        }, {}

    try:
        clock = _clock_capability(camera, items, log)
    except Exception as exc:
        clock = {"local_sync_supported": False, "probe_error": str(exc)}
        log(f"OPTIONAL CLOCK probe failed non-fatally: {exc}")

    try:
        shutter, commands = _shutter_capability(camera, items, log)
    except Exception as exc:
        shutter = {"control_detected": False, "probe_error": str(exc)}
        commands = {}
        log(f"OPTIONAL SHUTTER probe failed non-fatally: {exc}")

    return {"clock": clock, "shutter": shutter}, commands


def _timezone_target(tzcap, reference):
    if not tzcap.get("detected") or not tzcap.get("set_proven"):
        return None
    name = getattr(reference, "timezone_name", None)
    if name:
        for choice in tzcap.get("choices", []):
            if str(choice).casefold() == str(name).casefold():
                return choice
    offset = getattr(reference, "utc_offset_minutes", None)
    if offset is not None:
        return tzcap.get("offset_values", {}).get(str(int(offset)))
    return None


def _encode_local_wall_clock(value):
    if not isinstance(value, datetime):
        raise TypeError("reference.datetime_local must be a datetime")
    # libgphoto2 local datetime handlers convert the supplied time_t through
    # host localtime(). mktime() on the desired naive wall clock deliberately
    # creates the inverse representation without changing process-global TZ.
    naive = value.replace(tzinfo=None)
    return int(time.mktime(naive.timetuple()))


def sync_profile_datetime(camera, profile, reference, *, plugin_name="profile", model=None):
    """Apply characterized local date/time, plus timezone when safely mappable."""
    clock = (profile.get("capabilities") or {}).get("clock") or {}
    local = clock.get("local_datetime") or {}
    timezone = clock.get("timezone") or {}
    result = {
        "status": "unsupported",
        "datetime_synced": False,
        "timezone_synced": False,
        "datetime_applied": None,
        "timezone_name": getattr(reference, "timezone_name", None) or None,
        "utc_offset_minutes": getattr(reference, "utc_offset_minutes", None),
        "message": "Local date/time synchronization is not characterized for this camera",
        "plugin": plugin_name,
        "model": model or profile.get("model"),
    }

    if not local.get("set_proven") or not local.get("path"):
        return result

    tz_target = _timezone_target(timezone, reference)
    if tz_target is not None:
        _write_confirm(camera, timezone["path"], tz_target)
        result["timezone_synced"] = True

    target_dt = getattr(reference, "datetime_local", None)
    target_value = _encode_local_wall_clock(target_dt)
    _write_confirm(
        camera,
        local["path"],
        target_value,
        tolerance_s=3.0,
    )
    result["status"] = "ok"
    result["datetime_synced"] = True
    result["datetime_applied"] = target_dt.isoformat()
    if timezone.get("detected") and not result["timezone_synced"]:
        result["message"] = (
            "Local date/time synchronized; camera timezone control could not be "
            "mapped to the requested timezone"
        )
    else:
        result["message"] = "Local camera date/time synchronized"
    return result
