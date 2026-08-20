# generate_debug_partial.py
# Version : 5.1.00
# Date    : 2026-03-15
# Génère un todayeclipse.json de test pour une éclipse PARTIELLE (debug)
# Les heures sont en UTC. Les heures locales sont calculées à la volée par l'UI.
# Pour une éclipse partielle : C2 = C3 = TMAX (pas de phase de totalité).
#
from datetime import datetime, timezone, timedelta
import json
import os
import time as _time

_local_offset_s = -_time.timezone if _time.daylight == 0 else -_time.altzone
_local_offset_h = _local_offset_s / 3600

# ── Paramètres de base ────────────────────────────────────────────────────────
eclipse_title    = "DEBUG PARTIELLE — Espagne 2026"
eclipse_type     = "Partielle"
magnitude        = 0.93054
moon_sun_ratio   = 1.03296
sun_alt_tmax     = "7.6°"

interval_partial      = 20     # secondes — Phase PARTIELLE
interval_diamond_ring = 1      # secondes — non utilisé en partielle mais présent
duree_diamond_ring    = 12     # secondes
speed_partial         = 500
speed_diamond         = 500

# ── Calcul des heures UTC dynamiques ─────────────────────────────────────────
now    = datetime.now(timezone.utc)
TSTART = now + timedelta(seconds=60)

C1   = TSTART + timedelta(minutes=3)
C4   = C1     + timedelta(minutes=5)
TEND = C4     + timedelta(minutes=3)

# TMAX partielle = C1 + (C4-C1)/2
partial_sec = (C4 - C1).total_seconds()
TMAX = C1 + timedelta(seconds=partial_sec / 2)

# Partielle : C2 = C3 = TMAX (pas de totalité)
C2 = TMAX
C3 = TMAX

def fmt(dt):
    return dt.strftime("%H:%M:%S.%f")[:-3]

# ── Structure JSON (format actuel — sans heures locales) ─────────────────────
data = {
    "_comment":        "Généré par generate_debug_partial.py — test local rapide",
    "_eclipse":        eclipse_title,
    "_type":           eclipse_type,
    "_magnitude":      magnitude,
    "_moon_sun_ratio": moon_sun_ratio,
    "_duration":       "--",    # pas de durée de totalité
    "_sun_alt_tmax":   sun_alt_tmax,
    "_generated_utc":  datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
    "_date":           C1.strftime("%Y-%m-%d"),
    "_date_utc":       C1.strftime("%Y-%m-%d"),
    "_timezone":       _local_offset_h,
    "_circumstances_location": {
        "latitude": None, "longitude": None, "altitude_m": None,
        "comment": "DEBUG : circonstances translatées, aucune position réelle de validité."
    },

    "title":   eclipse_title,
    "C1":      fmt(C1),
    "C2":      fmt(C2),   # = TMAX pour éclipse partielle
    "C3":      fmt(C3),   # = TMAX pour éclipse partielle
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

# ── Sauvegarde dans le répertoire du script ───────────────────────────────────
out_dir  = os.path.dirname(os.path.abspath(__file__))
filename = os.path.join(out_dir, "todayeclipse.json")

with open(filename, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=4)

print(f"Fichier généré : {filename}")
print(f"  TSTART  : {fmt(TSTART)} UTC")
print(f"  C1      : {fmt(C1)} UTC  (début partialité)")
print(f"  TMAX    : {fmt(TMAX)} UTC  (maximum — C2=C3=TMAX)")
print(f"  C4      : {fmt(C4)} UTC  (fin partialité)")
print(f"  TEND    : {fmt(TEND)} UTC")
