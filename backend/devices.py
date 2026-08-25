"""Device registry helpers and bounded, read-only auto-detection.

Detection only suggests a plugin when exactly one registered plugin reports a
match.  In particular, registry order is never used as an implicit priority.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from importlib import import_module
from typing import Any, Callable, Mapping

TTL = timedelta(hours=72)
CATEGORIES = ("camera", "gps", "focuser", "mount")

# Resolve submodules explicitly: plugins.__init__ also contains lazy helper
# functions with these names, which ``from plugins import camera`` can select.
camera = import_module("plugins.camera")
gps = import_module("plugins.gps")
focuser = import_module("plugins.focuser")
mount = import_module("plugins.mount")


def camera_plugin_for_model(model: str | None) -> str | None:
    """Return the sole camera plugin matching *model*, otherwise ``None``."""
    matches = []
    try:
        registered = camera._load_plugin_classes()
    except Exception:
        return None
    for plugin in registered:
        try:
            if plugin.matches(model or ""):
                matches.append(_plugin_id(plugin))
        except Exception:
            continue
    matches = [plugin_id for plugin_id in matches if plugin_id]
    return matches[0] if len(set(matches)) == 1 else None


# Public wording used by callers that treat detection as a suggestion.
suggest_camera_plugin = camera_plugin_for_model


def ttl_expired(updated_at_iso: str | datetime | None,
                now_utc: datetime | None = None) -> bool:
    """Return whether *updated_at_iso* is strictly more than 72 hours old."""
    if not updated_at_iso:
        return True
    try:
        updated_at = (updated_at_iso if isinstance(updated_at_iso, datetime)
                      else datetime.fromisoformat(updated_at_iso.replace("Z", "+00:00")))
    except (TypeError, ValueError):
        return True
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=timezone.utc)
    else:
        updated_at = updated_at.astimezone(timezone.utc)
    now = now_utc or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    else:
        now = now.astimezone(timezone.utc)
    return now - updated_at > TTL


def normalize_selection(payload: Mapping[str, Any] | str | None) -> dict[str, Any]:
    """Normalize an equipment selection without discarding other settings."""
    if isinstance(payload, Mapping):
        normalized = dict(payload)
        plugin_id = normalized.get("plugin")
    else:
        normalized = {"plugin": payload}
        plugin_id = payload
    normalized["active"] = plugin_id not in (None, "", "none")
    return normalized


def detect_camera(model: str | None = None) -> dict[str, Any]:
    """Detect the connected camera and suggest the matching plugin.

    When no model is supplied, ask the camera registry for the most specific
    model reported by libgphoto2 autodetect.  Detection remains generic:
    backend code never knows Sony/Nikon model names.
    """
    if not model:
        try:
            model = camera.get_camera_model(None)
        except Exception:
            model = None

    model = str(model or "").strip() or None
    suggested = camera_plugin_for_model(model) if model else None
    return _result(bool(model), model, model, suggested)


def detect_gps() -> dict[str, Any]:
    return _probe_registry(gps.available_plugins(), loader=None)


def detect_focuser() -> dict[str, Any]:
    return _probe_registry(focuser.available_plugins(), focuser.load_focuser)


def detect_mount() -> dict[str, Any]:
    return _probe_registry(mount.available_plugins(), mount.load_mount)


DETECTORS: dict[str, Callable[[], dict[str, Any]]] = {
    "camera": detect_camera,
    "gps": detect_gps,
    "focuser": detect_focuser,
    "mount": detect_mount,
}


def detect_all(timeout_by_category: Mapping[str, float]) -> dict[str, dict[str, Any]]:
    """Run all category detectors concurrently, each with its own timeout.

    Timed-out workers are daemon threads: an unresponsive hardware probe cannot
    delay another category or prevent application shutdown.
    """
    results: dict[str, dict[str, Any]] = {}
    state: dict[str, dict[str, Any]] = {}

    def run(category: str) -> None:
        try:
            state[category] = _coerce_result(DETECTORS[category]())
        except Exception as exc:
            state[category] = _empty_result({"error": str(exc)})

    workers = {}
    started = {}
    for category in CATEGORIES:
        started[category] = _monotonic()
        worker = threading.Thread(target=run, args=(category,), daemon=True,
                                  name=f"detect-{category}")
        workers[category] = worker
        worker.start()

    for category in CATEGORIES:
        timeout = max(0.0, float(timeout_by_category.get(category, 0)))
        remaining = max(0.0, timeout - (_monotonic() - started[category]))
        workers[category].join(remaining)
        if workers[category].is_alive():
            results[category] = _empty_result({"timeout": True})
        else:
            results[category] = state.get(category, _empty_result())
    return results


def _probe_registry(registry: Any, loader: Callable[..., Any] | None) -> dict[str, Any]:
    candidates = []
    for plugin_id, plugin in _registry_entries(registry):
        if plugin_id in (None, "", "none"):
            continue
        try:
            target = plugin
            if target is None and loader is not None:
                target = loader(plugin_id, log_fn=lambda *_: None)
            probe = getattr(target, "probe", None)
            if callable(probe) and probe():
                candidates.append(plugin_id)
        except Exception:
            continue
    unique = list(dict.fromkeys(candidates))
    suggested = unique[0] if len(unique) == 1 else None
    return _result(bool(unique), unique, None, suggested)


def _registry_entries(registry: Any):
    if isinstance(registry, Mapping):
        yield from registry.items()
        return
    for entry in registry or ():
        if isinstance(entry, Mapping):
            plugin_id = entry.get("id") or entry.get("plugin_id")
            plugin = entry.get("plugin") or entry.get("class") or entry.get("cls")
        else:
            plugin_id, plugin = _plugin_id(entry), entry
        yield plugin_id, plugin


def _plugin_id(plugin: Any) -> str | None:
    return (getattr(plugin, "plugin_id", None) or getattr(plugin, "id", None)
            or getattr(plugin, "name", None))


def _result(detected: bool, info: Any, model: str | None,
            suggested: str | None) -> dict[str, Any]:
    return {"detected": detected, "detected_info": info,
            "detected_model": model, "suggested_plugin": suggested}


def _empty_result(info: Any = None) -> dict[str, Any]:
    return _result(False, info, None, None)


def _coerce_result(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return _result(bool(value.get("detected")), value.get("detected_info"),
                       value.get("detected_model"), value.get("suggested_plugin"))
    return _empty_result()


def _monotonic() -> float:
    # Kept behind a helper to make timeout behavior deterministic in tests.
    import time
    return time.monotonic()


__all__ = ["CATEGORIES", "DETECTORS", "TTL", "camera_plugin_for_model",
           "detect_all", "detect_camera", "detect_focuser", "detect_gps",
           "detect_mount", "normalize_selection", "suggest_camera_plugin",
           "ttl_expired"]
