from datetime import datetime

from plugins.camera.nikon import NikonDSLRPlugin
from services.camera_service import CaptureIntent


def _intent(plan):
    return CaptureIntent(
        shutter_min=None,
        shutter_max=None,
        step_ev=None,
        speeds=None,
        phase="totality",
        target_time=datetime(2027, 8, 2, 12, 0, 0),
        deadline=None,
        overflow_policy="truncate",
        origin="sequencer",
        request_id="nikon-audit",
        exposure_plan=plan,
    )


def test_nikon_audit_matches_photo_by_photo_execution():
    plugin = NikonDSLRPlugin(
        None,
        lambda *_args, **_kwargs: None,
    )

    plan = [
        {"shutter": "1/1000", "iso": 100},
        {"shutter": "1/500", "iso": 100},
        {"shutter": "1/250", "iso": 200},
    ]

    prepared = plugin.prepare_capture(_intent(plan))
    operations = plugin.audit_prepared_capture(prepared)

    assert prepared.token[0] == "exposure_plan"

    assert operations == [
        {
            "action": "set",
            "parameter": "iso",
            "value": "100",
        },
        {
            "action": "set",
            "parameter": "shutterspeed2",
            "value": "1/1000",
            "fallback_parameter": "shutterspeed",
        },
        {
            "action": "trigger_capture",
            "shutter": "1/1000",
            "iso": 100,
            "expected_frames": 1,
        },
        {
            "action": "delay",
            "duration_ms": 50,
            "reason": "nikon_photo_spacing",
        },
        {
            "action": "set",
            "parameter": "shutterspeed2",
            "value": "1/500",
            "fallback_parameter": "shutterspeed",
        },
        {
            "action": "trigger_capture",
            "shutter": "1/500",
            "iso": 100,
            "expected_frames": 1,
        },
        {
            "action": "delay",
            "duration_ms": 50,
            "reason": "nikon_photo_spacing",
        },
        {
            "action": "set",
            "parameter": "iso",
            "value": "200",
        },
        {
            "action": "set",
            "parameter": "shutterspeed2",
            "value": "1/250",
            "fallback_parameter": "shutterspeed",
        },
        {
            "action": "trigger_capture",
            "shutter": "1/250",
            "iso": 200,
            "expected_frames": 1,
        },
        {
            "action": "delay",
            "duration_ms": 50,
            "reason": "nikon_photo_spacing",
        },
    ]


def test_nikon_audit_never_emits_capturemode_or_bracket():
    plugin = NikonDSLRPlugin(
        None,
        lambda *_args, **_kwargs: None,
    )

    prepared = plugin.prepare_capture(
        _intent([
            {"shutter": "1/1000", "iso": 100},
            {"shutter": "1/500", "iso": 100},
        ])
    )

    operations = plugin.audit_prepared_capture(prepared)

    assert not any(
        op.get("parameter") == "capturemode"
        for op in operations
    )

    assert not any(
        op.get("action") in {
            "bracket_press",
            "bracket_release",
        }
        for op in operations
    )


def test_nikon_audit_changes_iso_only_when_required():
    plugin = NikonDSLRPlugin(
        None,
        lambda *_args, **_kwargs: None,
    )

    prepared = plugin.prepare_capture(
        _intent([
            {"shutter": "1/1000", "iso": 100},
            {"shutter": "1/500", "iso": 100},
            {"shutter": "1/250", "iso": 200},
            {"shutter": "1/125", "iso": 200},
            {"shutter": "1/60", "iso": 400},
        ])
    )

    operations = plugin.audit_prepared_capture(prepared)

    iso_sets = [
        op["value"]
        for op in operations
        if op.get("action") == "set"
        and op.get("parameter") == "iso"
    ]

    assert iso_sets == ["100", "200", "400"]


def _audited_nikon_capture(plan, rig_id=2):
    from backend.sequencer_compiler import (
        CaptureTarget,
        MaterializedRigCapture,
        audit_materialized_nikon_capture,
    )

    target = CaptureTarget(
        phase="totality",
        phase_window="TOTALITY",
        sequence_index=0,
        target_time=datetime(2027, 8, 2, 12, 0, 0),
        deadline=None,
    )

    shutters = tuple(
        str(item["shutter"])
        for item in plan
    )

    capture = MaterializedRigCapture(
        rig_id=rig_id,
        backend="nikon-dslr",
        target=target,
        aperture=None,
        iso_requested=int(plan[0]["iso"]),
        original_shutters=shutters,
        atmos_applied=False,
        motion_policy="none",
        motion_ceiling_s=None,
        corrections=tuple(),
        warnings=tuple(),
        final_exposure_plan=tuple(plan),
    )

    return audit_materialized_nikon_capture(capture)


