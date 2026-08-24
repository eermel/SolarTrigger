import sys
from datetime import timedelta, tzinfo
from types import SimpleNamespace

import pytest

from backend.timezone_service import calculate_timezone_from_coords


@pytest.mark.parametrize(
    ("timezone_name", "eclipse_date", "expected_offset"),
    [
        ("Europe/Paris", "2026-08-12", 2.0),
        ("Europe/Paris", "2026-01-15", 1.0),
        ("Africa/Abidjan", "2026-08-12", 0.0),
        ("Africa/Cairo", "2027-08-02", 3.0),
    ],
)
def test_timezone_offset_uses_eclipse_date(
    monkeypatch, timezone_name, eclipse_date, expected_offset
):
    timezone_at_calls = []
    timezone_calls = []
    utcoffset_dates = []

    class MockTimezone(tzinfo):
        def utcoffset(self, value):
            date_value = value.date()
            utcoffset_dates.append(date_value.isoformat())
            if timezone_name == "Europe/Paris":
                hours = 2 if date_value.month == 8 else 1
            elif timezone_name == "Africa/Cairo":
                hours = 3 if date_value.isoformat() == "2027-08-02" else 2
            else:
                hours = 0
            return timedelta(hours=hours)

        def dst(self, value):
            return timedelta(0)

    class MockTimezoneFinder:
        def timezone_at(self, **coordinates):
            timezone_at_calls.append(coordinates)
            return timezone_name

    def timezone(name):
        timezone_calls.append(name)
        return MockTimezone()

    monkeypatch.setitem(
        sys.modules,
        "timezonefinder",
        SimpleNamespace(TimezoneFinder=MockTimezoneFinder),
    )
    monkeypatch.setitem(sys.modules, "pytz", SimpleNamespace(timezone=timezone))

    offset = calculate_timezone_from_coords(48.0, 2.0, eclipse_date=eclipse_date)

    assert offset == expected_offset
    assert timezone_at_calls == [{"lat": 48.0, "lng": 2.0}]
    assert timezone_calls == [timezone_name]
    assert eclipse_date in utcoffset_dates
