"""Regression tests for profile-only production camera routing."""

import plugins.camera as camera_plugins


class _FakeCamera:
    pass


class _Specialized:
    name = "specialized"
    specificity = 999

    def __init__(self, camera, log_fn=print):
        self.camera = camera
        self.log = log_fn

    @staticmethod
    def matches(model_string):
        return model_string == "Sony ILCE-7M5 (PC Control)"


def test_characterized_camera_uses_profile_plugin_even_if_reference_exists(monkeypatch):
    profile = {
        "backend": "profile-test",
        "model": "Sony ILCE-7M5 (PC Control)",
    }

    monkeypatch.setattr(
        camera_plugins,
        "get_camera_model",
        lambda _camera: "Sony ILCE-7M5 (PC Control)",
    )

    import backend.camera_profiles as camera_profiles
    import plugins.camera.profile as profile_module

    monkeypatch.setattr(camera_profiles, "profile_for_model", lambda _model: profile)

    class _Profile:
        def __init__(self, camera, log_fn, selected_profile):
            self.camera = camera
            self.profile = selected_profile

    monkeypatch.setattr(profile_module, "ProfilePlugin", _Profile)
    monkeypatch.setattr(
        camera_plugins,
        "_load_reference_plugin_classes",
        lambda: (_ for _ in ()).throw(AssertionError("production must not inspect reference plugins")),
    )

    plugin = camera_plugins.load_plugin(_FakeCamera(), log_fn=lambda _msg: None)

    assert isinstance(plugin, _Profile)
    assert plugin.profile is profile


def test_uncharacterized_camera_fails_closed_without_reference_fallback(monkeypatch):
    messages = []
    monkeypatch.setattr(
        camera_plugins,
        "get_camera_model",
        lambda _camera: "Sony ILCE-7M5 (PC Control)",
    )

    import backend.camera_profiles as camera_profiles

    monkeypatch.setattr(camera_profiles, "profile_for_model", lambda _model: None)
    monkeypatch.setattr(
        camera_plugins,
        "_load_reference_plugin_classes",
        lambda: (_ for _ in ()).throw(AssertionError("production must not fall back to reference plugins")),
    )

    plugin = camera_plugins.load_plugin(_FakeCamera(), log_fn=messages.append)

    assert plugin is None
    assert any("production runtime disabled" in message for message in messages)


def test_reference_plugin_requires_explicit_reference_loader(monkeypatch):
    monkeypatch.setattr(
        camera_plugins,
        "get_camera_model",
        lambda _camera: "Sony ILCE-7M5 (PC Control)",
    )
    monkeypatch.setattr(
        camera_plugins,
        "_load_reference_plugin_classes",
        lambda: [_Specialized],
    )

    plugin = camera_plugins.load_reference_plugin(
        _FakeCamera(), log_fn=lambda _msg: None
    )

    assert isinstance(plugin, _Specialized)
