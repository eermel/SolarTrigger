"""Acceptance scenarios for FEAT-008's brand-neutral scheduler contract."""

from datetime import datetime, timedelta, timezone
import inspect
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
from backend.trigger_runtime import RuntimeClock
from plugins.camera.base import CameraPlugin, CaptureResult
from scripts import eclipse_trigger as trigger
from services.camera_service import CameraService, PreparedCapture
sys.argv = _argv


class ControllableTime:
    def __init__(self, start):
        self.start = start
        self.elapsed_s = 0.0

    def wall_now(self):
        return self.start.replace(tzinfo=timezone.utc)

    def monotonic(self):
        return self.elapsed_s

    def sleep(self, seconds):
        self.elapsed_s += seconds


class SpyCameraPlugin(CameraPlugin):
    """Shutter-free spy with controllable preparation and capture durations."""

    name = "feat008-spy"

    def __init__(
        self,
        fake_time,
        preparation_s=0.0,
        capture_s=0.0,
        exposures_s=None,
        estimated_total_s=None,
    ):
        super().__init__(camera=object(), log_fn=lambda _message: None)
        self.fake_time = fake_time
        self.preparation_s = preparation_s
        self.capture_s = capture_s
        self.exposures_s = exposures_s if exposures_s is not None else [0.01]
        self.estimated_total_s = (
            capture_s if estimated_total_s is None else estimated_total_s
        )
        self.setting_calls = []
        self.prepared = []
        self.triggered = []

    @staticmethod
    def matches(model_string):
        return True

    def init_settings(self, **_kwargs):
        return None

    def set_exposure_settings(self, aperture=None, iso=None):
        applied = {}
        if aperture is not None:
            applied["aperture"] = aperture
        if iso is not None:
            applied["iso"] = iso
        self.setting_calls.append(applied)
        return applied

    def prepare_capture(self, intent):
        self.prepared.append((self.fake_time.elapsed_s, intent))
        self.fake_time.sleep(self.preparation_s)
        return PreparedCapture(
            token=intent,
            estimated_total_s=self.estimated_total_s,
            exposures_s=list(self.exposures_s),
            planned_count=len(self.exposures_s),
            plugin_name=self.name,
        )

    def trigger_prepared(self, prepared, deadline=None):
        self.triggered.append(
            (self.fake_time.elapsed_s, prepared.token.target_time, deadline)
        )
        self.fake_time.sleep(self.capture_s)
        return CaptureResult(
            frames=len(self.exposures_s),
            planned=len(self.exposures_s),
            detail="spy",
        )

    def shoot_speeds(self, *_args, **_kwargs):
        raise AssertionError("prepared-capture scheduler must not use legacy shooting")


@pytest.fixture(params=["real", "dry-run"])
def scheduler(request, monkeypatch):
    nominal_start = datetime(2026, 8, 12, 20, 0, 0)
    start = (
        nominal_start
        if request.param == "real"
        else datetime(2026, 8, 22, 10, 0, 30)
    )
    fake_time = ControllableTime(start)
    clock = RuntimeClock(
        wall_clock_fn=fake_time.wall_now,
        monotonic_fn=fake_time.monotonic,
        sleep_fn=fake_time.sleep,
    )
    clock.configure(simulate=False)
    logs = []
    monkeypatch.setattr(trigger, "now", clock.now)
    monkeypatch.setattr(trigger, "_log", logs.append)
    monkeypatch.setattr(trigger, "_watchdog_write", lambda *_args: None)

    def wait_until(_service, target, deadline=None):
        remaining_s = clock.remaining(target)
        if remaining_s > 0:
            clock.sleep(remaining_s)

    monkeypatch.setattr(trigger, "_usb_wait_or_hold", wait_until)
    return start, fake_time, clock, logs


def _service(clock, plugin):
    service = CameraService(clock=clock)
    service.camera = object()
    service.plugin = plugin
    return service


def _run_grid(scheduler, *, interval_s, capture_s, preparation_s=0.0, slots=3):
    start, fake_time, clock, logs = scheduler
    plugin = SpyCameraPlugin(
        fake_time, preparation_s=preparation_s, capture_s=capture_s
    )
    trigger._run_absolute_grid(
        _service(clock, plugin),
        "partial",
        ["1/500"],
        start,
        start + timedelta(seconds=interval_s * slots - 0.01),
        interval_s,
    )
    return start, plugin, logs


def _target_offsets(start, plugin):
    return [
        (target - start).total_seconds()
        for _triggered_at, target, _deadline in plugin.triggered
    ]


