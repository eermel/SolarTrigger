#!/usr/bin/env python3
"""
eclipse_calculator_jubier.py
─────────────────────────────
Version : 5.1.02
Date    : 2026-03-15

Ajout de _type_global dans le JSON : type de l'éclipse dans la bande (ex: Totale)
distinct de _type qui est le type à la position GPS (ex: Partielle depuis Paris).

Calcule C1, C2, TMAX, C3, C4 en utilisant DIRECTEMENT le JavaScript
de Xavier Jubier, via un mini serveur Flask + Playwright/Chromium headless.

Architecture :
  1. Flask sert index.html + les JS de Jubier sur 127.0.0.1:5051
  2. Playwright ouvre la page dans Chromium headless
  3. On injecte lat/lon/alt/eclipse via JS (bypass readform pour éviter alert())
     → on appelle getall() directement après avoir rempli obsvconst[]
  4. On lit c1[1], c2[1], mid[1], c3[1], c4[1] (paramètres t de Bessel)
     et on les convertit en UTC via la même formule que gettime() de Jubier
  5. On génère todayeclipse.json

Dépendances (à installer sur le Pi) :
    pip3 install flask playwright --break-system-packages
    playwright install chromium
    # OU utiliser le Chromium système :
    sudo apt install -y chromium chromium-driver
    PLAYWRIGHT_BROWSERS_PATH=0 pip3 install playwright --break-system-packages

Usage :
    python3 eclipse_calculator_jubier.py --lat 25.6872 --lon 32.6396 --alt 80 --tz 2 --eclipse 2027-08-02
    python3 eclipse_calculator_jubier.py --lat 40.4168 --lon -3.7038 --alt 650 --tz 2 --eclipse 2026-08-12
    python3 eclipse_calculator_jubier.py --list
"""

import argparse
import json
import os
import shutil
import sys
import threading
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ── Couleurs ANSI ─────────────────────────────────────────────────────────────
R  = "\033[1;31m"
G  = "\033[1;32m"
Y  = "\033[1;33m"
B  = "\033[1;34m"
CY = "\033[1;36m"
OR = "\033[38;2;255;127;0m"
PK = "\033[38;5;198m"
RE = "\033[0m"

# ── Chemins ───────────────────────────────────────────────────────────────────
SCRIPT_DIR  = Path(__file__).parent
JUBIER_DIR  = SCRIPT_DIR / "jubier_files"
FLASK_HOST  = "127.0.0.1"
FLASK_PORT  = 5051

# ── Fichiers requis de Jubier ─────────────────────────────────────────────────
REQUIRED_FILES = [
    "index.html",
    "SolarEclipseTimerSVG_VML.js",
    "SolarEclipseTimerDefaultSettings.js",
    "NewPopWindow.js",
]

# ── Éclipses disponibles ──────────────────────────────────────────────────────
# "val" = valeur du <select id="eclipse_index"> dans index.html de Jubier
# obsvconst[6] = 28 * (val + 65) dans le JS
ECLIPSES = {
    "2026-08-12": {"label": "2026 Aug 12 — Totale (Espagne/Méditerranée)", "val": "59"},
    "2027-08-02": {"label": "2027 Aug 02 — Totale (Égypte/Louxor)",        "val": "61"},
    "2028-07-22": {"label": "2028 Jul 22 — Totale",                         "val": "63"},
    "2030-11-25": {"label": "2030 Nov 25 — Totale",                         "val": "69"},
    "2034-03-20": {"label": "2034 Mar 20 — Totale",                         "val": "76"},
    "2035-09-02": {"label": "2035 Sep 02 — Totale",                         "val": "79"},
}

# ══════════════════════════════════════════════════════════════════════════════
# 1. SETUP FICHIERS JUBIER
# ══════════════════════════════════════════════════════════════════════════════

