# generate_debug_total.py
# Version : 5.1.00
# Date    : 2026-03-15
# Génère un todayeclipse.json de test pour une éclipse TOTALE (debug)
# Compatible avec la validation START de app.py (tous champs requis).
#
from datetime import datetime, timezone, timedelta
import json, os, time as _time
_local_offset_s = -_time.timezone if _time.daylight == 0 else -_time.altzone
_local_offset_h = _local_offset_s / 3600

# ── Paramètres de base ────────────────────────────────────────────────────────
eclipse_title    = "DEBUG Totale — test rapide Pi"
eclipse_type     = "Totale"
magnitude        = 1.03296
moon_sun_ratio   = 1.03296
sun_alt_tmax     = "7.6°"

interval_partial      = 20    # secondes
interval_diamond_ring = 1     # secondes
duree_diamond_ring    = 12    # secondes
speed_partial         = 500
speed_diamond         = 500

# ── Calcul des heures UTC dynamiques ─────────────────────────────────────────
now    = datetime.now(timezone.utc)
TSTART = now + timedelta(seconds=60)

C1   = TSTART + timedelta(minutes=1, seconds=30)
C2   = C1     + timedelta(minutes=2)
C3   = C2     + timedelta(seconds=30)   # totalité 30s pour test rapide
C4   = C3     + timedelta(minutes=2)
TEND = C4     + timedelta(minutes=1, seconds=30)

totality_sec = (C3 - C2).total_seconds()
TMAX = C2 + timedelta(seconds=totality_sec / 2)

def fmt(dt):
    return dt.strftime("%H:%M:%S.%f")[:-3]

# ── Structure JSON — compatibilité totale avec validation START ───────────────
data = {
    "_comment":        "Généré par generate_debug_total.py — test local rapide",
    "_eclipse":        eclipse_title,
    "_type":           eclipse_type,
    "_type_global":    eclipse_type,
    "_magnitude":      magnitude,
    "_moon_sun_ratio": moon_sun_ratio,
    "_duration":       f"{int(totality_sec // 60)}m {int(totality_sec % 60)}s",
    "_sun_alt_tmax":   sun_alt_tmax,
    "_generated_utc":  now.strftime("%Y-%m-%d %H:%M:%S UTC"),
    "_date":           now.strftime("%Y-%m-%d"),
    "_date_utc":       now.strftime("%Y-%m-%d"),
    "_timezone":       _local_offset_h,
    "_circumstances_location": {
        "latitude": None, "longitude": None, "altitude_m": None,
        "comment": "DEBUG : circonstances translatées, aucune position réelle de validité."
    },

    "title":   eclipse_title,
    "C1":      fmt(C1),
    "C2":      fmt(C2),
    "C3":      fmt(C3),
    "C4":      fmt(C4),
    "TMAX":    fmt(TMAX),
    "TSTART":  fmt(TSTART),
    "TEND":    fmt(TEND),

    "interval_partial":         interval_partial,
    "interval_diamond_ring":    interval_diamond_ring,
    "duree_diamond_ring":       duree_diamond_ring,
    "shutterspeed_partial":     f"1/{speed_partial}",
    "shutterspeed_diamondring": f"1/{speed_diamond}",

    "phase1a": {
        "interval_s":  interval_partial,
        "speed_denom": speed_partial,
    },
    "diamond_ring": {
        "interval_s":  interval_diamond_ring,
        "duration_s":  duree_diamond_ring,
        "speed_denom": speed_diamond,
    },
    "phase3b": {
        "interval_s":  interval_partial,
        "speed_denom": speed_partial,
    },
    "totality": {
        "speeds": [
            "1/4000", "1/2000", "1/1000", "1/500", "1/250",
            "1/125",  "1/60",   "1/30",   "1/15",  "1/8",
            "1/4",    "1/2",    "1",      "2",     "4"
        ]
    }
}

out_dir  = os.path.dirname(os.path.abspath(__file__))
filename = os.path.join(out_dir, "todayeclipse.json")

with open(filename, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=4)

print(f"Fichier généré : {filename}")
print(f"  TSTART  : {fmt(TSTART)} UTC  (dans 60s)")
print(f"  C1      : {fmt(C1)} UTC  (+3min)")
print(f"  C2      : {fmt(C2)} UTC  (début totalité, +5min)")
print(f"  TMAX    : {fmt(TMAX)} UTC  (milieu, {int(totality_sec)}s de totalité)")
print(f"  C3      : {fmt(C3)} UTC  (fin totalité)")
print(f"  C4      : {fmt(C4)} UTC")
print(f"  TEND    : {fmt(TEND)} UTC")
