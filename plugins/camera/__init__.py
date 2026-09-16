#!/usr/bin/env python3
"""Single production camera path: characterized profile -> ProfilePlugin."""
from .base import CameraPlugin, CaptureResult

def get_camera_model(camera):
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
    try:
        import gphoto2 as gp
        detected = list(gp.Camera.autodetect())
        port = camera.get_port_info().get_path() if camera is not None else None
        for model, detected_port in detected:
            if (port is not None and detected_port != port) or (port is None and len(detected) != 1):
                continue
            model = specific(model)
            if model:
                return model
    except Exception:
        pass
    return ""

def load_plugin(camera, log_fn=print):
    """Production loader. Fail closed if no unique characterized profile exists."""
    model = get_camera_model(camera)
    from backend.camera_profiles import profile_for_model
    from .profile import ProfilePlugin
    profile = profile_for_model(model)
    if profile is None:
        log_fn(f"No characterized camera profile for model '{model}'; production runtime disabled")
        return None
    log_fn(f"Characterized profile selected: {profile['backend']} (model '{model}')")
    return ProfilePlugin(camera, log_fn, profile)

def _load_reference_plugin_classes():
    """Historical Sony/Nikon executors; explicit DEV/reference use only."""
    from importlib import import_module
    import inspect
    from pathlib import Path
    classes = []
    for path in sorted(Path(__file__).parent.glob("*.py")):
        if path.stem.startswith("_") or path.stem in ("base", "profile"):
            continue
        try:
            module = import_module(f"{__name__}.{path.stem}")
        except Exception:
            continue
        for candidate in vars(module).values():
            if (inspect.isclass(candidate) and candidate.__module__ == module.__name__
                    and issubclass(candidate, CameraPlugin) and not inspect.isabstract(candidate)
                    and getattr(candidate, "specificity", 0) > 0):
                classes.append(candidate)
    return sorted(classes, key=lambda cls: getattr(cls, "specificity", 0), reverse=True)

def load_reference_plugin(camera, log_fn=print):
    """Explicit benchmark/oracle loader. Never called by production load_plugin."""
    model = get_camera_model(camera)
    for plugin_cls in _load_reference_plugin_classes():
        if plugin_cls.matches(model):
            log_fn(f"REFERENCE ONLY plugin selected: {plugin_cls.name} (model '{model}')")
            return plugin_cls(camera, log_fn)
    return None

__all__ = ["CameraPlugin", "CaptureResult", "load_plugin", "load_reference_plugin", "get_camera_model"]
