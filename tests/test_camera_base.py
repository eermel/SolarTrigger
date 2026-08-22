from types import SimpleNamespace

from plugins.camera.base import CameraPlugin


class DummyCameraPlugin(CameraPlugin):
    name = "dummy"

    @staticmethod
    def matches(model_string):
        return False

    def init_settings(self, aperture=None, iso=None, image_format="RAW",
                      white_balance="Daylight"):
        pass

    def set_exposure_settings(self, aperture=None, iso=None):
        pass

    def shoot_speeds(self, v_max, v_min, step_il, photo_num_start=0,
                     deadline=None):
        pass


def test_sync_datetime_defaults_to_unsupported():
    plugin = DummyCameraPlugin(camera=object(), log_fn=lambda message: None)
    ref = SimpleNamespace(timezone_name="Europe/Paris", utc_offset_minutes=120)

    result = plugin.sync_datetime(ref)

    assert result["status"] == "unsupported"
    assert result["datetime_synced"] is False
    assert result["timezone_synced"] is False
    assert result["datetime_applied"] is None
    assert result["timezone_name"] == ref.timezone_name
    assert result["utc_offset_minutes"] == ref.utc_offset_minutes
    assert result["message"]
    assert result["plugin"] == plugin.name


def test_sync_datetime_handles_missing_reference_values():
    plugin = DummyCameraPlugin(camera=object(), log_fn=lambda message: None)

    result = plugin.sync_datetime(None)

    assert result["timezone_name"] is None
    assert result["utc_offset_minutes"] is None
