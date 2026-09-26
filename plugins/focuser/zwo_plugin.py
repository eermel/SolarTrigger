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

from .base import (FocuserPlugin, DIR_IN, DIR_OUT, STEP_COARSE, STEP_FINE)

# zwo_eaf.py est desormais dans ce meme package (plugins/focuser/).
from .zwo_eaf import ZwoEaf, EafError

# defauts (regles sur le vrai materiel ; surchargeables via config)
DEFAULT_COARSE = 150       # bon compromis fluidite/rapidite (trouver la zone)
DEFAULT_FINE = 20          # petit pas pour lunette rapide (WO Z73) -- affinage


class ZwoFocuser(FocuserPlugin):
    plugin_id = "zwo_eaf"
    display_name = "ZWO EAF (SDK USB)"

    def __init__(self, log_fn=print, config=None):
        super().__init__(log_fn, config)
        self.eaf = ZwoEaf()
        self.step_coarse = int(self.config.get("step_coarse", DEFAULT_COARSE))
        self.step_fine = int(self.config.get("step_fine", DEFAULT_FINE))
        # limite haute logicielle (protection butee mecanique du focuseur).
        # None => on garde la valeur par defaut du SDK (pas de reduction).
        self.max_step_limit = self.config.get("max_step_limit", None)
        self._hold_thread = None
        self._hold_stop = threading.Event()

    # -- detection --------------------------------------------------------- #
    @staticmethod
    def probe(config=None):
        try:
            return bool(ZwoEaf().enumerate_devices())
        except Exception:
            return False

    @staticmethod
    def inventory(config=None):
        """Retourne tous les ZWO EAF visibles par le SDK."""
        return ZwoEaf().enumerate_devices()

    # -- connexion --------------------------------------------------------- #
    def connect(self):
        device_id = self.config.get("device_id")
        info = self.eaf.connect(device_id=device_id)
        # Protection butee : reposer la limite haute a CHAQUE demarrage
        # (systeme autonome -> ne pas dependre de la memoire de l'EAF).
        if self.max_step_limit is not None:
            applied = self.eaf.set_max_step(int(self.max_step_limit))
            self.log(f"   [zwo_eaf] max_step limite a {applied} "
                     f"(protection butee)")
        self.log(f"   [zwo_eaf] connected: {info}")
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

    def set_current_position(self, value):
        """Reset the EAF hardware position counter/reference."""
        return self.eaf.set_current_position(int(value))

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
        """Run one native asynchronous EAFMove until button release.

        The ZWO SDK has no direction/speed command for manual jogging.  Its
        documented continuous-motion pattern is therefore one absolute move
        toward the corresponding software limit, followed by EAFStop when the
        operator releases the button.  Do not emulate a hold with repeated
        relative moves: every EAFMove boundary produces a mechanical pause.
        """
        del step  # step size is for short clicks, not held-button velocity.
        try:
            target = self.max_step if direction == DIR_OUT else 0
            self.eaf.move_to(target, wait=False)
            self._hold_stop.wait()
        except EafError as e:
            self.log(f"   [zwo_eaf] hold: error {e} -> stop")
        finally:
            try:
                self.eaf.stop()
            except Exception:
                pass

    def start_continuous(self, direction, mode=STEP_COARSE):
        if direction not in (DIR_IN, DIR_OUT):
            raise ValueError(f"Unknown direction: {direction}")
        # si un maintien tourne deja, on l'arrete d'abord
        self.stop_continuous()
        step = self.step_coarse if mode == STEP_COARSE else self.step_fine
        self._hold_stop.clear()
        self._hold_thread = threading.Thread(
            target=self._hold_loop, args=(direction, step), daemon=True)
        self._hold_thread.start()
        self.log(f"   [zwo_eaf] maintien {direction} pas={step} started")

    def stop_continuous(self):
        if self._hold_thread and self._hold_thread.is_alive():
            self._hold_stop.set()
            self._hold_thread.join(timeout=3.0)
            self.log("   [zwo_eaf] maintien arrete")
        self._hold_thread = None
