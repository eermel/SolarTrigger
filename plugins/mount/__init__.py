#!/usr/bin/env python3
"""
mount_plugins/__init__.py
Version : 1.0.00

Registre des plugins de monture + selection.

Selection EXPLICITE par identifiant (ce que fera la future page d'equipement,
facon ASIAIR : une liste deroulante par type d'equipement + 'none'). La
detection auto reste possible via probe() mais n'est pas le mode par defaut.

Ajouter une monture : creer mount_plugins/<x>_plugin.py avec une classe heritant
de MountPlugin, puis l'enregistrer dans _PLUGIN_CLASSES ci-dessous.

Note : les imports des plugins concrets sont PARESSEUX (dans les fonctions),
car onstep_plugin importe pyserial via onstep.py. Ainsi lister les plugins
disponibles pour l'UI ne necessite pas que le materiel/les libs soient presents.
"""

from collections.abc import Mapping

from .base import MountPlugin

# identifiant -> (module, classe, nom_affichage). Module importe paresseusement.
# Le nom d'affichage est ici pour que available_plugins() fonctionne SANS
# importer le plugin (donc sans pyserial ni materiel).
_PLUGIN_CLASSES = {
    "indi": ("indi_plugin", "IndiMount", "INDI / EQMod compatible"),
    "onstep": ("onstep_plugin", "OnStepMount",
               "OnStep / Tessek Mini 11 (LX200 serie)"),
    # a venir :
    # "zwo":     ("zwo_plugin", "ZwoMount", "ZWO AM3N"),
    # "synscan": ("synscan_plugin", "SynScanMount", "Skywatcher AZ-GTi (SynScan)"),
}

# entree speciale "aucun equipement de ce type"
NONE_ID = "none"


def available_plugins():
    """Liste pour peupler la liste deroulante de l'UI :
    [{'id':..., 'name':...}, ...] avec 'none' en tete. N'importe AUCUN plugin
    (pas de dependance materielle requise pour juste lister)."""
    items = [{"id": NONE_ID, "name": "Aucune monture"}]
    for pid, entry in _PLUGIN_CLASSES.items():
        items.append({"id": pid, "name": entry[2]})
    return items


def load_mount(plugin_id, log_fn=print, config=None):
    """Instancie le plugin choisi par son id. Retourne None pour 'none' ou id
    inconnu (avec log)."""
    if plugin_id in (None, NONE_ID, ""):
        log_fn("Aucune monture selectionnee (none).")
        return None
    entry = _PLUGIN_CLASSES.get(plugin_id)
    if not entry:
        log_fn(f"Plugin monture inconnu : '{plugin_id}'")
        return None
    mod_name, cls_name = entry[0], entry[1]
    try:
        import importlib
        mod = importlib.import_module(f".{mod_name}", __package__)
        cls = getattr(mod, cls_name)
    except Exception as e:
        log_fn(f"Chargement du plugin '{plugin_id}' impossible : {e}")
        return None
    log_fn(f"Plugin monture : {cls.display_name}")
    return cls(log_fn=log_fn, config=config)


def detect_mount(candidates=None, log_fn=print, config_by_id=None):
    """Auto-detection optionnelle : essaie probe() de chaque candidat et
    retourne le premier qui repond. `candidates` = liste d'ids (defaut : tous).
    `config_by_id` = dict id -> config (ports, etc.)."""
    config_by_id = config_by_id or {}
    ids = candidates or list(_PLUGIN_CLASSES.keys())
    for pid in ids:
        entry = _PLUGIN_CLASSES.get(pid)
        if not entry:
            continue
        mod_name, cls_name = entry[0], entry[1]
        try:
            import importlib
            mod = importlib.import_module(f".{mod_name}", __package__)
            cls = getattr(mod, cls_name)
            if cls.probe(config_by_id.get(pid)):
                log_fn(f"Monture detectee : {cls.display_name}")
                return load_mount(pid, log_fn, config_by_id.get(pid))
        except Exception as e:
            log_fn(f"probe {pid} : {e}")
    log_fn("Aucune monture detectee.")
    return None


def inventory_mounts(candidates=None, log_fn=print, config_by_id=None):
    """Enumerate physical mount instances exposed by registered plugins.

    Plugins implementing ``inventory()`` may return several physical devices.
    Legacy single-instance plugins remain supported through ``probe()``.
    """
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
                    normalized.setdefault("category", "mount")
                    normalized.setdefault("backend", pid)
                    devices.append(normalized)
                continue

            if cls.probe(config):
                devices.append({
                    "category": "mount",
                    "backend": pid,
                    "model": pid,
                })

        except Exception as exc:
            log_fn(f"inventory {pid} : {exc}")

    return devices


__all__ = ["MountPlugin", "available_plugins", "load_mount", "detect_mount",
           "inventory_mounts", "NONE_ID"]
