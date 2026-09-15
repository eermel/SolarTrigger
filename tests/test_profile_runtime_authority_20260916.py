import json

import pytest

import backend.camera_profiles as camera_profiles
import plugins.camera as camera_plugins
from plugins.camera.profile import ProfilePlugin


def _profile(model="Sony ILCE-7M5"):
    return {
        "schema_version": 1,
        "config_type": "camera_profile",
        "manufacturer": "Sony",
        "model": model,
        "backend": "profile-sony-test",
        "strategy": "sequential",
        "commands": {
            "manual_mode": {"path": "/main/actions/expprogram", "value": "M"},
            "capture_target": {"path": "/main/settings/capturetarget", "value": "Memory card"},
            "raw": {"path": "/main/imgsettings/imagequality", "value": "RAW"},
            "iso": {"path": "/main/imgsettings/iso", "values": {"100": "100"}},
            "shutter": {
                "path": "/main/capturesettings/shutterspeed",
                "values": {"1/1000": "1/1000"},
            },
            "trigger_single": {"method": "trigger_capture"},
        },
        "brackets": {},
    }


def test_characterized_profile_has_priority_over_matching_legacy_plugin(monkeypatch):
    profile = _profile()

    class LegacySony:
        name = "legacy-sony"
        specificity = 999

        @staticmethod
        def matches(model):
            return True

        def __init__(self, camera, log_fn):
            raise AssertionError("legacy plugin must not be instantiated")

    monkeypatch.setattr(camera_plugins, "get_camera_model", lambda camera: profile["model"])
    monkeypatch.setattr(
        camera_profiles,
        "profile_for_model",
        lambda model: profile,
    )
    monkeypatch.setattr(
        camera_plugins,
        "_load_plugin_classes",
        lambda: [LegacySony],
    )

    selected = camera_plugins.load_plugin(object(), log_fn=lambda *_: None)

    assert isinstance(selected, ProfilePlugin)
    assert selected.profile["strategy"] == "sequential"
    assert selected.profile["backend"] == "profile-sony-test"


def test_profile_strategy_is_runtime_authority_for_bracket(monkeypatch):
    profile = _profile("Nikon Z9")
    profile["strategy"] = "bracket"
    profile["commands"]["capture_mode"] = {
        "path": "/main/capturesettings/capturemode",
        "value": "Single",
    }
    profile["brackets"] = {
        "3": {
            "step_ev": 1,
            "mode": "Bracket 3",
            "trigger": {"method": "widget", "path": "/main/actions/bulb", "value": 1},
            "total_ms": 1000,
            "atomic_ms": 1000,
        }
    }

    monkeypatch.setattr(camera_plugins, "get_camera_model", lambda camera: "Nikon Z9")
    monkeypatch.setattr(camera_profiles, "profile_for_model", lambda model: profile)
    monkeypatch.setattr(
        camera_plugins,
        "_load_plugin_classes",
        lambda: pytest.fail("legacy registry must not be consulted for characterized camera"),
    )

    selected = camera_plugins.load_plugin(object(), log_fn=lambda *_: None)

    assert isinstance(selected, ProfilePlugin)
    assert selected.profile["strategy"] == "bracket"
    assert "3" in selected.profile["brackets"]


def test_uncharacterized_camera_can_still_use_legacy_fallback(monkeypatch):
    events = []

    class Legacy:
        name = "legacy"
        specificity = 10

        @staticmethod
        def matches(model):
            return model == "Old Camera"

        def __init__(self, camera, log_fn):
            events.append("legacy")

    monkeypatch.setattr(camera_plugins, "get_camera_model", lambda camera: "Old Camera")
    monkeypatch.setattr(camera_profiles, "profile_for_model", lambda model: None)
    monkeypatch.setattr(camera_plugins, "_load_plugin_classes", lambda: [Legacy])

    selected = camera_plugins.load_plugin(object(), log_fn=lambda *_: None)

    assert isinstance(selected, Legacy)
    assert events == ["legacy"]


def test_timing_only_file_no_longer_marks_camera_characterized(tmp_path, monkeypatch):
    profile_dir = tmp_path / "camera_profiles"
    timing_dir = tmp_path / "camera_timing"
    profile_dir.mkdir()
    timing_dir.mkdir()

    (timing_dir / "legacy.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "config_type": "camera_timing",
                "backend": "nikon-z",
                "manufacturer": "Nikon",
                "model": "Nikon Z9",
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(camera_profiles, "PROFILE_DIR", profile_dir)

    assert camera_profiles.is_characterized_model("Nikon", "Nikon Z9") is False


def test_valid_profile_marks_camera_characterized(tmp_path, monkeypatch):
    profile_dir = tmp_path / "camera_profiles"
    profile_dir.mkdir()

    profile = _profile("Nikon Z9")
    profile["manufacturer"] = "Nikon"
    (profile_dir / "z9.json").write_text(
        json.dumps(profile),
        encoding="utf-8",
    )

    monkeypatch.setattr(camera_profiles, "PROFILE_DIR", profile_dir)

    assert camera_profiles.is_characterized_model("Nikon", "Nikon Z9") is True
    assert camera_profiles.is_characterized_model("Canon", "Nikon Z9") is False
