#!/usr/bin/env python3
"""
camera_plugins/sony.py
Version : 1.0.00

Plugin Sony (valide sur A7V / ILCE-7M5, libgphoto2 >= 2.5.34).

Strategie : bracket(s) continu(s) INTERNE(s). Une plage de vitesses est
planifiee par sony_planner.plan() puis executee bracket par bracket.

Regles de sequencement DUREMENT validees (voir sony_planner pour le decoupage) :
  * capturemode = 'Continuous Bracket X EV N Img.' ; la vitesse centrale se
    regle en Single Shot AVANT de basculer en bracket.
  * Regler la vitesse quand le boitier est occupe -> 'read only'. On RE-ESSAIE
    l'ecriture tant qu'elle renvoie read-only (la relecture PTP n'est pas
    fiable, on ne s'y fie pas). -> set_speed_blocking()
  * Le declenchement de la rafale se fait par MAINTIEN d'obturateur : bulb=1 ...
    bulb=0. Un simple trigger_capture ne sort qu'une vue.
  * Fin de rafale : on COMPTE les FILE_ADDED attendus. CAPTURE_COMPLETE n'est
    PAS fiable (il arrive en milieu de sequence). On ne relache qu'apres avoir
    compte les N vues, ou sur un vrai silence > pose_lente + marge.
  * settle_idle() apres chaque rafale : draine les evenements residuels pour
    que le boitier soit au repos avant le prochain reglage de vitesse.
"""

import time

import gphoto2 as gp

from .base import CameraPlugin, CaptureResult, seconds_until_deadline
from . import sony_planner as planner

READONLY_RETRY_S = 6.0     # duree max de retry sur 'read only'
SETTLE_MAX_S = 2.0         # duree max d'attente de repos apres une rafale


