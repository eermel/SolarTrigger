"""Integration coverage for brand-neutral totality adaptation near C3."""

from datetime import datetime, timedelta
import sys
import types

import pytest

from backend.timeline import build_timeline, rebase_timeline


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


SPEEDS = [
    "1/4000", "1/2000", "1/1000", "1/500", "1/250", "1/125",
    "1/60", "1/30", "1/15", "1/8", "1/4",
]


def _timeline(*, rebased=False):
    circumstances = {
        "_date": "2026-08-12",
        "TSTART": "20:28:00",
        "C2": "20:29:00",
        "TMAX": "20:30:00",
        "C3": "20:31:00",
        "TEND": "20:32:00",
    }
    timeline = build_timeline(circumstances)
    if rebased:
        timeline = rebase_timeline(timeline, datetime(2030, 1, 2, 3, 4, 5))
    return timeline


class RefusingSizeService:
    """Brand-neutral service double whose only policy is accepted bracket size."""

    def __init__(self, accepted_sizes, estimate_s=0.5, exposures_s=None):
        self.accepted_sizes = set(accepted_sizes)
        self.estimate_s = estimate_s
        self.exposures_s = exposures_s
        self.attempts = []
        self.triggered = []

    def apply_phase_settings(self, aperture=None, iso=None):
        pass

    def prepare_capture(self, intent):
        selected = list(intent.speeds)
        self.attempts.append(selected)
        if len(selected) not in self.accepted_sizes:
            raise RuntimeError(f"test service refuses M={len(selected)}")
        exposures = (
            list(self.exposures_s)
            if self.exposures_s is not None
            else [trigger.parse_shutterspeed(speed) for speed in selected]
        )
        return types.SimpleNamespace(
            token=intent,
            estimated_total_s=float(self.estimate_s),
            exposures_s=exposures,
            planned_count=len(selected),
            plugin_name="refusing-size-test-double",
        )

    def trigger_prepared(self, prepared, deadline=None):
        self.triggered.append((list(prepared.token.speeds), deadline))
        return CaptureResult(
            frames=len(prepared.token.speeds),
            planned=len(prepared.token.speeds),
            detail="test double",
        )


def _select(n, accepted_sizes, *, rebased=False, remaining_s=1.0, **service_args):
    timeline = _timeline(rebased=rebased)
    c3 = timeline["C3"]
    target = c3 - timedelta(seconds=remaining_s)
    service = RefusingSizeService(accepted_sizes, **service_args)
    prepared, deadline = trigger._prepare_totality_sub_bracket(
        service, SPEEDS[:n], target, c3,
    )
    return service, prepared, deadline, target, c3


def _expected_uniform(source, size):
    indices = trigger._select_uniform_indices(source, size)
    return [source[index] for index in indices]


def test_nine_selects_eight_admissible_with_preserved_geometry():
    service, prepared, deadline, _, c3 = _select(9, {8})

    selected = prepared.token.speeds
    assert [len(attempt) for attempt in service.attempts] == [9, 8]
    assert selected == _expected_uniform(SPEEDS[:9], 8)
    assert selected[0] == SPEEDS[0]
    assert selected[-1] == SPEEDS[8]
    assert [SPEEDS.index(speed) for speed in selected] == sorted(
        SPEEDS.index(speed) for speed in selected
    )
    assert deadline == c3


@pytest.mark.parametrize(
    ("configured_count", "accepted_count"),
    [(8, 5), (10, 6), (11, 7)],
)
def test_arbitrary_sizes_preserve_endpoints_uniform_spacing_and_order(
    configured_count, accepted_count,
):
    service, prepared, _, _, _ = _select(configured_count, {accepted_count})
    source = SPEEDS[:configured_count]
    selected = prepared.token.speeds
    indices = [source.index(speed) for speed in selected]

    assert [len(attempt) for attempt in service.attempts] == list(
        range(configured_count, accepted_count - 1, -1)
    )
    assert selected == _expected_uniform(source, accepted_count)
    assert indices[0] == 0 and indices[-1] == configured_count - 1
    assert indices == sorted(indices)
    gaps = [right - left for left, right in zip(indices, indices[1:])]
    assert max(gaps) - min(gaps) <= 1


def test_single_fallback_chooses_longest_admissible(monkeypatch):
    timeline = _timeline()
    target = timeline["C3"] - timedelta(seconds=0.1)
    monkeypatch.setattr(trigger, "now", lambda: target)

    class SinglePolicyService(RefusingSizeService):
        def prepare_capture(self, intent):
            selected = list(intent.speeds)
            self.attempts.append(selected)
            if len(selected) != 1:
                raise RuntimeError("test service accepts singles only")
            exposure = trigger.parse_shutterspeed(selected[0])
            estimate = 1.2 if selected[0] == "1/4" else exposure + 0.09
            return types.SimpleNamespace(
                token=intent,
                estimated_total_s=estimate,
                exposures_s=[exposure],
            )

    service = SinglePolicyService({1})
    prepared, deadline = trigger._prepare_totality_sub_bracket(
        service, SPEEDS[:11], target, timeline["C3"],
    )

    assert service.attempts[-2:] == [["1/4"], ["1/8"]]
    assert prepared.token.speeds == ["1/8"]
    assert deadline == timeline["C3"] + timedelta(
        seconds=trigger.C3_OVERFLOW_GRACE_S
    )


def test_no_admissible_subset_skips_slot(monkeypatch):
    timeline = _timeline()
    c3 = timeline["C3"]
    target = c3 - timedelta(seconds=0.2)
    service = RefusingSizeService(set())
    logs = []
    monkeypatch.setattr(trigger, "now", lambda: target)
    monkeypatch.setattr(trigger, "_log", logs.append)
    monkeypatch.setattr(trigger, "_usb_wait_or_hold", lambda *args, **kwargs: None)

    trigger._run_absolute_grid(
        service, "phase2", SPEEDS[:8], target, c3, 1.0, deadline=c3,
    )

    assert service.triggered == []
    assert "reason=no_admissible_subset" in "\n".join(logs)


def test_accepted_overflow_never_extends_past_c3_grace():
    service, prepared, deadline, target, c3 = _select(
        8, {8}, remaining_s=0.2, estimate_s=0.8, exposures_s=[0.05] * 8,
    )

    estimated_end = target + timedelta(seconds=prepared.estimated_total_s)
    grace = c3 + timedelta(seconds=trigger.C3_OVERFLOW_GRACE_S)
    assert c3 < estimated_end <= grace
    assert deadline == grace
    assert all(exposure <= trigger.SHORT_EXPOSURE_MAX_S
               for exposure in prepared.exposures_s)
    assert service.attempts == [SPEEDS[:8]]


def test_real_and_dry_run_rebase_select_identical_subsets():
    real = _select(11, {7}, rebased=False)
    dry_run = _select(11, {7}, rebased=True)

    assert real[1].token.speeds == dry_run[1].token.speeds
    assert real[0].attempts == dry_run[0].attempts
    assert real[2] - real[4] == dry_run[2] - dry_run[4]
    assert real[4] - real[3] == dry_run[4] - dry_run[3]
