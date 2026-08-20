#!/usr/bin/env python3
"""
camera_plugins/nikon.py
Version : 2.0.00

Plugins Nikon, avec detection a DEUX NIVEAUX (marque + modele) :

  NikonBasePlugin   -- logique commune photo-par-photo (non selectionnable seule)
  NikonDSLRPlugin   -- reflex (D850, D780, D6...) + FALLBACK pour tout Nikon
                       inconnu (avec avertissement). Comportement valide sur D850.
  NikonZPlugin      -- hybrides Z (Z8, Z9, Z6, Z7...) : herite du reflex et
                       ajoute viewfinder=1 (liveview) requis par certains opcodes
                       Z-series. Base TESTABLE, a affiner avec les vraies erreurs.

Le registre choisit le plugin de plus haute `specificity` dont matches() repond
True. Z (specificity 20) est teste avant DSLR (10). Ainsi un Z9 prend
NikonZPlugin, un D850 prend NikonDSLRPlugin, et un Nikon inconnu retombe sur
NikonDSLRPlugin (fallback reflex).

Strategie de capture : PHOTO PAR PHOTO -- une vue par vitesse.
"""

import time
import math

import gphoto2 as gp

from .base import CameraPlugin, CaptureResult, seconds_until_deadline

NIKON_SPEEDS = [
    ("30", 30), ("25", 25), ("20", 20), ("15", 15), ("13", 13), ("10", 10),
    ("8", 8), ("6", 6), ("5", 5), ("4", 4), ("3", 3), ("2.5", 2.5), ("2", 2),
    ("1.6", 1.6), ("1.3", 1.3), ("1", 1), ("0.8", 0.8), ("0.6", 0.6),
    ("0.5", 0.5), ("0.4", 0.4), ("1/3", 1/3), ("1/4", 1/4), ("1/5", 1/5),
    ("1/6", 1/6), ("1/8", 1/8), ("1/10", 1/10), ("1/13", 1/13), ("1/15", 1/15),
    ("1/20", 1/20), ("1/25", 1/25), ("1/30", 1/30), ("1/40", 1/40),
    ("1/50", 1/50), ("1/60", 1/60), ("1/80", 1/80), ("1/100", 1/100),
    ("1/125", 1/125), ("1/160", 1/160), ("1/200", 1/200), ("1/250", 1/250),
    ("1/320", 1/320), ("1/400", 1/400), ("1/500", 1/500), ("1/640", 1/640),
    ("1/800", 1/800), ("1/1000", 1/1000), ("1/1250", 1/1250),
    ("1/1600", 1/1600), ("1/2000", 1/2000), ("1/2500", 1/2500),
    ("1/3200", 1/3200), ("1/4000", 1/4000), ("1/5000", 1/5000),
    ("1/6400", 1/6400), ("1/8000", 1/8000),
]

Z_MODEL_PATTERNS = ("z8", "z9", "z6", "z7", "z5", "z50", "zf", "zfc", "z30")


def _ev(sec):
    return math.log2(sec)


def _parse(s):
    s = str(s).strip()
    if "/" in s:
        a, b = s.split("/")
        return float(a) / float(b)
    return float(s)


def _norm(model):
    return (model or "").lower().replace(" ", "").replace("-", "")


def _speeds_between(v_max, v_min, step_il):
    vmax_s, vmin_s = _parse(v_max), _parse(v_min)
    if vmax_s > vmin_s:
        vmax_s, vmin_s = vmin_s, vmax_s
    ev_fast, ev_slow = _ev(vmax_s), _ev(vmin_s)
    n = round((ev_slow - ev_fast) / step_il) + 1
    if n < 1:
        n = 1
    out, prev = [], None
    for k in range(n):
        target = ev_fast + k * step_il
        b = min(NIKON_SPEEDS, key=lambda x: abs(_ev(x[1]) - target))
        if b[0] != prev:
            out.append(b[0])
        prev = b[0]
    return out


class NikonBasePlugin(CameraPlugin):
    name = "nikon-base"
    specificity = 0

    @staticmethod
    def matches(model_string):
        return False

    def _set(self, name, value):
        try:
            cfg = self.camera.get_config()
            w = cfg.get_child_by_name(name)
            w.set_value(str(value))
            self.camera.set_config(cfg)
            return True
        except gp.GPhoto2Error:
            return False

    def _set_speed(self, speed):
        for name in ("shutterspeed2", "shutterspeed"):
            if self._set(name, speed):
                return True
        return False

    def _fire(self, speed):
        try:
            self.camera.trigger_capture()
            return True
        except gp.GPhoto2Error as e:
            self.log(f"   [{self.name}] declenchement {speed} : {e}")
            return False

    def init_settings(self, aperture=None, iso=None, image_format="NEF (Raw)",
                      white_balance="Daylight"):
        self.log(f"   [{self.name}] init reglages")
        self._set("expprogram", "M")
        if iso is not None:
            self._set("iso", str(iso))
        self._set("capturetarget", "Memory card")
        if white_balance:
            self._set("whitebalance", white_balance)
        if aperture is not None:
            self._set("f-number", aperture)
        for val in (image_format, "NEF (Raw)", "RAW"):
            if self._set("imagequality", val):
                break

    def set_exposure_settings(self, aperture=None, iso=None):
        if iso is not None:
            self._set("iso", str(iso))
        if aperture is not None:
            self._set("f-number", aperture)

    def shoot_speeds(self, v_max, v_min, step_il, photo_num_start=0,
                     deadline=None):
        import datetime
        speeds = _speeds_between(v_max, v_min, step_il)
        self.log(f"   [{self.name}] {v_max}->{v_min} @ {step_il} IL : "
                 f"{len(speeds)} vues")
        count = 0
        for sp in speeds:
            if deadline is not None:
                remaining = seconds_until_deadline(deadline)
                if remaining < (_parse(sp) + 1.5):
                    self.log(f"   [{self.name}] deadline : tronque (ok)")
                    break
            if self._set_speed(sp) and self._fire(sp):
                count += 1
            time.sleep(0.05)
        return CaptureResult(frames=count, planned=len(speeds),
                             detail="photo-par-photo")


class NikonDSLRPlugin(NikonBasePlugin):
    name = "nikon-dslr"
    specificity = 10

    @staticmethod
    def matches(model_string):
        m = _norm(model_string)
        if "nikon" not in m:
            return False
        if any(p in m for p in Z_MODEL_PATTERNS):
            return False       # laisser NikonZPlugin (plus specifique)
        return True            # tout autre Nikon -> reflex (fallback inclus)

    def init_settings(self, *args, **kwargs):
        model = ""
        try:
            model = self.camera.get_abilities().model
        except Exception:
            pass
        known = ("d850", "d780", "d6", "d5", "d500", "d810", "d750")
        if not any(k in _norm(model) for k in known):
            self.log(f"   [nikon-dslr] AVERTISSEMENT : modele '{model}' non "
                     f"explicitement valide ; fallback reflex (photo-par-photo).")
        super().init_settings(*args, **kwargs)


class NikonZPlugin(NikonBasePlugin):
    name = "nikon-z"
    specificity = 20

    @staticmethod
    def matches(model_string):
        m = _norm(model_string)
        return "nikon" in m and any(p in m for p in Z_MODEL_PATTERNS)

    def init_settings(self, *args, **kwargs):
        super().init_settings(*args, **kwargs)
        # Z-series : liveview requis pour certains opcodes. Base testable.
        if self._set("viewfinder", "1"):
            self.log("   [nikon-z] viewfinder=1 (liveview actif)")
        else:
            self.log("   [nikon-z] viewfinder non reglable (a diagnostiquer)")
