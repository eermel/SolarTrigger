#!/usr/bin/env python3
"""Camera service used by the trigger process.

The trigger expresses capture intent only. Brand/model specific PTP details live
in plugins.camera.*.
"""
from __future__ import annotations

import math
import time
from statistics import median

from plugins.camera import load_plugin, get_camera_model
from plugins.camera.base import CaptureResult


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
    def __init__(self, log_fn=print, camera_factory=None, plugin_loader=load_plugin, clock=None):
        self.log = log_fn
        self.camera_factory = camera_factory
        self.plugin_loader = plugin_loader
        self.clock = clock
        self.camera = None
        self.plugin = None
        self.model = ""

    @property
    def connected(self):
        return self.camera is not None and self.plugin is not None

    def connect(self):
        if self.camera is None:
            if self.camera_factory is None:
                import gphoto2 as gp
                self.camera = gp.Camera()
            else:
                self.camera = self.camera_factory()
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

    def get_battery_level(self):
        if not self.plugin:
            return None
        return self.plugin.get_battery_level()

    def shoot_speed_list(self, speeds, photo_num_start=0, deadline=None):
        if not self.plugin:
            raise RuntimeError("caméra non connectée")
        plugin_deadline = deadline
        if deadline is not None and self.clock is not None:
            # Convert absolute UTC phase deadline to a monotonic deadline once.
            # Camera plugins are then immune to system/NTP/GPS wall-clock jumps.
            plugin_deadline = time.monotonic() + max(0.0, self.clock.remaining(deadline))
        speeds = [str(s) for s in speeds]
        fastest, slowest, step_il, regular = _normalized_speed_plan(speeds)
        if regular:
            return self.plugin.shoot_speeds(fastest, slowest, step_il,
                                             photo_num_start=photo_num_start,
                                             deadline=plugin_deadline)

        # Preserve an irregular explicit list exactly rather than inventing EVs.
        total = 0
        for speed in speeds:
            res = self.plugin.shoot_single(speed,
                                           photo_num=photo_num_start + total,
                                           deadline=plugin_deadline)
            total += res.frames
        return CaptureResult(frames=total, planned=len(speeds),
                             detail="liste explicite")


__all__ = ["CameraService", "_normalized_speed_plan"]
