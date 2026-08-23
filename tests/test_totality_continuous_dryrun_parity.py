"""Parity and grid non-regression coverage for the totality scheduler."""

from datetime import datetime, timedelta
import sys
import types


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

saved_argv = sys.argv
sys.argv = [sys.argv[0]]
from plugins.camera.base import CaptureResult
from scripts import eclipse_trigger as trigger
sys.argv = saved_argv


SPEEDS = ["1/4000", "1/2000", "1/1000", "1/500", "1/250"]


class FakeClock:
    def __init__(self, value):
        self.value = value

    def now(self):
        return self.value


class TracingCameraService:
    def __init__(self, clock, accepted_sizes, duration_s=0.2):
        self.clock = clock
        self.accepted_sizes = set(accepted_sizes)
        self.duration_s = duration_s
        self.settings = []
        self.attempts = []
        self.triggered = []

    def apply_phase_settings(self, aperture=None, iso=None):
        self.settings.append((aperture, iso))

    def prepare_capture(self, intent):
        selected = list(intent.speeds)
        self.attempts.append((self.clock.now(), selected, intent))
        if len(selected) not in self.accepted_sizes:
            raise RuntimeError(f"test service refuses M={len(selected)}")
        return types.SimpleNamespace(
            token=intent,
            estimated_total_s=self.duration_s,
            exposures_s=[trigger.parse_shutterspeed(speed) for speed in selected],
            planned_count=len(selected),
            plugin_name="tracing-test-double",
        )

    def trigger_prepared(self, prepared, deadline=None):
        selected = list(prepared.token.speeds)
        self.triggered.append((self.clock.now(), selected, deadline))
        self.clock.value += timedelta(seconds=self.duration_s)
        return CaptureResult(
            frames=len(selected), planned=len(selected), detail="test double"
        )


def _run_continuous(monkeypatch, start, accepted_sizes):
    phase_end = start + timedelta(seconds=0.6)
    clock = FakeClock(start)
    service = TracingCameraService(clock, accepted_sizes)
    logs = []
    waits = []
    monkeypatch.setattr(trigger, "now", clock.now)
    monkeypatch.setattr(trigger, "_log", logs.append)
    monkeypatch.setattr(trigger, "_watchdog_write", lambda *_args: None)

    def wait_until(_service, target, deadline=None):
        waits.append((clock.now(), target, deadline))
        clock.value = max(clock.value, target)

    monkeypatch.setattr(trigger, "_usb_wait_or_hold", wait_until)

    photo_num = trigger._run_continuous_totality(
        service, SPEEDS, start, phase_end, photo_num_start=10
    )
    return service, logs, waits, photo_num, phase_end


def _relative_attempts(service, origin):
    return [
        ((attempted_at - origin).total_seconds(), selected)
        for attempted_at, selected, _intent in service.attempts
    ]


def _relative_triggers(service, origin, phase_end):
    return [
        (
            (triggered_at - origin).total_seconds(),
            selected,
            (deadline - phase_end).total_seconds(),
        )
        for triggered_at, selected, deadline in service.triggered
    ]


def test_continuous_real_and_dry_run_select_and_trigger_identical_subsets(
    monkeypatch,
):
    real_start = datetime(2026, 8, 12, 20, 30, 0)
    dry_run_start = datetime(2030, 1, 2, 3, 4, 5)

    real = _run_continuous(monkeypatch, real_start, {3})
    dry_run = _run_continuous(monkeypatch, dry_run_start, {3})

    assert _relative_attempts(real[0], real_start) == _relative_attempts(
        dry_run[0], dry_run_start
    )
    assert _relative_triggers(real[0], real_start, real[4]) == _relative_triggers(
        dry_run[0], dry_run_start, dry_run[4]
    )
    assert [len(selected) for _, selected, _intent in real[0].attempts] == [
        5, 4, 3, 5, 4, 3, 5, 4, 3
    ]
    assert real[3] == dry_run[3] == 19


def test_continuous_real_and_dry_run_stop_identically_without_admissible_subset(
    monkeypatch,
):
    real_start = datetime(2026, 8, 12, 20, 30, 0)
    dry_run_start = datetime(2030, 1, 2, 3, 4, 5)

    real = _run_continuous(monkeypatch, real_start, set())
    dry_run = _run_continuous(monkeypatch, dry_run_start, set())

    assert _relative_attempts(real[0], real_start) == _relative_attempts(
        dry_run[0], dry_run_start
    )
    assert [len(selected) for _, selected, _intent in real[0].attempts] == [
        5, 4, 3, 2, 1, 1, 1, 1, 1
    ]
    assert real[0].triggered == dry_run[0].triggered == []
    assert real[3] == dry_run[3] == 10
    assert len(real[2]) == len(dry_run[2]) == 1
    assert real[2][0][1:] == (real[4], real[4])
    assert dry_run[2][0][1:] == (dry_run[4], dry_run[4])
    assert sum("reason=no_admissible_subset" in line for line in real[1]) == 1
    assert sum("reason=no_admissible_subset" in line for line in dry_run[1]) == 1


def test_absolute_grid_interval_keeps_prepare_trigger_and_log_semantics(monkeypatch):
    start = datetime(2026, 8, 12, 20, 0, 0)
    clock = FakeClock(start)
    service = TracingCameraService(clock, {1}, duration_s=0.0)
    logs = []
    monkeypatch.setattr(trigger, "now", clock.now)
    monkeypatch.setattr(trigger, "_log", logs.append)
    monkeypatch.setattr(trigger, "_watchdog_write", lambda *_args: None)
    monkeypatch.setattr(
        trigger,
        "_prepare_totality_sub_bracket",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("a non-totality grid must not use subset adaptation")
        ),
    )

    def wait_until(_service, target, deadline=None):
        clock.value = max(clock.value, target)

    monkeypatch.setattr(trigger, "_usb_wait_or_hold", wait_until)

    photo_num = trigger._run_absolute_grid(
        service,
        "partial",
        ["1/500"],
        start + timedelta(seconds=2),
        start + timedelta(seconds=9),
        3,
        aperture="f/8",
        iso="100",
        photo_num_start=7,
    )

    targets = [start + timedelta(seconds=offset) for offset in (2, 5, 8)]
    assert service.settings == [("f/8", "100")]
    assert [intent.phase for _, _, intent in service.attempts] == ["partial"] * 3
    assert [intent.target_time for _, _, intent in service.attempts] == targets
    assert [at for at, _, _deadline in service.triggered] == targets
    assert [selected for _, selected, _deadline in service.triggered] == [
        ["1/500"]
    ] * 3
    assert photo_num == 10
    joined = "\n".join(logs)
    for key in (
        "prep_start=",
        "prep_end=",
        "wait_start=",
        "shutter_cmd=",
        "shutter_return=",
        "events_retrieval_complete=",
        "settle_complete=",
        "total_duration_s=",
        "trigger_minus_target_s=",
    ):
        assert key in joined
    assert "reason=no_admissible_subset" not in joined
