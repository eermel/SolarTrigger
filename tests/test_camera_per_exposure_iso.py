"""Execution contract for per-exposure shutter/ISO plans."""

from datetime import datetime
import sys
import types

# Camera plugins only need these gphoto2 symbols for these unit tests.
if "gphoto2" not in sys.modules:
    fake_gphoto2 = types.ModuleType("gphoto2")
    fake_gphoto2.GPhoto2Error = RuntimeError
    fake_gphoto2.GP_WIDGET_TOGGLE = object()
    fake_gphoto2.GP_EVENT_FILE_ADDED = object()
    fake_gphoto2.GP_EVENT_TIMEOUT = object()
    sys.modules["gphoto2"] = fake_gphoto2

from plugins.camera.base import CaptureResult
from plugins.camera.nikon import NikonDSLRPlugin
from plugins.camera.sony import SonyPlugin
from services.camera_service import CameraService, CaptureIntent


def _intent(exposure_plan):
    return CaptureIntent(
        shutter_min=None,
        shutter_max=None,
        step_ev=1.0,
        speeds=[item["shutter"] for item in exposure_plan],
        phase="C2",
        target_time=datetime(2026, 8, 12, 17, 46, 12),
        deadline=None,
        overflow_policy="truncate",
        exposure_plan=exposure_plan,
    )


class RecordingNikonPlugin(NikonDSLRPlugin):
    def __init__(self):
        super().__init__(camera=None, log_fn=lambda _message: None)
        self.iso_calls = []
        self.singles = []
        self.ranges = []

    def set_exposure_settings(self, aperture=None, iso=None):
        if iso is not None:
            self.iso_calls.append(str(iso))

    def shoot_single(self, speed, photo_num=0, deadline=None):
        self.singles.append(str(speed))
        return CaptureResult(frames=1, planned=1, detail="single")

    def shoot_speeds(
        self,
        v_max,
        v_min,
        step_il,
        photo_num_start=0,
        deadline=None,
    ):
        self.ranges.append((str(v_max), str(v_min), float(step_il)))
        return CaptureResult(frames=1, planned=1, detail="range")


class RecordingSonyPlugin(SonyPlugin):
    def __init__(self):
        super().__init__(camera=None, log_fn=lambda _message: None)
        self.iso_calls = []
        self.singles = []
        self.brackets = []

    def set_exposure_settings(self, aperture=None, iso=None):
        if iso is not None:
            self.iso_calls.append(str(iso))

    def _fire_single(self, speed, deadline=None):
        self.singles.append(str(speed))
        return 1

    def _fire_bracket(self, item, deadline=None):
        self.brackets.append(item)
        return item.nimg


def test_nikon_executes_every_materialized_pair_and_changes_iso_only_when_needed():
    plugin = RecordingNikonPlugin()

    plan = [
        {"shutter": "1/1000", "iso": 100},
        {"shutter": "1/500", "iso": 100},
        {"shutter": "1/250", "iso": 200},
        {"shutter": "1/125", "iso": 200},
        {"shutter": "1/60", "iso": 400},
    ]

    prepared = plugin.prepare_capture(_intent(plan))
    result = plugin.trigger_prepared(prepared)

    assert plugin.ranges == []
    assert plugin.singles == [
        "1/1000",
        "1/500",
        "1/250",
        "1/125",
        "1/60",
    ]
    assert plugin.iso_calls == ["100", "200", "400"]
    assert result.frames == 5
    assert result.planned == 5


def test_sony_variable_iso_plan_uses_singles_and_never_mixes_iso_in_bracket():
    plugin = RecordingSonyPlugin()

    plan = [
        {"shutter": "1/1000", "iso": 100},
        {"shutter": "1/500", "iso": 100},
        {"shutter": "1/250", "iso": 200},
        {"shutter": "1/125", "iso": 400},
    ]

    prepared = plugin.prepare_capture(_intent(plan))
    result = plugin.trigger_prepared(prepared)

    assert plugin.brackets == []
    assert plugin.singles == [
        "1/1000",
        "1/500",
        "1/250",
        "1/125",
    ]
    assert plugin.iso_calls == ["100", "200", "400"]
    assert result.frames == 4
    assert result.planned == 4


