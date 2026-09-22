from __future__ import annotations
import copy, json, threading
from pathlib import Path
from typing import Any

DEFAULT_STATE = {
    "gps": {"connected": False, "synced": False, "lat": None, "lon": None,
            "alt": None, "date": None, "satellites": 0, "hdop": None,
            "sync_time": None, "timezone": None, "timezone_name": None,
            "utc_offset_minutes": None, "gps_sync_running": False},
    "camera": {"connected": False, "brand": None, "model": None, "battery": None},
    "eclipse": None,
    "circumstances": {"loaded": False, "active_file": None, "meta": {}},
    "capture": {"loaded": False, "active_file": None, "meta": {}},
    "devices": {
        "camera": {"plugin": "none", "active": False},
        "gps": {"plugin": "none", "active": False},
        "focuser": {"plugin": "none", "active": False},
        "mount": {"plugin": "none", "active": False},
        "updated_at": None,
    },
    "focuser_settings": {
        "mode": "slow", "slow_step": 20, "fast_step": 150, "updated_at": None,
    },
    "trigger": {
        "running": False,
        "phase": "idle",
        "rigs": {
            str(rig_id): {
                "running": False,
                "phase": "idle",
                "mode": None,
                "speed": None,
            }
            for rig_id in range(1, 5)
        },
    },
    "gps_sync_running": False,
    "calc_running": False,
}

class StateStore:
    """Thread-safe runtime state with explicit persistence boundaries."""
    PERSISTED_KEYS = (
        "gps",
        "camera",
        "eclipse",
        "camera_config_file",
        "circumstances",
        "capture",
        "devices",
        "focuser_settings",
    )
    DEVICE_NAMES = ("camera", "gps", "focuser", "mount")

    def __init__(self, path: Path, defaults: dict | None = None):
        self.path = Path(path)
        self.lock = threading.RLock()
        self._defaults = copy.deepcopy(defaults or DEFAULT_STATE)
        self._state = self._load()

    def _load(self) -> dict:
        base = copy.deepcopy(self._defaults)
        if self.path.exists():
            try:
                saved = json.loads(self.path.read_text(encoding="utf-8"))
                for key, val in saved.items():
                    if key == "devices" and isinstance(val, dict):
                        val = self._persistable_devices(val)
                    if isinstance(val, dict) and isinstance(base.get(key), dict):
                        base[key].update(val)
                    else:
                        base[key] = val
            except Exception:
                pass
        base["trigger"] = copy.deepcopy(self._defaults["trigger"])
        base["gps_sync_running"] = False
        base["calc_running"] = False
        base.setdefault("gps", {})["gps_sync_running"] = False
        base.setdefault("circumstances", {})["loaded"] = False
        base.setdefault("capture", {})["loaded"] = False
        return base

    def _persistable_devices(self, devices: dict) -> dict:
        persisted = {"updated_at": devices.get("updated_at")}
        for name in self.DEVICE_NAMES:
            device = devices.get(name, {})
            if not isinstance(device, dict):
                device = {}
            persisted[name] = {
                "plugin": device.get("plugin", "none"),
                "active": device.get("active", False),
            }
        return persisted

    @property
    def data(self) -> dict:
        """Compatibility view. Prefer get/update/snapshot for new code."""
        return self._state

    def snapshot(self, key: str | None = None):
        with self.lock:
            value = self._state if key is None else self._state.get(key)
            return copy.deepcopy(value)

    def get(self, key: str, default=None):
        with self.lock:
            return copy.deepcopy(self._state.get(key, default))

    def set(self, key: str, value: Any, persist: bool = False):
        with self.lock:
            self._state[key] = value
        if persist:
            self.save()

    def update(self, key: str, values: dict, persist: bool = False):
        with self.lock:
            current = self._state.setdefault(key, {})
            if not isinstance(current, dict):
                current = {}
                self._state[key] = current
            current.update(copy.deepcopy(values))
        if persist:
            self.save()

    def update_trigger_rig(self, rig_id: int, values: dict) -> None:
        rig_key = str(int(rig_id))
        with self.lock:
            trigger = self._state.setdefault("trigger", {})
            rigs = trigger.setdefault("rigs", {})
            current = rigs.setdefault(
                rig_key,
                {"running": False, "phase": "idle", "mode": None, "speed": None},
            )
            current.update(copy.deepcopy(values))
            if rig_id == 1:
                for key in ("running", "phase", "mode", "speed"):
                    if key in values:
                        trigger[key] = copy.deepcopy(values[key])

    def reset_boot_sensitive(self) -> None:
        with self.lock:
            self._state["trigger"] = copy.deepcopy(self._defaults["trigger"])
            self._state["gps_sync_running"] = False
            self._state["calc_running"] = False
            gps = self._state.setdefault("gps", {})
            gps["gps_sync_running"] = False
            gps["connected"] = False
            gps["synced"] = False
            gps["sync_time"] = None
            gps["date"] = None

    def save(self) -> None:
        with self.lock:
            payload = {
                key: copy.deepcopy(self._state[key])
                for key in self.PERSISTED_KEYS
                if key in self._state
            }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)
