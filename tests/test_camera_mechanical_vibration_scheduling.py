from datetime import datetime, timedelta

import pytest

from backend.anchor_sequencer import (
    _pack_totality_to_c3_anchor,
)
from backend.sequencer_compiler import (
    AuditedRigCapture,
    CameraTimingProfile,
    CaptureTarget,
    SequenceWindow,
    _mechanical_vibration_delay_delta,
    materialize_capture_target_for_rig,
)


BASE = datetime(2027, 8, 2, 10, 0, 0)


def _capture(
    target_time,
    shutter,
    *,
    phase="totality",
    window="phase_2",
    duration_ms=1000.0,
    strategy="sequential",
    vibration=False,
    delay_s=2,
):
    return AuditedRigCapture(
        rig_id=1,
        backend="profile-test",
        target=CaptureTarget(
            target_time=target_time,
            phase=phase,
            phase_window=window,
            sequence_index=0,
            deadline=None,
        ),
        aperture="f/8",
        exposure_plan=(
            {
                "shutter": shutter,
                "iso": 100,
            },
        ),
        prepared_mode="profile",
        estimated_total_s=duration_ms / 1000.0,
        planned_count=1,
        operations=(
            {
                "action": "trigger_capture",
                "shutter": shutter,
                "duration_ms": duration_ms,
                "timing_contract_version": 2,
            },
        ),
        camera_strategy=strategy,
        mechanical_vibration_enabled=vibration,
        mechanical_vibration_delay_s=delay_s,
    )


def _profile():
    return CameraTimingProfile(
        backend="profile-test",
    )


@pytest.mark.parametrize(
    ("shutter", "expected_s"),
    [
        ("1/80", 0.0),
        ("1/60", 2.0),
        ("1/30", 2.0),
        ("1", 2.0),
    ],
)
def test_sequential_threshold_is_1_60_inclusive(
    shutter,
    expected_s,
):
    capture = _capture(
        BASE,
        shutter,
        vibration=True,
        delay_s=2,
    )

    assert (
        _mechanical_vibration_delay_delta(capture).total_seconds()
        == pytest.approx(expected_s)
    )


def test_vibration_off_adds_no_delay():
    capture = _capture(
        BASE,
        "1/30",
        vibration=False,
        delay_s=2,
    )

    assert _mechanical_vibration_delay_delta(capture) == timedelta(0)


def test_zero_delay_adds_no_delay():
    capture = _capture(
        BASE,
        "1/30",
        vibration=True,
        delay_s=0,
    )

    assert _mechanical_vibration_delay_delta(capture) == timedelta(0)


def test_bracket_strategy_ignores_stored_vibration_policy():
    capture = _capture(
        BASE,
        "1/30",
        strategy="bracket",
        vibration=True,
        delay_s=5,
    )

    assert _mechanical_vibration_delay_delta(capture) == timedelta(0)


def test_non_totality_never_receives_mechanical_delay():
    capture = _capture(
        BASE,
        "1/30",
        phase="diamond_ring",
        window="phase_1b",
        vibration=True,
        delay_s=5,
    )

    assert _mechanical_vibration_delay_delta(capture) == timedelta(0)


def _pack(*, vibration, delay_s=2, strategy="sequential"):
    window = SequenceWindow(
        name="phase_2",
        phase="totality",
        start=BASE,
        end=BASE + timedelta(seconds=10),
        interval_s=None,
    )

    def factory(
        phase,
        phase_window,
        target_time,
        sequence_index,
        deadline,
    ):
        return _capture(
            target_time,
            "1/30",
            phase=phase,
            window=phase_window,
            duration_ms=1000.0,
            strategy=strategy,
            vibration=vibration,
            delay_s=delay_s,
        )

    c3_capture = _capture(
        BASE + timedelta(seconds=6),
        "1/500",
        phase="diamond_ring",
        window="phase_3a",
        duration_ms=1000.0,
        strategy=strategy,
        vibration=vibration,
        delay_s=delay_s,
    )

    captures, _state = _pack_totality_to_c3_anchor(
        factory,
        _profile(),
        window,
        boundary_start=BASE,
        c3_capture=c3_capture,
        margin=timedelta(milliseconds=250),
        initial_state={},
    )

    return captures


def test_settle_delay_reduces_photos_before_hard_c3_boundary():
    without_delay = _pack(
        vibration=False,
    )
    with_delay = _pack(
        vibration=True,
        delay_s=2,
    )

    assert len(without_delay) == 5
    assert len(with_delay) == 1


def test_bracket_strategy_does_not_reduce_totality_capacity():
    baseline = _pack(
        vibration=False,
        strategy="bracket",
    )
    bracket_with_stored_policy = _pack(
        vibration=True,
        delay_s=5,
        strategy="bracket",
    )

    assert len(bracket_with_stored_policy) == len(baseline)


def test_settle_gap_is_encoded_by_timestamps_not_wait_operations():
    captures = _pack(
        vibration=True,
        delay_s=2,
    )

    assert len(captures) == 1

    capture = captures[0]

    assert all(
        operation.get("action") != "delay"
        for operation in capture.operations
    )
    assert all(
        operation.get("action") != "wait"
        for operation in capture.operations
    )


def test_two_rigs_can_use_independent_vibration_policies():
    rig1 = _capture(
        BASE,
        "1/30",
        vibration=True,
        delay_s=2,
    )

    rig2 = AuditedRigCapture(
        **{
            **rig1.__dict__,
            "rig_id": 2,
            "mechanical_vibration_enabled": False,
        }
    )

    assert (
        _mechanical_vibration_delay_delta(rig1).total_seconds()
        == 2
    )
    assert _mechanical_vibration_delay_delta(rig2) == timedelta(0)


def test_invalid_enabled_sequential_delay_fails_closed():
    capture = _capture(
        BASE,
        "1/30",
        vibration=True,
        delay_s=2,
    )

    invalid = AuditedRigCapture(
        **{
            **capture.__dict__,
            "mechanical_vibration_delay_s": 6,
        }
    )

    with pytest.raises(
        ValueError,
        match="mechanical_vibration_delay_s",
    ):
        _mechanical_vibration_delay_delta(invalid)


def test_exposure_opt_override_becomes_materialized_vibration_policy():
    target = CaptureTarget(
        target_time=BASE,
        phase="totality",
        phase_window="phase_2",
        sequence_index=0,
        deadline=None,
    )

    rig = {
        "rig_id": 1,
        "devices": {
            "camera": {
                "backend": "profile-test",
            },
        },
        "optics": {},
        "photo": {
            "atmos_enabled": False,
            "anti_trailing_enabled": False,
            "mechanical_vibration_enabled": False,
            "mechanical_vibration_delay_s": 2,
            "motion_tolerance_px": 1.0,
            "iso_compensation_enabled": True,
            "iso_max": 6400,
        },
    }

    photo_config = {
        "phases": {
            "totality": {
                "enabled": True,
                "interval_s": 1,
                "iso": 100,
                "aperture": "f/8",
                "shutter_min": "1/30",
                "shutter_max": "1/30",
                "step_ev": 1.0,
            },
        },
    }

    exposure_opt = {
        "atmospheric_attenuation_enabled": False,
        "rigs": [
            {
                "rig_id": 1,
                "photo": {
                    "mechanical_vibration_enabled": True,
                    "mechanical_vibration_delay_s": 4,
                },
            },
        ],
    }

    capture = materialize_capture_target_for_rig(
        target,
        rig,
        photo_config,
        exposure_opt,
        {},
    )

    assert capture.mechanical_vibration_enabled is True
    assert capture.mechanical_vibration_delay_s == 4
