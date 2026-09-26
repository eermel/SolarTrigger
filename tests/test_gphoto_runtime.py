import os
from types import SimpleNamespace

from backend import gphoto_runtime


def test_import_gphoto2_preserves_explicit_native_driver_paths(monkeypatch):
    monkeypatch.setenv("CAMLIBS", "/usr/local/lib/libgphoto2/2.5.34.1")
    monkeypatch.setenv("IOLIBS", "/usr/local/lib/libgphoto2_port/0.12.2")
    fake = SimpleNamespace()

    def fake_import(name):
        assert name == "gphoto2"
        os.environ["CAMLIBS"] = "/venv/gphoto2/libgphoto2/camlibs"
        os.environ["IOLIBS"] = "/venv/gphoto2/libgphoto2/iolibs"
        return fake

    monkeypatch.setattr(gphoto_runtime.importlib, "import_module", fake_import)

    assert gphoto_runtime.import_gphoto2() is fake
    assert os.environ["CAMLIBS"] == "/usr/local/lib/libgphoto2/2.5.34.1"
    assert os.environ["IOLIBS"] == "/usr/local/lib/libgphoto2_port/0.12.2"


def test_import_gphoto2_keeps_wheel_defaults_without_explicit_selection(monkeypatch):
    monkeypatch.delenv("CAMLIBS", raising=False)
    monkeypatch.delenv("IOLIBS", raising=False)
    fake = SimpleNamespace()

    def fake_import(_name):
        os.environ["CAMLIBS"] = "/wheel/camlibs"
        os.environ["IOLIBS"] = "/wheel/iolibs"
        return fake

    monkeypatch.setattr(gphoto_runtime.importlib, "import_module", fake_import)
    gphoto_runtime.import_gphoto2()

    assert os.environ["CAMLIBS"] == "/wheel/camlibs"
    assert os.environ["IOLIBS"] == "/wheel/iolibs"


def test_runtime_modules_do_not_import_gphoto2_directly():
    """All runtime imports must preserve SolarTrigger's native CAMLIBS/IOLIBS."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    runtime_files = (
        "flask_app/app.py",
        "backend/camera_auxiliary_capabilities.py",
        "backend/camera_characterization.py",
        "plugins/camera/sony.py",
        "plugins/camera/nikon.py",
        "plugins/camera/profile.py",
        "plugins/camera/__init__.py",
        "services/camera_service.py",
        "backend/device_inventory.py",
    )
    forbidden = ("import gphoto2 as gp", "from gphoto2 ")

    offenders = []
    for relative in runtime_files:
        source = (root / relative).read_text(encoding="utf-8")
        if any(token in source for token in forbidden):
            offenders.append(relative)

    assert offenders == []
