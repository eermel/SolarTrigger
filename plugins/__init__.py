#!/usr/bin/env python3
"""
plugins/__init__.py
Version : 1.0.00

Package parent regroupant les trois familles de plugins d'equipement du
SolarEclipse Trigger :

    plugins/camera/   -- boitiers photo (Sony, Nikon D850, Nikon Z...)
    plugins/mount/    -- montures equatoriales (OnStep/Tessek, ...)
    plugins/focuser/  -- focuseurs EAF (ZWO, ...)
    plugins/gps/      -- sources GPS (NMEA serie, gpsd, ...)

Chaque famille garde son propre registre et son contrat. Ce parent ne fait que
les rassembler sous une arborescence commune, pratique pour la future page
d'equipement (qui balaiera les available_plugins() des trois familles).

Import paresseux : on n'importe une famille que si on l'utilise, pour ne pas
exiger gphoto2 / pyserial / le SDK EAF quand on ne touche qu'une seule famille.
"""


def camera():
    """Retourne le module registre camera (import a la demande)."""
    from . import camera
    return camera


def mount():
    from . import mount
    return mount


def focuser():
    from . import focuser
    return focuser


def gps():
    from . import gps
    return gps
