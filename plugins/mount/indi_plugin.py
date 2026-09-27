"""INDI implementation of the common mount plugin interface."""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from .base import (
    MountPlugin,
    RATE_LUNAR,
    RATE_SIDEREAL,
    RATE_SOLAR,
)
from .indi_client import IndiClientError, IndiSubprocessClient, IndiTcpSession


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
    display_name = "INDI telescope"

    def __init__(self, log_fn=print, config=None, client=None):
        super().__init__(log_fn, config)
        self.device_name = (
            self.config.get("device_name")
            or self.config.get("device")
            or "EQMod Mount"
        )
        self.timeout = float(self.config.get("timeout", 3.0))
        self.home_timeout = float(self.config.get("home_timeout", 120.0))
        self.poll_interval = float(self.config.get("poll_interval", 0.05))
        self._runtime_tcp_enabled = client is None
        self.client = client or IndiSubprocessClient(
            host=self.config.get("host", "127.0.0.1"),
            port=int(self.config.get("port", 7624)),
            device=self.device_name,
            timeout_s=float(self.config.get("client_timeout", 4.0)),
        )
        self._control_session = None
        self._connected = False
        self._move_rate = None

    @staticmethod
    def probe(config=None):
        cfg = config or {}
        device_name = (
            cfg.get("device_name")
            or cfg.get("device")
            or "EQMod Mount"
        )
        client = IndiSubprocessClient(
            host=cfg.get("host", "127.0.0.1"),
            port=int(cfg.get("port", 7624)),
            device=device_name,
            timeout_s=float(cfg.get("client_timeout", 4.0)),
        )
        try:
            client.ensure_device_present(device_name)
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
        device_name = (
            cfg.get("device_name")
            or cfg.get("device")
            or "EQMod Mount"
        )
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
                "DEVICE_INFO.*",
                "MOUNTINFORMATION.*",
            ])
        except Exception:
            return []

        serial_port = (
            props.get("DEVICE_PORT", {}).get("PORT")
            if isinstance(props, dict)
            else None
        )
        stable_path = cls._stable_serial_path(serial_port)

        device_info = props.get("DEVICE_INFO", {}) if isinstance(props, dict) else {}
        mount_info = props.get("MOUNTINFORMATION", {}) if isinstance(props, dict) else {}

        model = (
            cls._first_text(mount_info, "MOUNT_MODEL", "MODEL")
            or cls._first_text(device_info, "MODEL", "DEVICE_MODEL")
            or device_name
        )
        manufacturer = (
            cls._first_text(mount_info, "MANUFACTURER", "MOUNT_MANUFACTURER")
            or cls._first_text(device_info, "MANUFACTURER", "DEVICE_MANUFACTURER")
        )
        serial = (
            cls._first_text(
                mount_info,
                "MOUNT_SERIAL",
                "SERIAL",
                "SERIAL_NUMBER",
                "SERIALNUMBER",
            )
            or cls._first_text(
                device_info,
                "SERIAL",
                "SERIAL_NUMBER",
                "SERIALNUMBER",
                "DEVICE_SERIAL",
            )
        )

        entry = {
            "category": "mount",
            "backend": cls.plugin_id,
            "model": model,
            "device_name": device_name,
            "fallback_physical_path": stable_path,
        }
        if manufacturer:
            entry["manufacturer"] = manufacturer
        if serial:
            entry["serial"] = serial
        return [entry]

    def connect(self):
        """Connect a generic INDI telescope without assuming EQMod.

        Serial drivers may receive a stable serial_by_id path from the RIG
        binding. Network/native INDI drivers are allowed to keep their own
        connection transport and therefore do not require a serial path.
        """
        serial_port = (
            self.config.get("serial_port")
            or self.config.get("fallback_physical_path")
        )
        if serial_port and not os.path.exists(serial_port):
            raise IndiClientError(
                "SERIAL_PORT_MISSING",
                f"Serial port does not exist: {serial_port}",
            )
        if serial_port and not os.access(serial_port, os.R_OK | os.W_OK):
            raise IndiClientError(
                "SERIAL_PERMISSION_DENIED",
                f"Serial port is not readable and writable: {serial_port}",
            )

        try:
            self.client.ensure_device_present(self.device_name)
            props = self._props()

            assignments = {}
            if serial_port:
                connection_mode = props.get("CONNECTION_MODE", {})
                if connection_mode:
                    assignments["CONNECTION_MODE"] = {
                        name: "On" if name == "CONNECTION_SERIAL" else "Off"
                        for name in connection_mode
                    }
                else:
                    # Legacy EQMod/OnStep setups relied on these standard
                    # property names even when the first property snapshot was
                    # incomplete. An explicit serial binding remains
                    # authoritative.
                    assignments["CONNECTION_MODE"] = {
                        "CONNECTION_SERIAL": "On",
                        "CONNECTION_TCP": "Off",
                    }
                assignments["DEVICE_PORT"] = {"PORT": serial_port}

            baud_prop = props.get("DEVICE_BAUD_RATE", {})
            if "baud" in self.config and baud_prop:
                baud_element = self._find_element(
                    baud_prop,
                    str(self.config["baud"]),
                )
                if baud_element is None:
                    raise IndiClientError(
                        "PROPERTY_UNSUPPORTED",
                        f"Unsupported INDI baud rate: {self.config['baud']}",
                    )
                assignments["DEVICE_BAUD_RATE"] = {
                    name: "On" if name == baud_element else "Off"
                    for name in baud_prop
                }

            auto_prop = props.get("DEVICE_AUTO_SEARCH", {})
            if serial_port and auto_prop:
                assignments["DEVICE_AUTO_SEARCH"] = {
                    name: "On" if name == "INDI_DISABLED" else "Off"
                    for name in auto_prop
                }

            start_monitor = getattr(self.client, "start_monitor", None)
            if callable(start_monitor):
                start_monitor()

            # The legacy LX200 OnStep driver accepts the same standard INDI
            # vectors as EQMod, but field testing with driver 1.17 shows that
            # manual slew writes sent on our persistent XML/TCP session can be
            # acknowledged without reaching the controller.  Keep OnStep on
            # the proven indi_setprop transport for every runtime write.  This
            # also keeps speed, tracking, Home and STOP on one transport.
            self._open_control_session(props)

            if assignments:
                self._set_props(assignments)

            connection = props.get("CONNECTION", {})
            if connection:
                self._set_props({
                    "CONNECTION": {
                        "CONNECT": "On",
                        "DISCONNECT": "Off",
                    }
                })
                if not self._wait_for(
                    lambda p: self._switch_on(
                        p.get("CONNECTION", {}),
                        "CONNECT",
                    )
                ):
                    raise IndiClientError(
                        "CONNECTION_FAILED",
                        f"INDI device did not connect: {self.device_name}",
                    )

            if self._runtime_tcp_enabled and self._is_onstep_driver(props):
                # CONNECT=On is not sufficient for the legacy OnStep driver.
                # A cold/partial startup can leave the device logically
                # connected without publishing the manual-slew vectors.
                self._ensure_onstep_runtime_ready()

            # Safety invariant: selecting/connecting a mount in SolarTrigger
            # must never inherit tracking left active by a previous client.
            self._ensure_tracking_stopped()
            self._connected = True
        except IndiClientError:
            self._cleanup_runtime_channels()
            raise
        except Exception as exc:
            self._cleanup_runtime_channels()
            self._raise_mapped(
                "CONNECTION_FAILED",
                "Unable to connect to INDI mount",
                exc,
            )

    def disconnect(self):
        try:
            self._set_props({"CONNECTION": {"CONNECT": "Off", "DISCONNECT": "On"}})
            self._wait_for(lambda p: not self._switch_on(p.get("CONNECTION", {}), "CONNECT"))
        except Exception:
            pass
        finally:
            self._cleanup_runtime_channels()
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
            if (
                self._runtime_tcp_enabled
                and self._is_onstep_driver(props)
                and not self._onstep_operational_props(props)
            ):
                props = self._ensure_onstep_runtime_ready()
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
                "driver": self._text(info, "DRIVER_EXEC") or "indi",
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
            self._set_props({
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
            self._set_props({"TELESCOPE_TRACK_MODE": {
                name: "On" if name == element else "Off" for name in props["TELESCOPE_TRACK_MODE"]
            }})
        except IndiClientError:
            raise
        except Exception as exc:
            self._raise_mapped("CONNECTION_FAILED", "Unable to set INDI tracking mode", exc)

    def _ensure_tracking_stopped(self):
        props = self._props(["TELESCOPE_TRACK_STATE.*"])
        tracking = props.get("TELESCOPE_TRACK_STATE", {})
        if not tracking:
            return
        if not self._switch_on(tracking, "TRACK_ON"):
            return
        self._set_props({
            "TELESCOPE_TRACK_STATE": {
                "TRACK_ON": "Off",
                "TRACK_OFF": "On",
            }
        })
        if not self._wait_for(
            lambda current: not self._switch_on(
                current.get("TELESCOPE_TRACK_STATE", {}),
                "TRACK_ON",
            )
        ):
            raise IndiClientError(
                "CONNECTION_FAILED",
                "INDI mount tracking could not be disabled safely",
            )

    def stop_tracking(self):
        try:
            self._set_props({"TELESCOPE_TRACK_STATE": {"TRACK_ON": "Off", "TRACK_OFF": "On"}})
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
        if self._runtime_tcp_enabled:
            identity = self._props(["DRIVER_INFO.*", "CONNECTION.*"])
            if self._is_onstep_driver(identity):
                self._ensure_onstep_runtime_ready()
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
        """Use the standard INDI Home capability when the driver exposes it.

        Older EQMod deployments are kept compatible through the historical
        PARK/CURRENTSTEPPERS fallback.
        """
        props = self._props(["TELESCOPE_HOME.*", "HOME_INIT.*", "DRIVER_INFO.*"])
        home_prop = props.get("TELESCOPE_HOME", {})
        if home_prop:
            element = None
            for candidate in ("GO", "HOME_GO"):
                if candidate in home_prop:
                    element = candidate
                    break
            if element is None:
                element = next(
                    (
                        name for name in home_prop
                        if "GO" in name.upper()
                    ),
                    None,
                )
            if element is None:
                raise IndiClientError(
                    "PROPERTY_UNSUPPORTED",
                    "INDI telescope Home property has no GO action",
                )

            self._set_props({
                "TELESCOPE_HOME": {
                    name: "On" if name == element else "Off"
                    for name in home_prop
                }
            })

            deadline = time.monotonic() + self.home_timeout
            seen_active = False
            while True:
                if callable(is_cancelled) and is_cancelled():
                    try:
                        self.stop()
                    finally:
                        raise RuntimeError("mount home cancelled")

                current = self._props(["TELESCOPE_HOME.*"]).get(
                    "TELESCOPE_HOME",
                    {},
                )
                active = self._switch_on(current, element)
                seen_active = seen_active or active
                if seen_active and not active:
                    self._move_rate = None
                    return
                if time.monotonic() >= deadline:
                    try:
                        self.stop()
                    finally:
                        raise IndiClientError(
                            "TIMEOUT",
                            "INDI mount did not reach Home before timeout",
                        )
                time.sleep(self.poll_interval)

        # Older OnStep INDI drivers expose the mechanical Home action through
        # HOME_INIT.RETURN_HOME rather than the standard TELESCOPE_HOME vector.
        # This is a true Home operation: it must never be emulated with PARK.
        home_init = props.get("HOME_INIT", {})
        if self._is_onstep_driver(props) and "RETURN_HOME" in home_init:
            # Match the command proven by the legacy OnStep driver: only
            # pulse RETURN_HOME.  Other HOME_INIT switches (notably AT_HOME)
            # are independent actions and must not be rewritten as part of
            # Return Home.
            self._set_props({
                "HOME_INIT": {
                    "RETURN_HOME": "On",
                }
            })

            deadline = time.monotonic() + self.home_timeout
            while True:
                if callable(is_cancelled) and is_cancelled():
                    try:
                        self.stop()
                    finally:
                        raise RuntimeError("mount home cancelled")

                current = self._props([
                    "HOME_INIT.*",
                    "TELESCOPE_PARK.*",
                    "OnStep Status.*",
                ])
                park_state = current.get("TELESCOPE_PARK", {})
                if self._switch_on(park_state, "PARK"):
                    raise IndiClientError(
                        "CONNECTION_FAILED",
                        "OnStep entered Park while returning Home",
                    )

                status = current.get("OnStep Status", {})
                park_text = self._first_text(status, "Park") or ""
                if "at home" in park_text.casefold() and "unparked" in park_text.casefold():
                    self._move_rate = None
                    return

                # HOME_INIT is momentary on legacy OnStep.  Do not interpret
                # RETURN_HOME=Off alone as completion: the real driver turns
                # it Off immediately after accepting the command.
                if time.monotonic() >= deadline:
                    try:
                        self.stop()
                    finally:
                        raise IndiClientError(
                            "TIMEOUT",
                            "OnStep mount did not reach Home before timeout",
                        )
                time.sleep(self.poll_interval)

        # PARK is not a generic Home implementation.  In particular OnStep
        # uses a distinct mechanical Home command and may refuse UNPARK until
        # date/time/location have been initialised.  Falling through to the
        # historical EQMod PARK sequence can therefore leave an OnStep mount
        # parked and make all manual slew buttons appear dead.
        if not self._is_eqmod_driver(props):
            raise IndiClientError(
                "PROPERTY_UNSUPPORTED",
                "INDI mount does not expose native Home; refusing PARK fallback",
            )

        return self._go_home_eqmod_legacy(is_cancelled=is_cancelled)

    def _go_home_eqmod_legacy(self, is_cancelled=None):
        """Historical EQMod Home implementation retained for compatibility."""
        tolerance_steps = 5

        try:
            # Stop manual slew without sending TELESCOPE_ABORT_MOTION.
            # An ABORT immediately before PARK can cancel the EQMod park slew.
            self._set_props({
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
                self._set_props({
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
            self._set_props({
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
            identity = self._props(["DRIVER_INFO.*"])
            if self._runtime_tcp_enabled and self._is_onstep_driver(identity):
                self._set_onstep_speed_live(value)
                return

            prop = self._props(["TELESCOPE_SLEW_RATE.*"]).get(
                "TELESCOPE_SLEW_RATE",
                {},
            )
            selected = self._find_element(prop, value)
            if selected is None:
                raise IndiClientError(
                    "PROPERTY_UNSUPPORTED",
                    f"Unsupported slew speed: {value}",
                )
            self._set_props({"TELESCOPE_SLEW_RATE": {
                name: "On" if name == selected else "Off" for name in prop
            }})
            self._move_rate = value
        except IndiClientError:
            raise
        except Exception as exc:
            self._raise_mapped(
                "CONNECTION_FAILED",
                "Unable to set INDI slew speed",
                exc,
            )

    def _set_onstep_speed_live(self, value):
        """Write an OnStep slew rate only against freshly advertised INDI state.

        The persistent monitor deliberately keeps the last known vector so UI
        status stays cheap.  That cache must never authorize a write after the
        OnStep driver has disconnected and withdrawn TELESCOPE_SLEW_RATE.
        """
        client = self._fresh_subprocess_client()
        props = self._ensure_onstep_indi_live(
            client,
            require_operational=True,
        )
        prop = props.get("TELESCOPE_SLEW_RATE", {})
        selected = self._find_element(prop, value)
        if selected is None:
            raise IndiClientError(
                "PROPERTY_UNSUPPORTED",
                f"Unsupported slew speed: {value}",
            )

        # For an INDI 1-of-many vector, setting only the selected element is
        # sufficient.  Avoid replaying cached OFF values for elements that may
        # no longer be advertised by the live driver generation.
        client.set_props({
            "TELESCOPE_SLEW_RATE": {
                selected: "On",
            }
        })

        if not self._wait_fresh_for(
            client,
            lambda current: self._switch_on(
                current.get("TELESCOPE_SLEW_RATE", {}),
                selected,
            ),
            ["TELESCOPE_SLEW_RATE.*", "CONNECTION.*"],
        ):
            raise IndiClientError(
                "CONNECTION_FAILED",
                "OnStep slew speed change was not confirmed by live readback",
            )

        self._connected = True
        self._move_rate = value

    def get_slew_speed_capabilities(self):
        try:
            props = self._props()
            if (
                self._runtime_tcp_enabled
                and self._is_onstep_driver(props)
                and not self._onstep_operational_props(props)
            ):
                props = self._ensure_onstep_runtime_ready()
            return self._slew_capabilities(props)
        except IndiClientError:
            raise
        except Exception as exc:
            self._raise_mapped("CONNECTION_FAILED", "Unable to discover INDI slew speeds", exc)

    def set_location(self, lat, lon, elev):
        try:
            props = self._props(["GEOGRAPHIC_COORD.*", "DRIVER_INFO.*"])
            prop = props.get("GEOGRAPHIC_COORD")
            if not prop:
                raise IndiClientError(
                    "PROPERTY_UNSUPPORTED",
                    "INDI geographic coordinates are unsupported",
                )
            assignments = {
                "LAT": lat,
                "LONG": lon,
                "ELEV": elev,
            }
            if self._is_onstep_driver(props):
                self._set_onstep_vector_atomic("GEOGRAPHIC_COORD", assignments)
                self._verify_onstep_location(lat, lon, elev)
            else:
                self._set_props({"GEOGRAPHIC_COORD": assignments})
        except IndiClientError:
            raise
        except Exception as exc:
            self._raise_mapped("CONNECTION_FAILED", "Unable to set INDI location", exc)

    def sync_site_time(self, lat, lon, elev, utc_iso, utc_offset_hours):
        """Synchronize site/time only after an explicit operator request."""
        try:
            props = self._props([
                "GEOGRAPHIC_COORD.*",
                "TIME_UTC.*",
                "DRIVER_INFO.*",
            ])
            onstep = self._is_onstep_driver(props)

            if onstep:
                return self._sync_onstep_direct(
                    lat,
                    lon,
                    elev,
                    utc_iso,
                    utc_offset_hours,
                )

            location = props.get("GEOGRAPHIC_COORD")
            time_prop = props.get("TIME_UTC")
            if not location:
                raise IndiClientError(
                    "PROPERTY_UNSUPPORTED",
                    "INDI geographic coordinates are unsupported",
                )
            if not time_prop or "UTC" not in time_prop or "OFFSET" not in time_prop:
                raise IndiClientError(
                    "PROPERTY_UNSUPPORTED",
                    "INDI UTC time synchronization is unsupported",
                )

            location_values = {
                "LAT": lat,
                "LONG": lon,
                "ELEV": elev,
            }
            time_values = {
                "UTC": str(utc_iso),
                "OFFSET": f"{float(utc_offset_hours):+.2f}",
            }

            # EQMod and other standard INDI telescope drivers use the normal
            # vector transport and must confirm every applied value.
            self._set_props({
                "GEOGRAPHIC_COORD": location_values,
                "TIME_UTC": time_values,
            })
            self._verify_site_time(
                lat,
                lon,
                elev,
                utc_iso,
                utc_offset_hours,
                driver_label="INDI",
            )

            return {
                "latitude": float(lat),
                "longitude": float(lon),
                "elevation": float(elev),
                "utc": str(utc_iso),
                "utc_offset_hours": float(utc_offset_hours),
            }
        except IndiClientError:
            raise
        except Exception as exc:
            self._raise_mapped(
                "CONNECTION_FAILED",
                "Unable to synchronize INDI mount site/time",
                exc,
            )

    def _fresh_subprocess_client(self):
        """Return a one-shot client that never reads the persistent monitor cache."""
        return IndiSubprocessClient(
            host=self.config.get("host", "127.0.0.1"),
            port=int(self.config.get("port", 7624)),
            device=self.device_name,
            timeout_s=float(self.config.get("client_timeout", 4.0)),
        )

    def _seed_runtime_cache(self, props):
        seed = getattr(self.client, "seed_monitor_cache", None)
        if callable(seed):
            seed(props)

    def _ensure_onstep_runtime_ready(self):
        """Repair a half-connected OnStep before exposing/using manual slew."""
        patterns = [
            "DRIVER_INFO.*",
            "CONNECTION.*",
            "DEVICE_PORT.*",
            "TELESCOPE_SLEW_RATE.*",
            "TELESCOPE_MOTION_NS.*",
            "TELESCOPE_MOTION_WE.*",
        ]
        cached = self._props(patterns)
        if not self._is_onstep_driver(cached):
            return cached
        if self._onstep_operational_props(cached):
            return cached

        fresh = self._fresh_subprocess_client()
        ready = self._ensure_onstep_indi_live(
            fresh,
            require_operational=True,
        )
        self._seed_runtime_cache(ready)
        return ready

    def _wait_fresh_for(
        self,
        client,
        predicate,
        patterns,
        *,
        timeout_s=None,
    ):
        effective_timeout = (
            self.timeout
            if timeout_s is None
            else max(0.0, float(timeout_s))
        )
        deadline = time.monotonic() + effective_timeout
        first = True
        while first or time.monotonic() < deadline:
            first = False
            current = client.get_props(patterns)
            if predicate(current):
                return True
            if time.monotonic() >= deadline:
                break
            time.sleep(self.poll_interval)
        return False

    def _onstep_serial_port(self, client):
        configured = (
            self.config.get("serial_port")
            or self.config.get("fallback_physical_path")
        )
        if configured:
            return str(configured)

        live = client.get_props(["DEVICE_PORT.PORT"])
        port = self._text(live.get("DEVICE_PORT", {}), "PORT")
        if not port:
            raise IndiClientError(
                "SERIAL_PORT_MISSING",
                "OnStep serial transport is unavailable for direct synchronization",
            )
        return port

    def _set_onstep_indi_connection(
        self,
        client,
        connected,
        serial_port=None,
        *,
        timeout_s=None,
    ):
        if connected and serial_port:
            client.set_props({"DEVICE_PORT": {"PORT": serial_port}})

        client.set_props({
            "CONNECTION": {
                "CONNECT": "On" if connected else "Off",
                "DISCONNECT": "Off" if connected else "On",
            }
        })

        expected = "CONNECT" if connected else "DISCONNECT"
        if not self._wait_fresh_for(
            client,
            lambda current: self._switch_on(
                current.get("CONNECTION", {}),
                expected,
            ),
            ["CONNECTION.*"],
            timeout_s=timeout_s,
        ):
            state = "connect" if connected else "disconnect"
            raise IndiClientError(
                "CONNECTION_FAILED",
                f"Unable to {state} OnStep INDI driver for direct synchronization",
            )
        self._connected = bool(connected)

    def _ensure_onstep_indi_live(
        self,
        client,
        *,
        require_slew_rate=False,
        require_operational=False,
    ):
        patterns = [
            "CONNECTION.*",
            "DEVICE_PORT.*",
            "DRIVER_INFO.*",
        ]
        if require_slew_rate or require_operational:
            patterns.append("TELESCOPE_SLEW_RATE.*")
        if require_operational:
            patterns.extend([
                "TELESCOPE_MOTION_NS.*",
                "TELESCOPE_MOTION_WE.*",
            ])

        recovery_timeout_s = max(
            self.timeout,
            float(self.config.get("onstep_connect_timeout", 5.0)),
        )
        props = client.get_props(patterns)
        connection = props.get("CONNECTION", {})
        connected = self._switch_on(connection, "CONNECT")
        has_required = True
        if require_slew_rate:
            has_required = bool(props.get("TELESCOPE_SLEW_RATE"))
        if require_operational:
            has_required = self._onstep_operational_props(props)
        if connected and has_required:
            return props

        # The monitor may still contain a rate vector from an older driver
        # generation.  Force a clean live reconnect when the one-shot client
        # cannot see the operational vector.
        serial_port = self._onstep_serial_port(client)
        if connected:
            self._set_onstep_indi_connection(
                client,
                False,
                serial_port=serial_port,
                timeout_s=recovery_timeout_s,
            )
        self._set_onstep_indi_connection(
            client,
            True,
            serial_port=serial_port,
            timeout_s=recovery_timeout_s,
        )

        if not self._wait_fresh_for(
            client,
            lambda current: (
                self._switch_on(current.get("CONNECTION", {}), "CONNECT")
                and (
                    (
                        not require_operational
                        and (
                            not require_slew_rate
                            or bool(current.get("TELESCOPE_SLEW_RATE"))
                        )
                    )
                    or (
                        require_operational
                        and self._onstep_operational_props(current)
                    )
                )
            ),
            patterns,
            timeout_s=recovery_timeout_s,
        ):
            raise IndiClientError(
                "CONNECTION_FAILED",
                "OnStep INDI driver reconnected but did not advertise "
                "its operational properties",
            )
        self._connected = True
        ready = client.get_props(patterns)
        self._seed_runtime_cache(ready)
        return ready

    def _sync_onstep_direct(
        self,
        lat,
        lon,
        elev,
        utc_iso,
        utc_offset_hours,
    ):
        """Hand serial ownership to the proven direct OnStep LX200 setup path."""
        from .onstep import OnStep

        client = self._fresh_subprocess_client()
        monitor_active = bool(getattr(self.client, "monitor_active", False))
        stop_monitor = getattr(self.client, "stop_monitor", None)
        start_monitor = getattr(self.client, "start_monitor", None)
        clear_monitor_cache = getattr(
            self.client,
            "clear_monitor_cache",
            None,
        )

        if monitor_active and callable(stop_monitor):
            stop_monitor()
        if callable(clear_monitor_cache):
            clear_monitor_cache()

        serial_port = self._onstep_serial_port(client)
        sync_error = None
        reconnect_error = None

        try:
            live = client.get_props(["CONNECTION.*"])
            if not live.get("CONNECTION"):
                raise IndiClientError(
                    "CONNECTION_FAILED",
                    "OnStep INDI connection state is unavailable; refusing "
                    "direct serial handoff",
                )
            if self._switch_on(live.get("CONNECTION", {}), "CONNECT"):
                self._set_onstep_indi_connection(
                    client,
                    False,
                    serial_port=serial_port,
                )

            try:
                parsed_utc = datetime.fromisoformat(
                    str(utc_iso).replace("Z", "+00:00")
                )
            except ValueError as exc:
                raise ValueError(
                    f"invalid UTC synchronization value: {utc_iso!r}"
                ) from exc
            if parsed_utc.tzinfo is None:
                parsed_utc = parsed_utc.replace(tzinfo=timezone.utc)
            else:
                parsed_utc = parsed_utc.astimezone(timezone.utc)

            controller = OnStep(
                port=serial_port,
                baudrate=int(
                    self.config.get(
                        "baudrate",
                        self.config.get("baud", 9600),
                    )
                ),
                timeout=float(self.config.get("onstep_serial_timeout", 1.0)),
            )
            controller.connect()
            try:
                accepted = controller.set_datetime_location(
                    parsed_utc,
                    float(lat),
                    float(lon),
                    float(utc_offset_hours),
                )
            finally:
                controller.disconnect()

            if not accepted:
                raise IndiClientError(
                    "CONNECTION_FAILED",
                    "OnStep rejected direct site/time synchronization",
                )
        except Exception as exc:
            sync_error = exc
        finally:
            try:
                self._set_onstep_indi_connection(
                    client,
                    True,
                    serial_port=serial_port,
                )
            except Exception as exc:
                reconnect_error = exc
                self._connected = False
            finally:
                if callable(clear_monitor_cache):
                    try:
                        clear_monitor_cache()
                    except Exception as exc:
                        if reconnect_error is None:
                            reconnect_error = exc
                if monitor_active and callable(start_monitor):
                    try:
                        start_monitor()
                    except Exception as exc:
                        if reconnect_error is None:
                            reconnect_error = exc

        if reconnect_error is not None:
            raise IndiClientError(
                "CONNECTION_FAILED",
                "OnStep direct synchronization finished but the INDI driver "
                f"could not be restored: {reconnect_error}",
            ) from reconnect_error

        if sync_error is not None:
            if isinstance(sync_error, IndiClientError):
                raise sync_error
            raise IndiClientError(
                "CONNECTION_FAILED",
                f"Direct OnStep synchronization failed: {sync_error}",
            ) from sync_error

        self._connected = True
        return {
            "latitude": float(lat),
            "longitude": float(lon),
            "elevation": float(elev),
            "utc": str(utc_iso),
            "utc_offset_hours": float(utc_offset_hours),
            "transport": "onstep_direct",
        }

    def _set_onstep_vector_atomic(self, prop, elements):
        """Legacy atomic vector used only by the standalone set_location API."""
        with IndiTcpSession(
            host=self.config.get("host", "127.0.0.1"),
            port=int(self.config.get("port", 7624)),
            device=self.device_name,
            timeout_s=float(self.config.get("client_timeout", 4.0)),
        ) as session:
            if prop == "GEOGRAPHIC_COORD":
                session.set_number(prop, elements)
            else:
                raise IndiClientError(
                    "PROPERTY_UNSUPPORTED",
                    f"Unsupported atomic OnStep property: {prop}",
                )

    def _verify_onstep_location(self, lat, lon, elev):
        """Require the legacy set_location path to confirm its INDI readback."""
        deadline = time.monotonic() + self.timeout
        last = {}
        first = True
        while first or time.monotonic() < deadline:
            first = False
            last = self._props(["GEOGRAPHIC_COORD.*"]).get(
                "GEOGRAPHIC_COORD",
                {},
            )
            if (
                self._readback_close(last.get("LAT"), lat, 0.02)
                and self._longitude_error(last.get("LONG"), lon) <= 0.02
                and self._readback_close(last.get("ELEV"), elev, 5.0)
            ):
                return
            if time.monotonic() >= deadline:
                break
            time.sleep(self.poll_interval)
        raise IndiClientError(
            "CONNECTION_FAILED",
            "OnStep site synchronization was not confirmed by readback "
            f"(requested LAT={lat}, LONG={lon}, ELEV={elev}; readback={last})",
        )

    @staticmethod
    def _readback_close(actual, expected, tolerance):
        try:
            return abs(float(actual) - float(expected)) <= float(tolerance)
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _parse_utc_readback(value):
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _longitude_error(actual, expected):
        try:
            actual = float(actual) % 360.0
            expected = float(expected) % 360.0
        except (TypeError, ValueError):
            return float("inf")
        delta = abs(actual - expected)
        return min(delta, 360.0 - delta)

    def _verify_site_time(
        self,
        lat,
        lon,
        elev,
        utc_iso,
        utc_offset_hours,
        *,
        driver_label,
    ):
        """Require INDI readback to confirm site, UTC and timezone."""
        expected_utc = self._parse_utc_readback(utc_iso)
        if expected_utc is None:
            raise ValueError(f"invalid UTC synchronization value: {utc_iso!r}")

        deadline = time.monotonic() + self.timeout
        last_location = {}
        last_time = {}
        first = True
        while first or time.monotonic() < deadline:
            first = False
            current = self._props([
                "GEOGRAPHIC_COORD.*",
                "TIME_UTC.*",
            ])
            last_location = current.get("GEOGRAPHIC_COORD", {})
            last_time = current.get("TIME_UTC", {})

            read_utc = self._parse_utc_readback(last_time.get("UTC"))
            utc_error = (
                abs((read_utc - expected_utc).total_seconds())
                if read_utc is not None
                else float("inf")
            )
            if (
                self._readback_close(last_location.get("LAT"), lat, 0.02)
                and self._longitude_error(last_location.get("LONG"), lon) <= 0.02
                and self._readback_close(last_location.get("ELEV"), elev, 5.0)
                and self._readback_close(
                    last_time.get("OFFSET"),
                    utc_offset_hours,
                    0.01,
                )
                and utc_error <= 15.0
            ):
                return

            if time.monotonic() >= deadline:
                break
            time.sleep(self.poll_interval)

        raise IndiClientError(
            "CONNECTION_FAILED",
            f"{driver_label} site/time synchronization was not confirmed by "
            "readback "
            f"(requested LAT={lat}, LONG={lon}, ELEV={elev}, UTC={utc_iso}, "
            f"OFFSET={utc_offset_hours:+.2f}; "
            f"location_readback={last_location}, time_readback={last_time})",
        )

    def _cleanup_runtime_channels(self):
        """Close every runtime channel, including partial connect failures."""
        self._close_control_session()
        stop_monitor = getattr(self.client, "stop_monitor", None)
        if callable(stop_monitor):
            stop_monitor()

    def _open_control_session(self, props=None):
        """Open the persistent write channel for drivers proven compatible.

        LX200 OnStep deliberately keeps the subprocess/indi_setprop path.
        OnStep 1.17 has been observed accepting persistent XML/TCP slew writes
        without forwarding the motion to the physical controller, while the
        same vectors sent by indi_setprop work immediately.
        """
        if not self._runtime_tcp_enabled or self._control_session is not None:
            return
        if props is not None and self._is_onstep_driver(props):
            return
        session = IndiTcpSession(
            host=self.config.get("host", "127.0.0.1"),
            port=int(self.config.get("port", 7624)),
            device=self.device_name,
            timeout_s=float(self.config.get("client_timeout", 4.0)),
        )
        session.__enter__()
        try:
            session.start_reader()
        except Exception:
            session.close()
            raise
        self._control_session = session

    def _close_control_session(self):
        session, self._control_session = self._control_session, None
        if session is not None:
            session.close()

    def _set_props(self, assignments):
        """Write through the persistent TCP channel during normal runtime.

        Injected clients retain the historical set_props contract for unit
        tests and alternate callers. INDI vector type is explicit for the
        standard properties SolarTrigger writes.
        """
        session = self._control_session
        if session is None:
            self.client.set_props(assignments)
            return

        for prop, elements in assignments.items():
            if prop == "DEVICE_PORT":
                session.set_text(prop, elements)
            elif prop == "GEOGRAPHIC_COORD":
                session.set_number(prop, elements)
            elif prop == "TIME_UTC":
                session.set_text(prop, elements)
            else:
                session.set_switch(prop, elements)

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
            self._set_props(assignments)
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
    def _onstep_operational_props(cls, props):
        connection = props.get("CONNECTION", {})
        if not cls._switch_on(connection, "CONNECT"):
            return False
        return all(
            bool(props.get(name))
            for name in (
                "TELESCOPE_SLEW_RATE",
                "TELESCOPE_MOTION_NS",
                "TELESCOPE_MOTION_WE",
            )
        )

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

    def _is_onstep_driver(self, props):
        """Identify the OnStep INDI driver without relying on the device label alone."""
        info = props.get("DRIVER_INFO", {}) if isinstance(props, dict) else {}
        identity = " ".join(
            value
            for value in (
                self._text(info, "DRIVER_EXEC"),
                self._text(info, "DRIVER_NAME"),
                self.device_name,
            )
            if value
        ).casefold()
        return "onstep" in identity

    def _is_eqmod_driver(self, props):
        """True only for EQMod, the sole owner of the PARK-as-Home fallback."""
        info = props.get("DRIVER_INFO", {}) if isinstance(props, dict) else {}
        identity = " ".join(
            value
            for value in (
                self._text(info, "DRIVER_EXEC"),
                self._text(info, "DRIVER_NAME"),
                self.device_name,
            )
            if value
        ).casefold()
        return "eqmod" in identity

    @staticmethod
    def _error_code(exc):
        return getattr(exc, "code", "CONNECTION_FAILED")

    @staticmethod
    def _raise_mapped(default_code, message, exc):
        code = getattr(exc, "code", default_code)
        raise IndiClientError(code, f"{message}: {exc}") from exc


__all__ = ["IndiMount"]
