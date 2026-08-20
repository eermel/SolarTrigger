# generate_debug_realistic.py
# Version : 5.1.00
# Date    : 2026-03-19
# Génère un todayeclipse.json représentatif d'une VRAIE éclipse totale
# Basé sur les proportions réelles de l'éclipse du 12 août 2026 (Espagne)
# mais décalé à NOW+60s pour permettre un test immédiat.
#
# Durées réelles utilisées :
#   TSTART → C1      : 60 min  (pré-partielle)
#   C1     → C2      : 53 min  (partielle avant totalité)
#   C2     → C3      :  1 min 30s (totalité)
#   C3     → C4      : 50 min  (partielle après totalité)
#   C4     → TEND    : 60 min  (post-partielle)
#
# Paramètres réels :
#   interval_partial      : 180s
#   interval_diamond_ring : 4s
#   duree_diamond_ring    : 40s
#
from datetime import datetime, timezone, timedelta
import json, os, time as _time

# Décalage UTC local du système (en heures, ex: 1.0 pour CET, 2.0 pour CEST)
_local_offset_s = -_time.timezone if _time.daylight == 0 else -_time.altzone
_local_offset_h = _local_offset_s / 3600

# ── Paramètres réalistes ──────────────────────────────────────────────────────
eclipse_title    = "DEBUG Réaliste — proportions éclipse 2026 Espagne"
eclipse_type     = "Totale"
magnitude        = 1.02618
moon_sun_ratio   = 1.02618
sun_alt_tmax     = "35.4°"

interval_partial      = 180   # secondes — valeur réelle
interval_diamond_ring = 4     # secondes — valeur réelle
duree_diamond_ring    = 40    # secondes — valeur réelle
speed_partial         = 500
speed_diamond         = 500

# ── Calcul des heures UTC dynamiques ─────────────────────────────────────────
now    = datetime.now(timezone.utc)
TSTART = now + timedelta(seconds=60)

C1   = TSTART + timedelta(seconds=2*180 + 10)   # 2×180s + 10s
C2   = C1     + timedelta(seconds=5*180 + 15)   # 5×180s + 15s
C3   = C2     + timedelta(minutes=1, seconds=30) # 1m30 de totalité
C4   = C3     + timedelta(seconds=5*180 + 15)   # 5×180s + 15s
TEND = C4     + timedelta(seconds=2*180 + 10)   # 2×180s + 10s

totality_sec = (C3 - C2).total_seconds()
TMAX = C2 + timedelta(seconds=totality_sec / 2)

def fmt(dt):
    return dt.strftime("%H:%M:%S.%f")[:-3]

def dur(td):
    total = int(td.total_seconds())
    h, r = divmod(total, 3600)
    m, s = divmod(r, 60)
    if h:
        return f"{h}h{m:02d}m"
    return f"{m}m{s:02d}s"

# ── Structure JSON ─────────────────────────────────────────────────────────────
data = {
    "_comment":        "Généré par generate_debug_realistic.py — proportions réelles éclipse 2026",
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

total_dur = TEND - TSTART
print(f"Fichier généré : {filename}")
print(f"")
print(f"  TSTART  : {fmt(TSTART)} UTC  (dans 60s)")
print(f"  C1      : {fmt(C1)} UTC  (+{dur(C1-TSTART)} — début partielle)")
print(f"  C2      : {fmt(C2)} UTC  (+{dur(C2-TSTART)} — début totalité)")
print(f"  TMAX    : {fmt(TMAX)} UTC  (maximum)")
print(f"  C3      : {fmt(C3)} UTC  (fin totalité — {int(totality_sec)}s de totalité)")
print(f"  C4      : {fmt(C4)} UTC  (fin partielle)")
print(f"  TEND    : {fmt(TEND)} UTC  (+{dur(TEND-TSTART)} total)")
print(f"")
print(f"  Durée totale séquence : {dur(total_dur)}")
print(f"  Photos partielle 1a  : ~{int((C2-timedelta(seconds=duree_diamond_ring)-C1).total_seconds()//interval_partial)} photos (toutes les {interval_partial}s)")
print(f"  Photos diamond ring  : ~{int((duree_diamond_ring*2)//interval_diamond_ring)} photos (toutes les {interval_diamond_ring}s)")
print(f"  Photos totalité      : 15 vitesses × 2 passages")
print(f"  Photos partielle 3b  : ~{int((C4-C3-timedelta(seconds=duree_diamond_ring)).total_seconds()//interval_partial)} photos (toutes les {interval_partial}s)")
