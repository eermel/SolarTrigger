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
    from importlib import import_module
    import inspect
    from pathlib import Path
    classes = []
    for path in sorted(Path(__file__).parent.glob("*.py")):
        if path.stem.startswith("_") or path.stem in ("base", "profile"):
            continue
        try:
            module = import_module(f"{__name__}.{path.stem}")
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning("Cannot load camera module %s: %s", path.name, exc)
            continue
        for candidate in vars(module).values():
            if (inspect.isclass(candidate) and candidate.__module__ == module.__name__
                    and issubclass(candidate, CameraPlugin) and not inspect.isabstract(candidate)
                    and getattr(candidate, "specificity", 0) > 0):
                classes.append(candidate)
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

    # Never identify one body from another body's autodetect entry.
    try:
        import gphoto2 as gp
        detected = list(gp.Camera.autodetect())
        port = camera.get_port_info().get_path() if camera is not None else None
        for model, _port in detected:
            if (port is not None and _port != port) or (port is None and len(detected) != 1):
                continue
            model = specific(model)
            if model:
                return model
    except Exception:
        pass
    return ""


def load_plugin(camera, log_fn=print):
    """Return the runtime executor for the detected camera.

    A successfully characterized profile is the runtime authority.  The same
    ProfilePlugin path used during operational qualification must therefore be
    used on eclipse day; otherwise characterization could validate one
    execution engine while a brand-specific legacy plugin executes another.

    Hardware-specific Sony/Nikon/etc. plugins remain compatibility fallbacks
    only for cameras which do not have a valid characterized profile.
    """
    model = get_camera_model(camera)
    from backend.camera_profiles import profile_for_model
    from .profile import ProfilePlugin

    profile = profile_for_model(model)

    if profile is not None:
        strategy = profile.get("strategy")
        strategy_text = (
            f", strategy={strategy}"
            if isinstance(strategy, str) and strategy
            else ""
        )
        log_fn(
            f"Characterized camera profile selected: {profile['backend']} "
            f"(model '{model}'{strategy_text})"
        )
        return ProfilePlugin(camera, log_fn, profile)

    for plugin_cls in _load_plugin_classes():
        try:
            matched = plugin_cls.matches(model)
        except Exception as exc:
            log_fn(
                f"Detection error for {plugin_cls.__name__}: {exc}"
            )
            continue

        if not matched:
            continue

        log_fn(
            f"Plugin selected: {plugin_cls.name} "
            f"(model '{model}')"
        )

        try:
            return plugin_cls(camera, log_fn)
        except Exception as exc:
            log_fn(
                f"Plugin initialization failed for "
                f"{plugin_cls.__name__}: {exc}"
            )
            raise

    log_fn(
        f"No characterized camera profile for model '{model}'; "
        "using legacy plugin fallback only if one matched"
    )
    return None


__all__ = ["CameraPlugin", "CaptureResult", "load_plugin",
           "get_camera_model"]
