from datetime import date
from backend.timeline import parse_hms_seconds, build_timeline


def validate_circumstances(cfg: dict) -> list[str]:
    """Pure validation of eclipse circumstances.

    Replicates the exact --check rules/messages used in scripts/eclipse_trigger.py
    for validating circumstances C1..C4 and TMAX, their strict ordering using
    build_timeline, and atmospheric compensation requirements.
    """
    errors: list[str] = []

    # Presence/format of required times C1, C2, TMAX, C3, C4
    required = ("C1", "C2", "TMAX", "C3", "C4")
    times_ok = True

    for key in required:
        val = cfg.get(key)
        try:
            sec = parse_hms_seconds(val)
        except Exception:
            errors.append(f"Invalid {key}: {val}")
            times_ok = False
        else:
            if sec is None:
                errors.append(f"Missing {key}")
                times_ok = False

    # Strict chronological order using unfolded timeline
    if times_ok:
        try:
            tl = build_timeline({k: cfg.get(k) for k in required}, fallback_date=date.today())
            c1, c2, tmax, c3, c4 = tl["C1"], tl["C2"], tl["TMAX"], tl["C3"], tl["C4"]
            if not (c1 < c2 < tmax < c3 < c4):
                errors.append("Order error: C1<C2<TMAX<C3<C4 violated")
        except Exception as exc:
            # Keep message identical to current --check behavior
            errors.append(f"Order build error: {exc}")

    # Atmospheric compensation requirements
    if bool(cfg.get("atmo_compensation", False)):
        alts = {
            "C1_alt_deg": cfg.get("C1_alt_deg"),
            "C2_alt_deg": cfg.get("C2_alt_deg"),
            "TMAX_alt_deg": cfg.get("TMAX_alt_deg"),
            "C3_alt_deg": cfg.get("C3_alt_deg"),
            "C4_alt_deg": cfg.get("C4_alt_deg"),
        }
        missing_alt = [k for k, v in alts.items() if v is None]
        if missing_alt:
            errors.append("Missing altitudes for atmo_compensation: " + ",".join(missing_alt))
        loc = cfg.get("_circumstances_location")
        if not (isinstance(loc, dict) and loc.get("altitude_m") is not None):
            errors.append("Missing _circumstances_location.altitude_m for atmo_compensation")

    return errors
