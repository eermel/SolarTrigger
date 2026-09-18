"""Regression tests for ProfilePlugin's positive-only writable cache."""
from types import SimpleNamespace

import pytest

from plugins.camera.profile import ProfilePlugin


class CountingWidget:
    def __init__(self, name, value, readonly=False, children=()):
        self.name = name
        self.value = value
        self.readonly = readonly
        self.children = list(children)

    def get_name(self):
        return self.name

    def get_value(self):
        return self.value

    def set_value(self, value):
        self.value = value

    def get_readonly(self):
        return self.readonly

    def get_child_by_name(self, name):
        return next(child for child in self.children if child.name == name)


class CountingCamera:
    def __init__(self):
        self.get_config_calls = 0
        self.set_config_calls = 0
        self.fail_next_set = False
        self.iso = CountingWidget("iso", "100")
        self.shutter = CountingWidget("shutterspeed", "1/500")
        self.capture_mode = CountingWidget("capturemode", "Single Shot")
        self.root = CountingWidget(
            "main",
            None,
            children=[self.iso, self.shutter, self.capture_mode],
        )

    def get_config(self):
        self.get_config_calls += 1
        return self.root

    def set_config(self, config):
        self.set_config_calls += 1
        if self.fail_next_set:
            self.fail_next_set = False
            raise RuntimeError("simulated SET failure")


def profile_without_timing_contract():
    """Legacy Sony-like profile: runtime SET uses write_widget()."""
    return {
        "schema_version": 1,
        "config_type": "camera_profile",
        "backend": "profile-writable-cache-example",
        "manufacturer": "Example",
        "model": "Example Camera",
        "strategy": "bracket",
        "commands": {
            "manual_mode": {"path": "/main/mode", "value": "M"},
            "capture_target": {"path": "/main/target", "value": "card"},
            "raw": {"path": "/main/format", "value": "RAW"},
            "iso": {
                "path": "/main/iso",
                "values": {"100": "100", "200": "200"},
            },
            "shutter": {
                "path": "/main/shutterspeed",
                "values": {"1/500": "1/500", "1/1000": "1/1000"},
            },
            "capture_mode": {
                "path": "/main/capturemode",
                "value": "Single Shot",
                "values": {
                    "Single Shot": "Single Shot",
                    "Continuous Bracket 1 EV 3 Img.": (
                        "Continuous Bracket 1 EV 3 Img."
                    ),
                },
            },
            "trigger_single": {"method": "trigger_capture"},
        },
        "brackets": {
            "3": {
                "step_ev": 1,
                "mode": "Continuous Bracket 1 EV 3 Img.",
                "trigger": {"method": "trigger_capture"},
                "total_ms": 3000,
                "atomic_ms": 3000,
            }
        },
    }


def make_plugin(camera=None):
    return ProfilePlugin(
        camera or CountingCamera(),
        log_fn=lambda _msg: None,
        profile=profile_without_timing_contract(),
    )


def test_positive_writable_probe_is_reused_on_later_set():
    camera = CountingCamera()
    plugin = make_plugin(camera)

    assert plugin._apply("iso", "200") is True
    assert camera.get_config_calls == 2  # readonly probe + write fetch
    assert "iso" in plugin._writable_cache

    assert plugin._apply("iso", "100") is True
    assert camera.get_config_calls == 3  # write fetch only
    assert camera.set_config_calls == 2


def test_live_readonly_result_is_never_cached():
    camera = CountingCamera()
    camera.iso.readonly = True
    plugin = make_plugin(camera)

    assert plugin._live_writable("iso") is False
    assert plugin._live_writable("iso") is False

    assert camera.get_config_calls == 2
    assert "iso" not in plugin._writable_cache


def test_successful_mode_change_does_not_evict_other_positive_capabilities():
    camera = CountingCamera()
    plugin = make_plugin(camera)

    assert plugin._apply("shutter", "1/1000") is True
    assert "shutter" in plugin._writable_cache

    assert plugin._apply(
        "capture_mode", "Continuous Bracket 1 EV 3 Img."
    ) is True
    assert "shutter" in plugin._writable_cache

    # Force a real shutter SET after the mode transition. Since a previous
    # successful SET already proved this key writable, only write_widget's
    # own get_config() is allowed here; no dedicated readonly GET is repeated.
    plugin._known_settings.pop("shutter", None)
    before = camera.get_config_calls
    assert plugin._apply("shutter", "1/500") is True
    assert camera.get_config_calls == before + 1


def test_failed_set_evicts_positive_capability_and_next_attempt_reprobes():
    camera = CountingCamera()
    plugin = make_plugin(camera)

    assert plugin._apply("iso", "200") is True
    assert "iso" in plugin._writable_cache

    camera.fail_next_set = True
    with pytest.raises(RuntimeError, match="simulated SET failure"):
        plugin._apply("iso", "100")

    assert "iso" not in plugin._writable_cache
    plugin._known_settings.pop("iso", None)

    before = camera.get_config_calls
    assert plugin._apply("iso", "100") is True
    assert camera.get_config_calls == before + 2  # probe + write fetch
    assert "iso" in plugin._writable_cache


def test_capture_group_failure_clears_state_and_writable_caches(monkeypatch):
    plugin = make_plugin()
    plugin._known_settings["iso"] = "100"
    plugin._writable_cache.add("iso")

    operation = {"action": "set", "parameter": "iso", "value": "200"}
    monkeypatch.setattr(
        plugin,
        "audit_prepared_capture",
        lambda _prepared: (operation,),
    )
    monkeypatch.setattr(
        plugin,
        "_capture_groups",
        lambda _operations: ((operation,),),
    )
    monkeypatch.setattr(
        plugin,
        "_effective_capture_group",
        lambda group: group,
    )
    monkeypatch.setattr(
        plugin,
        "set_parameter",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("simulated group failure")
        ),
    )

    prepared = SimpleNamespace(planned_count=0, target_time=None)
    with pytest.raises(RuntimeError, match="simulated group failure"):
        plugin.trigger_prepared(prepared)

    assert plugin._known_settings == {}
    assert plugin._writable_cache == set()
