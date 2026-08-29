#!/usr/bin/env python3
"""Camera service used by the trigger process.

The trigger expresses capture intent only. Brand/model specific PTP details live
in plugins.camera.*.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, replace
from datetime import datetime
from statistics import median
from typing import Any, List, Optional

from plugins.camera import load_plugin, get_camera_model
from plugins.camera.base import CaptureResult


@dataclass
class CaptureIntent:
    """Brand-agnostic description of a requested capture sequence."""

    shutter_min: Optional[str]
    shutter_max: Optional[str]
    step_ev: Optional[float]
    speeds: Optional[List[str]]
    phase: str
    target_time: datetime
    deadline: Optional[datetime]
    overflow_policy: Optional[str]
    origin: Optional[str] = None
    request_id: Optional[str] = None


@dataclass
class PreparedCapture:
    """Opaque prepared capture and its brand-independent estimates."""

    token: Any
    estimated_total_s: Optional[float]
    exposures_s: Optional[List[float]]
    planned_count: Optional[int]
    plugin_name: str
    materialized: Optional[list] = None


def _parse_speed(value):
    s = str(value).strip()
    if "/" in s:
        a, b = s.split("/", 1)
        return float(a) / float(b)
    return float(s)


def _normalized_speed_plan(speeds, tolerance_il=0.12):
    """Return (fastest, slowest, step_il, regular).

    Duplicate values are removed and ordering is normalized from fastest to
    slowest. If the supplied list is not approximately regular in EV, callers
    should execute singles to preserve the exact requested values.
    """
    if not speeds:
        raise ValueError("liste de vitesses vide")
    unique = {}
    for value in speeds:
        unique[str(value)] = _parse_speed(value)
    ordered = sorted(unique.items(), key=lambda kv: kv[1])
    if len(ordered) == 1:
        return ordered[0][0], ordered[0][0], 1.0, True
    evs = [math.log2(sec) for _, sec in ordered]
    diffs = [evs[i + 1] - evs[i] for i in range(len(evs) - 1)]
    step = median(diffs)
    regular = all(abs(d - step) <= tolerance_il for d in diffs)
    return ordered[0][0], ordered[-1][0], float(step), regular


class CameraService:
    def __init__(
        self,
        log_fn=print,
        camera_factory=None,
        plugin_loader=load_plugin,
        clock=None,
        camera_identity=None,
    ):
        self.log = log_fn
        self.camera_factory = camera_factory
        self.plugin_loader = plugin_loader
        self.clock = clock
        self.camera_identity = (
            dict(camera_identity)
            if isinstance(camera_identity, dict)
            else None
        )
        self.camera = None
        self.plugin = None
        self.model = ""
        self._last_phase_settings = {}

    @staticmethod
    def _config_value(config, *names):
        for name in names:
            try:
                value = config.get_child_by_name(name).get_value()
            except Exception:
                continue
            text = str(value or "").strip()
            if text:
                return text
        return None

    def _open_camera_by_serial(self, gp, serial):
        """Open exactly the gphoto2 camera whose stable serial is requested."""
        expected = str(serial).strip()
        if not expected:
            raise RuntimeError("camera serial is empty")

        try:
            detected = list(gp.Camera.autodetect())
        except Exception as exc:
            raise RuntimeError(
                f"unable to enumerate cameras for serial {expected}"
            ) from exc

        port_list = gp.PortInfoList()
        port_list.load()

        for _model, port in detected:
            camera = gp.Camera()
            keep = False
            try:
                camera.set_port_info(
                    port_list[port_list.lookup_path(port)]
                )
                camera.init()
                config = camera.get_config()
                actual = self._config_value(
                    config,
                    "serialnumber",
                    "serial",
                    "serial_number",
                )
                if actual == expected:
                    keep = True
                    return camera
            except Exception:
                pass
            finally:
                if not keep:
                    try:
                        camera.exit()
                    except Exception:
                        pass

        raise RuntimeError(
            f"configured camera serial {expected} was not found"
        )

    @property
    def connected(self):
        return self.camera is not None and self.plugin is not None

    def connect(self):
        already_initialized = False

        if self.camera is None:
            if self.camera_factory is None:
                import gphoto2 as gp

                serial = (
                    self.camera_identity.get("serial")
                    if isinstance(self.camera_identity, dict)
                    else None
                )
                if serial:
                    self.camera = self._open_camera_by_serial(gp, serial)
                    already_initialized = True
                else:
                    # Backward-compatible standalone/unbound service.
                    self.camera = gp.Camera()
            else:
                self.camera = self.camera_factory()

        if not already_initialized:
            self.camera.init()

        self.model = get_camera_model(self.camera)
        self.plugin = self.plugin_loader(self.camera, self.log)
        if self.plugin is None:
            try:
                self.camera.exit()
            finally:
                self.camera = None
            raise RuntimeError(f"Aucun plugin caméra compatible pour '{self.model}'")
        self.log(f"Caméra : {self.model} — plugin {self.plugin.name}")
        return self.plugin

    def reconnect(self):
        if self.camera is None:
            return self.connect()
        self.camera.init()
        if self.plugin is None:
            self.plugin = self.plugin_loader(self.camera, self.log)
        if self.plugin is None:
            raise RuntimeError(f"Plugin caméra perdu pour '{self.model}'")
        return self.plugin

    def release(self):
        if self.camera is not None:
            try:
                self.camera.exit()
            except Exception:
                pass

    def close(self):
        self.release()
        self.camera = None
        self.plugin = None
        self._last_phase_settings = {}

    def init_settings(self, aperture=None, iso=None, image_format="RAW",
                      white_balance="Daylight"):
        if not self.plugin:
            raise RuntimeError("caméra non connectée")
        return self.plugin.init_settings(aperture=aperture, iso=iso,
                                         image_format=image_format,
                                         white_balance=white_balance)

    def set_exposure_settings(self, aperture=None, iso=None):
        if not self.plugin:
            raise RuntimeError("caméra non connectée")
        return self.plugin.set_exposure_settings(aperture=aperture, iso=iso)

    def apply_phase_settings(self, aperture=None, iso=None):
        if not self.plugin:
            raise RuntimeError("caméra non connectée")
        settings = {}
        if (aperture is not None
                and self._last_phase_settings.get("aperture") != aperture):
            settings["aperture"] = aperture
        if iso is not None and self._last_phase_settings.get("iso") != iso:
            settings["iso"] = iso
        if not settings:
            return None
        result = self.plugin.set_exposure_settings(**settings)
        self._last_phase_settings.update(settings)
        return result

    def prepare_capture(self, intent):
        if not self.plugin:
            raise RuntimeError("caméra non connectée")

        if intent.speeds:
            speeds = [str(speed) for speed in intent.speeds]
            fastest, slowest, step_ev, regular = _normalized_speed_plan(speeds)
            if regular:
                intent = replace(
                    intent,
                    shutter_min=slowest,
                    shutter_max=fastest,
                    step_ev=step_ev,
                    speeds=None,
                )
            else:
                intent = replace(intent, speeds=speeds)
        else:
            bounds = [
                str(speed)
                for speed in (intent.shutter_min, intent.shutter_max)
                if speed is not None
            ]
            if not bounds:
                raise ValueError("capture intent contains no shutter speeds")
            fastest, slowest, _, _ = _normalized_speed_plan(bounds)
            intent = replace(
                intent,
                shutter_min=slowest,
                shutter_max=fastest,
                step_ev=(
                    float(intent.step_ev)
                    if intent.step_ev is not None
                    else 1.0
                ),
                speeds=None,
            )

        return self.plugin.prepare_capture(intent)

    def trigger_prepared(
        self, prepared, deadline=None, *, monotonic_deadline=None
    ):
        if not self.plugin:
            raise RuntimeError("caméra non connectée")

        plugin_deadline = monotonic_deadline
        if monotonic_deadline is None and deadline is not None:
            if self.clock is None:
                raise RuntimeError("horloge d'exécution non configurée")
            plugin_deadline = (
                time.monotonic()
                + max(0.0, self.clock.remaining(deadline))
            )
        return self.plugin.trigger_prepared(prepared, deadline=plugin_deadline)

    def get_battery_level(self):
        if not self.plugin:
            return None
        return self.plugin.get_battery_level()

    def get_vibration_capabilities(self) -> dict | None:
        if not self.connected:
            return None
        return dict(getattr(self.plugin, "get_vibration_capabilities", lambda: {})())

    def read_info(self):
        if not self.connected:
            self.connect()

        try:
            config = self.camera.get_config()
        except Exception:
            config = None

        def read_config(*names):
            if config is None:
                return None
            for name in names:
                try:
                    return config.get_child_by_name(name).get_value()
                except Exception:
                    pass
            return None

        return {
            "plugin": getattr(self.plugin, "name", None),
            "model": self.model or get_camera_model(self.camera),
            "battery": self.plugin.get_battery_level(),
            "iso": read_config("iso"),
            "aperture": read_config("f-number"),
            "shutterspeed": read_config("shutterspeed", "shutterspeed2"),
            "mode": read_config("expprogram", "capturemode"),
            "storage": read_config("capturetarget"),
        }

    def sync_datetime(self, ref):
        if not self.connected:
            self.connect()

        result = dict(self.plugin.sync_datetime(ref))
        result.setdefault("status", "partial")
        result["datetime_synced"] = result.get("datetime_synced") is True
        result["timezone_synced"] = result.get("timezone_synced") is True
        result.setdefault("datetime_applied", None)
        result.setdefault(
            "timezone_name", getattr(ref, "timezone_name", None) or None
        )
        result.setdefault(
            "utc_offset_minutes",
            getattr(ref, "utc_offset_minutes", None) or None,
        )
        result.setdefault("message", "")
        if not result.get("plugin"):
            result["plugin"] = getattr(self.plugin, "name", None)
        if not result.get("model"):
            result["model"] = self.model or None
        return result

    def shoot_speed_list(
        self,
        speeds,
        photo_num_start=0,
        deadline=None,
        slowest_override_seconds=None,
        *,
        monotonic_deadline=None,
    ):
        if not self.plugin:
            raise RuntimeError("caméra non connectée")

        plugin_deadline = monotonic_deadline if monotonic_deadline is not None else deadline

        if monotonic_deadline is None and deadline is not None and self.clock is not None:
            # Convert absolute UTC phase deadline to a monotonic deadline once.
            # Camera plugins are then immune to system/NTP/GPS wall-clock jumps.
            plugin_deadline = (
                time.monotonic()
                + max(0.0, self.clock.remaining(deadline))
            )

        speeds = [str(s) for s in speeds]

        fastest, slowest, step_il, regular = _normalized_speed_plan(speeds)

        if regular:
            if slowest_override_seconds is not None:
                try:
                    override = float(slowest_override_seconds)
                except (TypeError, ValueError) as exc:
                    raise ValueError(
                        "slowest_override_seconds doit être numérique"
                    ) from exc

                if override <= 0.0:
                    raise ValueError(
                        "slowest_override_seconds doit être > 0"
                    )

                current_slowest_seconds = _parse_speed(slowest)

                if override < current_slowest_seconds:
                    raise ValueError(
                        "slowest_override_seconds ne peut pas raccourcir "
                        "la borne lente existante"
                    )

                slowest = str(override)

            return self.plugin.shoot_speeds(
                fastest,
                slowest,
                step_il,
                photo_num_start=photo_num_start,
                deadline=plugin_deadline,
            )

        if slowest_override_seconds is not None:
            raise ValueError(
                "slowest_override_seconds interdit pour une liste irrégulière"
            )

        # Preserve an irregular explicit list exactly rather than inventing EVs.
        total = 0

        for speed in speeds:
            res = self.plugin.shoot_single(
                speed,
                photo_num=photo_num_start + total,
                deadline=plugin_deadline,
            )
            total += res.frames

        return CaptureResult(
            frames=total,
            planned=len(speeds),
            detail="liste explicite",
        )


__all__ = [
    "CameraService",
    "CaptureIntent",
    "PreparedCapture",
    "_normalized_speed_plan",
]
