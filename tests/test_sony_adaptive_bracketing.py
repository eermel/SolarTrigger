"""Unit tests for Sony deadline-driven bracket reduction (no hardware)."""

import sys
import time
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

from plugins.camera.sony import SonyPlugin
from plugins.camera import sony_planner as planner


VIEWS = [
    "1/4000",
    "1/2000",
    "1/1000",
    "1/500",
    "1/250",
    "1/125",
    "1/60",
    "1/30",
    "1/15",
]


class FakeSonyPlugin(SonyPlugin):
    """Sony plugin whose capture entry points complete immediately."""

    def __init__(self):
        self.logs = []
        super().__init__(camera=None, log_fn=self.logs.append)
        self.brackets = []
        self.singles = []

    def _fire_bracket(self, item, deadline=None):
        self.brackets.append(item)
        return item.nimg

    def _fire_single(self, speed, deadline=None):
        self.singles.append(speed)
        return 1


@pytest.fixture
def plugin():
    return FakeSonyPlugin()


@pytest.fixture
def nine_frame_plan(monkeypatch):
    bracket = planner.Bracket(VIEWS[4], 1.0, 9, VIEWS.copy())
    monkeypatch.setattr(
        planner, "plan", lambda *_args, **_kwargs: (1.0, 9, [bracket])
    )
    return bracket


def _budget(item):
    return planner.estimate_duration(item) + planner.SAFETY_MARGIN_S


def _shoot_with_remaining(plugin, remaining_s):
    deadline = time.monotonic() + remaining_s
    return plugin.shoot_speeds("1/4000", "1/15", 1.0, deadline=deadline)


def test_sufficient_budget_executes_complete_plan_without_adaptation(
    plugin, nine_frame_plan
):
    result = _shoot_with_remaining(plugin, _budget(nine_frame_plan) + 1.0)

    assert plugin.brackets == [nine_frame_plan]
    assert plugin.singles == []
    assert result.frames == sum(item.nimg for item in [nine_frame_plan])
    assert result.planned == 9
    assert "adapt" not in result.detail
    assert not any("adaptation" in message for message in plugin.logs)


@pytest.mark.parametrize(
    ("selected_nimg", "next_larger_nimg"),
    [(7, 9), (5, 7), (3, 5)],
)
def test_fallback_selects_largest_admissible_fast_prefix(
    plugin, nine_frame_plan, selected_nimg, next_larger_nimg
):
    selected = planner.make_fast_subset(nine_frame_plan, selected_nimg)
    larger = (
        nine_frame_plan
        if next_larger_nimg == 9
        else planner.make_fast_subset(nine_frame_plan, next_larger_nimg)
    )
    remaining_s = (_budget(selected) + _budget(larger)) / 2

    result = _shoot_with_remaining(plugin, remaining_s)

    assert len(plugin.brackets) == 1
    candidate = plugin.brackets[0]
    assert candidate.nimg == selected_nimg
    assert candidate.views == nine_frame_plan.views[:selected_nimg]
    assert set(candidate.views) <= set(nine_frame_plan.views)
    assert not set(nine_frame_plan.views[selected_nimg:]) & set(candidate.views)
    assert _budget(candidate) <= remaining_s  # BUG-002 admission barrier
    assert plugin.singles == []
    assert result.frames == selected_nimg
    assert result.planned == nine_frame_plan.nimg


def test_three_frame_bracket_falls_back_to_fastest_single(
    plugin, nine_frame_plan
):
    three = planner.make_fast_subset(nine_frame_plan, 3)
    single = planner.SinglePhoto(nine_frame_plan.views[0])
    remaining_s = (_budget(single) + _budget(three)) / 2

    result = _shoot_with_remaining(plugin, remaining_s)

    assert plugin.brackets == []
    assert plugin.singles == [nine_frame_plan.views[0]]
    assert _budget(single) <= remaining_s  # BUG-002 admission barrier
    assert result.frames == 1
    assert result.planned == nine_frame_plan.nimg


def test_no_admissible_capture_returns_without_firing(plugin, nine_frame_plan):
    single = planner.SinglePhoto(nine_frame_plan.views[0])

    result = _shoot_with_remaining(plugin, _budget(single) - 0.5)

    assert plugin.brackets == []
    assert plugin.singles == []
    assert result.frames == 0
    assert result.planned == nine_frame_plan.nimg
