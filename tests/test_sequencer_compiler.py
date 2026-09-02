from datetime import datetime

import pytest

from backend.sequencer_compiler import (
    build_sequence_windows,
    compile_capture_targets,
)


def _timeline():
    return {
        "C1": datetime(2027, 8, 2, 10, 0, 0),
        "C2": datetime(2027, 8, 2, 10, 5, 0),
        "TMAX": datetime(2027, 8, 2, 10, 6, 0),
        "C3": datetime(2027, 8, 2, 10, 7, 0),
        "C4": datetime(2027, 8, 2, 10, 10, 0),
    }


def _photo():
    return {
        "phases": {
            "partial": {
                "interval_s": 30,
            },
            "diamond_ring": {
                "duration_s": 60,
                "interval_s": 10,
            },
            "totality": {
                "interval_s": 5,
            },
        }
    }


def test_builds_canonical_five_windows():
    windows = build_sequence_windows(
        _timeline(),
        _photo(),
        sequence_margin_min=2,
    )

    assert [
        (
            w.name,
            w.phase,
            w.start.strftime("%H:%M:%S"),
            w.end.strftime("%H:%M:%S"),
            w.interval_s,
        )
        for w in windows
    ] == [
        (
            "phase_1a",
            "partial",
            "09:58:00",
            "10:04:00",
            30.0,
        ),
        (
            "phase_1b",
            "diamond_ring",
            "10:04:00",
            "10:05:00",
            10.0,
        ),
        (
            "phase_2",
            "totality",
            "10:05:00",
            "10:07:00",
            None,
        ),
        (
            "phase_3a",
            "diamond_ring",
            "10:07:00",
            "10:08:00",
            10.0,
        ),
        (
            "phase_3b",
            "partial",
            "10:08:00",
            "10:12:00",
            30.0,
        ),
    ]


def test_partial_and_diamond_targets_are_interval_driven():
    targets = compile_capture_targets(
        _timeline(),
        _photo(),
        sequence_margin_min=2,
    )

    phase_1a = [
        target
        for target in targets
        if target.phase_window == "phase_1a"
    ]

    assert phase_1a[0].target_time.strftime("%H:%M:%S") == "09:58:00"
    assert phase_1a[1].target_time.strftime("%H:%M:%S") == "09:58:30"
    assert phase_1a[-1].target_time.strftime("%H:%M:%S") == "10:03:30"

    # Boundary belongs to Diamond Ring, never also Partial.
    assert all(
        target.target_time.strftime("%H:%M:%S") != "10:04:00"
        for target in phase_1a
    )

    phase_1b = [
        target
        for target in targets
        if target.phase_window == "phase_1b"
    ]

    assert [
        target.target_time.strftime("%H:%M:%S")
        for target in phase_1b
    ] == [
        "10:04:00",
        "10:04:10",
        "10:04:20",
        "10:04:30",
        "10:04:40",
        "10:04:50",
    ]


def test_deadline_is_next_target_or_phase_boundary():
    targets = compile_capture_targets(
        _timeline(),
        _photo(),
        sequence_margin_min=2,
    )

    phase_1b = [
        target
        for target in targets
        if target.phase_window == "phase_1b"
    ]

    assert phase_1b[0].deadline.strftime("%H:%M:%S") == "10:04:10"
    assert phase_1b[-1].deadline.strftime("%H:%M:%S") == "10:05:00"


def test_totality_is_one_continuous_c2_c3_window():
    targets = compile_capture_targets(
        _timeline(),
        _photo(),
        sequence_margin_min=2,
    )

    totality = [
        target
        for target in targets
        if target.phase == "totality"
    ]

    assert len(totality) == 1

    target = totality[0]

    assert target.target_time == datetime(
        2027, 8, 2, 10, 5, 0
    )
    assert target.deadline == datetime(
        2027, 8, 2, 10, 7, 0
    )
    assert target.sequence_index == 0
    assert target.phase_window == "phase_2"


@pytest.mark.parametrize(
    "margin",
    [-1, True, "5"],
)
def test_invalid_sequence_margin_is_rejected(margin):
    with pytest.raises(ValueError):
        build_sequence_windows(
            _timeline(),
            _photo(),
            sequence_margin_min=margin,
        )


from backend.sequencer_compiler import (
    CaptureTarget,
    apply_exposure_optimization,
    materialize_capture_target_for_rig,
    materialize_capture_targets,
)


