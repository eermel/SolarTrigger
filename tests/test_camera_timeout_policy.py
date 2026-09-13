from types import SimpleNamespace

from backend.camera_timeout_policy import camera_operation_timeout_s


def test_short_operation_keeps_base_timeout():
    assert (
        camera_operation_timeout_s(
            "read_info",
            (),
            {},
            30.0,
        )
        == 30.0
    )


def test_prepared_capture_can_exceed_base_timeout():
    prepared = SimpleNamespace(
        estimated_total_s=40.0,
        planned_count=10,
    )

    timeout = camera_operation_timeout_s(
        "trigger_prepared",
        (prepared,),
        {},
        30.0,
    )

    assert timeout > 40.0
    assert timeout > 30.0


def test_short_prepared_capture_never_reduces_base_timeout():
    prepared = SimpleNamespace(
        estimated_total_s=0.5,
        planned_count=1,
    )

    assert (
        camera_operation_timeout_s(
            "trigger_prepared",
            (prepared,),
            {},
            30.0,
        )
        == 30.0
    )


def test_short_speed_list_keeps_base_timeout():
    timeout = camera_operation_timeout_s(
        "shoot_speed_list",
        (["1/4000"] * 20,),
        {},
        30.0,
    )

    assert timeout == 30.0


def test_slowest_override_extends_timeout():
    normal = camera_operation_timeout_s(
        "shoot_speed_list",
        (["1/500", "1/250", "1/125"],),
        {},
        30.0,
    )

    extended = camera_operation_timeout_s(
        "shoot_speed_list",
        (["1/500", "1/250", "1/125"],),
        {"slowest_override_seconds": 20.0},
        30.0,
    )

    assert extended > normal
    assert extended > 60.0


def test_execute_photo_uses_shutter_and_expected_frames():
    timeout = camera_operation_timeout_s(
        "execute_photo",
        (
            {
                "shutter": "10",
                "expected_frames": 4,
            },
        ),
        {},
        30.0,
    )

    assert timeout > 40.0


def test_irregular_exposure_plan_is_summed():
    timeout = camera_operation_timeout_s(
        "execute_photo",
        (
            {
                "exposure_plan": [
                    {"shutter": "1", "frames": 2},
                    {"shutter": "4", "frames": 1},
                ]
            },
        ),
        {},
        30.0,
    )

    assert timeout >= 30.0
