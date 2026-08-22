"""Integration-like tests for the eclipse engine's absolute-grid scheduler."""

from datetime import datetime, timedelta
import sys
import types

import pytest


if "gphoto2" not in sys.modules:
    fake_gphoto2 = types.ModuleType("gphoto2")
    fake_gphoto2.GPhoto2Error = RuntimeError
    fake_gphoto2.GP_LOG_ERROR = 0
    fake_gphoto2.GP_LOG_VERBOSE = 1
    fake_gphoto2.GP_LOG_DEBUG = 2
    fake_gphoto2.GP_LOG_DATA = 3
    fake_gphoto2.use_python_logging = lambda mapping=None: None
    fake_gphoto2.check_result = lambda *args, **kwargs: None
    sys.modules["gphoto2"] = fake_gphoto2

_argv = sys.argv
sys.argv = [sys.argv[0]]
from plugins.camera.base import CaptureResult
from scripts import eclipse_trigger as trigger
sys.argv = _argv


class FakeClock:
    def __init__(self, value):
        self.value = value

    def now(self):
        return self.value


class FakeCameraService:
    def __init__(self, clock, durations=(), prepare_errors=(), trigger_errors=()):
        self.clock = clock
        self.durations = iter(durations)
        self.prepare_errors = set(prepare_errors)
        self.trigger_errors = set(trigger_errors)
        self.settings = []
        self.prepared_at = []
        self.triggered_at = []

    def apply_phase_settings(self, aperture=None, iso=None):
        self.settings.append((aperture, iso))

    def prepare_capture(self, intent):
        index = len(self.prepared_at)
        self.prepared_at.append((self.clock.now(), intent.target_time))
        if index in self.prepare_errors:
            raise RuntimeError("prepare failed")
        return types.SimpleNamespace(token=intent)

    def trigger_prepared(self, prepared, deadline=None):
        index = len(self.triggered_at)
        self.triggered_at.append((self.clock.now(), prepared.token.target_time))
        if index in self.trigger_errors:
            raise RuntimeError("trigger failed")
        self.clock.value += timedelta(seconds=next(self.durations, 0))
        return CaptureResult(frames=1, planned=1, detail="fake")


@pytest.fixture
def scheduler(monkeypatch):
    start = datetime(2026, 8, 12, 20, 0, 0)
    clock = FakeClock(start)
    logs = []
    monkeypatch.setattr(trigger, "now", clock.now)
    monkeypatch.setattr(trigger, "_log", logs.append)
    monkeypatch.setattr(trigger, "_watchdog_write", lambda *args: None)

    def wait_until(_service, target, deadline=None):
        if clock.value < target:
            clock.value = target

    monkeypatch.setattr(trigger, "_usb_wait_or_hold", wait_until)
    return start, clock, logs


@pytest.mark.parametrize("execution_path", ["simulate", "dry-run"])
def test_prepare_wait_trigger_keeps_absolute_targets_and_structured_logs(
    scheduler, execution_path, monkeypatch
):
    start, clock, logs = scheduler
    service = FakeCameraService(clock, durations=[3, 0])
    # Dry-run retains the real-mode clock after rebasing; simulation uses the
    # virtual clock. Both must execute this same scheduler implementation.
    monkeypatch.setattr(trigger, "_sim_mode", execution_path == "simulate")

    trigger._run_absolute_grid(
        service, execution_path, ["1/500"], start + timedelta(seconds=10),
        start + timedelta(seconds=30), 10, aperture="f/8", iso="100",
    )

    targets = [start + timedelta(seconds=10), start + timedelta(seconds=20)]
    assert service.settings == [("f/8", "100")]
    assert [target for _, target in service.prepared_at] == targets
    assert [at for at, _ in service.prepared_at] == [start, targets[0] + timedelta(seconds=3)]
    assert [at for at, _ in service.triggered_at] == targets
    joined = "\n".join(logs)
    assert "prep_start=" in joined and "wait_start=" in joined
    assert "shutter_cmd=" in joined and "shutter_return=" in joined
    assert "events_retrieval_complete=" in joined and "settle_complete=" in joined
    assert "total_duration_s=" in joined and "trigger_minus_target_s=" in joined


@pytest.mark.parametrize(
    ("first_duration", "expected_targets", "expected_second_trigger", "missed_slots"),
    [
        (12, [10, 20, 30], 22, None),
        (25, [10, 30, 40], 35, 1),
    ],
)
def test_late_and_fully_missed_slots_do_not_shift_grid(
    scheduler, first_duration, expected_targets, expected_second_trigger, missed_slots
):
    start, clock, logs = scheduler
    service = FakeCameraService(clock, durations=[first_duration, 0, 0])

    trigger._run_absolute_grid(
        service, "phase", ["1/500"], start + timedelta(seconds=10),
        start + timedelta(seconds=50), 10,
    )

    offsets = [int((target - start).total_seconds()) for _, target in service.triggered_at]
    assert offsets[:3] == expected_targets
    assert service.triggered_at[1][0] == start + timedelta(seconds=expected_second_trigger)
    joined = "\n".join(logs)
    assert "target_delay_s=" in joined
    if missed_slots is not None:
        assert f"missed_slots={missed_slots}" in joined


