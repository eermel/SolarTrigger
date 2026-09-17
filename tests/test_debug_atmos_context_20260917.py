from datetime import datetime, timezone

from backend.dryrun_circumstances import generate_debug_now
from backend.timeline import build_timeline
from backend.atmo import (
    interpolate_altitude,
    validate_atmospheric_timeline,
)


def test_debug_scenario_contains_complete_atmospheric_context():
    now = datetime(2026, 9, 17, 21, 0, 0, tzinfo=timezone.utc)

    circumstances = generate_debug_now(now)

    location = circumstances["_circumstances_location"]

    assert location["altitude_m"] == 0.0
    assert location["source"] == "debug_synthetic"

    for key in (
        "C1_alt_deg",
        "C2_alt_deg",
        "TMAX_alt_deg",
        "C3_alt_deg",
        "C4_alt_deg",
    ):
        assert isinstance(circumstances[key], float)


def test_debug_scenario_atmospheric_interpolation_is_valid():
    now = datetime(2026, 9, 17, 21, 0, 0, tzinfo=timezone.utc)

    circumstances = generate_debug_now(now)
    timeline = build_timeline(
        circumstances,
        fallback_date=now.date(),
    )

    atmospheric_timeline = {
        key: timeline[key]
        for key in ("C1", "C2", "TMAX", "C3", "C4")
    }

    validate_atmospheric_timeline(atmospheric_timeline)

    altitudes = {
        key: circumstances[key]
        for key in (
            "C1_alt_deg",
            "C2_alt_deg",
            "TMAX_alt_deg",
            "C3_alt_deg",
            "C4_alt_deg",
        )
    }

    altitude = interpolate_altitude(
        timeline["C1"],
        atmospheric_timeline,
        altitudes,
    )

    assert altitude == 20.0