def setup_files():
    JUBIER_DIR.mkdir(exist_ok=True)
    # Chemins de recherche des fichiers de Jubier
    search_dirs = [
        SCRIPT_DIR,
        Path("/mnt/user-data/uploads"),
        Path.home() / "python_solareclipsetrigger",
        Path.home() / "flaskapp_solareclipsetrigger",
    ]
    missing = []
    for fname in REQUIRED_FILES:
        dst = JUBIER_DIR / fname
        if dst.exists():
            continue
        found = False
        for d in search_dirs:
            src = d / fname
            if src.exists():
                shutil.copy2(src, dst)
                print(f"{G}  Copié : {fname}{RE}")
                found = True
                break
        if not found:
            missing.append(fname)

    # CSS optionnel — créer un placeholder si absent
    for css in ["communprivate.css", "commonprivate.css"]:
        if not (JUBIER_DIR / css).exists():
            (JUBIER_DIR / css).write_text("/* placeholder */")

    if missing:
        print(f"{R}Fichiers manquants dans {JUBIER_DIR} :{RE}")
        for f in missing:
            print(f"  {R}✗ {f}{RE}")
        print(f"\n{Y}Copiez les fichiers JS de Xavier Jubier dans :{RE}")
        print(f"  {JUBIER_DIR}")
        sys.exit(1)
    print(f"{G}  Fichiers Jubier OK{RE}")


# ══════════════════════════════════════════════════════════════════════════════
# 2. SERVEUR FLASK LOCAL
# ══════════════════════════════════════════════════════════════════════════════

def start_flask():
    try:
        from flask import Flask, send_from_directory
    except ImportError:
        print(f"{R}Flask manquant :{RE}")
        print(f"  pip3 install flask --break-system-packages")
        sys.exit(1)

    import logging
    logging.getLogger("werkzeug").setLevel(logging.ERROR)

    app = Flask(__name__)

    @app.route("/")
    def index():
        return send_from_directory(str(JUBIER_DIR), "index.html")

    @app.route("/<path:filename>")
    def static_file(filename):
        return send_from_directory(str(JUBIER_DIR), filename)

    thread = threading.Thread(
        target=lambda: app.run(host=FLASK_HOST, port=FLASK_PORT,
                               debug=False, use_reloader=False),
        daemon=True
    )
    thread.start()

    # Attendre que Flask soit prêt
    import urllib.request
    for _ in range(40):
        try:
            urllib.request.urlopen(f"http://{FLASK_HOST}:{FLASK_PORT}/", timeout=1)
            print(f"{G}  Flask OK → http://{FLASK_HOST}:{FLASK_PORT}/{RE}")
            return
        except Exception:
            time.sleep(0.25)
    print(f"{R}Flask n'a pas démarré.{RE}")
    sys.exit(1)


# ══════════════════════════════════════════════════════════════════════════════
# 3. JAVASCRIPT INJECTÉ DANS JUBIER
# ══════════════════════════════════════════════════════════════════════════════
#
# Stratégie : on ne passe PAS par readform() car il peut afficher des alert()
# bloquants. À la place, on remplit directement obsvconst[] en JS, puis on
# appelle getall() (le cœur de l'algorithme).
#
# Convention Jubier pour obsvconst[1] (longitude) :
#   positif = Ouest, négatif = Est  (INVERSE de la convention standard !)
#   source : obsvconst[1] *= parseFloat(document.getElementById("lonx").value)
#            avec lonx: E=-1, W=1
#
# Convention pour obsvconst[3] (fuseau) :
#   obsvconst[3] = tzh * tzx - dst
#   avec tzx: E=-1, W=1
#   => pour UTC+2 été (dst=1) : obsvconst[3] = 2*(-1) - 1 = -3 ? Non.
#   En relisant : obsvconst[3] *= tzx, puis -= dst
#   => tzh=2, tzm=0, tzx=-1 (Est), dst=1 : obsvconst[3] = -2 - 1 = -3
#   Mais gettime() fait : t + t0 - obsvconst[3] - dT/3600
#   Donc heure locale = UTC - obsvconst[3]
#   Pour UTC+2 : heure_locale = UTC + 2 => obsvconst[3] = -2 (sans DST)
#   Avec DST=1 (heure été) : obsvconst[3] = -3  (UTC+3 au total) → FAUX
#
# CLARIFICATION après relecture fine du JS readform() :
#   obsvconst[3] = (tzh + tzm/60) * tzx - dst
#   tzx = -1 pour Est → obsvconst[3] = -2 - 1 = -3 pour UTC+2 DST=1
#   gettime(): local_t = t + t0 - obsvconst[3] - dT/3600
#            = t + t0 + 3 - dT/3600  = UTC + 3 ✓ (CEST = UTC+2, mais DST fait +1)
#
# DONC : pour un fuseau UTC+TZ avec DST=0 : obsvconst[3] = -TZ
#        pour un fuseau UTC+TZ avec DST=1 : obsvconst[3] = -(TZ+1)
#
# En pratique on passe tz_offset = offset total (DST déjà inclus dans la valeur)
# et dst = 0 toujours dans l'injection JS.

