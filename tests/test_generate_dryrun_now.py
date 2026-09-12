from datetime import datetime, timezone

from backend.dryrun_circumstances import generate_dryrun_now


def test_translates_all_contacts_and_sets_effective_start_to_now_plus_five_minutes():
    circumstances = {
        "_date": "2027-08-02",
        "C1": "10:00:00",
        "C2": "11:00:00",
        "TMAX": "11:01:00",
        "C3": "11:02:00",
        "C4": "12:00:00",
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


def test_generation_does_not_mutate_source():
    source = {"_date": "2027-08-02", "C1": "10:00:00", "C4": "12:00:00"}
    original = dict(source)

    generate_dryrun_now(
        source,
        {"sequence_margin_min": 10},
        datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc),
    )

    assert source == original
