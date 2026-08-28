import pytest

from backend.exposure_selection import normalize_iso_up, safe_shutter_and_iso


@pytest.mark.parametrize(
    ("t_requested", "expected_iso"),
    [
        ("1/4", 200),
        ("4", 3200),
    ],
)
def test_shutter_limit_is_compensated_with_iso(
    t_requested: str,
    expected_iso: int,
) -> None:
    result = safe_shutter_and_iso(t_requested, 100, "1/8")

    assert result["shutter"] == "1/8"
    assert result["iso"] == expected_iso
    assert "shutter_limited" in result["corrections"]
    assert "iso_compensated" in result["corrections"]
    assert result["warnings"] == []


def test_shutter_limit_between_supported_values_selects_lower_shutter() -> None:
    result = safe_shutter_and_iso("1/4", 100, "0.14")

    assert result["shutter"] == "1/8"


def test_required_iso_above_configured_maximum_is_capped() -> None:
    result = safe_shutter_and_iso("1/4", 100, "1/8", iso_max=100)

    assert result["iso"] == 100
    assert "iso_capped" in result["warnings"]


@pytest.mark.parametrize(
    ("required_iso", "expected_iso"),
    [
        (250, 400),
        (3200, 3200),
    ],
)
def test_iso_is_rounded_up_to_supported_value(
    required_iso: int,
    expected_iso: int,
) -> None:
    assert normalize_iso_up(required_iso, [100, 200, 400, 800, 1600, 3200]) == (
        expected_iso
    )


@pytest.mark.parametrize(
    ("iso_max", "expected_iso"),
    [
        (6400, 6400),
        (3200, 3200),
    ],
)
def test_required_iso_7000_is_capped_after_rounding_up(
    iso_max: int,
    expected_iso: int,
) -> None:
    result = safe_shutter_and_iso(
        "1",
        7000,
        "1",
        supported_isos=[100, 200, 400, 800, 1600, 3200, 6400, 8000],
        iso_max=iso_max,
    )

    assert result["iso"] == expected_iso
    assert "iso_rounded" in result["corrections"]
    assert "iso_capped" in result["warnings"]