JS_CALCULATE = """(params) => {
    // Bloquer les alert() bloquants
    window.alert  = (msg) => { console.warn('alert blocked:', msg); };
    window.confirm = () => true;
    window.prompt  = () => '';

    const { lat_dd, lon_dd, alt_m, tz_offset, eclipse_val } = params;

    // Vérifier que les fonctions Jubier sont chargées
    if (typeof getall === 'undefined' || typeof obsvconst === 'undefined') {
        return { error: 'Fonctions Jubier non disponibles (getall, obsvconst)' };
    }

    const D2R = Math.PI / 180.0;

    // ── Remplir obsvconst[] directement (bypass readform) ─────────────────
    // [0] latitude en radians (Nord = positif)
    obsvconst[0] = lat_dd * D2R;

    // [1] longitude en radians — CONVENTION JUBIER : Est = négatif !
    obsvconst[1] = -lon_dd * D2R;

    // [2] altitude en mètres
    obsvconst[2] = alt_m;

    // [3] offset fuseau : négatif pour Est (UTC+tz_offset)
    // tz_offset = offset total en heures (ex: 2 pour UTC+2)
    obsvconst[3] = -tz_offset;

    // [4] et [5] : position géocentrique de l'observateur
    const tmp = Math.atan(0.996647189335 * Math.tan(obsvconst[0]));
    obsvconst[4] = (0.996647189335 * Math.sin(tmp))
                 + (obsvconst[2] * Math.sin(obsvconst[0]) / 6378137.0);
    obsvconst[5] = Math.cos(tmp)
                 + (obsvconst[2] * Math.cos(obsvconst[0]) / 6378137.0);

    // [6] index dans le tableau elements[] pour l'éclipse choisie
    obsvconst[6] = 28 * (parseInt(eclipse_val, 10) + 65);

    // ── Lancer le calcul complet ───────────────────────────────────────────
    try {
        getall();
    } catch(e) {
        return { error: 'getall() exception: ' + e.toString() };
    }

    // ── Extraire les résultats ─────────────────────────────────────────────
    const idx = obsvconst[6];
    const t0  = elements[1 + idx];   // heure TDT de référence (t=0)
    const dT  = elements[4 + idx];   // delta T en secondes

    // Convertir paramètre t → UTC (formule identique à gettime() dans Jubier)
    function formatHMSms(hours) {
        let totalMs = Math.round((((hours % 24) + 24) % 24) * 3600000.0);
        totalMs = ((totalMs % 86400000) + 86400000) % 86400000;
        const h = Math.floor(totalMs / 3600000); totalMs -= h * 3600000;
        const m = Math.floor(totalMs / 60000);   totalMs -= m * 60000;
        const sec = totalMs / 1000.0;
        const ss = sec.toFixed(3).padStart(6, '0');
        return String(h).padStart(2,'0')+':'+String(m).padStart(2,'0')+':'+ss;
    }

    function tToUTC(t) {
        return formatHMSms(t + t0 - (dT / 3600.0));
    }

    // Convertir paramètre t → heure locale
    // locale = UTC - obsvconst[3]  (obsvconst[3] = -tz_offset)
    function tToLocal(t) {
        let u = t + t0 - (dT / 3600.0) - obsvconst[3];
        return formatHMSms(u);
    }

    // mid[39] : 0=aucune 1=partielle 2=annulaire 3=totale
    const typeMap = {0:'Aucune', 1:'Partielle', 2:'Annulaire', 3:'Totale'};
    const eType = typeMap[mid[39]] || 'Inconnue';

    // Durée de totalité C2→C3
    let durSec = 0;
    if (mid[39] >= 2) {
        durSec = Math.abs(c3[1] - c2[1]) * 3600.0;
    }
    const durStr = Math.floor(durSec / 60) + 'm ' + Math.round(durSec % 60) + 's';

    // Magnitude et ratio Lune/Soleil
    const mag   = Math.round(mid[37] * 100000) / 100000;
    const ratio = Math.round(mid[38] * 100000) / 100000;

    // Altitude du Soleil à TMAX (en degrés)
    const sunAlt = (typeof mid[45] !== 'undefined')
                 ? (mid[45] * 180 / Math.PI).toFixed(1) + '°'
                 : 'n/a';

    return {
        eclipse_type  : eType,
        magnitude     : mag,
        moon_sun_ratio: ratio,
        duration_str  : durStr,
        duration_sec  : Math.round(durSec),
        sun_alt_tmax  : sunAlt,
        C1_utc    : mid[39] >= 1 ? tToUTC(c1[1])   : null,
        C2_utc    : mid[39] >= 2 ? tToUTC(c2[1])   : null,
        TMAX_utc  : mid[39] >= 1 ? tToUTC(mid[1])  : null,
        C3_utc    : mid[39] >= 2 ? tToUTC(c3[1])   : null,
        C4_utc    : mid[39] >= 1 ? tToUTC(c4[1])   : null,
        C1_local  : mid[39] >= 1 ? tToLocal(c1[1])  : null,
        C2_local  : mid[39] >= 2 ? tToLocal(c2[1])  : null,
        TMAX_local: mid[39] >= 1 ? tToLocal(mid[1]) : null,
        C3_local  : mid[39] >= 2 ? tToLocal(c3[1])  : null,
        C4_local  : mid[39] >= 1 ? tToLocal(c4[1])  : null,
    };
}"""