def test_nikon_initial_state_absorbs_first_iso_and_shutterspeed2():
    from backend.sequencer_compiler import derive_initial_state_required

    capture = _audited_nikon_capture([
        {"shutter": "1/1000", "iso": 100},
        {"shutter": "1/500", "iso": 100},
        {"shutter": "1/250", "iso": 200},
    ])

    initial = derive_initial_state_required({
        2: [capture],
    })

    assert initial == {
        2: {
            "iso": "100",
            "shutterspeed2": "1/1000",
        }
    }


def test_nikon_reducer_removes_trigger_initialized_first_sets():
    from backend.sequencer_compiler import reduce_audited_capture_operations

    capture = _audited_nikon_capture([
        {"shutter": "1/1000", "iso": 100},
        {"shutter": "1/500", "iso": 100},
        {"shutter": "1/250", "iso": 200},
    ])

    reduced, final_state = reduce_audited_capture_operations(
        capture,
        {
            "iso": "100",
            "shutterspeed2": "1/1000",
        },
    )

    operations = list(reduced.operations)

    # First ISO and first shutter are already established by Trigger.
    assert operations[0]["action"] == "trigger_capture"
    assert operations[0]["shutter"] == "1/1000"

    remaining_sets = [
        (
            op["parameter"],
            op["value"],
        )
        for op in operations
        if op.get("action") == "set"
    ]

    assert remaining_sets == [
        ("shutterspeed2", "1/500"),
        ("iso", "200"),
        ("shutterspeed2", "1/250"),
    ]

    assert final_state == {
        "iso": "200",
        "shutterspeed2": "1/250",
    }


def test_nikon_reducer_does_not_create_capturemode_state():
    from backend.sequencer_compiler import (
        derive_initial_state_required,
        reduce_audited_capture_operations,
    )

    capture = _audited_nikon_capture([
        {"shutter": "1/1000", "iso": 100},
    ])

    initial = derive_initial_state_required({
        2: [capture],
    })

    assert "capturemode" not in initial[2]

    _, final_state = reduce_audited_capture_operations(
        capture,
        initial[2],
    )

    assert "capturemode" not in final_state


