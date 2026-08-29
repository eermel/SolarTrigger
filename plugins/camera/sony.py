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

from .base import (
    CameraPlugin,
    CaptureResult,
    _normalized_speed_plan,
    seconds_until_deadline,
)
from . import sony_planner as planner

READONLY_RETRY_S = 6.0     # duree max de retry sur 'read only'
SETTLE_MAX_S = 2.0         # duree max d'attente de repos apres une rafale


class SonyPlugin(CameraPlugin):
    name = "sony"
    specificity = 20      # marque bien identifiee

    @staticmethod
    def matches(model_string):
        m = (model_string or "").lower()
        return "ilce-7m5" in m

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
    @staticmethod
    def _sequence_exposures(seq):
        exposures = []
        for item in seq:
            if isinstance(item, planner.SinglePhoto):
                exposures.append(planner.parse_speed(item.speed))
            else:
                exposures.extend(planner.parse_speed(view)
                                 for view in item.views)
        return exposures

    @staticmethod
    def _planned_count(seq):
        return sum(1 if isinstance(item, planner.SinglePhoto) else item.nimg
                   for item in seq)

    def prepare_capture(self, intent):
        """Build the Sony shutter sequence and its estimates without firing."""
        from services.camera_service import PreparedCapture

        if intent.speeds:
            speeds = [str(speed) for speed in intent.speeds]
            fastest, slowest, step_il, regular = _normalized_speed_plan(speeds)
            if regular:
                step, _, seq = planner.plan(fastest, slowest, step_il)
                description = f"{fastest}->{slowest} @ {step} IL"
            else:
                seq = [planner.SinglePhoto(speed) for speed in speeds]
                description = "explicit speed list"
        else:
            fastest = intent.shutter_max or intent.shutter_min
            slowest = intent.shutter_min or intent.shutter_max
            if fastest is None:
                raise ValueError("capture intent contains no shutter speeds")
            step_il = float(intent.step_ev) if intent.step_ev is not None else 1.0
            step, _, seq = planner.plan(str(fastest), str(slowest), step_il)
            description = f"{fastest}->{slowest} @ {step} IL"

        seq = tuple(seq)
        return PreparedCapture(
            token=("sony_sequence", seq, intent.deadline, description),
            estimated_total_s=sum(planner.estimate_duration(item)
                                  for item in seq),
            exposures_s=self._sequence_exposures(seq),
            planned_count=self._planned_count(seq),
            plugin_name=self.name,
        )

    def trigger_prepared(self, prepared, deadline=None):
        mode, seq, prepared_deadline, description = prepared.token
        if mode != "sony_sequence":
            raise ValueError(f"unsupported prepared capture mode: {mode!r}")
        effective_deadline = (deadline if deadline is not None
                              else prepared_deadline)
        return self._execute_sequence(seq, effective_deadline, description)

    def _execute_sequence(self, seq, deadline, description):
        planned = sum(1 if isinstance(x, planner.SinglePhoto) else x.nimg
                      for x in seq)
        self.log(f"   [sony] plan {description} : "
                 f"{len(seq)} sequence(s), {planned} vues")

        total = 0
        adapted = False
        for item in seq:
            if deadline is not None:
                remaining = seconds_until_deadline(deadline)
                if (remaining < planner.estimate_duration(item)
                        + planner.SAFETY_MARGIN_S):
                    if isinstance(item, planner.SinglePhoto):
                        self.log("   [sony] single refuse pour deadline")
                        break

                    self.log(f"   [sony] bracket {item.nimg} refuse "
                             "pour deadline")
                    selected = None
                    for nimg in (7, 5, 3):
                        if nimg >= item.nimg:
                            continue
                        candidate = planner.make_fast_subset(item, nimg)
                        remaining = seconds_until_deadline(deadline)
                        if (remaining >= planner.estimate_duration(candidate)
                                + planner.SAFETY_MARGIN_S):
                            selected = candidate
                            break

                    if selected is not None:
                        self.log("   [sony] adaptation deadline : bracket "
                                 f"rapide {selected.nimg} vues selectionne")
                        got = self._fire_bracket(selected, deadline=deadline)
                        self.log(f"   [sony] {selected.mode_string} centre "
                                 f"{selected.centre} : {got}/{selected.nimg}")
                        total += got
                        adapted = True
                        break

                    single = planner.SinglePhoto(item.views[0])
                    remaining = seconds_until_deadline(deadline)
                    if (remaining >= planner.estimate_duration(single)
                            + planner.SAFETY_MARGIN_S):
                        self.log("   [sony] adaptation deadline : single "
                                 f"rapide {single.speed} selectionne")
                        got = self._fire_single(single.speed,
                                                deadline=deadline)
                        self.log(f"   [sony] PHOTO {single.speed} : {got}/1")
                        total += got
                        adapted = True
                    else:
                        self.log("   [sony] adaptation deadline : aucune "
                                 "sequence admissible")
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
                             detail=f"{len(seq)} seq"
                                    f"{' adapt' if adapted else ''}")

    def shoot_speeds(self, v_max, v_min, step_il, photo_num_start=0,
                     deadline=None):
        step, _, seq = planner.plan(v_max, v_min, step_il)
        description = f"{v_max}->{v_min} @ {step} IL"
        return self._execute_sequence(seq, deadline, description)
