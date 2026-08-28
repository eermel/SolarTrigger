"""Tests for the camera plugin vibration capability surface."""

import sys
import types


if "gphoto2" not in sys.modules:
    fake_gphoto2 = types.ModuleType("gphoto2")
    fake_gphoto2.GPhoto2Error = RuntimeError
    fake_gphoto2.GP_WIDGET_TOGGLE = object()
    fake_gphoto2.GP_EVENT_FILE_ADDED = object()
    sys.modules["gphoto2"] = fake_gphoto2

from plugins.camera.nikon import NikonDSLRPlugin, NikonZPlugin
from plugins.camera.sony import SonyPlugin


def test_nikon_and_sony_plugins_report_no_vibration_capabilities():
    camera = object()
    log = lambda _message: None

    for plugin_class in (NikonDSLRPlugin, NikonZPlugin, SonyPlugin):
        plugin = plugin_class(camera, log)
        assert plugin.get_vibration_capabilities() == {}
