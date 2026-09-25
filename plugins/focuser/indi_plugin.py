"""Generic INDI focuser backend.

The plugin uses the standard INDI focuser properties and contains no
vendor-specific SDK logic.  It is the primary focuser backend for devices
advertised by the central INDI catalogue.
"""

from __future__ import annotations

import threading
import time

from plugins.mount.indi_client import IndiClientError, IndiSubprocessClient
from .base import FocuserPlugin, DIR_IN, DIR_OUT, STEP_COARSE


class IndiFocuser(FocuserPlugin):
    plugin_id = "indi"
    display_name = "INDI focuser"

    def __init__(self, log_fn=print, config=None, client=None):
        super().__init__(log_fn, config)
        self.device_name = (
            self.config.get("device_name")
            or self.config.get("device")
            or "Focuser"
        )
        self.timeout = float(self.config.get("timeout", 8.0))
        self.poll_interval = float(self.config.get("poll_interval", 0.05))
        self.step_coarse = int(self.config.get("step_coarse", 150))
        self.step_fine = int(self.config.get("step_fine", 20))
        self.client = client or IndiSubprocessClient(
            host=self.config.get("host", "127.0.0.1"),
            port=int(self.config.get("port", 7624)),
            device=self.device_name,
            timeout_s=float(self.config.get("client_timeout", 4.0)),
        )
        self._connected = False
        self._moving = False
        self._holding = False
        self._target_position: int | None = None
        self._hold_stop = threading.Event()
        self._hold_thread: threading.Thread | None = None

    @staticmethod
    def probe(config=None):
        cfg = config or {}
        device_name = cfg.get("device_name") or cfg.get("device") or "Focuser"
        client = IndiSubprocessClient(
            host=cfg.get("host", "127.0.0.1"),
            port=int(cfg.get("port", 7624)),
            device=device_name,
            timeout_s=float(cfg.get("client_timeout", 4.0)),
        )
        try:
            client.ensure_device_present(device_name)
            props = client.get_props([
                "ABS_FOCUS_POSITION.*",
                "REL_FOCUS_POSITION.*",
                "FOCUS_MOTION.*",
            ])
            return any(
                name in props
                for name in (
                    "ABS_FOCUS_POSITION",
                    "REL_FOCUS_POSITION",
                    "FOCUS_MOTION",
                )
            )
        except Exception:
            return False

    def connect(self):
        try:
            self.client.ensure_device_present(self.device_name)
            props = self.client.get_props(["*.*"])
            start_monitor = getattr(self.client, "start_monitor", None)
            if callable(start_monitor):
                start_monitor()

            if props.get("CONNECTION"):
                self.client.set_props({
                    "CONNECTION": {
                        "CONNECT": "On",
                        "DISCONNECT": "Off",
                    }
                })
                if not self._wait_for(
                    lambda current: self._switch_on(
                        current.get("CONNECTION", {}),
                        "CONNECT",
                    )
                ):
                    raise IndiClientError(
                        "CONNECTION_FAILED",
                        f"INDI focuser did not connect: {self.device_name}",
                    )
            self._connected = True
        except IndiClientError:
            raise
        except Exception as exc:
            raise IndiClientError(
                "CONNECTION_FAILED",
                f"Unable to connect to INDI focuser: {exc}",
            ) from exc

    def disconnect(self):
        try:
            self.stop()
            props = self._props(["CONNECTION.*"])
            if props.get("CONNECTION"):
                self.client.set_props({
                    "CONNECTION": {
                        "CONNECT": "Off",
                        "DISCONNECT": "On",
                    }
                })
        except Exception:
            pass
        finally:
            stop_monitor = getattr(self.client, "stop_monitor", None)
            if callable(stop_monitor):
                stop_monitor()
            self._connected = False
            self._moving = False
            self._holding = False
            self._target_position = None

    @property
    def connected(self):
        return self._connected

    def _props(self, patterns=None):
        return self.client.get_props(patterns)

    @staticmethod
    def _raw(value):
        if isinstance(value, dict):
            return value.get("value", value.get("state"))
        return value

    @classmethod
    def _number(cls, prop, element):
        value = cls._raw(prop.get(element))
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    @classmethod
    def _switch_on(cls, prop, element):
        return str(cls._raw(prop.get(element, "Off"))).casefold() in {
            "on", "true", "1"
        }

    def _wait_for(self, predicate):
        deadline = time.monotonic() + self.timeout
        while True:
            if predicate(self._props()):
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(self.poll_interval)

    def get_position(self):
        prop = self._props(["ABS_FOCUS_POSITION.*"]).get(
            "ABS_FOCUS_POSITION", {}
        )
        value = self._number(prop, "FOCUS_ABSOLUTE_POSITION")
        if value is None:
            raise IndiClientError(
                "PROPERTY_UNSUPPORTED",
                f"{self.device_name} does not expose absolute focus position",
            )
        return int(round(value))

    def _max_step(self):
        prop = self._props(["FOCUS_MAX.*"]).get("FOCUS_MAX", {})
        value = self._number(prop, "FOCUS_MAX_VALUE")
        if value is None:
            configured = self.config.get("max_step")
            try:
                value = float(configured) if configured is not None else None
            except (TypeError, ValueError):
                value = None
        return None if value is None else int(round(value))

    def _refresh_motion_state(self, position: int) -> None:
        if self._target_position is not None and position == self._target_position:
            self._moving = False
            self._holding = False
            self._target_position = None

    def status(self):
        position = self.get_position()
        self._refresh_motion_state(position)
        props = self._props(["FOCUS_TEMPERATURE.*"])
        temperature = self._number(
            props.get("FOCUS_TEMPERATURE", {}),
            "TEMPERATURE",
        )
        return {
            "connected": self.connected,
            "position": position,
            "max_step": self._max_step(),
            "moving": bool(self._moving),
            "holding": bool(self._holding),
            "temperature": temperature,
            "step_coarse": self.step_coarse,
            "step_fine": self.step_fine,
            "device": self.device_name,
        }

    def set_step(self, coarse=None, fine=None):
        if coarse is not None:
            self.step_coarse = int(coarse)
        if fine is not None:
            self.step_fine = int(fine)

    def move_to(self, position, wait=False):
        target = int(position)
        maximum = self._max_step()
        if target < 0 or (maximum is not None and target > maximum):
            raise ValueError("focuser target is outside supported limits")

        props = self._props(["ABS_FOCUS_POSITION.*"])
        if "ABS_FOCUS_POSITION" not in props:
            raise IndiClientError(
                "PROPERTY_UNSUPPORTED",
                f"{self.device_name} does not support absolute focus moves",
            )

        self._target_position = target
        self._moving = True
        self.client.set_props({
            "ABS_FOCUS_POSITION": {
                "FOCUS_ABSOLUTE_POSITION": target,
            }
        })

        if wait:
            if not self._wait_for(lambda _props: self.get_position() == target):
                raise IndiClientError(
                    "TIMEOUT",
                    f"INDI focuser did not reach position {target}",
                )
            self._moving = False
            self._holding = False
            self._target_position = None
        return target

    def move_relative(self, delta, wait=False):
        return self.move_to(self.get_position() + int(delta), wait=wait)

    def set_current_position(self, value):
        sync = self._props(["FOCUS_SYNC.*"]).get("FOCUS_SYNC", {})
        if not sync:
            return self.get_position()
        element = (
            "FOCUS_SYNC_VALUE"
            if "FOCUS_SYNC_VALUE" in sync
            else next(iter(sync), None)
        )
        if element is None:
            return self.get_position()
        self.client.set_props({"FOCUS_SYNC": {element: int(value)}})
        self._moving = False
        self._holding = False
        self._target_position = None
        return int(value)

    def stop(self):
        self._hold_stop.set()
        props = self._props([
            "FOCUS_ABORT_MOTION.*",
            "FOCUS_MOTION.*",
        ])
        abort = props.get("FOCUS_ABORT_MOTION", {})
        if abort:
            element = "ABORT" if "ABORT" in abort else next(iter(abort), None)
            if element is not None:
                self.client.set_props({
                    "FOCUS_ABORT_MOTION": {element: "On"}
                })
        elif props.get("FOCUS_MOTION"):
            self.client.set_props({
                "FOCUS_MOTION": {
                    name: "Off"
                    for name in props["FOCUS_MOTION"]
                }
            })
        self._moving = False
        self._holding = False
        self._target_position = None

    def _hold_loop(self, direction, step):
        signed = step if direction == DIR_OUT else -step
        try:
            while not self._hold_stop.is_set():
                current = self.get_position()
                maximum = self._max_step()
                target = current + signed
                if target < 0:
                    target = 0
                if maximum is not None and target > maximum:
                    target = maximum
                if target == current:
                    break

                self.move_to(target, wait=False)
                deadline = time.monotonic() + self.timeout
                while not self._hold_stop.is_set():
                    if self.get_position() == target:
                        break
                    if time.monotonic() >= deadline:
                        raise IndiClientError(
                            "TIMEOUT",
                            "INDI focuser jog step timed out",
                        )
                    self._hold_stop.wait(self.poll_interval)
        except Exception as exc:
            self.log(f"   [indi_focuser] jog stopped: {exc}")
        finally:
            if self._hold_stop.is_set():
                try:
                    self.stop()
                except Exception:
                    pass
            self._holding = False
            self._moving = False

    def start_continuous(self, direction, mode=STEP_COARSE):
        if direction not in {DIR_IN, DIR_OUT}:
            raise ValueError(f"Unknown direction: {direction}")
        self.stop_continuous()
        step = self.step_coarse if mode == STEP_COARSE else self.step_fine
        self._hold_stop.clear()
        self._holding = True
        self._moving = True
        self._hold_thread = threading.Thread(
            target=self._hold_loop,
            args=(direction, int(step)),
            name=f"indi-focuser-jog-{self.device_name}",
            daemon=True,
        )
        self._hold_thread.start()

    def stop_continuous(self):
        thread = self._hold_thread
        if thread is None:
            return
        self._hold_stop.set()
        if thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=min(self.timeout, 3.0))
        self._hold_thread = None
        try:
            self.stop()
        except Exception:
            pass


__all__ = ["IndiFocuser"]
