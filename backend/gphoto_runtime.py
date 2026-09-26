"""python-gphoto2 import helper for native SolarTrigger driver selection."""
from __future__ import annotations

import importlib
import os
from types import ModuleType


def import_gphoto2() -> ModuleType:
    """Import gphoto2 while preserving explicitly selected native driver paths.

    python-gphoto2 wheels bundle libgphoto2 drivers and may rewrite CAMLIBS and
    IOLIBS during import. SolarTrigger systemd units explicitly select the
    native driver directories, including /usr/local builds for recent cameras.
    """
    selected = {
        name: os.environ.get(name)
        for name in ("CAMLIBS", "IOLIBS")
        if os.environ.get(name)
    }
    gp = importlib.import_module("gphoto2")
    for name, value in selected.items():
        os.environ[name] = value
    return gp
