"""Non-regression coverage for continuous Phase 2 totality scheduling."""

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


class RefusingSizeService:
    """Camera double accepting only configured bracket sizes."""

    def __init__(self, clock, accepted_sizes, duration_s, exposures_s=None):
        self.clock = clock
        self.accepted_sizes = set(accepted_sizes)
        self.duration_s = duration_s
        self.exposures_s = exposures_s
        self.settings = []
        self.attempts = []
        self.triggered = []

    def apply_phase_settings(self, aperture=None, iso=None):
        self.settings.append((aperture, iso))

    def prepare_capture(self, intent):
        selected = list(intent.speeds)
        self.attempts.append((self.clock.now(), selected))
        if len(selected) not in self.accepted_sizes:
            raise RuntimeError(f"test service refuses M={len(selected)}")
        exposures = (
            list(self.exposures_s)
            if self.exposures_s is not None
            else [trigger.parse_shutterspeed(speed) for speed in selected]
        )
        return types.SimpleNamespace(
            token=intent,
            estimated_total_s=self.duration_s,
            exposures_s=exposures,
            planned_count=len(selected),
            plugin_name="refusing-size-test-double",
        )

    def trigger_prepared(self, prepared, deadline=None):
        started = self.clock.now()
        selected = list(prepared.token.speeds)
        self.triggered.append((started, selected, deadline))
        self.clock.value += timedelta(seconds=self.duration_s)
        return CaptureResult(
            frames=len(selected), planned=len(selected), detail="test double"
        )


def _patch_scheduler(monkeypatch, clock):
    logs = []
    waits = []
    monkeypatch.setattr(trigger, "now", clock.now)
    monkeypatch.setattr(trigger, "_log", logs.append)
    monkeypatch.setattr(trigger, "_watchdog_write", lambda *args: None)

    def wait_until(_service, target, deadline=None):
        waits.append((clock.now(), target, deadline))
        clock.value = max(clock.value, target)

    monkeypatch.setattr(trigger, "_usb_wait_or_hold", wait_until)
    return logs, waits


def test_continuous_runs_admissible_brackets_back_to_back(monkeypatch):
    start = datetime(2026, 8, 12, 20, 30, 0)
    c3 = start + timedelta(seconds=0.75)
    clock = FakeClock(start)
    _, waits = _patch_scheduler(monkeypatch, clock)
    service = RefusingSizeService(clock, {3}, duration_s=0.25)

    photo_num = trigger._run_continuous_totality(
        service, SPEEDS[:3], start, c3, photo_num_start=10
    )

    assert [item[0] for item in service.triggered] == [
        start,
        start + timedelta(seconds=0.25),
        start + timedelta(seconds=0.5),
    ]
    assert [item[1] for item in service.triggered] == [SPEEDS[:3]] * 3
    assert [attempt[0] for attempt in service.attempts] == [
        item[0] for item in service.triggered
    ]
    assert all(item[2] == c3 for item in service.triggered)
    assert waits == []
    assert photo_num == 19


def test_continuous_reduces_uniformly_near_c3_and_uses_grace(monkeypatch):
    c3 = datetime(2026, 8, 12, 20, 31, 0)
    start = c3 - timedelta(seconds=0.2)
    clock = FakeClock(start)
    _, waits = _patch_scheduler(monkeypatch, clock)
    service = RefusingSizeService(
        clock, {3}, duration_s=0.5, exposures_s=[0.05, 0.05, 0.05]
    )

    trigger._run_continuous_totality(service, SPEEDS, start, c3)

    expected = [
        SPEEDS[index] for index in trigger._select_uniform_indices(SPEEDS, 3)
    ]
    assert [len(attempt[1]) for attempt in service.attempts] == [5, 4, 3]
    assert len(service.triggered) == 1
    assert service.triggered[0][1] == expected
    assert service.triggered[0][2] == c3 + timedelta(
        seconds=trigger.C3_OVERFLOW_GRACE_S
    )
    assert clock.now() <= c3 + timedelta(seconds=trigger.C3_OVERFLOW_GRACE_S)
    assert waits == []


def test_continuous_stops_after_first_no_admissible_subset(monkeypatch):
    start = datetime(2026, 8, 12, 20, 30, 59, 800000)
    c3 = start + timedelta(seconds=0.2)
    clock = FakeClock(start)
    logs, waits = _patch_scheduler(monkeypatch, clock)
    service = RefusingSizeService(clock, set(), duration_s=0.1)

    trigger._run_continuous_totality(service, SPEEDS, start, c3)

    refusal_logs = [
        message for message in logs if "reason=no_admissible_subset" in message
    ]
    assert len(refusal_logs) == 1
    assert len(service.attempts) == 2 * len(SPEEDS) - 1
    assert service.triggered == []
    assert waits == [(start, c3, c3)]
    assert clock.now() == c3
