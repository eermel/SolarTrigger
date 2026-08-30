#!/usr/bin/env python3
"""Generate trigger JSON from the repository's Python eclipse engine."""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from backend.eclipse_engine.compute import compute_local_circumstances
from backend.eclipse_engine.loader import EclipseDataError, load_eclipse


EVENTS = ("C1", "C2", "TMAX", "C3", "C4")
_GLOBAL_TYPES = {"T": "Totale", "A": "Annulaire", "P": "Partielle", "H": "Hybride"}


def _finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise argparse.ArgumentTypeError("must be a finite number")
    return number


def _latitude(value: str) -> float:
    number = _finite_float(value)
    if not -90.0 <= number <= 90.0:
        raise argparse.ArgumentTypeError("must be between -90 and 90")
    return number


def _longitude(value: str) -> float:
    number = _finite_float(value)
    if not -180.0 <= number <= 180.0:
        raise argparse.ArgumentTypeError("must be between -180 and 180")
    return number


def _parse_time(value: str | None) -> float | None:
    if value is None:
        return None
    hours, minutes, seconds = value.split(":")
    return int(hours) * 3600.0 + int(minutes) * 60.0 + float(seconds)


def _format_time(total_seconds: float) -> str:
    total_ms = math.floor((total_seconds % 86400.0) * 1000.0 + 0.5) % 86_400_000
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    return f"{hours:02d}:{minutes:02d}:{remainder / 1000.0:06.3f}"


def _shift_time(value: str | None, hours: float) -> str:
    seconds = _parse_time(value)
    return "00:00:00.000" if seconds is None else _format_time(seconds + hours * 3600.0)


def _dataset_label(dataset: dict[str, Any], date_iso: str) -> str:
    source = dataset.get("source")
    option_text = source.get("option_text") if isinstance(source, dict) else None
    return option_text if isinstance(option_text, str) else date_iso


def _global_type(label: str, local_type: str) -> str:
    for code, name in _GLOBAL_TYPES.items():
        if f"({code})" in label.upper():
            return name
    return local_type


def build_trigger_config(
    dataset: dict[str, Any],
    circumstances: dict[str, Any],
    date_iso: str,
    latitude: float,
    longitude: float,
    altitude_m: float,
    tz_offset: float,
) -> dict[str, Any]:
    """Build the same trigger-facing document shape as the existing JS flow."""

    label = _dataset_label(dataset, date_iso)
    local_type = circumstances["eclipse_type"]
    utc = {event: circumstances[f"{event}_utc"] for event in EVENTS}
    local = {event: circumstances[f"{event}_local"] for event in EVENTS}

    return {
        "_comment": "Calculé par eclipse_calculator_py.py — moteur Python Jubier",
        "_eclipse": label,
        "_type_global": _global_type(label, local_type),
        "_type": local_type,
        "_magnitude": circumstances["magnitude"],
        "_moon_sun_ratio": circumstances["moon_sun_ratio"],
        "_obscuration_percent": circumstances["obscuration_percent"],
        "_duration": circumstances["duration_str"],
        "_sun_alt_tmax": circumstances["sun_alt_tmax"],
        "_generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "_date": date_iso,
        "_date_utc": date_iso,
        "_circumstances_location": {
            "latitude": float(latitude),
            "longitude": float(longitude),
            "altitude_m": float(altitude_m),
            "comment": "Circonstances calculées pour cette position GPS et cette altitude.",
        },
        "_timezone": f"UTC{tz_offset:+g}",
        "title": label,
        "C1": _shift_time(utc["C1"], 0.0),
        "C2": _shift_time(utc["C2"], 0.0) if utc["C2"] else None,
        "C3": _shift_time(utc["C3"], 0.0) if utc["C3"] else None,
        "C4": _shift_time(utc["C4"], 0.0),
        "TMAX": _shift_time(utc["TMAX"], 0.0),
        "TSTART": _shift_time(utc["C1"], -1.0),
        "TEND": _shift_time(utc["C4"], 1.0),
        "C1_local": _shift_time(local["C1"], 0.0),
        "C2_local": _shift_time(local["C2"], 0.0) if local["C2"] else None,
        "C3_local": _shift_time(local["C3"], 0.0) if local["C3"] else None,
        "C4_local": _shift_time(local["C4"], 0.0),
        "TMAX_local": _shift_time(local["TMAX"], 0.0),
        **{
            f"{event}_alt_deg": (
                None
                if circumstances.get(f"{event}_alt_deg") is None
                else float(circumstances[f"{event}_alt_deg"])
            )
            for event in EVENTS
        },
        "interval_partial": 180,
        "interval_diamond_ring": 4,
        "duree_diamond_ring": 40,
        "shutterspeed_partial": "1/500",
        "shutterspeed_diamondring": "1/500",
        "phase1a": {"interval_s": 180, "speed_denom": 500},
        "diamond_ring": {"interval_s": 4, "duration_s": 40, "speed_denom": 500},
        "phase3b": {"interval_s": 180, "speed_denom": 500},
    }


def default_output_path(date_iso: str, latitude: float, longitude: float) -> Path:
    return REPOSITORY_ROOT / "data" / "eclipses" / "out" / f"{date_iso}_{latitude:g}_{longitude:g}.json"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Calcule les circonstances d'une éclipse avec le moteur Python")
    parser.add_argument("--lat", type=_latitude, required=True, help="Latitude décimale (+ Nord)")
    parser.add_argument("--lon", type=_longitude, required=True, help="Longitude décimale (+ Est)")
    parser.add_argument("--alt", type=_finite_float, default=0.0, help="Altitude en mètres")
    parser.add_argument("--tz", type=_finite_float, default=0.0, help="Décalage UTC en heures, DST inclus")
    parser.add_argument("--date", "--eclipse", dest="date_iso", required=True, help="Date ISO YYYY-MM-DD")
    parser.add_argument("--output", type=Path, help="Fichier JSON de sortie")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        datetime.strptime(args.date_iso, "%Y-%m-%d")
        dataset = load_eclipse(args.date_iso)
        circumstances = compute_local_circumstances(
            dataset, args.lat, args.lon, args.alt, args.tz
        )
    except (ValueError, EclipseDataError) as exc:
        parser.error(str(exc))

    output = args.output or default_output_path(args.date_iso, args.lat, args.lon)
    output.parent.mkdir(parents=True, exist_ok=True)
    config = build_trigger_config(
        dataset, circumstances, args.date_iso, args.lat, args.lon, args.alt, args.tz
    )
    output.write_text(json.dumps(config, ensure_ascii=False, indent=4) + "\n", encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
