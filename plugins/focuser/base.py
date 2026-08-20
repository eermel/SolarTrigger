#!/usr/bin/env python3
"""
focuser_plugins/base.py
Version : 1.0.00

Contrat commun a tous les plugins de focuseur (EAF).

Meme philosophie que camera_plugins et mount_plugins : le moteur ne connait
aucun modele. Il choisit un plugin (explicitement via la future page
d'equipement, ou par detection), puis dialogue via l'interface FocuserPlugin.

Besoins (decides avec l'operateur) :
  - move_to(position)          : aller a une position absolue (0 = home)
  - move_relative(delta)       : avancer de +/- delta pas
  - set_step(coarse, fine)     : definir les deux tailles de pas
  - start_continuous(dir, mode): maintien facon ASIAIR -- tant que le bouton
                                 est enfonce, le focuseur avance (grand pas =
                                 coarse pour trouver la zone, petit = fine pour
                                 affiner). stop_continuous() au relachement.
  - stop()                     : arret immediat.

Le maintien continu tourne dans un THREAD (le SDK EAF n'a pas de "move continu")
avec deux garde-fous : butees [0, max_step] et TIMEOUT de securite (si
l'evenement "relache" se perd cote IHM, le mouvement s'arrete seul).
"""

from abc import ABC, abstractmethod

# directions du maintien continu
DIR_IN = "in"       # vers 0 (rentre)
DIR_OUT = "out"     # vers max_step (sort)
DIRECTIONS = (DIR_IN, DIR_OUT)

# modes de pas
STEP_COARSE = "coarse"
STEP_FINE = "fine"
STEP_MODES = (STEP_COARSE, STEP_FINE)


class FocuserPlugin(ABC):
    """Interface que chaque plugin de focuseur doit implementer."""

    plugin_id = "generic"
    display_name = "Focuseur generique"

    def __init__(self, log_fn=print, config=None):
        self.log = log_fn
        self.config = config or {}

    # -- detection optionnelle (la page d'equipement peut choisir a la place) #
    @staticmethod
    def probe(config=None):
        return False

    # -- connexion --------------------------------------------------------- #
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

    # -- etat -------------------------------------------------------------- #
    @abstractmethod
    def status(self):
        """Dict homogene : position, max_step, moving, temperature, ..."""
        ...

    @abstractmethod
    def get_position(self):
        ...

    # -- pas --------------------------------------------------------------- #
    @abstractmethod
    def set_step(self, coarse=None, fine=None):
        """Definit les tailles de pas grand (coarse) et petit (fine)."""
        ...

    # -- deplacements ------------------------------------------------------ #
    @abstractmethod
    def move_to(self, position, wait=False):
        """Va a une position absolue (bornee [0, max_step]). 0 = home."""
        ...

    @abstractmethod
    def move_relative(self, delta, wait=False):
        """Avance de +/- delta pas depuis la position courante."""
        ...

    @abstractmethod
    def stop(self):
        ...

    # -- maintien continu (facon ASIAIR) ----------------------------------- #
    @abstractmethod
    def start_continuous(self, direction, mode=STEP_COARSE):
        """Demarre le maintien : avance en boucle dans `direction` par pas
        `mode` (coarse/fine), jusqu'a stop_continuous(), une butee, ou le
        timeout de securite."""
        ...

    @abstractmethod
    def stop_continuous(self):
        """Arrete le maintien (relachement du bouton)."""
        ...