class SonyPlugin(CameraPlugin):
    name = "sony"
    specificity = 20      # marque bien identifiee

    @staticmethod
    def matches(model_string):
        m = (model_string or "").lower()
        return "sony" in m or "ilce" in m

    # ------------------------------------------------------------------ #
    # Reglage bas niveau
    # ------------------------------------------------------------------ #
    def _set(self, name, value):
        """Ecrit une config. Retourne (ok, is_readonly, err)."""
        try:
            cfg = self.camera.get_config()
            w = cfg.get_child_by_name(name)
        except gp.GPhoto2Error:
            return False, False, f"'{name}' absent"
        try:
            wtype = w.get_type()
            v = int(value) if wtype == gp.GP_WIDGET_TOGGLE else str(value)
            w.set_value(v)
            self.camera.set_config(cfg)
            return True, False, ""
        except gp.GPhoto2Error as e:
            msg = str(e).lower()
            ro = "read only" in msg or "read-only" in msg
            return False, ro, str(e)

    def _set_first_available(self, name, candidates):
        """Essaie plusieurs valeurs pour une config (ex. format RAW)."""
        for val in candidates:
            ok, _, _ = self._set(name, val)
            if ok:
                return True, val
        return False, None

    def set_speed_blocking(self, speed, deadline=None):
        """Regle shutterspeed en re-essayant tant que 'read only' (boitier
        occupe). Ne se fie PAS a la relecture. True si applique."""
        t0 = time.monotonic()
        while True:
            ok, ro, err = self._set("shutterspeed", speed)
            if ok:
                return True
            if not ro:
                self.log(f"   [sony] set vitesse {speed} : erreur {err}")
                return False
            if (time.monotonic() - t0) > READONLY_RETRY_S:
                self.log(f"   [sony] set vitesse {speed} : read-only persistant")
                return False
            if deadline is not None and seconds_until_deadline(deadline) <= 0:
                return False
            time.sleep(0.05)

    # ------------------------------------------------------------------ #
    # Draine / repos
    # ------------------------------------------------------------------ #
    def _drain_frames(self, expected, longest_exp_s):
        """Compte les FILE_ADDED jusqu'a `expected`, en ignorant
        CAPTURE_COMPLETE. Abandonne sur un silence > pose_lente + marge."""
        frames = 0
        t0 = time.monotonic()
        last = t0
        stall = longest_exp_s + 4.0
        timeout = longest_exp_s * expected + 20.0
        while (time.monotonic() - t0) < timeout:
            try:
                etype, edata = self.camera.wait_for_event(200)
            except gp.GPhoto2Error:
                break
            now = time.monotonic()
            if etype == gp.GP_EVENT_FILE_ADDED:
                frames += 1
                last = now
                if frames >= expected:
                    break
            if frames and (now - last) > stall:
                break
        return frames

    def _settle_idle(self, max_s=SETTLE_MAX_S):
        """Attend le repos du boitier (plus d'evenements) avant reconfig."""
        t0 = time.monotonic()
        last = t0
        while (time.monotonic() - t0) < max_s:
            try:
                etype, _ = self.camera.wait_for_event(100)
            except gp.GPhoto2Error:
                break
            now = time.monotonic()
            if etype == gp.GP_EVENT_TIMEOUT:
                if (now - last) > 0.3:
                    break
            else:
                last = now

    # ------------------------------------------------------------------ #
    # Init
    # ------------------------------------------------------------------ #
    def init_settings(self, aperture=None, iso=None, image_format="RAW",
                      white_balance="Daylight"):
        self.log("   [sony] init reglages")
        self._set("expprogram", "M")            # sinon shutterspeed read-only
        self._set("focusmode", "Manual")        # pas d'AF pendant la totalite
        self._set("capturetarget", "card")
        self._set("capturemode", "Single Shot")  # etat de depart propre
        if iso is not None:
            self._set("iso", str(iso))
        if aperture is not None:
            self._set("f-number", aperture)
        if white_balance:
            self._set("whitebalance", white_balance)
        if image_format:
            self._set_first_available(
                "imagequality", [image_format, "RAW", "NEF (Raw)", "Raw"])

    def set_exposure_settings(self, aperture=None, iso=None):
        # Speed can be read-only unless the body is in a neutral single-shot state.
        self._set("capturemode", "Single Shot")
        if iso is not None:
            self._set("iso", str(iso))
        if aperture is not None:
            self._set("f-number", aperture)

    # ------------------------------------------------------------------ #
    # Une rafale bracket
    # ------------------------------------------------------------------ #
    def _fire_bracket(self, brk, deadline=None):
        """Execute un Bracket planner. Retourne le nb de vues capturees."""
        # 1) etat propre + vitesse centrale (avec retry read-only)
        self._set("capturemode", "Single Shot")
        if not self.set_speed_blocking(brk.centre, deadline):
            return 0
        # 2) basculer en mode bracket
        ok, _, err = self._set("capturemode", brk.mode_string)
        if not ok:
            self.log(f"   [sony] set mode {brk.mode_string} : erreur {err}")
            return 0
        # 3) maintien obturateur -> rafale interne
        longest = max(planner.parse_speed(v) for v in brk.views)
        self._set("bulb", 1)
        frames = self._drain_frames(brk.nimg, longest)
        self._set("bulb", 0)
        self._settle_idle()
        return frames

    def _fire_single(self, speed, deadline=None):
        """Une seule vue a `speed` (cas v_max == v_min)."""
        self._set("capturemode", "Single Shot")
        if not self.set_speed_blocking(speed, deadline):
            return 0
        try:
            self.camera.trigger_capture()
        except gp.GPhoto2Error as e:
            self.log(f"   [sony] trigger {speed} : {e}")
            return 0
        n = self._drain_frames(1, planner.parse_speed(speed))
        self._settle_idle()
        return n

    # ------------------------------------------------------------------ #
    # API moteur
    # ------------------------------------------------------------------ #
    def shoot_speeds(self, v_max, v_min, step_il, photo_num_start=0,
                     deadline=None):
        step, n_frames, seq = planner.plan(v_max, v_min, step_il)
        planned = sum(1 if isinstance(x, planner.SinglePhoto) else x.nimg
                      for x in seq)
        self.log(f"   [sony] plan {v_max}->{v_min} @ {step} IL : "
                 f"{len(seq)} sequence(s), {planned} vues")

        # estimation de duree par element pour la barriere de deadline
        import datetime

        def est_seconds(item):
            if isinstance(item, planner.SinglePhoto):
                return planner.parse_speed(item.speed) + 1.0
            return sum(planner.parse_speed(v) for v in item.views) + 2.0

        total = 0
        for item in seq:
            if deadline is not None:
                remaining = seconds_until_deadline(deadline)
                if remaining < est_seconds(item):
                    self.log("   [sony] deadline : sequence tronquee (ok)")
                    break
            if isinstance(item, planner.SinglePhoto):
                got = self._fire_single(item.speed, deadline=deadline)
                self.log(f"   [sony] PHOTO {item.speed} : {got}/1")
            else:
                got = self._fire_bracket(item, deadline=deadline)
                self.log(f"   [sony] {item.mode_string} centre "
                         f"{item.centre} : {got}/{item.nimg}")
            total += got

        return CaptureResult(frames=total, planned=planned,
                             detail=f"{len(seq)} seq")
