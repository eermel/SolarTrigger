from datetime import datetime, timezone

import pytest

from backend.camera_timing_contract import SAFETY_POLICY
from backend.camera_validation import (
    analyse_validation,
    build_validation_recipe,
    materialize_validation_plan,
)
from backend.execution_plan_runtime import load_execution_plan


def _profile():
    shutters = [
        "1/8000", "1/4000", "1/2000", "1/1000", "1/500",
        "1/250", "1/125", "1/60", "1/30",
    ]
    return {
        "schema_version": 1,
        "config_type": "camera_profile",
        "backend": "profile-test_camera_1234abcd",
        "manufacturer": "Test",
        "model": "Test Camera",
        "characterized_at": "2026-09-07T08:00:00+00:00",
        "strategy": "bracket",
        "commands": {
            "manual_mode": {"path": "/main/mode", "value": "M", "get": True, "set": False},
            "capture_target": {"path": "/main/target", "value": "card+sdram", "get": True, "set": True},
            "raw": {"path": "/main/raw", "value": "RAW", "get": True, "set": True},
            "iso": {
                "path": "/main/iso", "get": True, "set": True,
                "values": {"100": "100", "200": "200", "400": "400"},
            },
            "capture_mode": {"path": "/main/drive", "value": "Single", "get": True, "set": True},
            "shutter": {
                "path": "/main/shutter", "get": True, "set": True,
                "values": {value: value for value in shutters},
            },
            "aperture": {
                "path": "/main/aperture", "get": True, "set": True,
                "values": {"f/5.6": "f/5.6", "f/8": "f/8", "f/11": "f/11"},
            },
            "trigger_single": {"method": "trigger_capture"},
        },
        "brackets": {
            str(frames): {
                "step_ev": 1,
                "mode": f"Bracket {frames}",
                "trigger": {"method": "widget", "path": "/main/capture", "value": 1, "release": 0},
            }
            for frames in (3, 5, 7, 9)
        },
        "warnings": [],
        "timing_contract": {
            "version": 3,
            "safety_policy": dict(SAFETY_POLICY),
            "set_overhead_ms": 3000,
            "single_overhead_ms": 600,
            "bracket_overhead_ms": 900,
            "bracket_inter_image_ms": 150,
            "supported_bracket_frames": [3, 5, 7, 9],
        },
    }


def _successful_sets(recipe):
    return [
        {
            "parameter": command["params"]["parameter"],
            "value": command["params"]["value"],
            "status": "success",
            "duration_ms": min(1.0, command["duration_ms"]),
            "budget_ms": command["duration_ms"],
        }
        for command in recipe["commands"]
        if command["action"] == "SET"
    ]


def test_recipe_is_deterministic_and_covers_all_brackets():
    first = build_validation_recipe(_profile())
    second = build_validation_recipe(_profile())

    assert first == second
    assert first["expected_photos"] == 28
    assert first["photo_command_count"] == 8
    assert first["supported_bracket_frames"] == [3, 5, 7, 9]
    frames = [
        command["frames"]
        for command in first["commands"]
        if command["action"] == "PHOTO"
    ]
    assert frames == [1, 1, 1, 1, 3, 5, 7, 9]
    assert first["estimated_duration_s"] > first["sequence_duration_s"]


def test_materialized_text_plan_round_trips_through_runtime_parser(tmp_path):
    recipe = build_validation_recipe(_profile())
    plan, text = materialize_validation_plan(
        recipe,
        rig_id=2,
        first_command_utc=datetime(2026, 9, 7, 9, 0, tzinfo=timezone.utc),
        profile_filename="test_profile.json",
        timing_filename="test_timing.json",
    )
    path = tmp_path / "validation.plan"
    path.write_text(text, encoding="utf-8")
    loaded = load_execution_plan(path)

    assert loaded["schema_version"] == 2
    assert len(loaded["commands"]) == len(plan["commands"])
    assert {command["rig_id"] for command in loaded["commands"]} == {2}
    assert sum(
        int(command["params"].get("expected_frames", 0))
        for command in loaded["commands"]
        if command["action"] == "PHOTO"
    ) == 28