def _trigger_offsets(plugin):
    return [triggered_at for triggered_at, _target, _deadline in plugin.triggered]


def test_two_and_half_second_capture_has_no_drift_on_180_second_grid(scheduler):
    start, plugin, _logs = _run_grid(
        scheduler, interval_s=180.0, capture_s=2.5
    )

    assert _target_offsets(start, plugin) == [0.0, 180.0, 360.0]
    assert _trigger_offsets(plugin) == [0.0, 180.0, 360.0]


def test_two_and_half_second_capture_targets_four_second_grid(scheduler):
    start, plugin, _logs = _run_grid(
        scheduler, interval_s=4.0, capture_s=2.5
    )

    assert _target_offsets(start, plugin) == [0.0, 4.0, 8.0]
    assert _trigger_offsets(plugin) == [0.0, 4.0, 8.0]


def test_preparation_finishing_before_target_triggers_exactly_at_target(scheduler):
    start, fake_time, clock, logs = scheduler
    fake_time.sleep(1.0)
    plugin = SpyCameraPlugin(fake_time, preparation_s=2.0)

    trigger._run_absolute_grid(
        _service(clock, plugin), "partial", ["1/500"],
        start + timedelta(seconds=5), start + timedelta(seconds=6), 4.0,
    )

    assert _trigger_offsets(plugin) == [5.0]
    assert not any("target_delay_s=" in message for message in logs)


def test_preparation_finishing_after_target_measures_delay_without_shifting_grid(
    scheduler,
):
    start, plugin, logs = _run_grid(
        scheduler, interval_s=4.0, capture_s=0.0, preparation_s=1.25
    )

    assert _target_offsets(start, plugin) == [0.0, 4.0, 8.0]
    assert _trigger_offsets(plugin) == [1.25, 4.0, 8.0]
    assert any("target_delay_s=1.250000" in message for message in logs)


def test_fully_missed_slot_is_skipped_and_scheduler_resumes_grid(scheduler):
    start, plugin, logs = _run_grid(
        scheduler, interval_s=4.0, capture_s=8.0, slots=5
    )

    assert _target_offsets(start, plugin) == [0.0, 8.0, 16.0]
    assert _trigger_offsets(plugin) == [0.0, 8.0, 16.0]
    assert any("missed_slots=1" in message for message in logs)


def test_differential_phase_settings_avoid_repeats_and_send_only_changes():
    fake_time = ControllableTime(datetime(2026, 8, 12, 20, 0, 0))
    clock = RuntimeClock(
        wall_clock_fn=fake_time.wall_now,
        monotonic_fn=fake_time.monotonic,
        sleep_fn=fake_time.sleep,
    )
    plugin = SpyCameraPlugin(fake_time)
    service = _service(clock, plugin)

    service.apply_phase_settings(aperture="f/8", iso="100")
    service.apply_phase_settings(aperture="f/8", iso="100")
    service.apply_phase_settings(aperture="f/8", iso="200")
    service.apply_phase_settings(aperture="f/11", iso="200")

    assert plugin.setting_calls == [
        {"aperture": "f/8", "iso": "100"},
        {"iso": "200"},
        {"aperture": "f/11"},
    ]


@pytest.mark.parametrize(
    ("target_before_c3_s", "estimated_total_s", "exposures_s", "accepted"),
    [
        (0.4, 0.8, [0.4], True),
        (0.1, 1.0, [0.4, 0.4], True),
        (0.1, 1.2, [0.4, 0.4], False),
        (0.2, 1.0, [1.0], False),
    ],
)
def test_c3_overflow_policy(
    target_before_c3_s, estimated_total_s, exposures_s, accepted
):
    c3 = datetime(2026, 8, 12, 20, 30, 0)
    prepared = PreparedCapture(
        token=None,
        estimated_total_s=estimated_total_s,
        exposures_s=exposures_s,
        planned_count=len(exposures_s),
        plugin_name="feat008-spy",
    )

    actual = trigger._c3_trigger_deadline(
        prepared, c3 - timedelta(seconds=target_before_c3_s), c3
    )

    expected = c3 + timedelta(seconds=trigger.C3_OVERFLOW_GRACE_S)
    assert actual == (expected if accepted else None)


def test_scheduler_has_no_sony_specific_constants_or_names():
    scheduler_source = inspect.getsource(trigger._run_absolute_grid).lower()

    assert "sony" not in scheduler_source
    assert "overhead_bracket" not in scheduler_source
    assert "safety_margin" not in scheduler_source