def _rig(
    rig_id=1,
    *,
    backend="sony",
    enabled=False,
):
    return {
        "rig_id": rig_id,
        "enabled": enabled,
        "devices": {
            "camera": {
                "backend": backend,
            },
        },
        "optics": {
            "focal_length_mm": 430.0,
        },
        "photo": {
            "atmos_enabled": False,
            "anti_trailing_enabled": False,
            "motion_tolerance_px": 1.0,
            "iso_compensation_enabled": True,
            "iso_max": 6400,
        },
    }


def _exposure_opt():
    return {
        "atmospheric_attenuation_enabled": False,
        "rigs": [
            {
                "rig_id": 1,
                "photo": {
                    "anti_trailing_enabled": False,
                    "motion_tolerance_px": 1,
                    "iso_compensation_enabled": True,
                    "iso_max": 6400,
                },
            },
        ],
    }


def _eclipse_context():
    return {}


def test_rig1_is_always_active_but_optional_rigs_are_not():
    targets = compile_capture_targets(
        _timeline(),
        _photo(),
        sequence_margin_min=2,
    )[:1]

    captures = materialize_capture_targets(
        targets,
        [
            _rig(1, enabled=False),
            _rig(2, enabled=False),
            _rig(3, enabled=True),
        ],
        {
            "phases": {
                "partial": {
                    "enabled": True,
                    "interval_s": 30,
                    "iso": 100,
                    "aperture": "f/8",
                    "shutter_min": "1/250",
                    "shutter_max": "1/1000",
                    "step_ev": 1.0,
                },
                "diamond_ring": _photo()["phases"]["diamond_ring"],
                "totality": {},
            },
        },
        _exposure_opt(),
        _eclipse_context(),
    )

    assert [item.rig_id for item in captures] == [1, 3]


def test_exposure_opt_overrides_are_ephemeral():
    rig = _rig()

    updated = apply_exposure_optimization(
        rig,
        {
            "atmospheric_attenuation_enabled": True,
            "rigs": [
                {
                    "rig_id": 1,
                    "photo": {
                        "iso_max": 3200,
                        "anti_trailing_enabled": True,
                    },
                },
            ],
        },
    )

    assert updated["photo"]["atmos_enabled"] is True
    assert updated["photo"]["iso_max"] == 3200
    assert updated["photo"]["anti_trailing_enabled"] is True

    assert rig["photo"]["atmos_enabled"] is False
    assert rig["photo"]["iso_max"] == 6400
    assert rig["photo"]["anti_trailing_enabled"] is False


def test_sony_target_expands_to_physical_shutters():
    target = CaptureTarget(
        target_time=datetime(2027, 8, 2, 10, 4, 0),
        phase="partial",
        phase_window="phase_1a",
        sequence_index=0,
        deadline=datetime(2027, 8, 2, 10, 4, 30),
    )

    photo = {
        "phases": {
            "partial": {
                "enabled": True,
                "interval_s": 30,
                "iso": 100,
                "aperture": "f/8",
                "shutter_min": "1/250",
                "shutter_max": "1/1000",
                "step_ev": 1.0,
            },
        },
    }

    capture = materialize_capture_target_for_rig(
        target,
        _rig(backend="sony"),
        photo,
        _exposure_opt(),
        _eclipse_context(),
    )

    assert capture.backend == "sony"
    assert capture.iso_requested == 100
    assert capture.aperture == "f/8"

    assert capture.original_shutters == (
        "1/1000",
        "1/500",
        "1/250",
    )

    assert capture.final_exposure_plan == (
        {"shutter": "1/1000", "iso": 100},
        {"shutter": "1/500", "iso": 100},
        {"shutter": "1/250", "iso": 100},
    )

    assert capture.motion_policy == "none"
    assert capture.motion_ceiling_s is None


from backend.sequencer_compiler import (
    audit_materialized_capture,
    audit_materialized_sony_capture,
)


def _sony_materialized_capture():
    target = CaptureTarget(
        target_time=datetime(2027, 8, 2, 10, 4, 0),
        phase="diamond_ring",
        phase_window="phase_1b",
        sequence_index=0,
        deadline=datetime(2027, 8, 2, 10, 4, 3),
    )

    return materialize_capture_target_for_rig(
        target,
        _rig(backend="sony"),
        {
            "phases": {
                "diamond_ring": {
                    "enabled": True,
                    "interval_s": 3,
                    "duration_s": 30,
                    "iso": 100,
                    "aperture": "f/8",
                    "shutter_min": "1/60",
                    "shutter_max": "1/1000",
                    "step_ev": 1.0,
                },
            },
        },
        _exposure_opt(),
        _eclipse_context(),
    )