def test_multirig_sony_and_d850_use_independent_measured_timings():
    from backend.sequencer_compiler import (
        CameraTimingProfile,
        CaptureTarget,
        MaterializedRigCapture,
        audit_materialized_capture,
        compile_and_merge_scheduled_rigs,
        derive_initial_state_required,
    )

    target_time = datetime(2027, 8, 2, 12, 0, 0)

    target_rig1 = CaptureTarget(
        phase="totality",
        phase_window="TOTALITY",
        sequence_index=0,
        target_time=target_time,
        deadline=None,
    )

    target_rig2 = CaptureTarget(
        phase="totality",
        phase_window="TOTALITY",
        sequence_index=0,
        target_time=target_time,
        deadline=None,
    )

    # Sony: exact native 5-view bracket.
    sony_plan = (
        {"shutter": "1/1000", "iso": 100},
        {"shutter": "1/500", "iso": 100},
        {"shutter": "1/250", "iso": 100},
        {"shutter": "1/125", "iso": 100},
        {"shutter": "1/60", "iso": 100},
    )

    sony_materialized = MaterializedRigCapture(
        rig_id=1,
        backend="sony",
        target=target_rig1,
        aperture=None,
        iso_requested=100,
        original_shutters=tuple(
            item["shutter"]
            for item in sony_plan
        ),
        atmos_applied=False,
        motion_policy="none",
        motion_ceiling_s=None,
        corrections=tuple(),
        warnings=tuple(),
        final_exposure_plan=sony_plan,
    )

    # Nikon D850: photo-by-photo.
    nikon_plan = (
        {"shutter": "1/1000", "iso": 100},
        {"shutter": "1/500", "iso": 100},
        {"shutter": "1/250", "iso": 200},
    )

    nikon_materialized = MaterializedRigCapture(
        rig_id=2,
        backend="nikon-dslr",
        target=target_rig2,
        aperture=None,
        iso_requested=100,
        original_shutters=tuple(
            item["shutter"]
            for item in nikon_plan
        ),
        atmos_applied=False,
        motion_policy="none",
        motion_ceiling_s=None,
        corrections=tuple(),
        warnings=tuple(),
        final_exposure_plan=nikon_plan,
    )

    sony = audit_materialized_capture(
        sony_materialized
    )

    nikon = audit_materialized_capture(
        nikon_materialized
    )

    initial_states = derive_initial_state_required({
        1: [sony],
        2: [nikon],
    })

    assert initial_states[1] == {
        "iso": "100",
        "capturemode": "Single Shot",
        "shutterspeed": "1/250",
    }

    assert initial_states[2] == {
        "iso": "100",
        "shutterspeed2": "1/1000",
    }

    merged, final_states = compile_and_merge_scheduled_rigs(
        {
            1: [sony],
            2: [nikon],
        },
        initial_states=initial_states,
        timing_profiles={
            1: CameraTimingProfile(
                backend="sony",
                set_iso_ms=830,
                set_capturemode_ms=838,
                set_shutter_ms=827,
                trigger_single_latency_ms=26,
                bracket_press_latency_ms=840,
                bracket_release_ms=854,
                settle_idle_ms=666,
            ),
            2: CameraTimingProfile(
                backend="nikon-dslr",
                set_iso_ms=550,
                set_capturemode_ms=0,
                set_shutter_ms=543,
                trigger_single_latency_ms=285,
                trigger_single_duration_ms=285,
                bracket_press_latency_ms=0,
                bracket_release_ms=0,
                settle_idle_ms=0,
            ),
        },
    )

    # ---------------------------------------------------------
    # Sony physical trigger
    # ---------------------------------------------------------

    sony_triggers = [
        event
        for event in merged
        if event.rig_id == 1
        and event.operation.get("action") == "bracket_press"
    ]

    assert len(sony_triggers) == 1

    assert sony_triggers[0].command_time == datetime(
        2027, 8, 2,
        11, 59, 59, 160000,
    )

    # ---------------------------------------------------------
    # Nikon first physical trigger
    # ---------------------------------------------------------

    nikon_triggers = [
        event
        for event in merged
        if event.rig_id == 2
        and event.operation.get("action") == "trigger_capture"
    ]

    assert len(nikon_triggers) == 3

    # First Nikon shutter and ISO were initialized by Trigger,
    # therefore the first physical command is the trigger itself.
    assert nikon_triggers[0].command_time == datetime(
        2027, 8, 2,
        11, 59, 59, 715000,
    )

    assert nikon_triggers[0].operation["shutter"] == "1/1000"
    assert nikon_triggers[0].operation["iso"] == 100

    # ---------------------------------------------------------
    # Nikon must use shutterspeed2 and never capturemode/bracket.
    # ---------------------------------------------------------

    nikon_operations = [
        event.operation
        for event in merged
        if event.rig_id == 2
    ]

    assert not any(
        op.get("parameter") == "capturemode"
        for op in nikon_operations
    )

    assert not any(
        op.get("action") in {
            "bracket_press",
            "bracket_release",
        }
        for op in nikon_operations
    )

    shutter_sets = [
        op
        for op in nikon_operations
        if op.get("action") == "set"
        and op.get("parameter") == "shutterspeed2"
    ]

    assert [
        op["value"]
        for op in shutter_sets
    ] == [
        "1/500",
        "1/250",
    ]

    # ---------------------------------------------------------
    # Global timed commands must be chronologically merged.
    # ---------------------------------------------------------

    timed = [
        event
        for event in merged
        if event.command_time is not None
    ]

    assert timed == sorted(
        timed,
        key=lambda event: (
            event.command_time,
            event.rig_id,
            event.sequence_index,
            event.operation_index,
        ),
    )

    # Backend states stay independent.
    assert final_states[1]["iso"] == "100"
    assert final_states[1]["capturemode"].startswith(
        "Continuous Bracket"
    )

    assert final_states[2] == {
        "iso": "200",
        "shutterspeed2": "1/250",
    }


