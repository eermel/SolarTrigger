from types import SimpleNamespace

import pytest

from backend.camera_timing_contract import SAFETY_POLICY
from plugins.camera.profile import (
    CameraPhysicalPreflightError,
    CameraPreflightError,
    ProfilePlugin,
)


class Node:
    def __init__(self, name, value):
        self.name = name
        self.value = value

    def get_value(self):
        return self.value

    def set_value(self, value):
        self.value = value


class DirectCamera:
    def __init__(self):
        self.nodes = {
            "iso": Node("iso", "100"),
            "shutterspeed": Node("shutterspeed", "1/500"),
            "capturemode": Node("capturemode", "Single Shot"),
            "capture": Node("capture", 0),
        }
        self.get_single_calls = []
        self.set_single_calls = []
        self.full_get_calls = 0

    def get_single_config(self, name):
        self.get_single_calls.append(name)
        return self.nodes[name]

    def set_single_config(self, name, node):
        assert self.nodes[name] is node
        self.set_single_calls.append((name, node.get_value()))

    def get_config(self):
        self.full_get_calls += 1
        raise AssertionError("timed runtime must not call get_config")


def profile(strategy="sequential"):
    brackets = {}
    if strategy == "bracket":
        brackets = {
            "3": {
                "step_ev": 1,
                "mode": "Bracket 3",
                "trigger": {
                    "method": "widget",
                    "path": "/main/actions/capture",
                    "name": "capture",
                    "writer": "single_config",
                    "value": 1,
                    "release": 0,
                },
                "shutter_requires_single_mode": False,
            }
        }
    return {
        "schema_version": 1,
        "config_type": "camera_profile",
        "backend": "profile-direct-state-test",
        "manufacturer": "Test",
        "model": "Direct Camera",
        "strategy": strategy,
        "commands": {
            "manual_mode": {"path": "/main/mode", "value": "M"},
            "capture_target": {"path": "/main/target", "value": "card"},
            "raw": {"path": "/main/format", "value": "RAW"},
            "iso": {
                "path": "/main/iso",
                "name": "iso",
                "writer": "single_config",
                "get": True,
                "set": True,
                "values": {"100": "100", "200": "200"},
            },
            "shutter": {
                "path": "/main/shutterspeed",
                "name": "shutterspeed",
                "writer": "single_config",
                "get": True,
                "set": True,
                "values": {
                    "1/1000": "1/1000",
                    "1/500": "1/500",
                    "1/250": "1/250",
                },
            },
            "capture_mode": {
                "path": "/main/capturemode",
                "name": "capturemode",
                "writer": "single_config",
                "get": True,
                "set": True,
                "value": "Single Shot",
                "values": {
                    "Single Shot": "Single Shot",
                    "Bracket 3": "Bracket 3",
                },
                "invalidates": [],
            },
            "trigger_single": {"method": "trigger_capture"},
        },
        "brackets": brackets,
        "timing_contract": {
            "version": 3,
            "safety_policy": dict(SAFETY_POLICY),
            "set_overhead_ms": 100,
            "single_overhead_ms": 100,
            "prepare_lead_ms": 300,
            "bracket_overhead_ms": 100 if strategy == "bracket" else 0,
            "bracket_inter_image_ms": 50 if strategy == "bracket" else 0,
            "supported_bracket_frames": [3] if strategy == "bracket" else [],
        },
    }


def prime(plugin, *names):
    for name in names:
        plugin._single_config_widgets[name] = plugin.camera.nodes[name]




def test_direct_preflight_set_failure_is_not_reported_as_physical_action(monkeypatch):
    data = profile("bracket")
    data["commands"]["capture_mode"].update({
        "name": "capturemode",
        "writer": "single_config",
        "set": True,
    })
    plugin = ProfilePlugin(None, log_fn=lambda _message: None, profile=data)

    reads = iter(["Bracket 3", "Bracket 3"])
    monkeypatch.setattr(plugin, "_preflight_read", lambda _key: next(reads))
    monkeypatch.setattr(plugin, "_prime_single_spec", lambda _spec: None)

    def fail_direct_set(_spec, _target):
        raise RuntimeError("[-2] Bad parameters")

    monkeypatch.setattr(plugin, "_direct_set_spec", fail_direct_set)

    with pytest.raises(CameraPreflightError, match="USB preflight SET failed") as error:
        plugin._ensure("capture_mode")

    assert not isinstance(error.value, CameraPhysicalPreflightError)
    assert "Bad parameters" in str(error.value)
    assert "single-shot release mode" not in str(error.value)


