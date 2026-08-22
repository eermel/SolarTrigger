from types import SimpleNamespace

from plugins.camera.base import CameraPlugin, CaptureResult
from services.camera_service import CaptureIntent


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


class RecordingCameraPlugin(DummyCameraPlugin):
    def __init__(self):
        super().__init__(camera=object(), log_fn=lambda message: None)
        self.calls = []

    def shoot_speeds(self, v_max, v_min, step_il, photo_num_start=0,
                     deadline=None):
        self.calls.append(
            ("speeds", v_max, v_min, step_il, photo_num_start, deadline)
        )
        planned = 1 if v_max == v_min else 3
        return CaptureResult(frames=planned, planned=planned, detail="recorded")


def capture_intent(*, speeds, deadline):
    return CaptureIntent(
        shutter_min=None,
        shutter_max=None,
        step_ev=None,
        speeds=speeds,
        phase="C2",
        target_time=None,
        deadline=deadline,
        overflow_policy=None,
    )


def assert_same_result(actual, expected):
    assert actual.frames == expected.frames
    assert actual.planned == expected.planned
    assert actual.detail == expected.detail


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


def test_default_prepare_and_trigger_matches_direct_single_capture():
    deadline = object()
    plugin = RecordingCameraPlugin()

    prepared = plugin.prepare_capture(
        capture_intent(speeds=["1/1000"], deadline=deadline)
    )

    assert plugin.calls == []
    assert prepared.planned_count == 1
    actual = plugin.trigger_prepared(prepared)

    direct_plugin = RecordingCameraPlugin()
    expected = direct_plugin.shoot_single("1/1000", deadline=deadline)
    assert_same_result(actual, expected)
    assert plugin.calls == direct_plugin.calls


def test_default_prepare_and_trigger_matches_direct_regular_bracket():
    prepared_deadline = object()
    override_deadline = object()
    plugin = RecordingCameraPlugin()

    prepared = plugin.prepare_capture(
        capture_intent(
            speeds=["1/250", "1/1000", "1/500"],
            deadline=prepared_deadline,
        )
    )

    assert plugin.calls == []
    assert prepared.planned_count == 3
    actual = plugin.trigger_prepared(prepared, deadline=override_deadline)

    direct_plugin = RecordingCameraPlugin()
    expected = direct_plugin.shoot_speeds(
        "1/1000", "1/250", 1.0, deadline=override_deadline
    )
    assert_same_result(actual, expected)
    assert plugin.calls == direct_plugin.calls
