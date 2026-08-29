#!/usr/bin/env python3
"""
focuser_plugins/__init__.py
Version : 1.0.00

Registre des plugins de focuseur + selection (meme principe que mount_plugins).
Selection explicite par id (future page d'equipement) + entree 'none'.
Imports paresseux : lister les plugins ne charge ni le SDK ni le materiel.
"""

from collections.abc import Mapping

from .base import FocuserPlugin

# id -> (module, classe, nom_affichage)
_PLUGIN_CLASSES = {
    "zwo_eaf": ("zwo_plugin", "ZwoFocuser", "ZWO EAF (SDK USB)"),
    # a venir : autres focuseurs (Pegasus, Moonlite...) = un fichier chacun.
}

NONE_ID = "none"


def available_plugins():
    items = [{"id": NONE_ID, "name": "Aucun focuseur"}]
    for pid, entry in _PLUGIN_CLASSES.items():
        items.append({"id": pid, "name": entry[2]})
    return items


def load_focuser(plugin_id, log_fn=print, config=None):
    if plugin_id in (None, NONE_ID, ""):
        log_fn("Aucun focuseur selectionne (none).")
        return None
    entry = _PLUGIN_CLASSES.get(plugin_id)
    if not entry:
        log_fn(f"Plugin focuseur inconnu : '{plugin_id}'")
        return None
    mod_name, cls_name = entry[0], entry[1]
    try:
        import importlib
        mod = importlib.import_module(f".{mod_name}", __package__)
        cls = getattr(mod, cls_name)
    except Exception as e:
        log_fn(f"Chargement du plugin '{plugin_id}' impossible : {e}")
        return None
    log_fn(f"Plugin focuseur : {cls.display_name}")
    return cls(log_fn=log_fn, config=config)


def detect_focuser(candidates=None, log_fn=print, config_by_id=None):
    config_by_id = config_by_id or {}
    ids = candidates or list(_PLUGIN_CLASSES.keys())
    for pid in ids:
        entry = _PLUGIN_CLASSES.get(pid)
        if not entry:
            continue
        try:
            import importlib
            mod = importlib.import_module(f".{entry[0]}", __package__)
            cls = getattr(mod, entry[1])
            if cls.probe(config_by_id.get(pid)):
                log_fn(f"Focuseur detecte : {cls.display_name}")
                return load_focuser(pid, log_fn, config_by_id.get(pid))
        except Exception as e:
            log_fn(f"probe {pid} : {e}")
    log_fn("Aucun focuseur detecte.")
    return None



def inventory_focusers(candidates=None, log_fn=print, config_by_id=None):
    """Enumere les instances physiques exposees par les plugins focuser."""
    config_by_id = config_by_id or {}
    ids = candidates or list(_PLUGIN_CLASSES.keys())
    devices = []

    for pid in ids:
        entry = _PLUGIN_CLASSES.get(pid)
        if not entry:
            continue

        try:
            import importlib

            mod = importlib.import_module(f".{entry[0]}", __package__)
            cls = getattr(mod, entry[1])
            config = config_by_id.get(pid)

            inventory = getattr(cls, "inventory", None)

            if callable(inventory):
                for physical in inventory(config) or ():
                    if not isinstance(physical, Mapping):
                        continue

                    normalized = dict(physical)
                    normalized.setdefault("category", "focuser")
                    normalized.setdefault("backend", pid)
                    devices.append(normalized)

                continue

            # Compatibilite avec les anciens plugins single-instance.
            if cls.probe(config):
                devices.append({
                    "category": "focuser",
                    "backend": pid,
                    "model": pid,
                })

        except Exception as exc:
            log_fn(f"inventory {pid} : {exc}")

    return devices


__all__ = ["FocuserPlugin", "available_plugins", "load_focuser",
           "detect_focuser", "inventory_focusers", "NONE_ID"]
