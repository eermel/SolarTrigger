import pytest

from plugins.camera import sony_planner as planner
from plugins.camera.sony_planner import (
    Bracket,
    SAFETY_MARGIN_S,
    SinglePhoto,
    estimate_duration,
)


def test_estimate_duration_for_bracket_produced_by_plan():
    _, _, sequence = planner.plan("1/4000", "1/250", 1.0)

    first_item = sequence[0]
    assert isinstance(first_item, Bracket)

    views_duration = sum(planner.parse_speed(view) for view in first_item.views)
    expected_duration = (
        views_duration
        + len(first_item.views) * planner.OVERHEAD_BRACKET_PER_FRAME_S
        + planner.OVERHEAD_BRACKET_FIXED_S
    )
    assert estimate_duration(first_item) == pytest.approx(
        expected_duration, abs=0.01
    )


@pytest.mark.parametrize(
    ("src_nimg", "nimg_target"),
    [(9, 7), (7, 5), (5, 3)],
)
def test_make_fast_subset_uses_prefix_and_recalculates_centre(
    src_nimg, nimg_target
):
    all_views = [
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
    src_views = all_views[:src_nimg]
    src_bracket = Bracket(
        src_views[src_nimg // 2], 1.0, src_nimg, src_views
    )

    subset = planner.make_fast_subset(src_bracket, nimg_target)

    assert subset.step == src_bracket.step
    assert subset.nimg == nimg_target
    assert subset.views == src_bracket.views[:nimg_target]
    assert subset.centre == subset.views[nimg_target // 2]
    assert estimate_duration(subset) == pytest.approx(
        sum(planner.parse_speed(view) for view in subset.views)
        + len(subset.views) * planner.OVERHEAD_BRACKET_PER_FRAME_S
        + planner.OVERHEAD_BRACKET_FIXED_S
    )


@pytest.mark.parametrize(
    ("item", "expected_duration", "expected_budget"),
    [
        (SinglePhoto("1/500"), 2.802, 3.302),
        (
            Bracket(
                "1/1000",
                1.0,
                5,
                ["1/4000", "1/2000", "1/1000", "1/500", "1/250"],
            ),
            5.75775,
            6.25775,
        ),
        (
            Bracket(
                "1/250",
                1.0,
                9,
                [
                    "1/4000",
                    "1/2000",
                    "1/1000",
                    "1/500",
                    "1/250",
                    "1/125",
                    "1/60",
                    "1/30",
                    "1/15",
                ],
            ),
            6.882416666666667,
            7.382416666666667,
        ),
        (
            Bracket(
                "1/4",
                1.0,
                7,
                ["1/30", "1/15", "1/8", "1/4", "1/2", "1", "2"],
            ),
            10.225,
            10.725,
        ),
    ],
)
def test_estimate_duration_and_req005_budget(
    item, expected_duration, expected_budget
):
    assert estimate_duration(item) == pytest.approx(expected_duration, abs=1e-6)
    assert estimate_duration(item) + SAFETY_MARGIN_S == pytest.approx(
        expected_budget, abs=0.001
    )


def test_fast_nine_frame_bracket_is_not_admitted_with_3_5_seconds_remaining():
    item = Bracket(
        "1/250",
        1.0,
        9,
        [
            "1/4000",
            "1/2000",
            "1/1000",
            "1/500",
            "1/250",
            "1/125",
            "1/60",
            "1/30",
            "1/15",
        ],
    )

    remaining_s = 3.5
    admission_budget_s = estimate_duration(item) + SAFETY_MARGIN_S
    admitted = remaining_s >= admission_budget_s

    assert admission_budget_s == pytest.approx(7.382, abs=0.001)
    assert not admitted
