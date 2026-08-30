from datetime import date
from backend.timeline import parse_hms_seconds, build_timeline


def validate_circumstances(cfg: dict) -> list[str]:
    """Validate central and partial eclipse circumstances."""

    errors: list[str] = []

    parsed: dict[str, float | None] = {}
    times_ok = True

    # Always required, including a partial eclipse.
    for key in ("C1", "TMAX", "C4"):
        val = cfg.get(key)
        try:
            parsed[key] = parse_hms_seconds(val)
        except Exception:
            errors.append(f"Invalid {key}: {val}")
            times_ok = False
        else:
            if parsed[key] is None:
                errors.append(f"Missing {key}")
                times_ok = False

    # C2 and C3 are optional, but form one indivisible pair.
    for key in ("C2", "C3"):
        val = cfg.get(key)
        try:
            parsed[key] = parse_hms_seconds(val)
        except Exception:
            errors.append(f"Invalid {key}: {val}")
            parsed[key] = None
            times_ok = False

    has_c2 = parsed.get("C2") is not None
    has_c3 = parsed.get("C3") is not None

    if has_c2 != has_c3:
        errors.append("C2 and C3 must both be present or both be absent")
        times_ok = False

    if times_ok:
        try:
            tl = build_timeline(cfg, fallback_date=date.today())

            if has_c2:
                if not (
                    tl["C1"]
                    < tl["C2"]
                    < tl["TMAX"]
                    < tl["C3"]
                    < tl["C4"]
                ):
                    errors.append(
                        "Order error: C1<C2<TMAX<C3<C4 violated"
                    )
            else:
                if not (tl["C1"] < tl["TMAX"] < tl["C4"]):
                    errors.append(
                        "Order error: C1<TMAX<C4 violated"
                    )
        except Exception as exc:
            errors.append(f"Order build error: {exc}")

    if bool(cfg.get("atmo_compensation", False)):
        required_altitudes = [
            "C1_alt_deg",
            "TMAX_alt_deg",
            "C4_alt_deg",
        ]
        if has_c2 and has_c3:
            required_altitudes.extend([
                "C2_alt_deg",
                "C3_alt_deg",
            ])

        missing_alt = [
            key
            for key in required_altitudes
            if cfg.get(key) is None
        ]
        if missing_alt:
            errors.append(
                "Missing altitudes for atmo_compensation: "
                + ",".join(missing_alt)
            )

        loc = cfg.get("_circumstances_location")
        if not (
            isinstance(loc, dict)
            and loc.get("altitude_m") is not None
        ):
            errors.append(
                "Missing _circumstances_location.altitude_m "
                "for atmo_compensation"
            )

    return errors
