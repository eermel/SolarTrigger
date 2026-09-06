from datetime import datetime, timedelta

import pytest

from backend.anchor_sequencer import (
    _make_contact_anchor,
    _pack_complete_totality_cycles,
    _validate_contact_capture,
)
from backend.sequencer_compiler import (
    AuditedRigCapture,
    CameraTimingProfile,
    CaptureTarget,
    SequenceWindow,
)


def _capture(target_time, shutters, *, duration_ms=2750.0, phase="diamond_ring", window="phase_3a"):
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
        exposure_plan=tuple(
            {"shutter": shutter, "iso": 100}
            for shutter in shutters
        ),
        prepared_mode="profile",
        estimated_total_s=duration_ms / 1000.0,
        planned_count=len(shutters),
        operations=(
            {
                "action": "trigger_capture",
                "shutter": shutters[0],
                "duration_ms": duration_ms,
                "timing_contract_version": 2,
            },
        ),
    )


def _profile():
    return CameraTimingProfile(
        backend="profile-test",
        trigger_single_latency_ms=0.0,
        trigger_single_duration_ms=0.0,
    )


def test_c3_contact_anchor_is_built_before_other_phases_and_crosses_contact():
    contact = datetime(2027, 8, 2, 10, 0, 0)
    shutters = ["1/8000", "1/4000", "1/2000", "1/1000", "1/500"]

    def factory(phase, phase_window, target_time, sequence_index, deadline):
        return _capture(target_time, shutters, phase=phase, window=phase_window)

    capture, _scheduled, start, end = _make_contact_anchor(
        factory,
        _profile(),
        contact_time=contact,
        phase_window="phase_3a",
        c3=True,
    )

    assert capture.target.target_time == contact - timedelta(seconds=1)
    assert start == capture.target.target_time
    assert end > contact


def test_c3_contact_rejects_exposure_slower_than_1_500():
    capture = _capture(
        datetime(2027, 8, 2, 10, 0, 0),
        ["1/8000", "1/4000", "1/2000", "1/1000", "1/250"],
    )
    with pytest.raises(ValueError, match="C3 contact exposure slower than 1/500"):
        _validate_contact_capture(capture, c3=True)


def test_contact_rejects_more_than_five_views():
    capture = _capture(
        datetime(2027, 8, 2, 10, 0, 0),
        ["1/16000", "1/8000", "1/4000", "1/2000", "1/1000", "1/500"],
    )
    with pytest.raises(ValueError, match="contact PHOTO exceeds 5 exposures"):
        _validate_contact_capture(capture, c3=False)


def test_totality_accepts_only_complete_cycles_between_reserved_contacts():
    start = datetime(2027, 8, 2, 10, 0, 0)
    window = SequenceWindow(
        name="phase_2",
        phase="totality",
        start=start,
        end=start + timedelta(seconds=10),
        interval_s=None,
    )

    def factory(phase, phase_window, target_time, sequence_index, deadline):
        # One physical 1-second PHOTO represents one complete synthetic cycle.
        return _capture(
            target_time,
            ["1/1000"],
            duration_ms=1000.0,
            phase=phase,
            window=phase_window,
        )

    captures, _state = _pack_complete_totality_cycles(
        factory,
        _profile(),
        window,
        boundary_start=start + timedelta(seconds=1),
        boundary_end=start + timedelta(seconds=4, milliseconds=500),
        initial_state={},
    )

    assert len(captures) == 3
    assert captures[0].target.target_time == start + timedelta(seconds=1)
    assert captures[-1].target.target_time == start + timedelta(seconds=3)