def test_sony_constant_iso_plan_keeps_normal_bracket_optimization():
    plugin = RecordingSonyPlugin()

    # Five views are the exact physical Sony 1 EV bracket generated for
    # this range. No extra physical exposure is introduced.
    plan = [
        {"shutter": "1/1000", "iso": 100},
        {"shutter": "1/500", "iso": 100},
        {"shutter": "1/250", "iso": 100},
        {"shutter": "1/125", "iso": 100},
        {"shutter": "1/60", "iso": 100},
    ]

    prepared = plugin.prepare_capture(_intent(plan))
    result = plugin.trigger_prepared(prepared)

    assert plugin.iso_calls == ["100"]
    assert plugin.brackets
    assert plugin.singles == []
    assert result.frames == prepared.planned_count
    assert result.planned == prepared.planned_count


def test_sony_constant_iso_rejects_bracket_with_physical_overshoot():
    plugin = RecordingSonyPlugin()

    # The native Sony planner would add 1/60 here. Because 1/60 is not
    # present in the final exposure_plan, the bracket must NOT be fired.
    plan = [
        {"shutter": "1/1000", "iso": 100},
        {"shutter": "1/500", "iso": 100},
        {"shutter": "1/250", "iso": 100},
        {"shutter": "1/125", "iso": 100},
    ]

    prepared = plugin.prepare_capture(_intent(plan))
    result = plugin.trigger_prepared(prepared)

    assert plugin.brackets == []
    assert plugin.singles == [
        "1/1000",
        "1/500",
        "1/250",
        "1/125",
    ]
    assert plugin.iso_calls == ["100"]
    assert result.frames == 4
    assert result.planned == 4


def test_camera_service_invalidates_cached_iso_after_materialized_plan():
    plugin = RecordingNikonPlugin()
    service = CameraService()
    service.plugin = plugin

    service.apply_phase_settings(iso="100")

    plan = [
        {"shutter": "1/1000", "iso": 100},
        {"shutter": "1/500", "iso": 200},
    ]

    prepared = service.prepare_capture(_intent(plan))
    service.trigger_prepared(prepared)

    assert "iso" not in service._last_phase_settings

    calls_before_restore = len(plugin.iso_calls)
    service.apply_phase_settings(iso="100")

    assert len(plugin.iso_calls) == calls_before_restore + 1
    assert plugin.iso_calls[-1] == "100"


def test_sony_mixed_plan_keeps_bracket_before_variable_iso_singles():
    plugin = RecordingSonyPlugin()

    plan = [
        # Exact native Sony 5-view bracket at constant ISO.
        {"shutter": "1/1000", "iso": 100},
        {"shutter": "1/500", "iso": 100},
        {"shutter": "1/250", "iso": 100},
        {"shutter": "1/125", "iso": 100},
        {"shutter": "1/60", "iso": 100},

        # Motion-limited tail: same shutter may repeat, but ISO now varies.
        {"shutter": "1/60", "iso": 200},
        {"shutter": "1/60", "iso": 400},
        {"shutter": "1/60", "iso": 800},
    ]

    prepared = plugin.prepare_capture(_intent(plan))
    result = plugin.trigger_prepared(prepared)

    assert prepared.token[0] == "sony_exposure_mixed"

    assert len(plugin.brackets) == 1
    assert plugin.singles == [
        "1/60",
        "1/60",
        "1/60",
    ]

    assert plugin.iso_calls == [
        "100",
        "200",
        "400",
        "800",
    ]

    assert result.frames == 8
    assert result.planned == 8
