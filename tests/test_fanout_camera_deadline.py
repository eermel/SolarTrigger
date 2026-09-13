from scripts.fanout_camera_adapter import FanoutCameraAdapter


class _DummyIpc:
    pass


def test_capture_result_preserves_deadline_detail():
    adapter = FanoutCameraAdapter(_DummyIpc(), log_fn=lambda _message: None)
    try:
        result = adapter._capture_result(
            "trigger_prepared",
            [(1, {"frames": 0, "planned": 3, "detail": "deadline"})],
        )
    finally:
        adapter.close()

    assert result.frames == 0
    assert result.planned == 3
    assert result.detail == "deadline"


def test_capture_result_deadline_wins_across_rigs():
    adapter = FanoutCameraAdapter(_DummyIpc(), log_fn=lambda _message: None)
    try:
        result = adapter._capture_result(
            "trigger_prepared",
            [
                (1, {"frames": 3, "planned": 3, "detail": None}),
                (2, {"frames": 0, "planned": 3, "detail": "deadline"}),
            ],
        )
    finally:
        adapter.close()

    assert result.frames == 3
    assert result.planned == 3
    assert result.detail == "deadline"


def test_capture_result_keeps_fanout_detail_without_deadline():
    adapter = FanoutCameraAdapter(_DummyIpc(), log_fn=lambda _message: None)
    try:
        result = adapter._capture_result(
            "trigger_prepared",
            [(1, {"frames": 3, "planned": 3, "detail": None})],
        )
    finally:
        adapter.close()

    assert result.detail == "fanout"