def test_materialized_sony_capture_uses_real_prepare_capture():
    capture = _sony_materialized_capture()

    audited = audit_materialized_sony_capture(capture)

    assert audited.rig_id == 1
    assert audited.backend == "sony"
    assert audited.aperture == "f/8"

    assert audited.prepared_mode == "sony_exposure_sequence"
    assert audited.planned_count == 5

    assert audited.exposure_plan == (
        {"shutter": "1/1000", "iso": 100},
        {"shutter": "1/500", "iso": 100},
        {"shutter": "1/250", "iso": 100},
        {"shutter": "1/125", "iso": 100},
        {"shutter": "1/60", "iso": 100},
    )


def test_sony_audit_contains_native_bracket_commands():
    audited = audit_materialized_sony_capture(
        _sony_materialized_capture()
    )

    operations = list(audited.operations)

    assert operations[0] == {
        "action": "set",
        "parameter": "iso",
        "value": "100",
    }

    assert operations[1] == {
        "action": "set",
        "parameter": "capturemode",
        "value": "Single Shot",
    }

    assert operations[2] == {
        "action": "set",
        "parameter": "shutterspeed",
        "value": "1/250",
    }

    assert operations[3] == {
        "action": "set",
        "parameter": "capturemode",
        "value": "Continuous Bracket 1.0 EV 5 Img.",
    }

    assert operations[4]["action"] == "bracket_press"
    assert operations[4]["parameter"] == "bulb"
    assert operations[4]["value"] == "1"
    assert operations[4]["frames"] == 5
    assert operations[4]["physical_views"] == [
        "1/1000",
        "1/500",
        "1/250",
        "1/125",
        "1/60",
    ]

    assert operations[5]["action"] == "expect_frames"
    assert operations[5]["count"] == 5

    assert operations[6] == {
        "action": "bracket_release",
        "parameter": "bulb",
        "value": "0",
    }

    assert operations[7] == {
        "action": "settle_idle",
    }


def test_generic_audit_dispatches_to_sony():
    capture = _sony_materialized_capture()

    audited = audit_materialized_capture(capture)

    assert audited.backend == "sony"
    assert audited.prepared_mode == "sony_exposure_sequence"


def test_unsupported_backend_is_explicitly_rejected():
    capture = _sony_materialized_capture()

    from dataclasses import replace

    capture = replace(
        capture,
        backend="canon",
    )

    with pytest.raises(
        ValueError,
        match="Sequencer audit backend not implemented",
    ):
        audit_materialized_capture(capture)


from backend.sequencer_compiler import (
    CameraTimingProfile,
    schedule_audited_capture,
)


def _sony_test_timing():
    # TEST VALUES ONLY — not calibrated hardware values.
    return CameraTimingProfile(
        backend="sony",
        set_iso_ms=100,
        set_capturemode_ms=120,
        set_shutter_ms=150,
        bracket_press_latency_ms=280,
        trigger_single_latency_ms=250,
        settle_idle_ms=500,
        bracket_atomic_ms_by_frames={
            3: 3000,
            5: 3200,
            7: 3600,
            9: 4000,
        },
    )


def test_sony_bracket_trigger_is_compensated_before_target():
    audited = audit_materialized_sony_capture(
        _sony_materialized_capture()
    )

    scheduled = schedule_audited_capture(
        audited,
        _sony_test_timing(),
    )

    trigger = next(
        item
        for item in scheduled
        if item.operation.get("action") == "bracket_press"
    )

    assert trigger.target_time == datetime(
        2027, 8, 2, 10, 4, 0
    )

    assert trigger.command_time == datetime(
        2027, 8, 2, 10, 3, 59, 720000
    )

    assert trigger.timing_relation == "trigger"


def test_sony_bracket_preparation_is_scheduled_backwards():
    audited = audit_materialized_sony_capture(
        _sony_materialized_capture()
    )

    scheduled = schedule_audited_capture(
        audited,
        _sony_test_timing(),
    )

    pre = [
        item
        for item in scheduled
        if item.timing_relation == "prepare"
    ]

    assert [
        item.command_time
        for item in pre
    ] == [
        # ISO starts first and reserves 100 ms.
        datetime(2027, 8, 2, 10, 3, 59, 230000),

        # Single Shot reserves the next 120 ms.
        datetime(2027, 8, 2, 10, 3, 59, 330000),

        # Centre shutter reserves 150 ms.
        datetime(2027, 8, 2, 10, 3, 59, 450000),

        # Native bracket mode reserves 120 ms.
        datetime(2027, 8, 2, 10, 3, 59, 600000),
    ]

    assert [
        (
            item.operation.get("action"),
            item.operation.get("parameter"),
            item.operation.get("value"),
        )
        for item in pre
    ] == [
        ("set", "iso", "100"),
        ("set", "capturemode", "Single Shot"),
        ("set", "shutterspeed", "1/250"),
        (
            "set",
            "capturemode",
            "Continuous Bracket 1.0 EV 5 Img.",
        ),
    ]


