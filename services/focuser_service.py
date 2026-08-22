"""Thread-safe application service for the selected focuser plugin."""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any, Callable

from backend.devices import ttl_expired
from plugins.focuser import load_focuser
from plugins.focuser.base import DIR_IN, DIR_OUT


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
        self._mode = "slow"
        self._slow_step = 20
        self._fast_step = 150
        self._settings_updated_at: str | None = None
        self._motion_command: str | None = None
        self._target_position: int | None = None
        self._load_settings()

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _persist_settings(self) -> None:
        self._settings_updated_at = self._now_iso()
        self._state_store.update_section(
            "focuser_settings",
            {
                "mode": self._mode,
                "slow_step": self._slow_step,
                "fast_step": self._fast_step,
                "updated_at": self._settings_updated_at,
            },
            persist=True,
        )

    def _load_settings(self) -> None:
        settings = self._state_store.snapshot("focuser_settings") or {}
        updated_at = settings.get("updated_at")
        if ttl_expired(updated_at):
            self._mode = "slow"
            self._slow_step = 20
            self._fast_step = 150
            self._persist_settings()
            return

        mode = settings.get("mode")
        slow_step = settings.get("slow_step")
        fast_step = settings.get("fast_step")
        if (
            mode not in ("slow", "fast")
            or not isinstance(slow_step, int)
            or isinstance(slow_step, bool)
            or not isinstance(fast_step, int)
            or isinstance(fast_step, bool)
        ):
            self._mode = "slow"
            self._slow_step = 20
            self._fast_step = 150
            self._persist_settings()
            return
        self._mode = mode
        self._slow_step = slow_step
        self._fast_step = fast_step
        self._settings_updated_at = updated_at

    def _ensure_settings_current(self) -> None:
        if ttl_expired(self._settings_updated_at):
            self._mode = "slow"
            self._slow_step = 20
            self._fast_step = 150
            self._persist_settings()

    def active_step(self) -> int:
        with self._lock:
            self._ensure_settings_current()
            return self._slow_step if self._mode == "slow" else self._fast_step

    @staticmethod
    def _plugin_direction(direction: str) -> str:
        """Map canonical and legacy API directions to the plugin contract."""
        try:
            return {
                "increase": DIR_OUT,
                "decrease": DIR_IN,
                "out": DIR_OUT,   # legacy HTTP compatibility
                "in": DIR_IN,     # legacy HTTP compatibility
            }[direction]
        except KeyError as exc:
            raise ValueError(
                "direction must be 'increase', 'decrease', 'in' or 'out'"
            ) from exc

    def set_mode(self, mode: str) -> dict:
        if mode not in ("slow", "fast"):
            raise ValueError("mode must be 'slow' or 'fast'")
        with self._lock:
            self._ensure_settings_current()
            self._mode = mode
            self._persist_settings()
            return self.status()

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
        self._ensure_settings_current()
        raw = dict(plugin.status() or {})
        # Position is deliberately read from the device, not from cached status.
        raw["position"] = plugin.get_position()
        if self._motion_command in ("go", "home") and not raw.get("moving"):
            self._motion_command = None
            self._target_position = None
        return {
            "connected": bool(plugin.connected),
            "position": raw.get("position"),
            "moving": bool(raw.get("moving", False)),
            "holding": bool(raw.get("holding", False)),
            "step_coarse": raw.get("step_coarse"),
            "step_fine": raw.get("step_fine"),
            "plugin": self._plugin_id,
            "mode": self._mode,
            "slow_step": self._slow_step,
            "fast_step": self._fast_step,
            "active_step": (
                self._slow_step if self._mode == "slow" else self._fast_step
            ),
            "motion_command": self._motion_command,
            "target_position": self._target_position,
        }

    def status(self) -> dict:
        """Return the selected plugin's live position and motion state."""
        with self._lock:
            self._ensure_settings_current()
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
                    "mode": self._mode,
                    "slow_step": self._slow_step,
                    "fast_step": self._fast_step,
                    "active_step": (
                        self._slow_step if self._mode == "slow" else self._fast_step
                    ),
                    "motion_command": self._motion_command,
                    "target_position": self._target_position,
                }
            return self._status_locked(self._plugin_for_operation())

    def home(self, wait: bool = False) -> dict:
        """Move to the focuser's zero position."""
        return self.move_to(0, wait=wait, _motion_command="home")

    def move_to(
        self,
        position: int,
        wait: bool = False,
        _motion_command: str = "go",
    ) -> dict:
        with self._lock:
            plugin = self._plugin_for_operation()
            target_position = int(position)
            plugin.move_to(target_position, wait=wait)
            self._motion_command = _motion_command
            self._target_position = target_position
            return self._status_locked(plugin)

    def move_relative(self, delta: int, wait: bool = False) -> dict:
        with self._lock:
            plugin = self._plugin_for_operation()
            plugin.move_relative(int(delta), wait=wait)
            return self._status_locked(plugin)

    def start_jog(self, direction: str, mode: str | None = None) -> dict:
        """Start jogging using the backend-authoritative mode.

        ``mode`` is retained only for compatibility with legacy callers.
        Its value is deliberately ignored: POST /api/focuser/mode is the
        authoritative way to select slow/fast operation.
        """
        with self._lock:
            self._ensure_settings_current()
            plugin = self._plugin_for_operation()
            plugin.start_continuous(
                self._plugin_direction(direction),
                "coarse" if self._mode == "fast" else "fine",
            )
            self._motion_command = "jog"
            self._target_position = None
            return self._status_locked(plugin)

    def stop_jog(self) -> dict:
        """Stop continuous motion; repeated calls are harmless."""
        with self._lock:
            self._motion_command = None
            self._target_position = None
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
            self._motion_command = None
            self._target_position = None
            active, plugin_id = self._selection()
            if not active or plugin_id == "none":
                self._close_locked()
                return self.status()
            plugin = self._plugin_for_operation()
            plugin.stop()
            return self._status_locked(plugin)

    def set_step(self, coarse: int | None = None, fine: int | None = None) -> dict:
        with self._lock:
            self._ensure_settings_current()
            plugin = self._plugin_for_operation()
            plugin.set_step(coarse=coarse, fine=fine)
            if coarse is not None:
                self._fast_step = int(coarse)
            if fine is not None:
                self._slow_step = int(fine)
            self._persist_settings()
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