@pytest.mark.parametrize(("stage", "kwargs"), [
    ("prepare", {"prepare_errors": [0]}),
    ("trigger", {"trigger_errors": [0]}),
])
def test_camera_error_logs_and_continues_on_next_absolute_slot(scheduler, stage, kwargs):
    start, clock, logs = scheduler
    service = FakeCameraService(clock, durations=[0, 0], **kwargs)

    trigger._run_absolute_grid(
        service, "phase", ["1/500"], start + timedelta(seconds=10),
        start + timedelta(seconds=31), 10,
    )

    assert any(f"stage={stage}" in line for line in logs)
    successful_targets = [target for _, target in service.triggered_at]
    assert start + timedelta(seconds=20) in successful_targets


def _prepared(estimated_total_s, exposures_s):
    return types.SimpleNamespace(
        estimated_total_s=estimated_total_s,
        exposures_s=exposures_s,
    )


def test_c3_overflow_accepts_short_exposure_ending_plus_0_4s():
    c3 = datetime(2026, 8, 12, 20, 30, 0)
    target = c3 - timedelta(seconds=0.4)
    prepared = _prepared(
        estimated_total_s=0.8,
        exposures_s=[0.4],
    )

    deadline = trigger._c3_trigger_deadline(prepared, target, c3)

    assert deadline == c3 + timedelta(seconds=trigger.C3_OVERFLOW_GRACE_S)


def test_c3_overflow_accepts_plus_0_9s_when_crossing_exposures_are_short():
    c3 = datetime(2026, 8, 12, 20, 30, 0)
    target = c3 - timedelta(seconds=0.1)
    prepared = _prepared(
        estimated_total_s=1.0,
        exposures_s=[0.4, 0.4],
    )

    deadline = trigger._c3_trigger_deadline(prepared, target, c3)

    assert deadline == c3 + timedelta(seconds=trigger.C3_OVERFLOW_GRACE_S)


def test_c3_overflow_refuses_sequence_ending_plus_1_1s():
    c3 = datetime(2026, 8, 12, 20, 30, 0)
    target = c3 - timedelta(seconds=0.1)
    prepared = _prepared(
        estimated_total_s=1.2,
        exposures_s=[0.4, 0.4],
    )

    deadline = trigger._c3_trigger_deadline(prepared, target, c3)

    assert deadline is None


def test_c3_overflow_refuses_one_second_exposure_crossing_c3():
    c3 = datetime(2026, 8, 12, 20, 30, 0)
    target = c3 - timedelta(seconds=0.2)
    prepared = _prepared(
        estimated_total_s=1.0,
        exposures_s=[1.0],
    )

    deadline = trigger._c3_trigger_deadline(prepared, target, c3)

    assert deadline is None


def test_c3_overflow_without_estimates_falls_back_to_strict_c3():
    c3 = datetime(2026, 8, 12, 20, 30, 0)
    target = c3 - timedelta(seconds=0.2)
    prepared = types.SimpleNamespace(
        estimated_total_s=None,
        exposures_s=None,
    )

    deadline = trigger._c3_trigger_deadline(prepared, target, c3)

    assert deadline == c3


def test_absolute_grid_logs_c3_overflow_accepted(scheduler):
    start, clock, logs = scheduler

    class EstimatedCameraService(FakeCameraService):
        def prepare_capture(self, intent):
            self.prepared_at.append((self.clock.now(), intent.target_time))
            return types.SimpleNamespace(
                token=intent,
                estimated_total_s=0.8,
                exposures_s=[0.4],
            )

    service = EstimatedCameraService(clock, durations=[0])

    c3 = start + timedelta(seconds=10)
    target = c3 - timedelta(seconds=0.4)

    trigger._run_absolute_grid(
        service,
        "phase2",
        ["1/500"],
        target,
        c3,
        10,
        deadline=c3,
    )

    joined = "\n".join(logs)
    assert "c3_overflow=accepted" in joined
    assert "hard_deadline=" in joined


def test_absolute_grid_logs_c3_overflow_refused(scheduler):
    start, clock, logs = scheduler

    class EstimatedCameraService(FakeCameraService):
        def prepare_capture(self, intent):
            self.prepared_at.append((self.clock.now(), intent.target_time))
            return types.SimpleNamespace(
                token=intent,
                estimated_total_s=1.2,
                exposures_s=[0.4, 0.4],
            )

    service = EstimatedCameraService(clock, durations=[0])

    c3 = start + timedelta(seconds=10)
    target = c3 - timedelta(seconds=0.1)

    trigger._run_absolute_grid(
        service,
        "phase2",
        ["1/500"],
        target,
        c3,
        10,
        deadline=c3,
    )

    joined = "\n".join(logs)
    assert "c3_overflow=refused" in joined


