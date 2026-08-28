from datetime import datetime, timedelta, timezone

import pytest

from backend.solar_position import solar_declination_deg_utc


def test_declination_near_march_equinox() -> None:
    result = solar_declination_deg_utc(datetime(2026, 3, 20, 14, 46))

    assert result == pytest.approx(0.0, abs=0.5)


def test_declination_near_june_solstice() -> None:
    result = solar_declination_deg_utc(
        datetime(2026, 6, 21, 8, 25, tzinfo=timezone.utc)
    )

    assert result == pytest.approx(23.44, abs=1.0)


def test_declination_is_deterministic() -> None:
    instant = datetime(2027, 8, 2, 10, 7, 18, 123456, tzinfo=timezone.utc)

    assert solar_declination_deg_utc(instant) == solar_declination_deg_utc(instant)


def test_aware_datetime_is_converted_to_utc() -> None:
    utc = datetime(2026, 6, 21, 8, 25, tzinfo=timezone.utc)
    same_instant = utc.astimezone(timezone(timedelta(hours=5, minutes=30)))

    assert solar_declination_deg_utc(same_instant) == pytest.approx(
        solar_declination_deg_utc(utc), abs=1e-12
    )


@pytest.mark.parametrize("value", [None, "2026-03-20T14:46:00Z", 0])
def test_non_datetime_values_are_rejected(value: object) -> None:
    with pytest.raises(TypeError, match="when_utc must be a datetime"):
        solar_declination_deg_utc(value)  # type: ignore[arg-type]
