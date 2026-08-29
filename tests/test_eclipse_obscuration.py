import pytest

from backend.eclipse_engine.compute import solar_obscuration_percent


def test_total_eclipse_obscures_entire_solar_disc():
    assert solar_obscuration_percent(1.03489, 1.07901) == 100.0


def test_annular_eclipse_leaves_visible_solar_ring():
    result = solar_obscuration_percent(0.97928, 0.95957)
    assert result == pytest.approx(92.07746, abs=1e-5)


def test_partial_eclipse_uses_circle_intersection_area():
    result = solar_obscuration_percent(0.34651, 1.05209)
    assert result == pytest.approx(23.49292, abs=1e-5)


def test_no_eclipse_has_zero_obscuration():
    assert solar_obscuration_percent(0.0, 1.0) == 0.0
