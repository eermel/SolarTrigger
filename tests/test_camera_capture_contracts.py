from datetime import datetime

from services.camera_service import CaptureIntent, PreparedCapture


def test_capture_intent_can_be_instantiated_with_representative_values():
    target_time = datetime(2026, 8, 12, 17, 46, 12)
    intent = CaptureIntent(
        shutter_min=None,
        shutter_max="1/1000",
        step_ev=None,
        speeds=["1/2000", "1/1000"],
        phase="C2",
        target_time=target_time,
        deadline=None,
        overflow_policy="truncate",
    )

    assert intent.shutter_min is None
    assert intent.shutter_max == "1/1000"
    assert intent.step_ev is None
    assert intent.speeds == ["1/2000", "1/1000"]
    assert isinstance(intent.speeds, list)
    assert isinstance(intent.phase, str)
    assert intent.target_time is target_time
    assert isinstance(intent.target_time, datetime)
    assert intent.deadline is None
    assert intent.overflow_policy == "truncate"


def test_prepared_capture_can_be_instantiated_with_representative_values():
    token = {"sequence_id": 42}
    prepared = PreparedCapture(
        token=token,
        estimated_total_s=1.75,
        exposures_s=None,
        planned_count=2,
        plugin_name="representative-plugin",
    )

    assert prepared.token is token
    assert isinstance(prepared.estimated_total_s, float)
    assert prepared.estimated_total_s == 1.75
    assert prepared.exposures_s is None
    assert isinstance(prepared.planned_count, int)
    assert prepared.planned_count == 2
    assert isinstance(prepared.plugin_name, str)
    assert prepared.plugin_name == "representative-plugin"
