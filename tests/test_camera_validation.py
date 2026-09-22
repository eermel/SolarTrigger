import inspect

import pytest

from backend.camera_timing_contract import SAFETY_POLICY
from backend.camera_validation import (
    CameraValidationJob,
    analyse_validation,
    build_validation_recipe,
)


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


def test_first_photo_uses_session_cold_start_floor_not_steady_state_budget():
    """Reproduces the observed FAIL: a fresh camera worker's very first PHOTO
    is a session cold-start, not a steady-state capture. Without a dedicated
    floor, budget_overrun_ms on that first PHOTO makes the scheduler skip the
    next scheduled command (the direct validation scheduler must reserve that cold-start cost).
    """
    profile = _profile()
    profile["timing_contract"]["single_overhead_ms"] = 1250
    profile["timing_contract"]["session_first_photo_overhead_ms"] = 5450

    recipe = build_validation_recipe(profile)
    photos = [c for c in recipe["commands"] if c["action"] == "PHOTO"]

    # First PHOTO of the whole recipe gets the cold-start floor...
    assert photos[0]["params"]["duration_ms"] > photos[1]["params"]["duration_ms"]
    assert photos[0]["params"]["duration_ms"] >= 5450.0
    # ...while every later single/bracket keeps using the steady-state budget.
    for photo in photos[1:]:
        assert photo["params"]["duration_ms"] < 5450.0


def test_missing_session_first_photo_overhead_falls_back_to_single_overhead():
    """Profiles characterized before this fix behave exactly as before."""
    profile = _profile()
    assert "session_first_photo_overhead_ms" not in profile["timing_contract"]

    with_field = build_validation_recipe(profile)

    profile_with_field = _profile()
    single_overhead_ms = profile_with_field["timing_contract"]["single_overhead_ms"]
    profile_with_field["timing_contract"]["session_first_photo_overhead_ms"] = (
        single_overhead_ms
    )
    explicit = build_validation_recipe(profile_with_field)

    # Setting session_first_photo_overhead_ms == single_overhead_ms must
    # produce byte-for-byte the same recipe as leaving the field absent.
    assert with_field == explicit


def test_runtime_optional_aperture_is_not_exercised_or_required_for_readback():
    """A manual lens must not turn optional aperture control into IVVQ FAIL."""
    profile = _profile()
    profile["commands"]["aperture"]["runtime_optional"] = True

    recipe = build_validation_recipe(profile)

    aperture_sets = [
        command
        for command in recipe["commands"]
        if command["action"] == "SET"
        and command["params"]["parameter"] == "f-number"
    ]

    assert aperture_sets == []
    assert "f-number" not in recipe["final_state"]

    # Camera coverage is unchanged: singles and all native brackets are still
    # exercised; only the lens-dependent aperture transitions are omitted.
    assert recipe["expected_photos"] == 28
    assert recipe["photo_command_count"] == 8
    assert recipe["supported_bracket_frames"] == [3, 5, 7, 9]


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


def test_validation_recipe_has_diagnostic_isolation_and_confirmation_grace():
    recipe = build_validation_recipe(_profile())
    commands = recipe["commands"]

    assert recipe["validation_diagnostic_guard_ms"] == pytest.approx(2000.0)
    assert recipe["validation_confirmation_grace_ms"] == pytest.approx(1000.0)

    for command in commands:
        if command["action"] == "PHOTO":
            assert command["params"]["validation_confirmation_grace_ms"] == pytest.approx(1000.0)

    for current, following in zip(commands, commands[1:]):
        slot = following["offset_ms"] - current["offset_ms"]
        assert slot == pytest.approx(current["duration_ms"] + 2000.0)


def test_late_file_confirmation_is_counted_and_reported_as_warning():
    recipe = build_validation_recipe(_profile())
    events = []
    first = True
    for command in recipe["commands"]:
        if command["action"] != "PHOTO":
            continue
        event = {
            "validation_photo_id": command["params"]["validation_photo_id"],
            "expected_frames": command["frames"],
            "confirmed_frames": command["frames"],
            "status": "success",
            "dispatch_error_ms": 1.0,
            "duration_ms": command["duration_ms"],
            "budget_ms": command["duration_ms"],
            "result": {"detail": "profile capture"},
        }
        if first:
            event["duration_ms"] = command["duration_ms"] + 80.0
            event["result"] = {
                "detail": "profile capture; validation confirmation after budget"
            }
            first = False
        events.append(event)

    result = analyse_validation(
        recipe=recipe,
        recording={"preflight": {}, "sets": _successful_sets(recipe), "photos": events, "gets": []},
        runtime_logs=[],
        readbacks=[],
        operator_outcome="ok",
    )

    assert result["confirmed_photos"] == 28
    assert result["verdict"] == "WARNING"
    assert any(error["type"] == "LATE_FILE_CONFIRMATION" for error in result["errors"])
    assert not any(error["type"] == "MISSING_PHOTO" for error in result["errors"])



def test_exact_usb_count_passes_without_operator_confirmation():
    recipe = build_validation_recipe(_profile())
    photos = []
    for command in recipe["commands"]:
        if command["action"] != "PHOTO":
            continue
        photos.append(
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
        recording={
            "preflight": {},
            "sets": _successful_sets(recipe),
            "photos": photos,
            "gets": [],
        },
        runtime_logs=[],
        readbacks=[],
        operator_outcome=None,
    )

    assert result["verdict"] == "PASS"
    assert result["confirmed_photos"] == result["expected_photos"] == 28
    assert result["operator_outcome"] is None
    assert not any(
        error["type"].startswith("OPERATOR_")
        for error in result["errors"]
    )


def test_validation_job_run_never_waits_for_operator_confirmation():
    source = inspect.getsource(CameraValidationJob._run)

    assert "_ask_operator" not in source
    assert "WAITING FOR OPERATOR CONFIRMATION" not in source
    assert "OPERATOR CONFIRMATION FAILED" not in source
    assert "AUTOMATIC VALIDATION COMPLETED" in source
    assert "operator_outcome=None" in source
