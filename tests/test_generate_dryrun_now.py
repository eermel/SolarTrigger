from copy import deepcopy
from datetime import datetime, timedelta, timezone

from backend.dryrun_circumstances import generate_dryrun_now, generate_dryrun_today


def test_translates_all_contacts_and_sets_effective_start_to_now_plus_five_minutes():
    circumstances = {
        "_date": "2027-08-02",
        "C1": "10:00:00",
        "C1_local": "12:00:00",
        "C2": "11:00:00",
        "C2_local": "13:00:00",
        "TMAX": "11:01:00",
        "TMAX_local": "13:01:00",
        "C3": "11:02:00",
        "C3_local": "13:02:00",
        "C4": "12:00:00",
        "C4_local": "14:00:00",
    }
    now = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)

    result = generate_dryrun_now(
        circumstances,
        {"sequence_margin_min": 10},
        now,
    )

    assert result["_date"] == "2026-09-12"
    assert result["TSTART"] == "12:05:00.000"
    assert result["C1"] == "12:15:00.000"
    assert result["C2"] == "13:15:00.000"
    assert result["C3"] == "13:17:00.000"
    assert result["C4"] == "14:15:00.000"
    assert result["TEND"] == "14:25:00.000"
    assert all(
        f"{name}_local" not in result
        for name in ("C1", "C2", "TMAX", "C3", "C4")
    )


def test_generation_does_not_mutate_source():
    source = {"_date": "2027-08-02", "C1": "10:00:00", "C4": "12:00:00"}
    original = dict(source)

    generate_dryrun_now(
        source,
        {"sequence_margin_min": 10},
        datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc),
    )

    assert source == original

def test_today_rebases_only_calendar_date_and_preserves_utc_times():
    source = {
        "_date": "2027-08-02",
        "_date_utc": "2027-08-02",
        "TSTART": "23:50:00.125",
        "C1": "23:55:00.250",
        "C2": "23:59:00.375",
        "TMAX": "00:00:30.500",
        "C3": "00:02:00.625",
        "C4": "00:30:00.750",
        "TEND": "00:40:00.875",
        "C1_local": "01:55:00.250",
    }
    original = deepcopy(source)
    local_midnight = datetime(
        2026,
        10,
        1,
        0,
        30,
        tzinfo=timezone(timedelta(hours=2)),
    )

    result = generate_dryrun_today(source, local_midnight)

    # The system clock is authoritative in UTC: 00:30 UTC+2 is still the
    # previous UTC calendar day.
    assert result["_date"] == "2026-09-30"
    assert result["_date_utc"] == "2026-09-30"
    for key in ("TSTART", "C1", "C2", "TMAX", "C3", "C4", "TEND"):
        assert result[key] == source[key]
    assert result["C1_local"] == source["C1_local"]
    assert source == original


def test_today_requires_timezone_aware_clock():
    try:
        generate_dryrun_today(
            {"C1": "10:00:00"},
            datetime(2026, 9, 30, 12, 0),
        )
    except ValueError as exc:
        assert "timezone-aware" in str(exc)
    else:
        raise AssertionError("timezone-naive now_utc must be rejected")