# ══════════════════════════════════════════════════════════════════════════════
# 4. PLAYWRIGHT
# ══════════════════════════════════════════════════════════════════════════════

def run_playwright(lat, lon, alt, tz_offset, eclipse_val):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(f"{R}Playwright manquant :{RE}")
        print(f"  pip3 install playwright --break-system-packages")
        print(f"  playwright install chromium")
        sys.exit(1)

    url = f"http://{FLASK_HOST}:{FLASK_PORT}/"

    # Chercher Chromium installé en système (Raspberry Pi)
    chromium_system_paths = [
        "/usr/bin/chromium-browser",
        "/usr/bin/chromium",
        "/snap/bin/chromium",
        "/usr/lib/chromium-browser/chromium-browser",
    ]

    launch_kwargs = {
        "headless": True,
        "args": [
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--disable-software-rasterizer",
        ]
    }

    for cp in chromium_system_paths:
        if Path(cp).exists():
            launch_kwargs["executable_path"] = cp
            print(f"{G}  Chromium système : {cp}{RE}")
            break

    params = {
        "lat_dd":      lat,
        "lon_dd":      lon,
        "alt_m":       alt,
        "tz_offset":   tz_offset,
        "eclipse_val": eclipse_val,
    }

    with sync_playwright() as pw:
        browser = pw.chromium.launch(**launch_kwargs)
        page = browser.new_page()

        print(f"{B}  Chargement de la page Jubier...{RE}")
        try:
            page.goto(url, wait_until="networkidle", timeout=20000)
        except Exception as e:
            print(f"{R}  Erreur chargement : {e}{RE}")
            browser.close()
            sys.exit(1)

        # Attendre que les fonctions JS Jubier soient disponibles
        try:
            page.wait_for_function("typeof getall !== 'undefined'", timeout=10000)
        except Exception:
            print(f"{R}  Les fonctions JS Jubier ne se sont pas chargées.{RE}")
            print(f"{Y}  Vérifiez que SolarEclipseTimerSVG_VML.js est dans {JUBIER_DIR}{RE}")
            browser.close()
            sys.exit(1)

        print(f"{B}  Exécution de l'algorithme Jubier...{RE}")
        result = page.evaluate(JS_CALCULATE, params)
        browser.close()

    return result


