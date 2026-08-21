import math

from backend.eclipse_engine.observer import prepare_observer_constants


def test_prepare_observer_constants_matches_browser_reference():
    # Captured from the source JavaScript for Paris and eclipse val 59.
    expected = (
        0.8527085313298616,
        -0.041053634665410614,
        35.0,
        -2.0,
        0.7494520263908953,
        0.6592019620046853,
        3472,
    )

    actual = prepare_observer_constants(59, 48.8566, 2.3522, 35.0, 2.0)

    for result, reference in zip(actual[:6], expected[:6]):
        assert abs(result - reference) <= math.ulp(reference)
    assert actual[6] == expected[6]


def test_prepare_observer_constants_uses_jubier_signs():
    constants = prepare_observer_constants(61, -12.5, -45.25, 0.0, -3.5)

    assert constants[0] < 0.0
    assert constants[1] > 0.0
    assert constants[3] == 3.5
    assert constants[6] == 28 * (61 + 65)