def test_post_trigger_operations_are_runtime_timed():
    audited = audit_materialized_sony_capture(
        _sony_materialized_capture()
    )

    scheduled = schedule_audited_capture(
        audited,
        _sony_test_timing(),
    )

    post = [
        item
        for item in scheduled
        if item.timing_relation == "post_trigger"
    ]

    assert [
        item.operation["action"]
        for item in post
    ] == [
        "expect_frames",
        "bracket_release",
        "settle_idle",
    ]

    assert all(
        item.command_time is None
        for item in post
    )


def test_timing_profile_backend_must_match_capture():
    audited = audit_materialized_sony_capture(
        _sony_materialized_capture()
    )

    with pytest.raises(
        ValueError,
        match="does not match",
    ):
        schedule_audited_capture(
            audited,
            CameraTimingProfile(
                backend="nikon-z",
            ),
        )


from backend.sequencer_compiler import (
    reduce_audited_capture_operations,
    reduce_audited_captures,
)


def test_trigger_initial_state_removes_unnecessary_first_sony_sets():
    audited = audit_materialized_sony_capture(
        _sony_materialized_capture()
    )

    reduced, final_state = reduce_audited_capture_operations(
        audited,
        {
            # State already guaranteed by Trigger before TSTART.
            "exposuremode": "Manual",
            "iso": "100",
            "capturemode": "Single Shot",
            "shutterspeed": "1/250",
        },
    )

    sets = [
        op
        for op in reduced.operations
        if op.get("action") == "set"
    ]

    # Sony native bracket preparation always reissues the centre shutter
    # while the camera is in Single Shot mode, even when its logical value
    # is already known from Trigger initial state.
    assert sets == [
        {
            "action": "set",
            "parameter": "shutterspeed",
            "value": "1/250",
        },
        {
            "action": "set",
            "parameter": "capturemode",
            "value": "Continuous Bracket 1.0 EV 5 Img.",
        },
    ]

    assert final_state["exposuremode"] == "Manual"
    assert final_state["iso"] == "100"
    assert final_state["shutterspeed"] == "1/250"
    assert (
        final_state["capturemode"]
        == "Continuous Bracket 1.0 EV 5 Img."
    )

    # Trigger itself is never removed.
    assert any(
        op.get("action") == "bracket_press"
        for op in reduced.operations
    )


def test_second_identical_sony_bracket_only_keeps_required_mode_transitions():
    first = audit_materialized_sony_capture(
        _sony_materialized_capture()
    )

    first_reduced, state = reduce_audited_capture_operations(
        first,
        {
            "iso": "100",
            "capturemode": "Single Shot",
            "shutterspeed": "1/250",
        },
    )

    second = audit_materialized_sony_capture(
        _sony_materialized_capture()
    )

    second_reduced, state = reduce_audited_capture_operations(
        second,
        state,
    )

    sets = [
        op
        for op in second_reduced.operations
        if op.get("action") == "set"
    ]

    # After a bracket, Sony remains in bracket capture mode.
    # Native bracket preparation must physically execute:
    #
    #   Single Shot -> centre shutter -> Continuous Bracket
    #
    # even when the centre shutter value is already logically known.
    assert sets == [
        {
            "action": "set",
            "parameter": "capturemode",
            "value": "Single Shot",
        },
        {
            "action": "set",
            "parameter": "shutterspeed",
            "value": "1/250",
        },
        {
            "action": "set",
            "parameter": "capturemode",
            "value": "Continuous Bracket 1.0 EV 5 Img.",
        },
    ]

    assert state["iso"] == "100"
    assert state["shutterspeed"] == "1/250"


def test_reduced_capture_changes_scheduling_lead_time():
    audited = audit_materialized_sony_capture(
        _sony_materialized_capture()
    )

    reduced, _state = reduce_audited_capture_operations(
        audited,
        {
            "iso": "100",
            "capturemode": "Single Shot",
            "shutterspeed": "1/250",
        },
    )

    scheduled = schedule_audited_capture(
        reduced,
        _sony_test_timing(),
    )

    timed = [
        item
        for item in scheduled
        if item.command_time is not None
    ]

    assert [
        (
            item.operation.get("action"),
            item.operation.get("parameter"),
            item.command_time,
        )
        for item in timed
    ] == [
        (
            "set",
            "shutterspeed",
            datetime(2027, 8, 2, 10, 3, 59, 450000),
        ),
        (
            "set",
            "capturemode",
            datetime(2027, 8, 2, 10, 3, 59, 600000),
        ),
        (
            "bracket_press",
            "bulb",
            datetime(2027, 8, 2, 10, 3, 59, 720000),
        ),
    ]


