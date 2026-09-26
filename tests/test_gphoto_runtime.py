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
