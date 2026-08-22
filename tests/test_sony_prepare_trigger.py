"""Unit tests for Sony prepared captures (no hardware)."""

from datetime import datetime
import sys
import types

import pytest

# sony.py only needs these names while its hardware methods are being defined.
if "gphoto2" not in sys.modules:
    fake_gphoto2 = types.ModuleType("gphoto2")
    fake_gphoto2.GPhoto2Error = RuntimeError
    fake_gphoto2.GP_WIDGET_TOGGLE = object()
    fake_gphoto2.GP_EVENT_FILE_ADDED = object()
    fake_gphoto2.GP_EVENT_TIMEOUT = object()
    sys.modules["gphoto2"] = fake_gphoto2

from plugins.camera import sony_planner as planner
from plugins.camera.sony import SonyPlugin
from services.camera_service import CameraService, CaptureIntent


class FakeSonyPlugin(SonyPlugin):
    def __init__(self):
        self.fired = []
        super().__init__(camera=None, log_fn=lambda _message: None)

    def _fire_bracket(self, item, deadline=None):
        self.fired.append(("bracket", item, deadline))
        return item.nimg

    def _fire_single(self, speed, deadline=None):
        self.fired.append(("single", speed, deadline))
        return 1


def _intent(*, shutter_min=None, shutter_max=None, step_ev=None, speeds=None):
    return CaptureIntent(
        shutter_min=shutter_min,
        shutter_max=shutter_max,
        step_ev=step_ev,
        speeds=speeds,
        phase="C2",
        target_time=datetime(2026, 8, 12, 17, 46, 12),
        deadline=None,
        overflow_policy="truncate",
    )


def test_prepare_regular_bracket_reports_expanded_exposures_and_estimate():
    plugin = FakeSonyPlugin()
    intent = _intent(shutter_max="1/1000", shutter_min="1/125", step_ev=1.0)

    prepared = plugin.prepare_capture(intent)
    sequence = prepared.token[1]
    expected_exposures = [
        planner.parse_speed(view)
        for item in sequence
        for view in item.views
    ]

    assert sequence
    assert all(isinstance(item, planner.Bracket) for item in sequence)
    assert prepared.exposures_s == pytest.approx(expected_exposures)
    assert prepared.planned_count == sum(item.nimg for item in sequence)
    assert prepared.estimated_total_s == pytest.approx(
        sum(planner.estimate_duration(item) for item in sequence)
    )
    assert prepared.estimated_total_s > 0


def test_prepare_single_reports_one_exposure_and_estimate():
    plugin = FakeSonyPlugin()

    prepared = plugin.prepare_capture(
        _intent(shutter_max="1/1000", shutter_min="1/1000", step_ev=1.0)
    )

    assert prepared.exposures_s == pytest.approx([1 / 1000])
    assert prepared.planned_count == 1
    assert prepared.estimated_total_s == pytest.approx(
        planner.estimate_duration(planner.SinglePhoto("1/1000"))
    )


def test_prepare_irregular_speeds_preserves_each_explicit_exposure():
    plugin = FakeSonyPlugin()
    speeds = ["1/1000", "1/320", "1/125"]

    prepared = plugin.prepare_capture(_intent(speeds=speeds))

    assert prepared.exposures_s == pytest.approx(
        [planner.parse_speed(speed) for speed in speeds]
    )
    assert prepared.planned_count == len(speeds)
    assert prepared.estimated_total_s == pytest.approx(
        sum(
            planner.estimate_duration(planner.SinglePhoto(speed))
            for speed in speeds
        )
    )


def test_trigger_prepared_executes_sequence_with_service_monotonic_deadline(
    monkeypatch,
):
    plugin = FakeSonyPlugin()
    service = CameraService(clock=types.SimpleNamespace(remaining=lambda _deadline: 8.0))
    service.plugin = plugin
    prepared = plugin.prepare_capture(
        _intent(shutter_max="1/1000", shutter_min="1/125", step_ev=1.0)
    )
    monkeypatch.setattr("services.camera_service.time.monotonic", lambda: 100.0)
    monkeypatch.setattr("plugins.camera.base.time.monotonic", lambda: 100.0)

    result = service.trigger_prepared(
        prepared, deadline=datetime(2026, 8, 12, 17, 46, 20)
    )

    assert plugin.fired
    assert all(deadline == 108.0 for _, _, deadline in plugin.fired)
    assert result.frames == prepared.planned_count
    assert result.planned == prepared.planned_count
    assert 0 <= result.frames <= result.planned