def test_reducer_keeps_independent_state_per_rig():
    capture1 = audit_materialized_sony_capture(
        _sony_materialized_capture()
    )

    from dataclasses import replace

    capture2 = replace(
        capture1,
        rig_id=2,
    )

    reduced, states = reduce_audited_captures(
        [capture1, capture2],
        {
            1: {
                "iso": "100",
                "capturemode": "Single Shot",
                "shutterspeed": "1/250",
            },
            2: {
                "iso": "200",
                "capturemode": "Single Shot",
                "shutterspeed": "1/250",
            },
        },
    )

    rig1_iso_sets = [
        op
        for op in reduced[0].operations
        if op.get("action") == "set"
        and op.get("parameter") == "iso"
    ]

    rig2_iso_sets = [
        op
        for op in reduced[1].operations
        if op.get("action") == "set"
        and op.get("parameter") == "iso"
    ]

    assert rig1_iso_sets == []

    assert rig2_iso_sets == [
        {
            "action": "set",
            "parameter": "iso",
            "value": "100",
        },
    ]

    assert states[1]["iso"] == "100"
    assert states[2]["iso"] == "100"


from backend.sequencer_compiler import (
    compile_and_merge_scheduled_rigs,
    merge_scheduled_operations,
)


def _audited_sony_for_rig(rig_id, target_time):
    base = audit_materialized_sony_capture(
        _sony_materialized_capture()
    )

    from dataclasses import replace

    target = replace(
        base.target,
        target_time=target_time,
        deadline=None,
    )

    return replace(
        base,
        rig_id=rig_id,
        target=target,
    )


def test_merge_preserves_simultaneous_commands_from_multiple_rigs():
    target = datetime(2027, 8, 2, 10, 4, 0)

    rig1 = _audited_sony_for_rig(1, target)
    rig2 = _audited_sony_for_rig(2, target)

    timing = _sony_test_timing()

    rig1_reduced, _ = reduce_audited_capture_operations(
        rig1,
        {
            "iso": "100",
            "capturemode": "Single Shot",
            "shutterspeed": "1/250",
        },
    )

    rig2_reduced, _ = reduce_audited_capture_operations(
        rig2,
        {
            "iso": "100",
            "capturemode": "Single Shot",
            "shutterspeed": "1/250",
        },
    )

    merged = merge_scheduled_operations({
        1: schedule_audited_capture(
            rig1_reduced,
            timing,
        ),
        2: schedule_audited_capture(
            rig2_reduced,
            timing,
        ),
    })

    triggers = [
        event
        for event in merged
        if event.operation.get("action") == "bracket_press"
    ]

    assert len(triggers) == 2

    assert triggers[0].command_time == datetime(
        2027, 8, 2, 10, 3, 59, 720000
    )
    assert triggers[1].command_time == datetime(
        2027, 8, 2, 10, 3, 59, 720000
    )

    assert [event.rig_id for event in triggers] == [1, 2]


def test_global_merge_does_not_delay_second_rig():
    target = datetime(2027, 8, 2, 10, 4, 0)

    rig1 = _audited_sony_for_rig(1, target)
    rig2 = _audited_sony_for_rig(2, target)

    merged, _states = compile_and_merge_scheduled_rigs(
        {
            1: [rig1],
            2: [rig2],
        },
        initial_states={
            1: {
                "iso": "100",
                "capturemode": "Single Shot",
                "shutterspeed": "1/250",
            },
            2: {
                "iso": "100",
                "capturemode": "Single Shot",
                "shutterspeed": "1/250",
            },
        },
        timing_profiles={
            "sony": _sony_test_timing(),
        },
    )

    triggers = [
        event
        for event in merged
        if event.operation.get("action") == "bracket_press"
    ]

    assert len(triggers) == 2
    assert triggers[0].command_time == triggers[1].command_time


