"""Tests for the camera plugin vibration capability surface."""

import sys
import types

import pytest


if "gphoto2" not in sys.modules:
    fake_gphoto2 = types.ModuleType("gphoto2")
    fake_gphoto2.GPhoto2Error = RuntimeError
    fake_gphoto2.GP_WIDGET_TOGGLE = object()
    fake_gphoto2.GP_EVENT_FILE_ADDED = object()
    sys.modules["gphoto2"] = fake_gphoto2

from plugins.camera.nikon import NikonDSLRPlugin, NikonZPlugin
from plugins.camera.sony import SonyPlugin
from services.camera_service import CameraService


class FakeCamera:
    def init(self):
        pass


class CapabilityPlugin:
    name = "capability-stub"

    def __init__(self, capabilities):
        self.capabilities = capabilities

    def get_vibration_capabilities(self):
        return self.capabilities


def test_nikon_and_sony_plugins_report_no_vibration_capabilities():
    camera = object()
    log = lambda _message: None

    for plugin_class in (NikonDSLRPlugin, NikonZPlugin, SonyPlugin):
        plugin = plugin_class(camera, log)
        assert plugin.get_vibration_capabilities() == {}


def test_service_passes_through_a_copy_of_plugin_capabilities(monkeypatch):
    capabilities = {"sensor_shift": True, "min_delay_ms": 50}
    plugin = CapabilityPlugin(capabilities)
    monkeypatch.setattr(
        "services.camera_service.get_camera_model", lambda _camera: "Test Camera"
    )
    service = CameraService(
        camera_factory=FakeCamera,
        plugin_loader=lambda _camera, _log: plugin,
        log_fn=lambda _message: None,
    )
    service.connect()

    result = service.get_vibration_capabilities()

    assert result == capabilities
    assert result is not capabilities


def test_service_returns_none_when_disconnected():
    assert CameraService().get_vibration_capabilities() is None


@pytest.mark.parametrize("plugin_class", [NikonDSLRPlugin, NikonZPlugin, SonyPlugin])
def test_service_reports_no_vibration_capabilities_for_nikon_and_sony(
    monkeypatch, plugin_class
):
    monkeypatch.setattr(
        "services.camera_service.get_camera_model", lambda _camera: "Test Camera"
    )
    service = CameraService(
        camera_factory=FakeCamera,
        plugin_loader=lambda camera, log: plugin_class(camera, log),
        log_fn=lambda _message: None,
    )

    service.connect()

    assert service.get_vibration_capabilities() == {}
