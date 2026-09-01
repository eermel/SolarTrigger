import sys
import types

import pytest

if "gphoto2" not in sys.modules:
    fake_gphoto2 = types.ModuleType("gphoto2")
    fake_gphoto2.GPhoto2Error = RuntimeError
    fake_gphoto2.GP_WIDGET_TOGGLE = object()
    fake_gphoto2.GP_EVENT_FILE_ADDED = object()
    fake_gphoto2.GP_EVENT_TIMEOUT = object()
    sys.modules["gphoto2"] = fake_gphoto2

from plugins.camera.nikon import NikonDSLRPlugin, NikonZPlugin


class FakeWidget:
    def __init__(self, value=None, *, readonly=False):
        self.value = value
        self.readonly = readonly
        self.set_calls = []

    def get_readonly(self):
        return self.readonly

    def set_value(self, value):
        self.set_calls.append(value)
        self.value = value


class FakeConfig:
    def __init__(self, widgets):
        self.widgets = widgets

    def get_child_by_name(self, name):
        if name not in self.widgets:
            raise KeyError(name)
        return self.widgets[name]


class FakeCamera:
    def __init__(self, widgets):
        self.config = FakeConfig(widgets)
        self.set_config_calls = 0
        self.trigger_calls = 0

    def get_config(self):
        return self.config

    def set_config(self, config):
        assert config is self.config
        self.set_config_calls += 1

    def trigger_capture(self):
        self.trigger_calls += 1


def make_plugin(widgets):
    camera = FakeCamera(widgets)
    plugin = NikonZPlugin(camera)
    plugin.log = lambda _message: None
    return plugin


@pytest.mark.parametrize(
    "model",
    [
        "Nikon Z 9",
        "NIKON Z 8",
        "Nikon Z6",
        "Nikon Z7 II",
        "Nikon Zf",
    ],
)
def test_nikon_z_matches_z_models(model):
    assert NikonZPlugin.matches(model)


def test_nikon_z_does_not_match_d850():
    assert not NikonZPlugin.matches("Nikon DSC D850")
    assert NikonDSLRPlugin.matches("Nikon DSC D850")


def test_nikon_z_sets_shutterspeed2_when_writable():
    speed = FakeWidget("1/125", readonly=False)
    plugin = make_plugin({"shutterspeed2": speed})

    assert plugin._set_speed("1/1000") is True

    assert speed.value == "1/1000"
    assert speed.set_calls == ["1/1000"]
    assert plugin.camera.set_config_calls == 1


def test_nikon_z_falls_back_to_shutterspeed():
    speed = FakeWidget("1/125", readonly=False)
    plugin = make_plugin({"shutterspeed": speed})

    assert plugin._set_speed("1/2000") is True

    assert speed.value == "1/2000"
    assert plugin.camera.set_config_calls == 1


def test_nikon_z_falls_back_when_shutterspeed2_is_read_only():
    speed2 = FakeWidget("1/125", readonly=True)
    speed = FakeWidget("1/125", readonly=False)
    plugin = make_plugin(
        {
            "shutterspeed2": speed2,
            "shutterspeed": speed,
        }
    )

    assert plugin._set_speed("1/4000") is True

    assert speed2.set_calls == []
    assert speed.set_calls == ["1/4000"]


def test_nikon_z_read_only_shutter_fails_explicitly():
    plugin = make_plugin(
        {
            "shutterspeed2": FakeWidget("1", readonly=True),
            "shutterspeed": FakeWidget("1", readonly=True),
        }
    )

    with pytest.raises(RuntimeError, match="manual exposure mode"):
        plugin._set_speed("1/1000")


def test_nikon_z_missing_shutter_control_fails_explicitly():
    plugin = make_plugin({})

    with pytest.raises(RuntimeError, match="cannot be applied"):
        plugin._set_speed("1/1000")


def test_nikon_z_shoot_single_sets_speed_then_triggers():
    speed = FakeWidget("1/125", readonly=False)
    plugin = make_plugin({"shutterspeed2": speed})

    result = plugin.shoot_single("1/1000")

    assert result.frames == 1
    assert result.planned == 1
    assert speed.set_calls == ["1/1000"]
    assert plugin.camera.trigger_calls == 1
