"""Thread-safe application service for the selected focuser plugin."""

from __future__ import annotations

import threading
from typing import Any, Callable

from plugins.focuser import load_focuser


class FocuserService:
    """Serialize focuser access and expose JSON-friendly operation results.

    The plugin is loaded lazily from the ``devices.focuser`` selection.  A
    connection is retained after an operation because non-blocking moves and
    continuous jogging depend on it; :meth:`close` releases it explicitly.
    """

    def __init__(
        self,
        state_store,
        log_fn: Callable[[str], None] = print,
        config: dict | None = None,
        plugin_loader: Callable[..., Any] = load_focuser,
    ):
        self._state_store = state_store
        self._log = log_fn
        self._config = config
        self._plugin_loader = plugin_loader
        self._lock = threading.RLock()
        self._plugin = None
        self._plugin_id: str | None = None

    def _selection(self) -> tuple[bool, str]:
        devices = self._state_store.snapshot("devices") or {}
        selection = devices.get("focuser") or {}
        return bool(selection.get("active", False)), str(
            selection.get("plugin") or "none"
        )

    def _close_locked(self) -> None:
        plugin, self._plugin = self._plugin, None
        self._plugin_id = None
        if plugin is not None and getattr(plugin, "connected", False):
            plugin.disconnect()

    def _plugin_for_operation(self):
        active, plugin_id = self._selection()
        if not active or plugin_id == "none":
            self._close_locked()
            raise RuntimeError("focuser is inactive")

        if self._plugin is not None and self._plugin_id != plugin_id:
            self._close_locked()
        if self._plugin is None:
            self._plugin = self._plugin_loader(
                plugin_id, self._log, config=self._config
            )
            if self._plugin is None:
                raise RuntimeError(f"unable to load focuser plugin '{plugin_id}'")
            self._plugin_id = plugin_id
        if not self._plugin.connected:
            self._plugin.connect()
        return self._plugin

    def _status_locked(self, plugin) -> dict:
        raw = dict(plugin.status() or {})
        # Position is deliberately read from the device, not from cached status.
        raw["position"] = plugin.get_position()
        return {
            "connected": bool(plugin.connected),
            "position": raw.get("position"),
            "moving": bool(raw.get("moving", False)),
            "holding": bool(raw.get("holding", False)),
            "step_coarse": raw.get("step_coarse"),
            "step_fine": raw.get("step_fine"),
            "plugin": self._plugin_id,
        }

    def status(self) -> dict:
        """Return the selected plugin's live position and motion state."""
        with self._lock:
            active, plugin_id = self._selection()
            if not active or plugin_id == "none":
                self._close_locked()
                return {
                    "connected": False,
                    "position": None,
                    "moving": False,
                    "holding": False,
                    "step_coarse": None,
                    "step_fine": None,
                    "plugin": plugin_id,
                }
            return self._status_locked(self._plugin_for_operation())

    def home(self, wait: bool = False) -> dict:
        """Move to the focuser's zero position."""
        return self.move_to(0, wait=wait)

    def move_to(self, position: int, wait: bool = False) -> dict:
        with self._lock:
            plugin = self._plugin_for_operation()
            plugin.move_to(int(position), wait=wait)
            return self._status_locked(plugin)

    def move_relative(self, delta: int, wait: bool = False) -> dict:
        with self._lock:
            plugin = self._plugin_for_operation()
            plugin.move_relative(int(delta), wait=wait)
            return self._status_locked(plugin)

    def start_jog(self, direction: str, mode: str = "coarse") -> dict:
        with self._lock:
            plugin = self._plugin_for_operation()
            plugin.start_continuous(direction, mode)
            return self._status_locked(plugin)

    def stop_jog(self) -> dict:
        """Stop continuous motion; repeated calls are harmless."""
        with self._lock:
            active, plugin_id = self._selection()
            if not active or plugin_id == "none":
                self._close_locked()
                return self.status()
            plugin = self._plugin_for_operation()
            plugin.stop_continuous()
            return self._status_locked(plugin)

    def stop(self) -> dict:
        """Stop all motion; repeated calls are harmless."""
        with self._lock:
            active, plugin_id = self._selection()
            if not active or plugin_id == "none":
                self._close_locked()
                return self.status()
            plugin = self._plugin_for_operation()
            plugin.stop()
            return self._status_locked(plugin)

    def set_step(self, coarse: int | None = None, fine: int | None = None) -> dict:
        with self._lock:
            plugin = self._plugin_for_operation()
            plugin.set_step(coarse=coarse, fine=fine)
            return self._status_locked(plugin)

    def close(self) -> None:
        """Stop jogging and disconnect the active plugin."""
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


__all__ = ["FocuserService"]