def test_camera_backends_are_not_tied_to_rig_numbers():
    from backend.sequencer_compiler import (
        CameraTimingProfile,
        CaptureTarget,
        MaterializedRigCapture,
        audit_materialized_capture,
        compile_and_merge_scheduled_rigs,
        derive_initial_state_required,
    )

    target_time = datetime(2027, 8, 2, 12, 0, 0)

    def target(rig_id):
        return CaptureTarget(
            phase="totality",
            phase_window="TOTALITY",
            sequence_index=0,
            target_time=target_time,
            deadline=None,
        )

    nikon_plan = (
        {"shutter": "1/1000", "iso": 100},
        {"shutter": "1/500", "iso": 200},
    )

    sony_plan = (
        {"shutter": "1/1000", "iso": 100},
        {"shutter": "1/500", "iso": 100},
        {"shutter": "1/250", "iso": 100},
        {"shutter": "1/125", "iso": 100},
        {"shutter": "1/60", "iso": 100},
    )

    nikon = audit_materialized_capture(
        MaterializedRigCapture(
            rig_id=1,
            backend="nikon-dslr",
            target=target(1),
            aperture=None,
            iso_requested=100,
            original_shutters=tuple(
                item["shutter"] for item in nikon_plan
            ),
            atmos_applied=False,
            motion_policy="none",
            motion_ceiling_s=None,
            corrections=tuple(),
            warnings=tuple(),
            final_exposure_plan=nikon_plan,
        )
    )

    sony = audit_materialized_capture(
        MaterializedRigCapture(
            rig_id=4,
            backend="sony",
            target=target(4),
            aperture=None,
            iso_requested=100,
            original_shutters=tuple(
                item["shutter"] for item in sony_plan
            ),
            atmos_applied=False,
            motion_policy="none",
            motion_ceiling_s=None,
            corrections=tuple(),
            warnings=tuple(),
            final_exposure_plan=sony_plan,
        )
    )

    initial_states = derive_initial_state_required({
        1: [nikon],
        4: [sony],
    })

    assert initial_states[1] == {
        "iso": "100",
        "shutterspeed2": "1/1000",
    }

    assert initial_states[4] == {
        "iso": "100",
        "capturemode": "Single Shot",
        "shutterspeed": "1/250",
    }

    merged, _final_states = compile_and_merge_scheduled_rigs(
        {
            1: [nikon],
            4: [sony],
        },
        initial_states=initial_states,
        timing_profiles={
            1: CameraTimingProfile(
                backend="nikon-dslr",
                set_iso_ms=550,
                set_shutter_ms=543,
                trigger_single_latency_ms=285,
                trigger_single_duration_ms=285,
            ),
            4: CameraTimingProfile(
                backend="sony",
                set_iso_ms=830,
                set_capturemode_ms=838,
                set_shutter_ms=827,
                trigger_single_latency_ms=26,
                bracket_press_latency_ms=840,
                bracket_release_ms=854,
                settle_idle_ms=666,
            ),
        },
    )

    nikon_trigger = next(
        event
        for event in merged
        if event.rig_id == 1
        and event.operation.get("action") == "trigger_capture"
    )

    sony_trigger = next(
        event
        for event in merged
        if event.rig_id == 4
        and event.operation.get("action") == "bracket_press"
    )

    assert nikon_trigger.backend == "nikon-dslr"
    assert nikon_trigger.command_time == datetime(
        2027, 8, 2, 11, 59, 59, 715000
    )

    assert sony_trigger.backend == "sony"
    assert sony_trigger.command_time == datetime(
        2027, 8, 2, 11, 59, 59, 160000
    )


