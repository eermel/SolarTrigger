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


class StatefulSonyPlugin(SonyPlugin):
    def __init__(self):
        super().__init__(camera=None, log_fn=lambda _message: None)
        self.raw_sets = []

    def _set(self, name, value):
        self.raw_sets.append((name, str(value)))
        return True, False, ""

    def _drain_frames(self, expected, _longest_exp_s):
        return expected

    def _settle_idle(self, max_s=2.0):
        return None


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


def test_repeated_identical_native_bracket_only_presses_shutter_again():
    plugin = StatefulSonyPlugin()
    _step, _count, sequence = planner.plan("1/2000", "1/500", 1.0)
    bracket = sequence[0]

    assert plugin._fire_bracket(bracket) == bracket.nimg
    first_calls = list(plugin.raw_sets)
    assert ("capturemode", "Single Shot") in first_calls
    assert ("shutterspeed", str(bracket.centre)) in first_calls
    assert ("capturemode", str(bracket.mode_string)) in first_calls

    plugin.raw_sets.clear()
    assert plugin._fire_bracket(bracket) == bracket.nimg
    assert plugin.raw_sets == [("bulb", "1"), ("bulb", "0")]


def test_unchanged_iso_does_not_break_an_already_configured_bracket():
    plugin = StatefulSonyPlugin()
    plugin._known_settings = {
        "iso": "100",
        "capturemode": "Continuous Bracket 1.0 EV 3 Img.",
    }

    plugin.set_exposure_settings(iso="100")

    assert plugin.raw_sets == []
    assert plugin._known_settings["capturemode"].startswith("Continuous Bracket")


def test_failed_setting_is_unknown_and_retried(monkeypatch):
    plugin = StatefulSonyPlugin()
    outcomes = iter(((False, False, "usb error"), (True, False, "")))

    def flaky_set(name, value):
        plugin.raw_sets.append((name, str(value)))
        return next(outcomes)

    monkeypatch.setattr(plugin, "_set", flaky_set)

    assert plugin._set_state("iso", "100")[0] is False
    assert "iso" not in plugin._state_cache()
    assert plugin._set_state("iso", "100")[0] is True
    assert plugin.raw_sets == [("iso", "100"), ("iso", "100")]


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("Sony ILCE-7M5 (PC Control)", True),
        ("Sony Corporation ILCE-7M5", True),
        ("ILCE-7M5", True),
        ("Sony Alpha-A6600 (PC Control)", False),
        ("Sony Corporation ILCE-6600", False),
        ("Nikon DSC D850", False),
        (None, False),
        ("", False),
    ],
)
def test_sony_plugin_matches_only_a7v(model, expected):
    assert SonyPlugin.matches(model) is expected