def test_rig_state_reduction_is_independent_before_merge():
    target = datetime(2027, 8, 2, 10, 4, 0)

    rig1 = _audited_sony_for_rig(1, target)
    rig2 = _audited_sony_for_rig(2, target)

    merged, states = compile_and_merge_scheduled_rigs(
        {
            1: [rig1],
            2: [rig2],
        },
        initial_states={
            1: {
                "iso": "100",
                "capturemode": "Single Shot",
                "shutterspeed": "1/250",
            },
            2: {
                "iso": "200",
                "capturemode": "Single Shot",
                "shutterspeed": "1/250",
            },
        },
        timing_profiles={
            "sony": _sony_test_timing(),
        },
    )

    rig1_iso_sets = [
        event
        for event in merged
        if event.rig_id == 1
        and event.operation.get("action") == "set"
        and event.operation.get("parameter") == "iso"
    ]

    rig2_iso_sets = [
        event
        for event in merged
        if event.rig_id == 2
        and event.operation.get("action") == "set"
        and event.operation.get("parameter") == "iso"
    ]

    assert rig1_iso_sets == []
    assert len(rig2_iso_sets) == 1
    assert rig2_iso_sets[0].operation["value"] == "100"

    assert states[1]["iso"] == "100"
    assert states[2]["iso"] == "100"


def test_merge_orders_different_rig_command_times_globally():
    target = datetime(2027, 8, 2, 10, 4, 0)

    rig1 = _audited_sony_for_rig(1, target)
    rig2 = _audited_sony_for_rig(2, target)

    merged, _states = compile_and_merge_scheduled_rigs(
        {
            1: [rig1],
            2: [rig2],
        },
        initial_states={
            1: {
                "iso": "100",
                "capturemode": "Single Shot",
                "shutterspeed": "1/250",
            },
            2: {
                "iso": "100",
                "capturemode": "Single Shot",
                "shutterspeed": "1/250",
            },
        },
        timing_profiles={
            "sony": CameraTimingProfile(
                backend="sony",
                set_capturemode_ms=120,
                bracket_press_latency_ms=280,
                bracket_atomic_ms_by_frames={
                    3: 3000,
                    5: 3200,
                    7: 3600,
                    9: 4000,
                },
            ),
        },
    )

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


from backend.sequencer_compiler import (
    build_execution_plan_document,
    format_execution_plan_lines,
)


def _two_rig_merged_plan():
    target = datetime(2027, 8, 2, 10, 4, 0)

    rig1 = _audited_sony_for_rig(1, target)
    rig2 = _audited_sony_for_rig(2, target)

    initial_states = {
        1: {
            "exposuremode": "Manual",
            "iso": "100",
            "capturemode": "Single Shot",
            "shutterspeed": "1/250",
        },
        2: {
            "exposuremode": "Manual",
            "iso": "100",
            "capturemode": "Single Shot",
            "shutterspeed": "1/250",
        },
    }

    merged, final_states = compile_and_merge_scheduled_rigs(
        {
            1: [rig1],
            2: [rig2],
        },
        initial_states=initial_states,
        timing_profiles={
            "sony": _sony_test_timing(),
        },
    )

    return merged, initial_states, final_states


def test_execution_plan_is_json_serializable():
    import json

    merged, initial_states, final_states = (
        _two_rig_merged_plan()
    )

    plan = build_execution_plan_document(
        merged,
        initial_states=initial_states,
        final_states=final_states,
        circumstances_file="dryrun_short.json",
        photo_setup_file="photo_dryrun_short.json",
        exposure_opt_file="expo_exposure_dryrun_short.json",
    )

    encoded = json.dumps(plan)

    assert encoded
    assert plan["schema_version"] == 2
    assert plan["config_type"] == "execution_plan"
    assert plan["commands"]

    assert all(
        command["time_utc"].endswith("Z")
        for command in plan["commands"]
    )

    assert {
        command["action"]
        for command in plan["commands"]
    } <= {"SET", "PHOTO"}


def test_execution_plan_keeps_trigger_initial_state_as_requirement_only():
    merged, initial_states, final_states = (
        _two_rig_merged_plan()
    )

    plan = build_execution_plan_document(
        merged,
        initial_states=initial_states,
        final_states=final_states,
    )

    assert plan["initial_state_required"]["1"] == {
        "exposuremode": "Manual",
        "iso": "100",
        "capturemode": "Single Shot",
        "shutterspeed": "1/250",
    }

    # Initial state is established before execution and must not be
    # duplicated as fake timed commands.
    rig1_iso_sets = [
        command
        for command in plan["commands"]
        if command["rig_id"] == 1
        and command["action"] == "SET"
        and command["params"].get("parameter") == "iso"
    ]

    assert rig1_iso_sets == []


def test_execution_plan_preserves_simultaneous_multirig_targets():
    merged, initial_states, final_states = (
        _two_rig_merged_plan()
    )

    plan = build_execution_plan_document(
        merged,
        initial_states=initial_states,
        final_states=final_states,
    )

    photos = [
        command
        for command in plan["commands"]
        if command["action"] == "PHOTO"
    ]

    assert [
        (command["rig_id"], command["time_utc"])
        for command in photos
    ] == [
        (1, "2027-08-02T10:03:59.720Z"),
        (2, "2027-08-02T10:03:59.720Z"),
    ]


