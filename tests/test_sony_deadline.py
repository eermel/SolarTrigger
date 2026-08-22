import pytest

from plugins.camera.sony_planner import (
    Bracket,
    SAFETY_MARGIN_S,
    SinglePhoto,
    estimate_duration,
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