def test_absolute_grid_logs_legacy_strict_when_estimates_missing(scheduler):
    start, clock, logs = scheduler

    class LegacyCameraService(FakeCameraService):
        def prepare_capture(self, intent):
            self.prepared_at.append((self.clock.now(), intent.target_time))
            return types.SimpleNamespace(
                token=intent,
                estimated_total_s=None,
                exposures_s=None,
            )

    service = LegacyCameraService(clock, durations=[0])

    c3 = start + timedelta(seconds=10)
    target = c3 - timedelta(seconds=0.2)

    trigger._run_absolute_grid(
        service,
        "phase2",
        ["1/500"],
        target,
        c3,
        10,
        deadline=c3,
    )

    joined = "\n".join(logs)
    assert "c3_overflow=legacy_strict" in joined


@pytest.mark.parametrize("execution_path", ["real", "dry-run"])
def test_totality_sub_bracket_selects_first_accepted_candidate(
    execution_path, monkeypatch
):
    target = datetime(2026, 8, 12, 20, 29, 59)
    c3 = target + timedelta(seconds=1)
    configured = [f"1/{4000 // (index + 1)}" for index in range(9)]

    class SelectiveCameraService:
        def __init__(self):
            self.attempted = []

        def prepare_capture(self, intent):
            self.attempted.append(intent.speeds)
            if len(intent.speeds) != 8:
                raise RuntimeError("candidate refused")
            return types.SimpleNamespace(
                token=intent,
                estimated_total_s=0.5,
                exposures_s=[0.01] * len(intent.speeds),
            )

    monkeypatch.setattr(trigger, "_sim_mode", execution_path == "dry-run")
    service = SelectiveCameraService()

    prepared, deadline = trigger._prepare_totality_sub_bracket(
        service, configured, target, c3,
    )

    expected_indices = trigger._select_uniform_indices(configured, 8)
    expected = [configured[index] for index in expected_indices]
    assert [len(candidate) for candidate in service.attempted] == [9, 8]
    assert prepared.token.speeds == expected
    assert len(prepared.token.speeds) == 8
    assert deadline == c3


def test_totality_sub_bracket_selects_longest_admissible_single(monkeypatch):
    target = datetime(2026, 8, 12, 20, 29, 59, 900000)
    c3 = target + timedelta(seconds=0.1)
    configured = ["1/4000", "1/1000", "1/250"]
    monkeypatch.setattr(trigger, "now", lambda: target)

    class SingleOnlyCameraService:
        def __init__(self):
            self.attempted = []

        def prepare_capture(self, intent):
            self.attempted.append(intent.speeds)
            if len(intent.speeds) > 1:
                raise RuntimeError("bracket refused")
            exposure_s = trigger.parse_shutterspeed(intent.speeds[0])
            estimated_total_s = (
                1.2 if intent.speeds[0] == "1/250" else exposure_s + 0.098
            )
            return types.SimpleNamespace(
                token=intent,
                estimated_total_s=estimated_total_s,
                exposures_s=[exposure_s],
            )

    service = SingleOnlyCameraService()

    prepared, deadline = trigger._prepare_totality_sub_bracket(
        service, configured, target, c3,
    )

    assert service.attempted == [configured, [configured[0], configured[-1]],
                                 ["1/250"], ["1/1000"]]
    assert prepared.token.speeds == ["1/1000"]
    assert deadline == c3


def test_totality_sub_bracket_skips_when_no_single_is_admissible(
    scheduler, monkeypatch
):
    start, clock, logs = scheduler
    target = start + timedelta(seconds=1)
    c3 = target + timedelta(seconds=0.1)
    configured = ["1/1000", "1/250"]

    class NoAdmissibleCameraService(FakeCameraService):
        def prepare_capture(self, intent):
            self.prepared_at.append((self.clock.now(), intent.target_time))
            if len(intent.speeds) > 1:
                raise RuntimeError("bracket refused")
            return types.SimpleNamespace(
                token=intent,
                estimated_total_s=1.2,
                exposures_s=[trigger.parse_shutterspeed(intent.speeds[0])],
            )

    service = NoAdmissibleCameraService(clock)
    monkeypatch.setattr(trigger, "_usb_wait_or_hold", lambda *args, **kwargs: None)

    trigger._run_absolute_grid(
        service, "phase2", configured, target, c3, 1, deadline=c3,
    )

    assert service.triggered_at == []
    assert "reason=no_admissible_subset" in "\n".join(logs)