# ══════════════════════════════════════════════════════════════════════════════
# 5. GÉNÉRATION JSON
# ══════════════════════════════════════════════════════════════════════════════

def _parse_hms_seconds(value):
    if not value:
        return None
    h, m, sec = str(value).split(":")
    return int(h) * 3600.0 + int(m) * 60.0 + float(sec)


def _fmt_hms_ms(total_seconds):
    total_seconds = total_seconds % 86400.0
    # Arrondi milliseconde avec gestion du report à minuit.
    total_ms = int(round(total_seconds * 1000.0)) % 86_400_000
    h, rem = divmod(total_ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    sec = rem / 1000.0
    return f"{h:02d}:{m:02d}:{sec:06.3f}"


def shift_utc(hms, delta_h):
    """Décale une heure UTC sans perdre les fractions de seconde."""
    value = _parse_hms_seconds(hms)
    return None if value is None else _fmt_hms_ms(value + delta_h * 3600.0)


def generate_json(res, lat, lon, alt, tz_offset, eclipse_key, output="todayeclipse.json"):
    label  = ECLIPSES[eclipse_key]["label"]
    tstart = shift_utc(res["C1_utc"], -1.0)
    tend   = shift_utc(res["C4_utc"], +1.0)
    tz_str = f"UTC{tz_offset:+g}"

    def hms(v):
        """Normalise en HH:MM:SS.mmm sans tronquer la précision temporelle."""
        if not v: return "00:00:00.000"
        return _fmt_hms_ms(_parse_hms_seconds(v))

    # Type global de l'éclipse (dans la bande de totalité) — extrait du label
    import re as _re
    m = _re.search(r"(Totale|Annulaire|Partielle|Hybride)", label, _re.IGNORECASE)
    type_global = m.group(1).capitalize() if m else "Totale"

    date_str = eclipse_key

    cfg = {
        "_comment":              "Calculé par eclipse_calculator_jubier.py — Algorithme JS Xavier Jubier",
        "_eclipse":              label,
        "_type_global":          type_global,           # type dans la bande (ex: Totale)
        "_type":                 res["eclipse_type"],   # type à la position GPS (ex: Partielle)
        "_magnitude":            res["magnitude"],
        "_moon_sun_ratio":       res["moon_sun_ratio"],
        "_duration":             res["duration_str"],
        "_sun_alt_tmax":         res["sun_alt_tmax"],
        "_generated_utc":        datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "_date":                 date_str,
        "_date_utc":             date_str,  # alias compatibilité v7.0
        "_circumstances_location": {
            "latitude": float(lat),
            "longitude": float(lon),
            "altitude_m": float(alt),
            "comment": "Circonstances calculées pour cette position GPS et cette altitude.",
        },
        "_timezone":             tz_str,   # timezone à la date de l'éclipse (DST inclus)
        "title":                 label,
        "C1":                    hms(res["C1_utc"]),
        "C2":                    hms(res["C2_utc"]   or res["TMAX_utc"]),
        "C3":                    hms(res["C3_utc"]   or res["TMAX_utc"]),
        "C4":                    hms(res["C4_utc"]),
        "TMAX":                  hms(res["TMAX_utc"]),
        "TSTART":                hms(tstart),
        "TEND":                  hms(tend),
        # Heures locales (timezone DST incluse)
        "C1_local":              hms(res["C1_local"]),
        "C2_local":              hms(res["C2_local"]   or res["TMAX_local"]),
        "C3_local":              hms(res["C3_local"]   or res["TMAX_local"]),
        "C4_local":              hms(res["C4_local"]),
        "TMAX_local":            hms(res["TMAX_local"]),
        "interval_partial":         180,
        "interval_diamond_ring":    4,
        "duree_diamond_ring":       40,
        "shutterspeed_partial":     "1/500",
        "shutterspeed_diamondring": "1/500",
        "phase1a": {
            "interval_s":   180,
            "speed_denom":  500,
        },
        "diamond_ring": {
            "interval_s":   4,
            "duration_s":   40,
            "speed_denom":  500,
        },
        "phase3b": {
            "interval_s":   180,
            "speed_denom":  500,
        },
    }
    with open(output, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=4, ensure_ascii=False)
    return cfg


# ══════════════════════════════════════════════════════════════════════════════
# 6. AFFICHAGE
# ══════════════════════════════════════════════════════════════════════════════

def print_results(res, lat, lon, alt, tz_offset, eclipse_key):
    label  = ECLIPSES[eclipse_key]["label"]
    tz_str = f"UTC{tz_offset:+.0f}"
    dur    = res.get("duration_str", "?")
    print(f"\n{CY}╔══════════════════════════════════════════════════════════════╗")
    print(f"║  {label[:60]:<60}║")
    print(f"╠══════════════════════════════════════════════════════════════╣")
    print(f"║  Lat {lat:+.5f}°  Lon {lon:+.5f}°  Alt {alt}m{'':<17}║")
    print(f"║  Type      : {res['eclipse_type']:<48}║")
    print(f"║  Magnitude : {res['magnitude']:<48}║")
    print(f"║  Totalité  : {dur:<48}║")
    print(f"║  Soleil    : {res['sun_alt_tmax']:<5} altitude à TMAX{'':<34}║")
    print(f"╠══════════════════════════════════════════════════════════════╣")
    print(f"║  {'Contact':<20}  {'Local (' + tz_str + ')':>10}   {'UTC':>10}{'':<14}║")
    print(f"║  {'─'*58}║{RE}")

    rows = [
        ("C1  (1er contact)",  res["C1_local"],   res["C1_utc"],   G),
        ("C2  (2e  contact)",  res["C2_local"],   res["C2_utc"],   PK),
        ("TMAX (maximum)",     res["TMAX_local"], res["TMAX_utc"], OR),
        ("C3  (3e  contact)",  res["C3_local"],   res["C3_utc"],   PK),
        ("C4  (4e  contact)",  res["C4_local"],   res["C4_utc"],   G),
    ]
    for lbl, loc, utc, col in rows:
        if utc is None:
            continue
        print(f"{CY}║  {col}{lbl:<20}  {(loc or 'n/a'):>10}   {(utc or 'n/a'):>10}{CY}{'':<14}║{RE}")
    print(f"{CY}╚══════════════════════════════════════════════════════════════╝{RE}")


# ══════════════════════════════════════════════════════════════════════════════
# 7. MAIN
# ══════════════════════════════════════════════════════════════════════════════

def auto_eclipse():
    """Retourne la prochaine éclipse totale disponible."""
    today = datetime.now(timezone.utc).date()
    future = [k for k in ECLIPSES
              if datetime.strptime(k, "%Y-%m-%d").date() >= today]
    return min(future) if future else list(ECLIPSES.keys())[0]


def main():
    ap = argparse.ArgumentParser(
        description="Calcul circonstances éclipse — JS Jubier via Playwright/Chromium headless",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=(
            "Exemples :\n"
            "  # Louxor 2027 (UTC+2)\n"
            "  python3 eclipse_calculator_jubier.py --lat 25.6872 --lon 32.6396 --alt 80 --tz 2 --eclipse 2027-08-02\n\n"
            "  # Madrid 2026 (CEST = UTC+2)\n"
            "  python3 eclipse_calculator_jubier.py --lat 40.4168 --lon -3.7038 --alt 650 --tz 2 --eclipse 2026-08-12\n"
        )
    )
    ap.add_argument("--lat",     type=float, help="Latitude décimale (+ Nord, - Sud)")
    ap.add_argument("--lon",     type=float, help="Longitude décimale (+ Est, - Ouest)")
    ap.add_argument("--alt",     type=float, default=0,    help="Altitude en mètres (défaut: 0)")
    ap.add_argument("--tz",      type=float, default=0,    help="Offset UTC total en heures ex: 2 pour UTC+2 (inclure DST si applicable)")
    ap.add_argument("--eclipse", type=str,   default=None, help="Clé éclipse : 2026-08-12 ou 2027-08-02 etc.")
    ap.add_argument("--output",  type=str,   default="todayeclipse.json")
    ap.add_argument("--list",    action="store_true", help="Lister les éclipses disponibles")
    ap.add_argument("--no-json", action="store_true", help="Afficher seulement, sans générer le JSON")
    args = ap.parse_args()

    print("╔══════════════════════════════════════════════════════════════╗")
    print("║  Solar Eclipse Calculator — JS Xavier Jubier + Playwright   ║")
    print("║  Les calculs sont effectués par le JS original de Jubier    ║")
    print("╚══════════════════════════════════════════════════════════════╝")

    if args.list:
        next_e = auto_eclipse()
        print(f"{G}Éclipses disponibles :{RE}")
        for k, v in ECLIPSES.items():
            mark = f"  {Y}◄ prochaine{RE}" if k == next_e else ""
            print(f"  {Y}{k}{RE}  {v['label']}{mark}")
        sys.exit(0)

    if args.lat is None or args.lon is None:
        ap.print_help()
        print(f"\n{R}Erreur : --lat et --lon sont obligatoires.{RE}")
        sys.exit(1)

    eclipse_key = args.eclipse or auto_eclipse()
    if eclipse_key not in ECLIPSES:
        print(f"{R}Éclipse inconnue : '{eclipse_key}'{RE}")
        print(f"Disponibles : {', '.join(ECLIPSES.keys())}")
        sys.exit(1)

    print(f"{B}Éclipse  : {Y}{eclipse_key}{RE} — {ECLIPSES[eclipse_key]['label']}")
    print(f"{B}Position : Lat {args.lat:+.5f}°  Lon {args.lon:+.5f}°  Alt {args.alt}m{RE}")
    print(f"{B}Fuseau   : UTC{args.tz:+.1f}{RE}\n")

    print(f"{B}[1/3] Vérification des fichiers Jubier...{RE}")
    setup_files()

    print(f"{B}[2/3] Démarrage du serveur Flask...{RE}")
    start_flask()

    print(f"{B}[3/3] Calcul via Chromium headless + JS Jubier...{RE}")
    result = run_playwright(
        lat          = args.lat,
        lon          = args.lon,
        alt          = args.alt,
        tz_offset    = args.tz,
        eclipse_val  = ECLIPSES[eclipse_key]["val"],
    )

    if not result or "error" in result:
        msg = result.get("error", "Résultat vide") if result else "Pas de résultat"
        print(f"{R}❌ Erreur : {msg}{RE}")
        sys.exit(1)

    print_results(result, args.lat, args.lon, args.alt, args.tz, eclipse_key)

    if not args.no_json:
        generate_json(result, args.lat, args.lon, args.alt, args.tz, eclipse_key, args.output)
        print(f"\n{G}✅ Fichier généré : {Y}{args.output}{RE}")
        print(f"\n{B}Lancer le trigger :{RE}")
        print(f"   python3 Total_Solar_Eclipse_Trigger_script_v3_8_2_pi_only.py --file {args.output}")


if __name__ == "__main__":
    main()
