#!/usr/bin/env python3
"""
focuser_plugins/zwo_plugin.py
Version : 1.2.00

Plugin focuseur ZWO EAF. Enveloppe le module bas niveau zwo_eaf.py (valide sur
le materiel) derriere le contrat FocuserPlugin. zwo_eaf.py reste intouche.

Le maintien continu (facon ASIAIR) est simule par un THREAD qui envoie des
move_relative repetes tant que le bouton est "enfonce", avec deux garde-fous :
  - butees : arret si on atteint 0 ou max_step ;
  - timeout de securite (defaut 5 s) : si l'evenement "relache" se perd cote
    IHM, le maintien s'arrete seul.
"""

import threading
import time

from .base import (FocuserPlugin, DIR_IN, DIR_OUT, STEP_COARSE, STEP_FINE)

# zwo_eaf.py est desormais dans ce meme package (plugins/focuser/).
from .zwo_eaf import ZwoEaf, EafError

# defauts (regles sur le vrai materiel ; surchargeables via config)
DEFAULT_COARSE = 150       # bon compromis fluidite/rapidite (trouver la zone)
DEFAULT_FINE = 20          # petit pas pour lunette rapide (WO Z73) -- affinage
HOLD_TIMEOUT_S = 5.0       # securite : arret auto du maintien
HOLD_INTERVAL_S = 0.02     # cadence des pas en maintien (quasi continu)


class ZwoFocuser(FocuserPlugin):
    plugin_id = "zwo_eaf"
    display_name = "ZWO EAF (SDK USB)"

    def __init__(self, log_fn=print, config=None):
        super().__init__(log_fn, config)
        self.eaf = ZwoEaf()
        self.step_coarse = int(self.config.get("step_coarse", DEFAULT_COARSE))
        self.step_fine = int(self.config.get("step_fine", DEFAULT_FINE))
        self.hold_timeout = float(self.config.get("hold_timeout", HOLD_TIMEOUT_S))
        self.hold_interval = float(self.config.get("hold_interval",
                                                   HOLD_INTERVAL_S))
        # limite haute logicielle (protection butee mecanique du focuseur).
        # None => on garde la valeur par defaut du SDK (pas de reduction).
        self.max_step_limit = self.config.get("max_step_limit", None)
        self._hold_thread = None
        self._hold_stop = threading.Event()

    # -- detection --------------------------------------------------------- #
    @staticmethod
    def probe(config=None):
        try:
            e = ZwoEaf()
            info = e.connect()
            e.disconnect()
            return info is not None
        except Exception:
            return False

    # -- connexion --------------------------------------------------------- #
    def connect(self):
        info = self.eaf.connect()
        # Protection butee : reposer la limite haute a CHAQUE demarrage
        # (systeme autonome -> ne pas dependre de la memoire de l'EAF).
        if self.max_step_limit is not None:
            applied = self.eaf.set_max_step(int(self.max_step_limit))
            self.log(f"   [zwo_eaf] max_step limite a {applied} "
                     f"(protection butee)")
        self.log(f"   [zwo_eaf] connecte : {info}")
        return info

    def set_max_step(self, value):
        """Regle la limite haute logicielle et memorise pour les prochains
        demarrages (via config)."""
        applied = self.eaf.set_max_step(int(value))
        self.max_step_limit = applied
        self.log(f"   [zwo_eaf] max_step = {applied}")
        return applied

    def disconnect(self):
        self.stop_continuous()
        self.eaf.disconnect()

    @property
    def connected(self):
        return self.eaf.connected

    @property
    def max_step(self):
        return self.eaf.max_step

    # -- etat -------------------------------------------------------------- #
    def status(self):
        st = self.eaf.status()
        st["step_coarse"] = self.step_coarse
        st["step_fine"] = self.step_fine
        st["holding"] = self._hold_thread is not None and \
            self._hold_thread.is_alive()
        return st

    def get_position(self):
        return self.eaf.get_position()

    # -- pas --------------------------------------------------------------- #
    def set_step(self, coarse=None, fine=None):
        if coarse is not None:
            self.step_coarse = int(coarse)
        if fine is not None:
            self.step_fine = int(fine)
        self.log(f"   [zwo_eaf] pas coarse={self.step_coarse} "
                 f"fine={self.step_fine}")

    # -- deplacements ------------------------------------------------------ #
    def move_to(self, position, wait=False):
        return self.eaf.move_to(position, wait=wait)

    def move_relative(self, delta, wait=False):
        return self.eaf.move_relative(delta, wait=wait)

    def stop(self):
        self.stop_continuous()
        self.eaf.stop()

    # -- maintien continu -------------------------------------------------- #
    def _hold_loop(self, direction, step):
        """Boucle du thread de maintien : avance par pas jusqu'a stop, butee,
        ou timeout de securite."""
        signed = +step if direction == DIR_OUT else -step
        t0 = time.monotonic()
        while not self._hold_stop.is_set():
            # timeout de securite
            if (time.monotonic() - t0) > self.hold_timeout:
                self.log("   [zwo_eaf] maintien : timeout securite -> stop")
                break
            try:
                pos = self.eaf.get_position()
                # butees : inutile de pousser au-dela
                if signed > 0 and pos >= self.max_step:
                    self.log("   [zwo_eaf] maintien : butee haute atteinte")
                    break
                if signed < 0 and pos <= 0:
                    self.log("   [zwo_eaf] maintien : butee basse atteinte")
                    break
                self.eaf.move_relative(signed, wait=True, timeout=2.0)
            except EafError as e:
                self.log(f"   [zwo_eaf] maintien : erreur {e} -> stop")
                break
            # petite pause entre deux pas (cadence)
            self._hold_stop.wait(self.hold_interval)
        # securite finale
        try:
            self.eaf.stop()
        except Exception:
            pass

    def start_continuous(self, direction, mode=STEP_COARSE):
        if direction not in (DIR_IN, DIR_OUT):
            raise ValueError(f"Direction inconnue : {direction}")
        # si un maintien tourne deja, on l'arrete d'abord
        self.stop_continuous()
        step = self.step_coarse if mode == STEP_COARSE else self.step_fine
        self._hold_stop.clear()
        self._hold_thread = threading.Thread(
            target=self._hold_loop, args=(direction, step), daemon=True)
        self._hold_thread.start()
        self.log(f"   [zwo_eaf] maintien {direction} pas={step} demarre")

    def stop_continuous(self):
        if self._hold_thread and self._hold_thread.is_alive():
            self._hold_stop.set()
            self._hold_thread.join(timeout=3.0)
            self.log("   [zwo_eaf] maintien arrete")
        self._hold_thread = None
