from __future__ import annotations

from types import SimpleNamespace

import pytest

from services.camera_service import CameraService


class FakeWidget:
    def __init__(self, value):
        self.value = value

    def get_value(self):
        return self.value


class FakeConfig:
    def __init__(self, values):
        self.values = values

    def get_child_by_name(self, name):
        if name not in self.values:
            raise KeyError(name)
        return FakeWidget(self.values[name])


class FakeCamera:
    autodetected = [
        ("Nikon Corporation D850", "usb:001,028"),
        ("Sony Corporation ILCE-7M5", "usb:001,027"),
    ]
    serials = {
        "usb:001,028": "D850-SERIAL",
        "usb:001,027": "A7V-SERIAL",
    }
    instances = []

    def __init__(self):
        self.port = None
        self.initialized = False
        self.exited = False
        type(self).instances.append(self)

    @classmethod
    def autodetect(cls):
        return list(cls.autodetected)

    def set_port_info(self, port_info):
        self.port = port_info.path

    def init(self):
        self.initialized = True

    def exit(self):
        self.exited = True

    def get_config(self):
        return FakeConfig({
            "serialnumber": self.serials[self.port],
            "cameramodel": {
                port: model for model, port in self.autodetected
            }[self.port],
        })


class FakePortInfoList:
    def __init__(self):
        self.paths = []

    def load(self):
        self.paths = [port for _model, port in FakeCamera.autodetected]

    def lookup_path(self, path):
        return self.paths.index(path)

    def __getitem__(self, index):
        return SimpleNamespace(path=self.paths[index])


class FakePlugin:
    name = "fake"

    def get_battery_level(self):
        return 100


class FakeGp:
    Camera = FakeCamera
    PortInfoList = FakePortInfoList


@pytest.fixture(autouse=True)
def reset_fake_camera():
    FakeCamera.instances = []


def test_connect_selects_camera_by_stable_serial_not_autodetect_order(
    monkeypatch,
):
    monkeypatch.setitem(__import__("sys").modules, "gphoto2", FakeGp)
    monkeypatch.setattr(
        "services.camera_service.get_camera_model",
        lambda camera: {
            port: model for model, port in FakeCamera.autodetected
        }[camera.port],
    )

    service = CameraService(
        camera_identity={
            "backend": "sony",
            "model": "ILCE-7M5",
            "serial": "A7V-SERIAL",
        },
        plugin_loader=lambda camera, log: FakePlugin(),
        log_fn=lambda *_args: None,
    )

    service.connect()

    assert service.camera.port == "usb:001,027"
    assert service.model == "Sony Corporation ILCE-7M5"


def test_connect_never_falls_back_to_first_camera_when_serial_is_missing(
    monkeypatch,
):
    monkeypatch.setitem(__import__("sys").modules, "gphoto2", FakeGp)

    service = CameraService(
        camera_identity={
            "backend": "sony",
            "model": "ILCE-7M5",
            "serial": "ABSENT-SERIAL",
        },
        plugin_loader=lambda camera, log: FakePlugin(),
        log_fn=lambda *_args: None,
    )

    with pytest.raises(RuntimeError, match="ABSENT-SERIAL"):
        service.connect()

    assert service.camera is None


def test_camera_worker_builds_default_service_with_bound_identity(monkeypatch):
    from backend.camera_worker import CameraWorker

    captured = {}

    class CapturingService:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def close(self):
            pass

    monkeypatch.setattr("backend.camera_worker.CameraService", CapturingService)

    worker = CameraWorker(
        rig_id=1,
        clock="CLOCK",
        log_fn=lambda *_args: None,
    )
    camera = {
        "backend": "sony",
        "model": "ILCE-7M5",
        "serial": "A7V-SERIAL",
    }

    worker.configure_camera(camera)
    worker._ensure_service()

    assert captured["camera_identity"] == camera
    assert captured["clock"] == "CLOCK"
