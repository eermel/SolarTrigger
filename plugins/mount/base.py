#!/usr/bin/env python3
"""
mount_plugins/base.py
Version : 1.0.00

Contrat commun a tous les plugins de monture equatoriale.

Meme philosophie que camera_plugins : le moteur ne connait aucun modele de
monture. Il choisit un plugin (explicitement via la future page d'equipement,
ou par detection), puis dialogue uniquement via l'interface MountPlugin.

Perimetre volontairement LIMITE au besoin eclipse reel (decide avec l'operateur) :
  - PAS de GoTo par coordonnees : la mise en station de jour ne permet pas un
    pointage fiable. L'operateur vise manuellement via les commandes de
    direction, la monture assure le suivi.
  - Donc : connexion, statut, tracking (sideral/solaire/lunaire/off),
    mouvements manuels (4 directions), vitesse de deplacement, stop, home,
    arret d'urgence, ping.

Chaque plugin traduit ces operations dans son protocole (LX200/OnStep,
SynScan, INDI...) sans que le moteur en sache rien.
"""

from abc import ABC, abstractmethod


# Directions manuelles, independantes du protocole. Chaque plugin fait la
# correspondance vers ses commandes physiques (calibrees par monture).
DIR_DEC_LEFT = "dec_left"
DIR_DEC_RIGHT = "dec_right"
DIR_AD_LEFT = "ad_left"
DIR_AD_RIGHT = "ad_right"
DIRECTIONS = (DIR_DEC_LEFT, DIR_DEC_RIGHT, DIR_AD_LEFT, DIR_AD_RIGHT)

# Taux de suivi normalises.
RATE_SIDEREAL = "sidereal"
RATE_SOLAR = "solar"
RATE_LUNAR = "lunar"
TRACKING_RATES = (RATE_SIDEREAL, RATE_SOLAR, RATE_LUNAR)


class MountPlugin(ABC):
    """Interface que chaque plugin de monture doit implementer.

    log_fn : fonction de log (meme flux que le moteur).
    config : dict optionnel de parametres du plugin (port serie, baudrate,
             hote/port TCP...), fourni par la config / page d'equipement."""

    #: identifiant du plugin (pour la liste deroulante et les logs)
    plugin_id = "generic"
    #: nom lisible pour l'UI
    display_name = "Monture generique"

    def __init__(self, log_fn=print, config=None):
        self.log = log_fn
        self.config = config or {}

    # ------------------------------------------------------------------ #
    # Detection / disponibilite (optionnel : la page d'equipement choisit)
    # ------------------------------------------------------------------ #
    @staticmethod
    def probe(config=None):
        """Test NON destructif de presence : tente une connexion + un ping,
        retourne True si une monture de ce type repond. Utilise seulement si
        on veut de l'auto-detection ; la selection explicite n'en a pas besoin.
        Par defaut : non implemente -> False."""
        return False

    # ------------------------------------------------------------------ #
    # Connexion
    # ------------------------------------------------------------------ #
    @abstractmethod
    def connect(self):
        ...

    @abstractmethod
    def disconnect(self):
        ...

    @property
    @abstractmethod
    def connected(self):
        ...

    @abstractmethod
    def ping(self):
        """Retourne un dict {'ok': bool, ...} sans lever d'exception."""
        ...

    # ------------------------------------------------------------------ #
    # Statut / informations
    # ------------------------------------------------------------------ #
    @abstractmethod
    def status(self):
        """Dict d'etat homogene (connected, ra, dec, tracking, tracking_rate,
        move_rate, at_home, ...)."""
        ...

    # ------------------------------------------------------------------ #
    # Suivi
    # ------------------------------------------------------------------ #
    @abstractmethod
    def start_tracking(self, rate=RATE_SIDEREAL):
        ...

    @abstractmethod
    def stop_tracking(self):
        ...

    @property
    @abstractmethod
    def tracking(self):
        ...

    # ------------------------------------------------------------------ #
    # Mouvements manuels
    # ------------------------------------------------------------------ #
    @abstractmethod
    def move(self, direction):
        """direction dans DIRECTIONS."""
        ...

    @abstractmethod
    def stop(self):
        """Arrete les mouvements manuels (pas le tracking)."""
        ...

    @abstractmethod
    def set_speed(self, speed):
        """Vitesse de deplacement manuel (unite propre au plugin)."""
        ...

    # ------------------------------------------------------------------ #
    # Home / securite
    # ------------------------------------------------------------------ #
    def go_home(self, timeout=120):
        """Optionnel : toutes les montures n'ont pas de home. Par defaut, non
        supporte -> exception explicite."""
        raise NotImplementedError("go_home non supporte par ce plugin")

    @abstractmethod
    def emergency_stop(self):
        """Arret d'urgence : coupe mouvements ET tracking, sans lever."""
        ...