def test_direct_preflight_uses_authoritative_full_readback_after_set(monkeypatch):
    data = profile("bracket")
    plugin = ProfilePlugin(None, log_fn=lambda _message: None, profile=data)

    reads = iter(["Bracket 3", "Single Shot"])
    monkeypatch.setattr(plugin, "_preflight_read", lambda _key: next(reads))
    monkeypatch.setattr(plugin, "_prime_single_spec", lambda _spec: None)

    calls = []
    monkeypatch.setattr(
        plugin,
        "_direct_set_spec",
        lambda _spec, target: calls.append(target),
    )
    monkeypatch.setattr(
        plugin,
        "_read",
        lambda _key: (_ for _ in ()).throw(
            AssertionError("preflight must use authoritative full GET")
        ),
    )

    assert plugin._ensure("capture_mode") is True
    assert calls == ["Single Shot"]
    assert plugin._known_settings["capture_mode"] == "Single Shot"


def test_get_only_preflight_mismatch_is_explicitly_physical(monkeypatch):
    data = profile("bracket")
    data["commands"]["capture_mode"].update({
        "name": "capturemode",
        "writer": "single_config",
        "set": False,
    })
    plugin = ProfilePlugin(None, log_fn=lambda _message: None, profile=data)
    monkeypatch.setattr(plugin, "_preflight_read", lambda _key: "Bracket 3")

    with pytest.raises(CameraPhysicalPreflightError, match="single-shot release mode"):
        plugin._ensure("capture_mode")

def test_timed_set_uses_single_config_only_and_skips_unchanged_value():
    camera = DirectCamera()
    plugin = ProfilePlugin(camera, profile=profile())
    prime(plugin, "iso", "shutterspeed", "capturemode")
    plugin._known_settings["iso"] = "100"

    assert plugin._apply("iso", "100") is False
    assert camera.set_single_calls == []
    assert camera.full_get_calls == 0

    assert plugin._apply("iso", "200") is True
    assert camera.set_single_calls == [("iso", "200")]
    assert camera.full_get_calls == 0


def test_timed_direct_set_fails_closed_if_preflight_did_not_prime_writer():
    plugin = ProfilePlugin(DirectCamera(), profile=profile())
    with pytest.raises(Exception, match="not primed"):
        plugin._apply("iso", "200")


def test_manual_aperture_is_never_a_runtime_failure_or_set():
    p = profile()
    p["commands"]["aperture"] = {
        "path": "/main/f-number",
        "get": True,
        "set": False,
        "values": {"f/8": "f/8"},
    }
    camera = DirectCamera()
    plugin = ProfilePlugin(camera, profile=p)
    assert plugin._apply("aperture", "f/8") is False
    assert camera.set_single_calls == []
    assert camera.full_get_calls == 0


def test_repeated_native_bracket_with_known_state_reduces_to_photo_only():
    camera = DirectCamera()
    plugin = ProfilePlugin(camera, profile=profile("bracket"))
    plugin._known_settings.update({
        "iso": "100",
        "capture_mode": "Bracket 3",
        "shutter": "1/500",
    })
    group = (
        {"action": "set", "parameter": "iso", "value": "100", "duration_ms": 100},
        {"action": "set", "parameter": "capturemode", "value": "Bracket 3", "duration_ms": 100},
        {"action": "set", "parameter": "shutterspeed", "value": "1/500", "duration_ms": 100},
        {"action": "bracket_press", "frames": 3, "duration_ms": 500},
    )
    effective = plugin._effective_capture_group(group)
    assert [op["action"] for op in effective] == ["bracket_press"]


def test_capture_mode_dependency_invalidates_only_characterized_settings():
    p = profile("bracket")
    p["commands"]["capture_mode"]["invalidates"] = ["shutter"]
    camera = DirectCamera()
    plugin = ProfilePlugin(camera, profile=p)
    prime(plugin, "capturemode", "shutterspeed")
    plugin._known_settings.update({
        "capture_mode": "Single Shot",
        "shutter": "1/500",
    })

    assert plugin._apply("capture_mode", "Bracket 3") is True
    assert "shutter" not in plugin._known_settings
    assert plugin._apply("shutter", "1/500") is True
    assert camera.set_single_calls == [
        ("capturemode", "Bracket 3"),
        ("shutterspeed", "1/500"),
    ]
