#!/usr/bin/env python3
"""
camera_plugins/base.py
Version : 1.0.00

Contrat commun a tous les plugins d'appareil photo.

Le moteur (eclipse_trigger) ne connait aucun modele de boitier : il detecte
la camera, demande au registre le plugin qui correspond, puis dialogue
uniquement via l'interface CameraPlugin ci-dessous. Ajouter un boitier =
ecrire un nouveau plugin qui herite de CameraPlugin, sans toucher au moteur.

Une "sequence de vitesses" est decrite de facon uniforme par (v_max, v_min,
step_il) : de la vitesse la plus RAPIDE a la plus LENTE, par pas de step_il IL.
Chaque plugin traduit cela comme il peut :
  - Nikon : photo par photo (une vue par vitesse).
  - Sony  : bracket(s) continu(s) internes, ou photo unique si une seule vitesse.
"""

from abc import ABC, abstractmethod
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from services.camera_service import CaptureIntent, PreparedCapture


def seconds_until_deadline(deadline):
    """Seconds remaining until a deadline.

    Numeric deadlines are monotonic timestamps and are the preferred trigger
    contract. Datetime support is kept only for compatibility with standalone
    plugin tests/tools.
    """
    if deadline is None:
        return None
    if isinstance(deadline, (int, float)):
        return float(deadline) - time.monotonic()
    from datetime import datetime, timezone
    if deadline.tzinfo is None:
        current = datetime.now(timezone.utc).replace(tzinfo=None)
    else:
        current = datetime.now(deadline.tzinfo)
    return (deadline - current).total_seconds()


class CaptureResult:
    """Compte-rendu d'une sequence de prises de vue."""
    def __init__(self, frames=0, planned=0, detail=None):
        self.frames = frames        # vues effectivement declenchees/detectees
        self.planned = planned      # vues prevues par le plan
        self.detail = detail or ""  # texte court pour le log

    def __repr__(self):
        return f"<CaptureResult {self.frames}/{self.planned} {self.detail!r}>"


class CameraPlugin(ABC):
    """Interface que chaque plugin d'appareil doit implementer.

    Le plugin recoit une fonction de log (log_fn) pour ecrire dans le meme flux
    que le moteur, et l'objet camera gphoto2 deja initialise."""

    #: nom lisible du plugin (pour les logs)
    name = "generic"

    def __init__(self, camera, log_fn=print):
        self.camera = camera
        self.log = log_fn

    # ------------------------------------------------------------------ #
    # Detection
    # ------------------------------------------------------------------ #
    @staticmethod
    @abstractmethod
    def matches(model_string):
        """Retourne True si ce plugin sait piloter le boitier dont le modele
        gphoto2 est `model_string` (ex. 'Sony ILCE-7M5 (PC Control)')."""
        raise NotImplementedError

    # ------------------------------------------------------------------ #
    # Initialisation des reglages (bloc d'init au lancement de la sequence)
    # ------------------------------------------------------------------ #
    @abstractmethod
    def init_settings(self, aperture=None, iso=None, image_format="RAW",
                      white_balance="Daylight"):
        """Force le boitier dans un etat connu : mode M, focus manuel, format,
        ISO, ouverture, balance des blancs, cible d'enregistrement carte, etc.
        Chaque plugin connait les noms de config propres a sa marque."""
        raise NotImplementedError

    # ------------------------------------------------------------------ #
    # Coeur : prendre une sequence de vitesses
    # ------------------------------------------------------------------ #
    @abstractmethod
    def set_exposure_settings(self, aperture=None, iso=None):
        """Update only phase-dependent exposure base settings.

        This deliberately excludes model-specific transport/capture-mode setup,
        which remains owned by the plugin.
        """
        raise NotImplementedError

    def get_battery_level(self):
        """Return battery percentage as int when the camera exposes it."""
        try:
            cfg = self.camera.get_config()
            for name in ("batterylevel", "battery", "Battery Level"):
                try:
                    raw = cfg.get_child_by_name(name).get_value()
                    return int(float(str(raw).strip().rstrip("%")))
                except Exception:
                    continue
        except Exception:
            pass
        return None

    def sync_datetime(self, ref):
        """Report that camera date/time synchronization is unsupported."""
        return {
            "status": "unsupported",
            "datetime_synced": False,
            "timezone_synced": False,
            "datetime_applied": None,
            "timezone_name": getattr(ref, "timezone_name", None) or None,
            "utc_offset_minutes": getattr(ref, "utc_offset_minutes", None) or None,
            "message": f"Date/time synchronization is unsupported by {self.name}",
            "plugin": self.name,
        }

    @abstractmethod
    def shoot_speeds(self, v_max, v_min, step_il, photo_num_start=0,
                     deadline=None):
        """Prend la plage [v_max (rapide) .. v_min (lente)] par pas step_il IL.
        v_max == v_min -> une seule vue.
        `deadline` (datetime|None) : ne pas demarrer une prise qui finirait
        apres cette heure. Retourne un CaptureResult."""
        raise NotImplementedError

    # ------------------------------------------------------------------ #
    # Utilitaire optionnel : une seule vue a une vitesse donnee (ex. 8 s)
    # ------------------------------------------------------------------ #
    def shoot_single(self, speed, photo_num=0, deadline=None):
        """Prend UNE vue a la vitesse `speed`. Par defaut, delegue a
        shoot_speeds avec v_max == v_min ; un plugin peut surcharger."""
        return self.shoot_speeds(speed, speed, 1.0,
                                 photo_num_start=photo_num, deadline=deadline)
