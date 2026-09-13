"""Regression tests for camera plugin routing."""

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

    def init_settings(
        self,
        aperture=None,
        iso=None,
        image_format="RAW",
        white_balance="Daylight",
    ):
        return None

    def set_exposure_settings(self, aperture=None, iso=None):
        return None



def test_characterized_camera_prefers_specialized_plugin(monkeypatch):
    profile = {
        "backend": "profile-test",
        "model": "Sony ILCE-7M5 (PC Control)",
    }

    monkeypatch.setattr(
        camera_plugins,
        "get_camera_model",
        lambda _camera: "Sony ILCE-7M5 (PC Control)",
    )
    monkeypatch.setattr(
        camera_plugins,
        "_load_plugin_classes",
        lambda: [_Specialized],
    )

    import backend.camera_profiles as camera_profiles

    monkeypatch.setattr(
        camera_profiles,
        "profile_for_model",
        lambda _model: profile,
    )

    plugin = camera_plugins.load_plugin(_FakeCamera(), log_fn=lambda _msg: None)

    assert isinstance(plugin, _Specialized)


def test_characterized_camera_uses_profile_plugin_only_without_specialized_match(
    monkeypatch,
):
    profile = {
        "backend": "profile-fallback",
        "model": "Generic Test Camera",
    }

    monkeypatch.setattr(
        camera_plugins,
        "get_camera_model",
        lambda _camera: "Generic Test Camera",
    )
    monkeypatch.setattr(
        camera_plugins,
        "_load_plugin_classes",
        lambda: [],
    )

    import backend.camera_profiles as camera_profiles
    import plugins.camera.profile as profile_module

    monkeypatch.setattr(
        camera_profiles,
        "profile_for_model",
        lambda _model: profile,
    )

    class _Fallback:
        def __init__(self, camera, log_fn, selected_profile):
            self.camera = camera
            self.profile = selected_profile

    monkeypatch.setattr(profile_module, "ProfilePlugin", _Fallback)

    plugin = camera_plugins.load_plugin(_FakeCamera(), log_fn=lambda _msg: None)

    assert isinstance(plugin, _Fallback)


