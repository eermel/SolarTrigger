"""Thread-safe application service for the selected mount plugin."""

from __future__ import annotations

import inspect
import math
import threading
from numbers import Real
from typing import Any, Callable

from plugins.mount import load_mount


class MountService:
    """Serialize mount access and expose its manual-slew state."""

    _DIRECTIONS = frozenset(("north", "south", "east", "west"))

    def __init__(
        self,
        state_store,
        log_fn: Callable[[str], None] = print,
        config: dict | None = None,
        plugin_loader: Callable[..., Any] = load_mount,
    ):
        self._state_store = state_store
        self._log = log_fn
        self._config = config
        self._plugin_loader = plugin_loader
        self._lock = threading.RLock()
        self._plugin_access_lock = threading.RLock()
        self._plugin = None
        self._plugin_id: str | None = None
        self._location_pushed = False
        self._moving = False
        self._direction: str | None = None
        self._homing = False
        self._home_generation = 0
        self._tracking_mode = "solar"
        self._tracking_enabled = False

    def _selection(self) -> tuple[bool, str]:
        devices = self._state_store.snapshot("devices") or {}
        selection = devices.get("mount") or {}
        return bool(selection.get("active", False)), str(
            selection.get("plugin") or "none"
        )

    def _clear_motion_locked(self) -> None:
        self._moving = False
        self._direction = None

    def _close_locked(self) -> None:
        plugin, self._plugin = self._plugin, None
        self._plugin_id = None
        self._location_pushed = False
        self._clear_motion_locked()
        if plugin is not None and getattr(plugin, "connected", False):
            plugin.disconnect()

    def _plugin_for_operation(self):
        active, plugin_id = self._selection()
        if not active or plugin_id == "none":
            self._close_locked()
            raise RuntimeError("mount is inactive")

        if self._plugin is not None and self._plugin_id != plugin_id:
            self._close_locked()
        if self._plugin is None:
            self._plugin = self._plugin_loader(
                plugin_id, self._log, config=self._config
            )
            if self._plugin is None:
                raise RuntimeError(f"unable to load mount plugin '{plugin_id}'")
            self._plugin_id = plugin_id
        if not self._plugin.connected:
            plugin = self._plugin
            try:
                plugin.connect()
                self._push_gps_location(plugin)
                get_tracking_capabilities = getattr(
                    plugin, "get_tracking_capabilities", None
                )
                tracking_capabilities = (
                    get_tracking_capabilities()
                    if callable(get_tracking_capabilities)
                    else None
                )
                if (
                    isinstance(tracking_capabilities, dict)
                    and tracking_capabilities.get("toggle") is True
                ):
                    plugin.stop_tracking()
                    raw_status = dict(plugin.status() or {})
                    if "tracking" in raw_status:
                        self._tracking_enabled = bool(raw_status["tracking"])
                    else:
                        tracking = getattr(plugin, "tracking", None)
                        if isinstance(tracking, bool):
                            self._tracking_enabled = tracking
            except Exception:
                self._plugin = None
                self._plugin_id = None
                self._location_pushed = False
                self._clear_motion_locked()
                try:
                    plugin.disconnect()
                except Exception:
                    pass
                raise
        return self._plugin

    @staticmethod
    def _valid_location_value(value) -> bool:
        return (
            isinstance(value, Real)
            and not isinstance(value, bool)
            and math.isfinite(value)
        )

    @staticmethod
    def _set_plugin_location(plugin, latitude, longitude, elevation) -> None:
        setter = getattr(plugin, "set_location", None)
        if not callable(setter):
            raise RuntimeError("location setting is unsupported by this mount")
        try:
            inspect.signature(setter).bind(latitude, longitude, elevation)
        except TypeError:
            setter(latitude, longitude)
        else:
            setter(latitude, longitude, elevation)

    def _push_gps_location(self, plugin) -> None:
        if self._location_pushed:
            return
        setter = getattr(plugin, "set_location", None)
        gps = self._state_store.snapshot("gps") or {}
        if not isinstance(gps, dict):
            return
        location = (gps.get("lat"), gps.get("lon"), gps.get("alt"))
        if not callable(setter) or not all(
            self._valid_location_value(value) for value in location
        ):
            return
        self._set_plugin_location(plugin, *location)
        self._location_pushed = True

    def _status_locked(self, plugin) -> dict:
        raw = dict(plugin.status() or {})
        if "moving" in raw:
            if raw["moving"]:
                self._moving = True
            else:
                self._clear_motion_locked()
        capabilities = plugin.get_slew_speed_capabilities()
        get_tracking_capabilities = getattr(
            plugin, "get_tracking_capabilities", None
        )
        tracking_capabilities = (
            get_tracking_capabilities()
            if callable(get_tracking_capabilities)
            else None
        )
        status = {
            "active": True,
            "connected": bool(plugin.connected),
            "moving": bool(self._moving),
            "direction": self._direction,
            "homing": bool(self._homing),
            "slew_speed": raw.get("move_rate") or None,
            "slew_speed_caps": capabilities or None,
            "tracking_mode": self._tracking_mode,
            "tracking_enabled": bool(self._tracking_enabled),
            "tracking_caps": tracking_capabilities,
            "plugin": self._plugin_id,
        }
        if "device" in raw:
            status["device"] = raw["device"]
        return status

    def _homing_status_locked(self, plugin_id: str) -> dict:
        """Return a backend-only snapshot while Home owns mount I/O."""
        return {
            "active": True,
            "connected": self._plugin is not None,
            "moving": bool(self._moving),
            "direction": self._direction,
            "homing": True,
            "slew_speed": None,
            "slew_speed_caps": None,
            "tracking_mode": self._tracking_mode,
            "tracking_enabled": bool(self._tracking_enabled),
            "tracking_caps": None,
            "plugin": self._plugin_id or plugin_id,
        }

    def status(self) -> dict:
        """Return connection, speed, and internally tracked slew state."""
        with self._lock:
            active, plugin_id = self._selection()
            if not active or plugin_id == "none":
                self._close_locked()
                return {
                    "active": False,
                    "connected": False,
                    "moving": False,
                    "direction": None,
                    "homing": bool(self._homing),
                    "slew_speed": None,
                    "slew_speed_caps": None,
                    "tracking_mode": self._tracking_mode,
                    "tracking_enabled": bool(self._tracking_enabled),
                    "tracking_caps": None,
                    "plugin": plugin_id,
                }
            if self._homing:
                return self._homing_status_locked(plugin_id)
            try:
                return self._status_locked(self._plugin_for_operation())
            except Exception as exc:
                self._close_locked()
                return {
                    "active": True,
                    "connected": False,
                    "moving": False,
                    "direction": None,
                    "homing": bool(self._homing),
                    "slew_speed": None,
                    "slew_speed_caps": None,
                    "tracking_mode": self._tracking_mode,
                    "tracking_enabled": bool(self._tracking_enabled),
                    "tracking_caps": None,
                    "plugin": plugin_id,
                    "error": {
                        "code": "SERIAL_ERROR",
                        "message": str(exc),
                    },
                }

    def set_tracking_mode(self, mode: str) -> dict:
        if mode not in {"solar", "sidereal"}:
            raise ValueError("tracking mode must be 'solar' or 'sidereal'")
        with self._lock:
            plugin = self._plugin_for_operation()
            setter = getattr(plugin, "set_tracking_mode", None)
            if callable(setter):
                setter(mode)
                self._tracking_mode = mode
            return self._status_locked(plugin)

    @staticmethod
    def _require_tracking_toggle(plugin) -> None:
        get_capabilities = getattr(plugin, "get_tracking_capabilities", None)
        capabilities = get_capabilities() if callable(get_capabilities) else None
        if not isinstance(capabilities, dict) or capabilities.get("toggle") is not True:
            raise RuntimeError("tracking toggle is unsupported by this mount")

    def start_tracking(self) -> dict:
        with self._lock:
            plugin = self._plugin_for_operation()
            self._require_tracking_toggle(plugin)
            plugin.start_tracking(self._tracking_mode)
            self._tracking_enabled = True
            return self._status_locked(plugin)

    def stop_tracking(self) -> dict:
        with self._lock:
            plugin = self._plugin_for_operation()
            self._require_tracking_toggle(plugin)
            plugin.stop_tracking()
            self._tracking_enabled = False
            return self._status_locked(plugin)

    @staticmethod
    def _validate_speed(value, capabilities: dict) -> None:
        kind = capabilities.get("kind")
        if kind == "discrete":
            values = capabilities.get("values") or []
            if not any(value == item.get("value") for item in values):
                raise ValueError("speed is not one of the supported values")
            return

        if kind == "range":
            minimum = capabilities.get("min")
            maximum = capabilities.get("max")
            step = capabilities.get("step")
            if (
                isinstance(value, bool)
                or not isinstance(value, Real)
                or minimum is None
                or maximum is None
                or value < minimum
                or value > maximum
            ):
                raise ValueError("speed is outside the supported range")
            if step is not None:
                if not isinstance(step, Real) or isinstance(step, bool) or step <= 0:
                    raise ValueError("mount reported an invalid speed step")
                steps = (value - minimum) / step
                if not math.isclose(steps, round(steps), rel_tol=1e-9, abs_tol=1e-9):
                    raise ValueError("speed does not align with the supported step")
            return

        raise ValueError("unsupported slew speed capabilities")

    def set_speed(self, value) -> dict:
        with self._lock:
            plugin = self._plugin_for_operation()
            capabilities = plugin.get_slew_speed_capabilities()
            if capabilities is None:
                raise RuntimeError("slew speed is unsupported by this mount")
            self._validate_speed(value, capabilities)
            plugin.set_speed(value)
            return self._status_locked(plugin)

    def set_location(self, latitude, longitude, elevation) -> dict:
        with self._lock:
            plugin = self._plugin_for_operation()
            self._set_plugin_location(plugin, latitude, longitude, elevation)
            return self._status_locked(plugin)

    def start_slew(self, direction: str) -> dict:
        if direction not in self._DIRECTIONS:
            raise ValueError("direction must be 'north', 'south', 'east' or 'west'")
        with self._lock:
            if self._homing:
                raise RuntimeError("mount is homing")
            plugin = self._plugin_for_operation()
            if not plugin.connected:
                raise RuntimeError("mount is not connected")
            plugin.move(direction)
            self._moving = True
            self._direction = direction
            return self._status_locked(plugin)

    def home_start(self) -> dict:
        """Start an asynchronous home operation."""
        with self._lock:
            plugin = self._plugin_for_operation()
            if self._moving:
                plugin.stop()
                self._clear_motion_locked()

            self._home_generation += 1
            generation = self._home_generation
            self._homing = True

            def is_cancelled() -> bool:
                with self._lock:
                    return (
                        generation != self._home_generation
                        or not self._homing
                    )

            def worker() -> None:
                try:
                    if "is_cancelled" in inspect.signature(
                        plugin.go_home
                    ).parameters:
                        plugin.go_home(is_cancelled=is_cancelled)
                    else:
                        plugin.go_home()
                except Exception as exc:
                    self._log(f"mount home failed: {exc}")
                finally:
                    with self._lock:
                        if (
                            generation == self._home_generation
                            and self._homing
                        ):
                            self._homing = False

            threading.Thread(target=worker, daemon=True).start()
            return self._homing_status_locked(self._plugin_id or "none")

    def stop(self) -> dict:
        """Cancel homing and stop manual motion; leave tracking unchanged."""
        with self._lock:
            self._home_generation += 1
            self._homing = False
            self._clear_motion_locked()
            active, plugin_id = self._selection()
            if not active or plugin_id == "none":
                self._close_locked()
                return self.status()

            plugin = self._plugin
            status = {
                "active": True,
                "connected": bool(
                    plugin is not None and getattr(plugin, "connected", False)
                ),
                "moving": False,
                "direction": None,
                "homing": False,
                "slew_speed": None,
                "slew_speed_caps": None,
                "tracking_mode": self._tracking_mode,
                "tracking_enabled": bool(self._tracking_enabled),
                "tracking_caps": None,
                "plugin": self._plugin_id or plugin_id,
            }

        if self._plugin_access_lock.acquire(timeout=0.5):
            try:
                if plugin is not None:
                    plugin.stop()
            except Exception as exc:
                self._log(f"mount stop failed: {exc}")
            finally:
                self._plugin_access_lock.release()
        else:
            self._log(
                "mount stop could not acquire hardware lock within 0.5 s; "
                "STOP not sent"
            )
        return status

    def close(self) -> None:
        with self._lock:
            self._close_locked()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass


__all__ = ["MountService"]
