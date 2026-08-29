"""INDI implementation of the common mount plugin interface."""

from __future__ import annotations

import os
import time
from .base import (
    MountPlugin,
    RATE_LUNAR,
    RATE_SIDEREAL,
    RATE_SOLAR,
)
from .indi_client import IndiClientError, IndiSubprocessClient


_RATE_ELEMENTS = {
    RATE_SIDEREAL: ("TRACK_SIDEREAL", "SIDEREAL"),
    RATE_SOLAR: ("TRACK_SOLAR", "SOLAR"),
    RATE_LUNAR: ("TRACK_LUNAR", "LUNAR"),
}
_DIRECTION_ELEMENTS = {
    "north": ("TELESCOPE_MOTION_NS", "MOTION_NORTH"),
    "south": ("TELESCOPE_MOTION_NS", "MOTION_SOUTH"),
    "east": ("TELESCOPE_MOTION_WE", "MOTION_EAST"),
    "west": ("TELESCOPE_MOTION_WE", "MOTION_WEST"),
    "dec_left": ("TELESCOPE_MOTION_NS", "MOTION_NORTH"),
    "dec_right": ("TELESCOPE_MOTION_NS", "MOTION_SOUTH"),
    "ad_left": ("TELESCOPE_MOTION_WE", "MOTION_WEST"),
    "ad_right": ("TELESCOPE_MOTION_WE", "MOTION_EAST"),
}


