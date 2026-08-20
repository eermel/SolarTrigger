#!/usr/bin/env python3
"""
camera_plugins/__init__.py
Version : 2.0.00

Registre des plugins d'appareil photo + detection automatique a DEUX NIVEAUX
(marque + modele), par priorite de specificite.

Le moteur appelle load_plugin(camera, log_fn) : on lit le modele remonte par
gphoto2, on choisit le premier plugin dont matches() repond True, et on
retourne une instance prete a l'emploi. Aucun modele de boitier n'est cable
dans le moteur.

Ajouter un boitier : creer camera_plugins/<marque>.py avec une classe heritant
de CameraPlugin, puis l'enregistrer dans PLUGINS ci-dessous. Rien d'autre a
toucher.
"""

from .base import CameraPlugin, CaptureResult

# NB : sony.py et nikon.py importent gphoto2. On les charge PARESSEUSEMENT dans
# load_plugin(), pour que sony_planner reste importable/testable sans gphoto2
# (utile pour valider le decoupage de brackets hors de la Pi).


def _load_plugin_classes():
    from .sony import SonyPlugin
    from .nikon import NikonZPlugin, NikonDSLRPlugin
    # Tries par specificite DECROISSANTE : le plus specifique matche en premier.
    # Z (20) avant DSLR (10) ; Sony (20) independant. On trie a l'execution.
    classes = [SonyPlugin, NikonZPlugin, NikonDSLRPlugin]
    return sorted(classes, key=lambda c: getattr(c, "specificity", 0),
                  reverse=True)


def get_camera_model(camera):
    """Return the most specific model string available.

    libgphoto2 can expose a generic ``USB PTP Class Camera`` ability even when
    autodetect knows the exact body (e.g. Sony ILCE-7M5). Generic PTP labels
    must therefore never stop plugin detection.
    """
    generic_markers = ("usb ptp class camera", "ptp class camera", "ptp camera")

    def specific(value):
        text = str(value or "").strip()
        return text if text and not any(m in text.lower() for m in generic_markers) else ""

    try:
        model = specific(camera.get_abilities().model)
        if model:
            return model
    except Exception:
        pass

    for name in ("cameramodel", "model", "modelname"):
        try:
            model = specific(camera.get_config().get_child_by_name(name).get_value())
            if model:
                return model
        except Exception:
            continue

    # Last reliable fallback: libgphoto2 autodetect. In this appliance one camera
    # is expected; with several devices, prefer the first specific model.
    try:
        import gphoto2 as gp
        for model, _port in gp.Camera.autodetect():
            model = specific(model)
            if model:
                return model
    except Exception:
        pass
    return ""


def load_plugin(camera, log_fn=print):
    """Detecte le boitier et retourne l'instance de plugin adaptee, ou None."""
    model = get_camera_model(camera)
    for plugin_cls in _load_plugin_classes():
        try:
            if plugin_cls.matches(model):
                log_fn(f"Plugin selectionne : {plugin_cls.name} "
                       f"(modele '{model}')")
                return plugin_cls(camera, log_fn)
        except Exception as e:
            log_fn(f"Erreur detection {plugin_cls.__name__} : {e}")
    log_fn(f"Aucun plugin pour le modele '{model}'")
    return None


__all__ = ["CameraPlugin", "CaptureResult", "load_plugin",
           "get_camera_model"]
