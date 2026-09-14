from __future__ import annotations

import threading

import pytest

import scripts.eclipse_trigger as trigger
from plugins.camera.base import CaptureResult


class DummyEmergencyCamera:
    def __init__(self, stopped):
        self.stopped = stopped
        self.settings = []
        self.initialized = []
        self.intents = []
        self.deadlines = []

    def initialize(self, **settings):
        self.initialized.append(settings)
        return None

    def apply_phase_settings(self, **settings):
        self.settings.append(settings)
        return True

    def prepare_capture(self, intent):
        self.intents.append(intent)
        return type(
            "Prepared",
            (),
            {"planned_count": 2, "estimated_total_s": 0.0},
        )()

    def trigger_prepared(self, prepared, deadline=None):
        del prepared
        self.deadlines.append(deadline)
        self.stopped.set()
        return CaptureResult(frames=2, planned=2)


def emergency_config():
    return {
        "config_type": "emergency_totality_photo_setup",
        "phases": {
            "totality": {
                "enabled": True,
                "interval_s": 0,
                "duration_s": None,
                "iso": 100,
                "aperture": "f/8",
                "speeds": ["1/4000", "1/2000"],
            }
        },
    }


def test_emergency_totality_uses_no_wall_clock(monkeypatch):
    stopped = threading.Event()
    camera = DummyEmergencyCamera(stopped)

    # The emergency runner must remain functional even if CLOCK_REALTIME is
    # unusable.  time.monotonic() remains the only timing source.
    real_time_module = trigger.time

    class MonotonicOnlyTime:
        @staticmethod
        def monotonic():
            return real_time_module.monotonic()

        @staticmethod
        def time():
            raise AssertionError("wall clock accessed")

    monkeypatch.setattr(trigger, "time", MonotonicOnlyTime)

    stats = trigger.run_emergency_totality(
        camera,
        emergency_config(),
        {"rig_id": 1, "photo": {}},
        stopped,
        log_fn=lambda _message: None,
    )

    assert stats == {"photos": 2, "errors": 0}
    assert camera.deadlines == [None]
    assert len(camera.intents) == 1
    intent = camera.intents[0]
    assert intent.target_time == trigger.EMERGENCY_TARGET_SENTINEL_UTC
    assert intent.deadline is None
    assert intent.origin == "emergency_totality"


def test_emergency_settings_failure_does_not_block_photo():
    stopped = threading.Event()

    class SettingsFailCamera(DummyEmergencyCamera):
        def initialize(self, **settings):
            del settings
            raise RuntimeError("SET failed")

    camera = SettingsFailCamera(stopped)
    messages = []
    stats = trigger.run_emergency_totality(
        camera,
        emergency_config(),
        {"rig_id": 1, "photo": {}},
        stopped,
        log_fn=messages.append,
    )

    assert stats["photos"] == 2
    assert camera.deadlines == [None]
    assert any("capture will still be attempted" in message for message in messages)


def test_sigusr1_control_flow_abandons_phase_runtime():
    assert issubclass(trigger.EmergencyTotalityRequested, RuntimeError)
    assert not hasattr(trigger, "_build_totality_only_schedule")


def test_totality_only_skips_runtime_clock_configuration(monkeypatch):
    class ForbiddenRuntimeClock:
        def __init__(self, *args, **kwargs):
            raise AssertionError("RuntimeClock must not be constructed in emergency mode")

    monkeypatch.setattr(trigger, "RuntimeClock", ForbiddenRuntimeClock)
    monkeypatch.setattr(
        trigger,
        "parse_args",
        lambda: type(
            "Args",
            (),
            {
                "simulate": False,
                "dry_run": False,
                "totality_only": True,
                "speed": 60.0,
                "camera": "unused-by-this-test",
                "exposure_opt": None,
                "file": None,
            },
        )(),
    )
    with pytest.raises(FileNotFoundError):
        trigger.main()
