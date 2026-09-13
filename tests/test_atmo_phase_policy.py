from datetime import datetime, timedelta

from backend.atmo import facteur_atmospherique, interpolate_altitude
from backend.preview_materializer import apply_atmos_if_enabled


def test_airmass_compensation_increases_toward_horizon():
    assert facteur_atmospherique(5, 0) > facteur_atmospherique(30, 0) > 1


def test_altitude_interpolation_uses_absolute_capture_time():
    base = datetime(2026, 8, 12, 18, 0)
    timeline = {"C1": base, "TMAX": base + timedelta(hours=1), "C4": base + timedelta(hours=2)}
    altitudes = {"C1_alt_deg": 10, "TMAX_alt_deg": 20, "C4_alt_deg": 30}
    assert interpolate_altitude(base + timedelta(minutes=30), timeline, altitudes) == 15


def test_one_center_derived_atmos_exposure_is_appended():
    base = datetime(2026, 8, 12, 18, 0)
    plan = (True, "1/2000", "1/500", 1.0, None)
    result, applied, shutter = apply_atmos_if_enabled(
        {"photo": {"atmos_enabled": True}},
        plan,
        base + timedelta(minutes=30),
        {
            "timeline": {"C1": base, "TMAX": base + timedelta(hours=1), "C4": base + timedelta(hours=2)},
            "altitudes": {"C1_alt_deg": 5, "TMAX_alt_deg": 5, "C4_alt_deg": 5},
            "location": {"altitude_m": 0},
        },
    )
    assert applied is True
    assert result[4][:-1] == ["1/2000", "1/1000", "1/500"]
    assert result[4][-1] == shutter