def test_execution_plan_text_exposes_real_sony_bracket():
    merged, initial_states, final_states = (
        _two_rig_merged_plan()
    )

    plan = build_execution_plan_document(
        merged,
        initial_states=initial_states,
        final_states=final_states,
    )

    lines = format_execution_plan_lines(plan)

    assert (
        "2027-08-02T10:03:59.600Z | RIG1 | "
        "SET capturemode=Continuous Bracket 1.0 EV 5 Img."
    ) in lines

    assert (
        "2027-08-02T10:03:59.720Z | RIG1 | PHOTO 1/250"
    ) in lines

    # Plugin/runtime protocol details never enter the Trigger command stream.
    text = "\n".join(lines)

    for forbidden in (
        "EXPECT",
        "BRACKET RELEASE",
        "SETTLE IDLE",
        "RUNTIME",
        "TARGET",
    ):
        assert forbidden not in text


from backend.sequencer_compiler import derive_initial_state_required


def test_initial_state_is_derived_from_first_capture_only():
    first = audit_materialized_sony_capture(
        _sony_materialized_capture()
    )

    initial = derive_initial_state_required({
        1: [first],
    })

    assert initial == {
        1: {
            "iso": "100",
            "capturemode": "Single Shot",
            "shutterspeed": "1/250",
        },
    }


def test_initial_state_is_independent_per_rig():
    rig1 = audit_materialized_sony_capture(
        _sony_materialized_capture()
    )

    from dataclasses import replace

    rig2 = replace(
        rig1,
        rig_id=2,
    )

    initial = derive_initial_state_required({
        1: [rig1],
        2: [rig2],
    })

    assert initial[1] == initial[2]
    assert initial[1] is not initial[2]


def test_multirig_can_use_different_timing_for_same_backend():
    target = datetime(2027, 8, 2, 10, 4, 0)

    rig1 = _audited_sony_for_rig(1, target)
    rig2 = _audited_sony_for_rig(2, target)

    merged, _states = compile_and_merge_scheduled_rigs(
        {
            1: [rig1],
            2: [rig2],
        },
        initial_states={
            1: {
                "iso": "100",
                "capturemode": "Single Shot",
                "shutterspeed": "1/250",
            },
            2: {
                "iso": "100",
                "capturemode": "Single Shot",
                "shutterspeed": "1/250",
            },
        },
        timing_profiles={
            1: CameraTimingProfile(
                backend="sony",
                set_capturemode_ms=120,
                bracket_press_latency_ms=275,
                bracket_atomic_ms_by_frames={
                    3: 3000,
                    5: 3200,
                    7: 3600,
                    9: 4000,
                },
            ),
            2: CameraTimingProfile(
                backend="sony",
                set_capturemode_ms=120,
                bracket_press_latency_ms=291,
                bracket_atomic_ms_by_frames={
                    3: 3000,
                    5: 3200,
                    7: 3600,
                    9: 4000,
                },
            ),
        },
    )

    triggers = [
        event
        for event in merged
        if event.operation.get("action") == "bracket_press"
    ]

    assert [
        (event.rig_id, event.command_time)
        for event in triggers
    ] == [
        (
            2,
            datetime(
                2027, 8, 2,
                10, 3, 59, 709000,
            ),
        ),
        (
            1,
            datetime(
                2027, 8, 2,
                10, 3, 59, 725000,
            ),
        ),
    ]


def test_execution_plan_format_keeps_runtime_post_trigger_with_its_capture():
    audited = audit_materialized_sony_capture(
        _sony_materialized_capture()
    )

    reduced, _state = reduce_audited_capture_operations(
        audited,
        {
            "iso": "100",
            "capturemode": "Single Shot",
            "shutterspeed": "1/250",
        },
    )

    scheduled = schedule_audited_capture(
        reduced,
        _sony_test_timing(),
    )

    merged = merge_scheduled_operations({
        1: scheduled,
    })

    plan = build_execution_plan_document(
        merged,
        initial_states={
            1: {
                "iso": "100",
                "capturemode": "Single Shot",
                "shutterspeed": "1/250",
            },
        },
    )

    # The final Trigger plan contains only executable physical commands.
    assert plan["commands"]

    assert {
        command["action"]
        for command in plan["commands"]
    } <= {"SET", "PHOTO"}

    assert all(
        command["time_utc"].endswith("Z")
        for command in plan["commands"]
    )

    lines = format_execution_plan_lines(plan)
    text = "\n".join(lines)

    assert "PHOTO" in text
    assert "EXPECT" not in text
    assert "BRACKET RELEASE" not in text
    assert "SETTLE IDLE" not in text
    assert "RUNTIME" not in text