def test_nikon_multiframe_schedule_is_sequential_and_statically_timed():
    from backend.sequencer_compiler import (
        CameraTimingProfile,
        reduce_audited_capture_operations,
        schedule_audited_capture,
    )

    audited = _audited_nikon_capture([
        {"shutter": "1/1000", "iso": 100},
        {"shutter": "1/500", "iso": 100},
        {"shutter": "1/250", "iso": 200},
    ])

    # Trigger establishes the first useful camera state before TSTART.
    reduced, _state = reduce_audited_capture_operations(
        audited,
        {
            "iso": "100",
            "shutterspeed2": "1/1000",
        },
    )

    scheduled = schedule_audited_capture(
        reduced,
        CameraTimingProfile(
            backend="nikon-dslr",
            set_iso_ms=550,
            set_capturemode_ms=0,
            set_shutter_ms=543,
            trigger_single_latency_ms=285,
            trigger_single_duration_ms=285,
            bracket_press_latency_ms=0,
            bracket_release_ms=0,
            settle_idle_ms=0,
        ),
    )

    # Reduced Nikon operations:
    #
    # trigger 1
    # delay 50
    # set shutter 1/500
    # trigger 2
    # delay 50
    # set ISO 200
    # set shutter 1/250
    # trigger 3
    # delay 50

    assert [
        (
            item.operation.get("action"),
            item.operation.get("parameter"),
            item.command_time,
        )
        for item in scheduled
    ] == [
        (
            "trigger_capture",
            None,
            datetime(2027, 8, 2, 11, 59, 59, 715000),
        ),
        (
            "delay",
            None,
            datetime(2027, 8, 2, 12, 0, 0),
        ),
        (
            "set",
            "shutterspeed2",
            datetime(2027, 8, 2, 12, 0, 0, 50000),
        ),
        (
            "trigger_capture",
            None,
            datetime(2027, 8, 2, 12, 0, 0, 593000),
        ),
        (
            "delay",
            None,
            datetime(2027, 8, 2, 12, 0, 0, 878000),
        ),
        (
            "set",
            "iso",
            datetime(2027, 8, 2, 12, 0, 0, 928000),
        ),
        (
            "set",
            "shutterspeed2",
            datetime(2027, 8, 2, 12, 0, 1, 478000),
        ),
        (
            "trigger_capture",
            None,
            datetime(2027, 8, 2, 12, 0, 2, 21000),
        ),
        (
            "delay",
            None,
            datetime(2027, 8, 2, 12, 0, 2, 306000),
        ),
    ]

    assert all(
        item.command_time is not None
        for item in scheduled
    )


def test_same_rig_overlapping_nikon_captures_are_rejected():
    import pytest

    from dataclasses import replace
    from datetime import timedelta

    from backend.sequencer_compiler import (
        CameraTimingProfile,
        compile_and_merge_scheduled_rigs,
    )

    exposure_plan = [
        {"shutter": "1/1000", "iso": 100},
        {"shutter": "1/500", "iso": 100},
        {"shutter": "1/250", "iso": 200},
    ]

    first = _audited_nikon_capture(
        exposure_plan,
        rig_id=1,
    )

    second = replace(
        first,
        target=replace(
            first.target,
            sequence_index=1,
            target_time=(
                first.target.target_time
                + timedelta(seconds=1)
            ),
        ),
    )

    timing = CameraTimingProfile(
        backend="nikon-dslr",
        set_iso_ms=550,
        set_shutter_ms=543,
        trigger_single_latency_ms=285,
        trigger_single_duration_ms=285,
    )

    with pytest.raises(
        ValueError,
        match="static command overlap for RIG 1",
    ):
        compile_and_merge_scheduled_rigs(
            {
                1: [first, second],
            },
            initial_states={
                1: {
                    "iso": "100",
                    "shutterspeed2": "1/1000",
                },
            },
            timing_profiles={
                1: timing,
            },
        )


def test_simultaneous_nikon_captures_on_different_rigs_are_allowed():
    from backend.sequencer_compiler import (
        CameraTimingProfile,
        compile_and_merge_scheduled_rigs,
    )

    exposure_plan = [
        {"shutter": "1/1000", "iso": 100},
        {"shutter": "1/500", "iso": 100},
        {"shutter": "1/250", "iso": 200},
    ]

    rig1 = _audited_nikon_capture(
        exposure_plan,
        rig_id=1,
    )

    rig2 = _audited_nikon_capture(
        exposure_plan,
        rig_id=2,
    )

    timing = CameraTimingProfile(
        backend="nikon-dslr",
        set_iso_ms=550,
        set_shutter_ms=543,
        trigger_single_latency_ms=285,
        trigger_single_duration_ms=285,
    )

    merged, _states = compile_and_merge_scheduled_rigs(
        {
            1: [rig1],
            2: [rig2],
        },
        initial_states={
            1: {
                "iso": "100",
                "shutterspeed2": "1/1000",
            },
            2: {
                "iso": "100",
                "shutterspeed2": "1/1000",
            },
        },
        timing_profiles={
            1: timing,
            2: timing,
        },
    )

    first_triggers = [
        event
        for event in merged
        if event.operation.get("action") == "trigger_capture"
        and event.operation.get("shutter") == "1/1000"
    ]

    assert len(first_triggers) == 2

    assert (
        first_triggers[0].command_time
        == first_triggers[1].command_time
    )

    assert {
        event.rig_id
        for event in first_triggers
    } == {1, 2}