def test_missing_bracket_is_reported_as_exact_missing_photo_count():
    recipe = build_validation_recipe(_profile())
    photo_commands = [
        command for command in recipe["commands"] if command["action"] == "PHOTO"
    ]
    events = []
    for command in photo_commands:
        frames = command["frames"]
        if frames == 7:
            continue
        events.append(
            {
                "validation_photo_id": command["params"]["validation_photo_id"],
                "expected_frames": frames,
                "confirmed_frames": frames,
                "status": "success",
                "dispatch_error_ms": 5.0,
                "duration_ms": command["duration_ms"],
                "budget_ms": command["duration_ms"],
            }
        )

    result = analyse_validation(
        recipe=recipe,
        recording={"preflight": {}, "sets": _successful_sets(recipe), "photos": events, "gets": []},
        runtime_logs=[],
        readbacks=[],
        operator_outcome="ok",
    )

    assert result["verdict"] == "FAIL"
    assert result["expected_photos"] == 28
    assert result["confirmed_photos"] == 21
    assert any(error["type"] == "MISSING_PHOTO" for error in result["errors"])


def test_extra_photos_are_warning_not_failure():
    recipe = build_validation_recipe(_profile())
    events = []
    for command in recipe["commands"]:
        if command["action"] != "PHOTO":
            continue
        events.append(
            {
                "validation_photo_id": command["params"]["validation_photo_id"],
                "expected_frames": command["frames"],
                "confirmed_frames": command["frames"],
                "status": "success",
                "dispatch_error_ms": 0.0,
                "duration_ms": command["duration_ms"],
                "budget_ms": command["duration_ms"],
            }
        )

    result = analyse_validation(
        recipe=recipe,
        recording={"preflight": {}, "sets": _successful_sets(recipe), "photos": events, "gets": []},
        runtime_logs=[],
        readbacks=[],
        operator_outcome="extra",
    )

    assert result["verdict"] == "WARNING"
    assert any(error["type"] == "EXTRA_PHOTO" for error in result["errors"])


def test_timing_statistics_use_signed_dispatch_error_and_population_stddev():
    recipe = build_validation_recipe(_profile())
    events = []
    for index, command in enumerate(
        [item for item in recipe["commands"] if item["action"] == "PHOTO"]
    ):
        events.append(
            {
                "validation_photo_id": command["params"]["validation_photo_id"],
                "expected_frames": command["frames"],
                "confirmed_frames": command["frames"],
                "status": "success",
                "dispatch_error_ms": (-10.0, 20.0, 30.0, 0.0, 5.0, -5.0, 15.0, 25.0)[index],
                "duration_ms": command["duration_ms"],
                "budget_ms": command["duration_ms"],
            }
        )

    result = analyse_validation(
        recipe=recipe,
        recording={"preflight": {}, "sets": _successful_sets(recipe), "photos": events, "gets": []},
        runtime_logs=[],
        readbacks=[],
        operator_outcome="ok",
    )
    timing = result["timing"]

    assert timing["physical_shutter_time_measured"] is False
    assert timing["count"] == 8
    assert timing["max_abs_ms"] == pytest.approx(30.0)
    assert timing["early_count"] == 2
    assert timing["late_count"] == 5
    assert timing["exact_count"] == 1
    assert timing["stddev_ms"] > 0


def test_camera_ipc_preserves_partial_frame_count(tmp_path):
    from backend.camera_ipc_server import CameraIpcServer, IpcError

    class Worker:
        def execute_photo(self, params, **kwargs):
            error = RuntimeError("Capture not confirmed: 5/7")
            error.observed_frames = 5
            error.expected_frames = 7
            raise error

    class Runtime:
        def get_for_rig(self, rig_id):
            return Worker() if rig_id == 1 else None

    server = CameraIpcServer(Runtime(), endpoint_dir=tmp_path)
    with pytest.raises(IpcError) as caught:
        server.handle_request(
            {
                "operation": "camera.execute_photo",
                "params": {"rig_id": 1, "params": {"frames": 7}},
            }
        )

    assert caught.value.code == "CAPTURE_COUNT_ERROR"
    assert caught.value.message == "Capture count mismatch: 5/7"