class IndiMount(MountPlugin):
    plugin_id = "indi"
    display_name = "INDI / EQMod compatible"

    def __init__(self, log_fn=print, config=None, client=None):
        super().__init__(log_fn, config)
        self.device_name = self.config.get("device", "EQMod Mount")
        self.timeout = float(self.config.get("timeout", 3.0))
        self.home_timeout = float(self.config.get("home_timeout", 120.0))
        self.poll_interval = float(self.config.get("poll_interval", 0.05))
        self.client = client or IndiSubprocessClient(
            host=self.config.get("host", "127.0.0.1"),
            port=int(self.config.get("port", 7624)),
            device=self.device_name,
            timeout_s=float(self.config.get("client_timeout", 4.0)),
        )
        self._connected = False
        self._move_rate = None

    @staticmethod
    def probe(config=None):
        cfg = config or {}
        client = IndiSubprocessClient(
            host=cfg.get("host", "127.0.0.1"),
            port=int(cfg.get("port", 7624)),
            device=cfg.get("device", "EQMod Mount"),
            timeout_s=float(cfg.get("client_timeout", 4.0)),
        )
        try:
            client.ensure_device_present(cfg.get("device", "EQMod Mount"))
            return True
        except Exception:
            return False

    @staticmethod
    def _stable_serial_path(serial_port):
        """Resolve a serial device to its stable /dev/serial/by-id alias."""
        raw = str(serial_port or "").strip()
        if not raw:
            return None

        prefix = "/dev/serial/by-id/"
        if raw.startswith(prefix):
            return raw

        target = os.path.realpath(raw)
        root = "/dev/serial/by-id"
        try:
            names = sorted(os.listdir(root))
        except OSError:
            return None

        for name in names:
            candidate = os.path.join(root, name)
            try:
                if os.path.realpath(candidate) == target:
                    return candidate
            except OSError:
                continue

        return None

    @classmethod
    def inventory(cls, config=None):
        """Describe the configured INDI mount with a physical serial identity."""
        cfg = dict(config or {})
        device_name = cfg.get("device", "EQMod Mount")
        client = IndiSubprocessClient(
            host=cfg.get("host", "127.0.0.1"),
            port=int(cfg.get("port", 7624)),
            device=device_name,
            timeout_s=float(cfg.get("client_timeout", 4.0)),
        )

        try:
            client.ensure_device_present(device_name)
            props = client.get_props([
                "DEVICE_PORT.PORT",
                "DRIVER_INFO.*",
            ])
        except Exception:
            return []

        serial_port = (
            props.get("DEVICE_PORT", {}).get("PORT")
            if isinstance(props, dict)
            else None
        )
        stable_path = cls._stable_serial_path(serial_port)

        return [{
            "category": "mount",
            "backend": cls.plugin_id,
            "model": device_name,
            "device_name": device_name,
            "fallback_physical_path": stable_path,
        }]

    def connect(self):
        serial_port = (
            self.config.get("serial_port")
            or self.config.get("fallback_physical_path")
        )
        if not serial_port:
            raise IndiClientError("SERIAL_PORT_MISSING", "Serial port is required")
        if not os.path.exists(serial_port):
            raise IndiClientError("SERIAL_PORT_MISSING", f"Serial port does not exist: {serial_port}")
        if not os.access(serial_port, os.R_OK | os.W_OK):
            raise IndiClientError(
                "SERIAL_PERMISSION_DENIED", f"Serial port is not readable and writable: {serial_port}"
            )
        try:
            self.client.ensure_device_present(self.device_name)
            assignments = {
                "CONNECTION_MODE": {"CONNECTION_SERIAL": "On", "CONNECTION_TCP": "Off"}
            }
            assignments["DEVICE_PORT"] = {"PORT": serial_port}
            props = self._props()
            baud_prop = props.get("DEVICE_BAUD_RATE", {})
            if "baud" in self.config:
                baud_element = self._find_element(baud_prop, str(self.config["baud"]))
                if baud_element is None:
                    raise IndiClientError(
                        "PROPERTY_UNSUPPORTED", f"Unsupported INDI baud rate: {self.config['baud']}"
                    )
                assignments["DEVICE_BAUD_RATE"] = {
                    name: "On" if name == baud_element else "Off" for name in baud_prop
                }
            auto_prop = props.get("DEVICE_AUTO_SEARCH", {})
            if auto_prop:
                assignments["DEVICE_AUTO_SEARCH"] = {
                    name: "On" if name == "INDI_DISABLED" else "Off"
                    for name in auto_prop
                }

            # Runtime control uses one persistent INDI monitor. The initial
            # one-shot _props() above primes its cache; subsequent reads are
            # therefore memory-only while the monitor applies authoritative
            # updates from indiserver.
            start_monitor = getattr(self.client, "start_monitor", None)
            if callable(start_monitor):
                start_monitor()

            self.client.set_props(assignments)
            self.client.set_props({"CONNECTION": {"CONNECT": "On", "DISCONNECT": "Off"}})
            if not self._wait_for(lambda p: self._switch_on(p.get("CONNECTION", {}), "CONNECT")):
                raise IndiClientError("CONNECTION_FAILED", f"INDI device did not connect: {self.device_name}")
            self._connected = True
        except IndiClientError:
            raise
        except Exception as exc:
            self._raise_mapped("CONNECTION_FAILED", "Unable to connect to INDI mount", exc)

    def disconnect(self):
        try:
            self.client.set_props({"CONNECTION": {"CONNECT": "Off", "DISCONNECT": "On"}})
            self._wait_for(lambda p: not self._switch_on(p.get("CONNECTION", {}), "CONNECT"))
        except Exception:
            pass
        finally:
            stop_monitor = getattr(self.client, "stop_monitor", None)
            if callable(stop_monitor):
                stop_monitor()
            self._connected = False

    @property
    def connected(self):
        """Return locally cached connection state without hardware I/O."""
        return self._connected

    def ping(self):
        try:
            self.client.ensure_device_present(self.device_name)
            return {"ok": True, "connected": self.connected}
        except Exception as exc:
            return {"ok": False, "error": self._error_code(exc)}

    def status(self):
        try:
            props = self._props()
            connection = props.get("CONNECTION", {})
            connected = self._switch_on(connection, "CONNECT") if connection else self._connected
            equatorial = props.get("EQUATORIAL_EOD_COORD", props.get("EQUATORIAL_COORD", {}))
            ra = self._number(equatorial, "RA")
            dec = self._number(equatorial, "DEC")
            tracking_prop = props.get("TELESCOPE_TRACK_STATE", {})
            tracking = self._switch_on(tracking_prop, "TRACK_ON")
            tracking_rate = self._selected_rate(props.get("TELESCOPE_TRACK_MODE", {}))
            info = props.get("DRIVER_INFO", {})
            device_info = props.get("DEVICE_INFO", {})
            mount_info = props.get("MOUNTINFORMATION", {})
            parked_prop = props.get("TELESCOPE_PARK", {})
            device = {
                "driver": self._text(info, "DRIVER_EXEC") or "indi_eqmod_telescope",
                "device": self.device_name,
                "model": self._first_text(mount_info, "MOUNT_MODEL")
                or self._first_text(device_info, "MODEL", "DEVICE_MODEL"),
                "motor_controller": self._first_text(mount_info, "MOUNT_CONTROL")
                or self._first_text(device_info, "MOTOR_CONTROLLER", "MOTOR_TYPE"),
                "mount_code": self._first_text(mount_info, "MOUNT_CODE")
                or self._first_text(device_info, "MOUNT_CODE", "MOUNT_TYPE"),
                "coordinates": {"ra": ra, "dec": dec},
                "parked": self._switch_on(parked_prop, "PARK") if parked_prop else None,
            }
            return {
                "connected": connected,
                "ra": ra,
                "dec": dec,
                "tracking": tracking,
                "tracking_rate": tracking_rate,
                "move_rate": self._move_rate,
                "at_home": None,
                "device": device,
                "tracking_capabilities": self._tracking_capabilities(props),
                "slew_speed_capabilities": self._slew_capabilities(props),
                "capabilities": {
                    "tracking": self._tracking_capabilities(props),
                    "slew_speed": self._slew_capabilities(props),
                    "park": "TELESCOPE_PARK" in props,
                    "location": "GEOGRAPHIC_COORD" in props,
                },
            }
        except IndiClientError:
            raise
        except Exception as exc:
            self._raise_mapped("CONNECTION_FAILED", "Unable to read INDI mount status", exc)

    def get_tracking_capabilities(self):
        try:
            return self._tracking_capabilities(self._props())
        except IndiClientError:
            raise
        except Exception as exc:
            self._raise_mapped("CONNECTION_FAILED", "Unable to discover tracking capabilities", exc)

    def start_tracking(self, rate=RATE_SIDEREAL):
        if rate not in _RATE_ELEMENTS:
            raise ValueError(f"Unknown tracking rate: {rate}")
        try:
            props = self._props()
            mode_prop = props.get("TELESCOPE_TRACK_MODE", {})
            element = self._rate_element(mode_prop, rate)
            if element is None:
                raise IndiClientError("PROPERTY_UNSUPPORTED", f"Tracking rate is unsupported: {rate}")
            self.client.set_props({
                "TELESCOPE_TRACK_MODE": {name: "On" if name == element else "Off" for name in mode_prop},
                "TELESCOPE_TRACK_STATE": {"TRACK_ON": "On", "TRACK_OFF": "Off"},
            })
            if not self._wait_for(
                lambda p: self._switch_on(p.get("TELESCOPE_TRACK_STATE", {}), "TRACK_ON")
            ):
                raise IndiClientError("CONNECTION_FAILED", "INDI tracking did not start")
        except IndiClientError:
            raise
        except Exception as exc:
            self._raise_mapped("CONNECTION_FAILED", "Unable to start INDI tracking", exc)

    def set_tracking_mode(self, mode):
        try:
            props = self._props()
            element = self._rate_element(props.get("TELESCOPE_TRACK_MODE", {}), mode)
            if element is None:
                raise IndiClientError("PROPERTY_UNSUPPORTED", f"Tracking rate is unsupported: {mode}")
            self.client.set_props({"TELESCOPE_TRACK_MODE": {
                name: "On" if name == element else "Off" for name in props["TELESCOPE_TRACK_MODE"]
            }})
        except IndiClientError:
            raise
        except Exception as exc:
            self._raise_mapped("CONNECTION_FAILED", "Unable to set INDI tracking mode", exc)

    def stop_tracking(self):
        try:
            self.client.set_props({"TELESCOPE_TRACK_STATE": {"TRACK_ON": "Off", "TRACK_OFF": "On"}})
            if not self._wait_for(
                lambda p: not self._switch_on(p.get("TELESCOPE_TRACK_STATE", {}), "TRACK_ON")
            ):
                raise IndiClientError("CONNECTION_FAILED", "INDI tracking did not stop")
        except IndiClientError:
            raise
        except Exception as exc:
            self._raise_mapped("CONNECTION_FAILED", "Unable to stop INDI tracking", exc)

    @property
    def tracking(self):
        try:
            return self._switch_on(self._props(["TELESCOPE_TRACK_STATE.*"]).get("TELESCOPE_TRACK_STATE", {}), "TRACK_ON")
        except IndiClientError:
            raise
        except Exception as exc:
            self._raise_mapped("CONNECTION_FAILED", "Unable to read INDI tracking state", exc)

    def move(self, direction):
        if direction not in _DIRECTION_ELEMENTS:
            raise ValueError(f"Unknown direction: {direction}")
        prop, selected = _DIRECTION_ELEMENTS[direction]
        opposite = {
            "MOTION_NORTH": "MOTION_SOUTH", "MOTION_SOUTH": "MOTION_NORTH",
            "MOTION_EAST": "MOTION_WEST", "MOTION_WEST": "MOTION_EAST",
        }[selected]
        self._set_mapped({prop: {selected: "On", opposite: "Off"}}, "Unable to move INDI mount")

    def stop(self):
        self._set_mapped({
            "TELESCOPE_MOTION_NS": {"MOTION_NORTH": "Off", "MOTION_SOUTH": "Off"},
            "TELESCOPE_MOTION_WE": {"MOTION_EAST": "Off", "MOTION_WEST": "Off"},
            "TELESCOPE_ABORT_MOTION": {"ABORT": "On"},
        }, "Unable to stop INDI mount")

    def emergency_stop(self):
        try:
            self.stop()
        except Exception:
            pass
        try:
            self.stop_tracking()
        except Exception:
            pass

    def go_home(self, is_cancelled=None):
        """Return the EQMod mount to its mechanical Home reference."""
        tolerance_steps = 5

        try:
            # Stop manual slew without sending TELESCOPE_ABORT_MOTION.
            # An ABORT immediately before PARK can cancel the EQMod park slew.
            self.client.set_props({
                "TELESCOPE_MOTION_NS": {
                    "MOTION_NORTH": "Off",
                    "MOTION_SOUTH": "Off",
                },
                "TELESCOPE_MOTION_WE": {
                    "MOTION_EAST": "Off",
                    "MOTION_WEST": "Off",
                },
                "TELESCOPE_TRACK_STATE": {
                    "TRACK_ON": "Off",
                    "TRACK_OFF": "On",
                },
            })

            props = self._props([
                "TELESCOPE_PARK.*",
                "TELESCOPE_PARK_POSITION.*",
                "CURRENTSTEPPERS.*",
            ])

            park_prop = props.get("TELESCOPE_PARK", {})
            park_position = props.get("TELESCOPE_PARK_POSITION", {})
            current_steps = props.get("CURRENTSTEPPERS", {})

            if not park_prop:
                raise IndiClientError(
                    "PROPERTY_UNSUPPORTED",
                    "INDI mount does not expose TELESCOPE_PARK",
                )

            try:
                park_ra = float(park_position["PARK_RA"])
                park_dec = float(park_position["PARK_DEC"])
            except (KeyError, TypeError, ValueError):
                raise IndiClientError(
                    "PROPERTY_UNSUPPORTED",
                    "INDI mount does not expose a valid mechanical park position",
                )

            try:
                current_ra = float(current_steps["RAStepsCurrent"])
                current_dec = float(current_steps["DEStepsCurrent"])
            except (KeyError, TypeError, ValueError):
                current_ra = None
                current_dec = None

            already_home = (
                current_ra is not None
                and current_dec is not None
                and abs(current_ra - park_ra) <= tolerance_steps
                and abs(current_dec - park_dec) <= tolerance_steps
            )

            if not already_home:
                self.client.set_props({
                    "TELESCOPE_PARK": {
                        "PARK": "On",
                    }
                })

                deadline = time.monotonic() + self.home_timeout

                while True:
                    if callable(is_cancelled) and is_cancelled():
                        try:
                            self.stop()
                        finally:
                            raise RuntimeError("mount home cancelled")

                    props = self._props([
                        "TELESCOPE_PARK.*",
                        "TELESCOPE_PARK_POSITION.*",
                        "CURRENTSTEPPERS.*",
                    ])

                    park_state = props.get("TELESCOPE_PARK", {})
                    current_steps = props.get("CURRENTSTEPPERS", {})

                    try:
                        current_ra = float(current_steps["RAStepsCurrent"])
                        current_dec = float(current_steps["DEStepsCurrent"])
                    except (KeyError, TypeError, ValueError):
                        current_ra = None
                        current_dec = None

                    at_home = (
                        current_ra is not None
                        and current_dec is not None
                        and abs(current_ra - park_ra) <= tolerance_steps
                        and abs(current_dec - park_dec) <= tolerance_steps
                    )

                    parked = self._switch_on(park_state, "PARK")

                    if parked and at_home:
                        break

                    if time.monotonic() >= deadline:
                        try:
                            self.stop()
                        finally:
                            raise IndiClientError(
                                "CONNECTION_FAILED",
                                "INDI mount did not reach Home before timeout",
                            )

                    time.sleep(self.poll_interval)

            # Finish operational, not parked.
            self.client.set_props({
                "TELESCOPE_PARK": {
                    "UNPARK": "On",
                }
            })

            if not self._wait_for(
                lambda p: self._switch_on(
                    p.get("TELESCOPE_PARK", {}),
                    "UNPARK",
                )
            ):
                raise IndiClientError(
                    "CONNECTION_FAILED",
                    "INDI mount reached Home but did not unpark",
                )

            self._move_rate = None

        except IndiClientError:
            raise
        except RuntimeError:
            raise
        except Exception as exc:
            self._raise_mapped(
                "CONNECTION_FAILED",
                "Unable to home INDI mount",
                exc,
            )

    def set_speed(self, value):
        try:
            prop = self._props(["TELESCOPE_SLEW_RATE.*"]).get("TELESCOPE_SLEW_RATE", {})
            selected = self._find_element(prop, value)
            if selected is None:
                raise IndiClientError("PROPERTY_UNSUPPORTED", f"Unsupported slew speed: {value}")
            self.client.set_props({"TELESCOPE_SLEW_RATE": {
                name: "On" if name == selected else "Off" for name in prop
            }})
            self._move_rate = value
        except IndiClientError:
            raise
        except Exception as exc:
            self._raise_mapped("CONNECTION_FAILED", "Unable to set INDI slew speed", exc)

    def get_slew_speed_capabilities(self):
        try:
            return self._slew_capabilities(self._props())
        except IndiClientError:
            raise
        except Exception as exc:
            self._raise_mapped("CONNECTION_FAILED", "Unable to discover INDI slew speeds", exc)

    def set_location(self, lat, lon, elev):
        try:
            prop = self._props(["GEOGRAPHIC_COORD.*"]).get("GEOGRAPHIC_COORD")
            if not prop:
                raise IndiClientError("PROPERTY_UNSUPPORTED", "INDI geographic coordinates are unsupported")
            self.client.set_props({"GEOGRAPHIC_COORD": {
                "LAT": lat, "LONG": lon, "ELEV": elev,
            }})
        except IndiClientError:
            raise
        except Exception as exc:
            self._raise_mapped("CONNECTION_FAILED", "Unable to set INDI location", exc)

    def _props(self, patterns=None):
        return self.client.get_props(patterns)

    def _wait_for(self, predicate):
        deadline = time.monotonic() + self.timeout
        while True:
            if predicate(self._props()):
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(self.poll_interval)

    def _set_mapped(self, assignments, message):
        try:
            self.client.set_props(assignments)
        except IndiClientError:
            raise
        except Exception as exc:
            self._raise_mapped("CONNECTION_FAILED", message, exc)

    @classmethod
    def _tracking_capabilities(cls, props):
        mode_prop = props.get("TELESCOPE_TRACK_MODE", {})
        modes = [rate for rate in (RATE_SIDEREAL, RATE_SOLAR, RATE_LUNAR)
                 if cls._rate_element(mode_prop, rate) is not None]
        return {"toggle": "TELESCOPE_TRACK_STATE" in props, "modes": modes}

    @classmethod
    def _slew_capabilities(cls, props):
        prop = props.get("TELESCOPE_SLEW_RATE")
        if not prop:
            return None
        return {"kind": "discrete", "unit": None, "min": None, "max": None, "step": None,
                "values": [{"value": name, "label": cls._label(value, name)} for name, value in prop.items()]}

    @classmethod
    def _selected_rate(cls, prop):
        for rate in (RATE_SIDEREAL, RATE_SOLAR, RATE_LUNAR):
            element = cls._rate_element(prop, rate)
            if element and cls._switch_on(prop, element):
                return rate
        return None

    @staticmethod
    def _rate_element(prop, rate):
        for candidate in _RATE_ELEMENTS.get(rate, ()):
            if candidate in prop:
                return candidate
        return None

    @classmethod
    def _find_element(cls, prop, value):
        wanted = str(value).casefold()
        for name, raw in prop.items():
            candidates = {name.casefold(), cls._label(raw, name).casefold()}
            digits = "".join(ch for ch in name if ch.isdigit())
            if digits:
                candidates.add(digits.casefold())
            if wanted in candidates:
                return name
        return None

    @staticmethod
    def _raw(value):
        if isinstance(value, dict):
            return value.get("value", value.get("state"))
        return value

    @classmethod
    def _switch_on(cls, prop, element):
        return str(cls._raw(prop.get(element, "Off"))).casefold() in ("on", "true", "1")

    @classmethod
    def _number(cls, prop, element):
        value = cls._raw(prop.get(element))
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return value

    @classmethod
    def _text(cls, prop, element):
        value = cls._raw(prop.get(element))
        return None if value in (None, "") else str(value)

    @classmethod
    def _first_text(cls, prop, *elements):
        return next((value for name in elements if (value := cls._text(prop, name)) is not None), None)

    @staticmethod
    def _label(value, fallback):
        if isinstance(value, dict):
            return str(value.get("label", fallback))
        return fallback

    @staticmethod
    def _error_code(exc):
        return getattr(exc, "code", "CONNECTION_FAILED")

    @staticmethod
    def _raise_mapped(default_code, message, exc):
        code = getattr(exc, "code", default_code)
        raise IndiClientError(code, f"{message}: {exc}") from exc


__all__ = ["IndiMount"]
