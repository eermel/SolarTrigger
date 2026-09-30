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


class SnapshotPreflightCamera:
    """Camera stub exposing one full configuration tree for wake-up tests."""

    def __init__(self, *, iso="100", target="card", image_format="RAW"):
        self.get_config_calls = 0
        self.set_config_calls = 0
        self.get_single_config_calls = 0
        self.set_single_config_calls = 0

        self.mode = CountingWidget("mode", "M")
        self.target = CountingWidget("target", target)
        self.image_format = CountingWidget("format", image_format)
        self.iso = CountingWidget("iso", iso)
        self.shutter = CountingWidget("shutterspeed", "1/500")
        self.capture_mode = CountingWidget("capturemode", "Single Shot")
        self.root = CountingWidget(
            "main",
            None,
            children=[
                self.mode,
                self.target,
                self.image_format,
                self.iso,
                self.shutter,
                self.capture_mode,
            ],
        )

    def get_config(self):
        self.get_config_calls += 1
        return self.root

    def set_config(self, config):
        assert config is self.root
        self.set_config_calls += 1

    def get_single_config(self, name):
        self.get_single_config_calls += 1
        return {
            "iso": self.iso,
            "shutterspeed": self.shutter,
            "capturemode": self.capture_mode,
        }[name]

    def set_single_config(self, name, node):
        expected = {
            "iso": self.iso,
            "shutterspeed": self.shutter,
            "capturemode": self.capture_mode,
        }[name]
        assert node is expected
        self.set_single_config_calls += 1


def profile_with_direct_iso_writer():
    profile = profile_without_timing_contract()
    profile["commands"]["iso"].update({
        "name": "iso",
        "writer": "single_config",
        "get": True,
        "set": True,
    })
    return profile


def make_direct_plugin(camera):
    return ProfilePlugin(
        camera,
        log_fn=lambda _msg: None,
        profile=profile_with_direct_iso_writer(),
    )


def test_wake_state_reads_one_snapshot_and_never_sets_camera():
    camera = SnapshotPreflightCamera()
    plugin = make_plugin(camera)

    state = plugin.wake_state()

    assert camera.get_config_calls == 1
    assert camera.set_config_calls == 0
    assert camera.set_single_config_calls == 0
    assert state["iso"] == "100"
    assert state["shutter"] == "1/500"
    assert state["capture_mode"] == "Single Shot"
    assert plugin._known_settings["iso"] == "100"
    assert plugin._known_settings["shutter"] == "1/500"


def test_preflight_reuses_single_wake_snapshot_when_camera_is_ready():
    camera = SnapshotPreflightCamera()
    plugin = make_plugin(camera)

    result = plugin.preflight({"iso": "100"})

    assert result["ok"] is True
    assert result["changed"] == []
    assert camera.get_config_calls == 1
    assert camera.set_config_calls == 0
    assert plugin._known_settings["iso"] == "100"


def test_direct_runtime_set_uses_cache_and_only_sets_on_value_change():
    camera = SnapshotPreflightCamera()
    plugin = make_direct_plugin(camera)

    result = plugin.preflight({"iso": "100"})
    assert result["ok"] is True
    assert camera.get_config_calls == 1

    full_reads = camera.get_config_calls
    direct_sets = camera.set_single_config_calls

    assert plugin._apply("iso", "100") is False
    assert camera.get_config_calls == full_reads
    assert camera.set_single_config_calls == direct_sets

    assert plugin._apply("iso", "200") is True
    assert camera.get_config_calls == full_reads
    assert camera.set_single_config_calls == direct_sets + 1
    assert plugin._known_settings["iso"] == "200"


def test_preflight_changed_direct_value_uses_snapshot_set_and_readback():
    camera = SnapshotPreflightCamera(iso="100")
    plugin = make_direct_plugin(camera)

    result = plugin.preflight({"iso": "200"})

    assert result["ok"] is True
    assert result["changed"] == ["iso"]
    # One wake snapshot + one authoritative full-tree readback after the SET.
    assert camera.get_config_calls == 2
    assert camera.set_single_config_calls == 1
    assert camera.iso.get_value() == "200"
    assert plugin._known_settings["iso"] == "200"


def test_tstart_init_reuses_authoritative_hardware_preflight_snapshot():
    camera = SnapshotPreflightCamera()
    plugin = make_direct_plugin(camera)

    preflight = plugin.preflight({"iso": "100"})
    assert preflight["ok"] is True
    assert camera.get_config_calls == 1
    primed_reads = camera.get_single_config_calls

    result = plugin.init_settings(
        aperture=None,
        iso="100",
        image_format="RAW",
        white_balance=None,
    )

    assert result["ok"] is True
    assert result["changed"] == []
    assert camera.get_config_calls == 1
    assert camera.get_single_config_calls == primed_reads
    assert camera.set_single_config_calls == 0


def test_init_without_prior_hardware_preflight_still_wakes_once():
    camera = SnapshotPreflightCamera()
    plugin = make_direct_plugin(camera)

    result = plugin.init_settings(
        aperture=None,
        iso="100",
        image_format="RAW",
        white_balance=None,
    )

    assert result["ok"] is True
    assert camera.get_config_calls == 1
    assert plugin._wake_state_valid is True


def test_run_teardown_invalidates_wake_snapshot_before_next_init():
    camera = SnapshotPreflightCamera()
    plugin = make_direct_plugin(camera)

    plugin.preflight({"iso": "100"})
    assert camera.get_config_calls == 1
    assert plugin._wake_state_valid is True

    plugin.clear_runtime_state()

    assert plugin._wake_state_valid is False
    assert plugin._known_settings == {}
    assert plugin._writable_cache == set()
    assert plugin._single_config_widgets == {}

    plugin.init_settings(
        aperture=None,
        iso="100",
        image_format="RAW",
        white_balance=None,
    )
    assert camera.get_config_calls == 2


def test_capture_failure_invalidates_wake_snapshot(monkeypatch):
    camera = SnapshotPreflightCamera()
    plugin = make_direct_plugin(camera)
    plugin.preflight({"iso": "100"})
    assert plugin._wake_state_valid is True

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
            RuntimeError("simulated runtime SET failure")
        ),
    )

    prepared = SimpleNamespace(planned_count=0, target_time=None)
    with pytest.raises(RuntimeError, match="simulated runtime SET failure"):
        plugin.trigger_prepared(prepared)

    assert plugin._wake_state_valid is False
