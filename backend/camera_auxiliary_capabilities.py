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
        import gphoto2 as gp
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
        cap = {
            "detected": True,
            "path": item["path"],
            "readonly": item["readonly"],
            "widget_type": item["widget_type"],
            "date_widget": item["date_widget"],
            "set_proven": False,
        }
        result[semantic] = cap
        if item["readonly"] or not item["date_widget"]:
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
    combined = f"{item['name']} {item['label_norm']}"
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


def characterize_auxiliary_capabilities(camera, job=None):
    """Characterize optional clock/shutter capabilities without fatal policy."""
    def log(message):
        if job is not None:
            try:
                job.log(message)
            except Exception:
                pass

    try:
        items = _walk(camera)
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