def test_periodic_bracket_is_skipped_when_atomic_end_exceeds_deadline():
    from dataclasses import replace
    from datetime import datetime

    capture = _audited_sony_for_rig(
        1,
        datetime(2027, 8, 2, 10, 1, 57),
    )

    capture = replace(
        capture,
        target=replace(
            capture.target,
            phase="diamond_ring",
            phase_window="phase_1b",
            sequence_index=9,
            deadline=datetime(2027, 8, 2, 10, 2, 0),
        ),
    )

    timing = replace(
        _sony_test_timing(),
        bracket_atomic_ms_by_frames={
            3: 3000,
            5: 4000,
            7: 4200,
            9: 4500,
        },
    )

    merged, _states = compile_and_merge_scheduled_rigs(
        {1: [capture]},
        initial_states={
            1: {
                "iso": "100",
                "capturemode": "Single Shot",
                "shutterspeed": "1/250",
            },
        },
        timing_profiles={
            "sony": timing,
        },
    )

    triggers = [
        event
        for event in merged
        if event.operation.get("action") == "bracket_press"
    ]

    assert triggers == []

def test_periodic_bracket_is_kept_when_atomic_end_fits_deadline():
    from dataclasses import replace
    from datetime import datetime

    capture = _audited_sony_for_rig(
        1,
        datetime(2027, 8, 2, 10, 1, 54),
    )

    capture = replace(
        capture,
        target=replace(
            capture.target,
            phase="diamond_ring",
            phase_window="phase_1b",
            sequence_index=8,
            deadline=datetime(2027, 8, 2, 10, 1, 57),
        ),
    )

    merged, _states = compile_and_merge_scheduled_rigs(
        {1: [capture]},
        initial_states={
            1: {
                "iso": "100",
                "capturemode": "Single Shot",
                "shutterspeed": "1/250",
            },
        },
        timing_profiles={
            "sony": _sony_test_timing(),
        },
    )

    triggers = [
        event
        for event in merged
        if event.operation.get("action") == "bracket_press"
    ]

    assert len(triggers) == 1


def test_pre_c2_bracket_is_skipped_if_it_blocks_totality_preparation():
    from dataclasses import replace
    from datetime import datetime

    base = audit_materialized_sony_capture(
        _sony_materialized_capture()
    )

    pre_c2 = replace(
        base,
        target=replace(
            base.target,
            phase="diamond_ring",
            phase_window="phase_1b",
            sequence_index=0,
            target_time=datetime(2027, 8, 2, 10, 4, 0),
            deadline=datetime(2027, 8, 2, 10, 4, 3),
        ),
    )

    totality = replace(
        base,
        target=replace(
            base.target,
            phase="totality",
            phase_window="phase_2",
            sequence_index=0,
            target_time=datetime(2027, 8, 2, 10, 4, 3),
            deadline=datetime(2027, 8, 2, 10, 4, 10),
        ),
    )

    c3 = replace(
        base,
        target=replace(
            base.target,
            phase="diamond_ring",
            phase_window="phase_3a",
            sequence_index=0,
            target_time=datetime(2027, 8, 2, 10, 4, 10),
            deadline=datetime(2027, 8, 2, 10, 4, 13),
        ),
    )

    captures = [pre_c2, totality, c3]

    initial = derive_initial_state_required({
        1: captures,
    })

    merged, _states = compile_and_merge_scheduled_rigs(
        {
            1: captures,
        },
        initial_states=initial,
        timing_profiles={
            1: _sony_test_timing(),
        },
    )

    pre_c2_photos = [
        event
        for event in merged
        if event.phase_window == "phase_1b"
        and event.operation.get("action")
        in {"trigger_capture", "bracket_press"}
    ]

    totality_photos = [
        event
        for event in merged
        if event.phase_window == "phase_2"
        and event.operation.get("action")
        in {"trigger_capture", "bracket_press"}
    ]

    c3_photos = [
        event
        for event in merged
        if event.phase_window == "phase_3a"
        and event.sequence_index == 0
        and event.operation.get("action")
        in {"trigger_capture", "bracket_press"}
    ]

    # The DR itself fits before C2, but its atomic reservation would occupy
    # time required to prepare the first TOTALITY PHOTO. It must therefore
    # never be started.
    assert pre_c2_photos == []

    # C2 and C3 remain executable hard anchors.
    assert totality_photos
    assert c3_photos
