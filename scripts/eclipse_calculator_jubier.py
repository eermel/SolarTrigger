#!/usr/bin/env python3
"""
Eclipse calculator via original Xavier Jubier JS under headless Chromium.
This script prepares todayeclipse.json consumed by the trigger.
"""
import argparse
import json
import shutil
import sys
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
JUBIER_DIR  = SCRIPT_DIR.parent / "jubier_files"

REQUIRED_FILES = [
    "SunMoonCalculatorSVG_VML.js",
]

ECLIPSES = {
    "2026-08-12": {"label": "2026 Aug 12 — Totale (Espagne/Méditerranée)", "val": "59"},
    "2027-08-02": {"label": "2027 Aug 02 — Totale (Égypte/Louxor)",        "val": "61"},
    "2028-07-22": {"label": "2028 Jul 22 — Totale",                         "val": "63"},
    "2030-11-25": {"label": "2030 Nov 25 — Totale",                         "val": "69"},
    "2034-03-20": {"label": "2034 Mar 20 — Totale",                         "val": "76"},
    "2035-09-02": {"label": "2035 Sep 02 — Totale",                         "val": "79"},
}


def setup_files():
    JUBIER_DIR.mkdir(exist_ok=True)
    search_dirs = [
        SCRIPT_DIR,
        SCRIPT_DIR.parent,
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


JS_CALCULATE = """(params) => {
    window.alert  = (msg) => { console.warn('alert blocked:', msg); };
    window.confirm = () => true;
    window.prompt  = () => '';

    const { lat_dd, lon_dd, alt_m, tz_offset, eclipse_val } = params;

    if (typeof getall === 'undefined' || typeof obsvconst === 'undefined') {
        return { error: 'Fonctions Jubier non disponibles (getall, obsvconst)' };
    }

    const D2R = Math.PI / 180.0;

    obsvconst[0] = lat_dd * D2R;
    obsvconst[1] = -lon_dd * D2R;
    obsvconst[2] = alt_m;
    obsvconst[3] = -tz_offset;

    const tmp = Math.atan(0.996647189335 * Math.tan(obsvconst[0]));
    obsvconst[4] = (0.996647189335 * Math.sin(tmp))
                 + (obsvconst[2] * Math.sin(obsvconst[0]) / 6378137.0);
    obsvconst[5] = Math.cos(tmp)
                 + (obsvconst[2] * Math.cos(obsvconst[0]) / 6378137.0);

    obsvconst[6] = 28 * (parseInt(eclipse_val, 10) + 65);

    try { getall(); } catch(e) { return { error: 'getall() exception: ' + e.toString() }; }

    const idx = obsvconst[6];
    const t0  = elements[1 + idx];
    const dT  = elements[4 + idx];

    function formatHMSms(hours) {
        let totalMs = Math.round((((hours % 24) + 24) % 24) * 3600000.0);
        totalMs = ((totalMs % 86400000) + 86400000) % 86400000;
        const h = Math.floor(totalMs / 3600000); totalMs -= h * 3600000;
        const m = Math.floor(totalMs / 60000);   totalMs -= m * 60000;
        const sec = totalMs / 1000.0;
        const ss = sec.toFixed(3).padStart(6, '0');
        return String(h).padStart(2,'0')+':'+String(m).padStart(2,'0')+':'+ss;
    }

    function tToUTC(t) { return formatHMSms(t + t0 - (dT / 3600.0)); }
    function tToLocal(t) { let u = t + t0 - (dT / 3600.0) - obsvconst[3]; return formatHMSms(u); }

    const typeMap = {0:'Aucune', 1:'Partielle', 2:'Annulaire', 3:'Totale'};
    const eType = typeMap[mid[39]] || 'Inconnue';

    let durSec = 0; if (mid[39] >= 2) { durSec = Math.abs(c3[1] - c2[1]) * 3600.0; }
    const durStr = Math.floor(durSec / 60) + 'm ' + Math.round(durSec % 60) + 's';

    const mag   = Math.round(mid[37] * 100000) / 100000;
    const ratio = Math.round(mid[38] * 100000) / 100000;

    const sunAlt = (typeof mid[45] !== 'undefined')
                 ? (mid[45] * 180 / Math.PI).toFixed(1) + '°'
                 : 'n/a';

    // Altitudes geometriques en radians -> degres, depuis [32]
    function rad2deg(x) { return x * 180.0 / Math.PI; }
    const C1_alt_deg   = (typeof c1[32]  !== 'undefined') ? rad2deg(c1[32])  : null;
    const C2_alt_deg   = (typeof c2[32]  !== 'undefined') ? rad2deg(c2[32])  : null;
    const TMAX_alt_deg = (typeof mid[32] !== 'undefined') ? rad2deg(mid[32]) : null;
    const C3_alt_deg   = (typeof c3[32]  !== 'undefined') ? rad2deg(c3[32])  : null;
    const C4_alt_deg   = (typeof c4[32]  !== 'undefined') ? rad2deg(c4[32])  : null;

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
        C1_alt_deg, C2_alt_deg, TMAX_alt_deg, C3_alt_deg, C4_alt_deg,
    };
}"""


def run_playwright(lat, lon, alt, tz_offset, eclipse_val):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(f"{R}Playwright manquant :{RE}")
        print(f"  pip3 install playwright --break-system-packages")
        print(f"  playwright install chromium")
        sys.exit(1)

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

        print(f"{B}  Chargement du calculateur JS Jubier...{RE}")
        try:
            page.goto("about:blank")
            page.add_script_tag(
                path=str(JUBIER_DIR / "SunMoonCalculatorSVG_VML.js")
            )
        except Exception as e:
            print(f"{R}  Erreur de chargement du calculateur JS : {e}{RE}")
            browser.close()
            sys.exit(1)

        try:
            page.wait_for_function("typeof getall !== 'undefined'", timeout=10000)
        except Exception:
            print(f"{R}  Les fonctions JS Jubier ne se sont pas chargées.{RE}")
            print(f"{Y}  Vérifiez que SunMoonCalculatorSVG_VML.js est dans {JUBIER_DIR}{RE}")
            browser.close()
            sys.exit(1)

        print(f"{B}  Exécution de l'algorithme Jubier...{RE}")
        result = page.evaluate(JS_CALCULATE, params)
        browser.close()

    return result


def _parse_hms_seconds(value):
    if not value:
        return None
    h, m, sec = str(value).split(":")
    return int(h) * 3600.0 + int(m) * 60.0 + float(sec)


def _fmt_hms_ms(total_seconds):
    total_seconds = total_seconds % 86400.0
    total_ms = int(round(total_seconds * 1000.0)) % 86_400_000
    h, rem = divmod(total_ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    sec = rem / 1000.0
    return f"{h:02d}:{m:02d}:{sec:06.3f}"


def shift_utc(hms, delta_h):
    value = _parse_hms_seconds(hms)
    return None if value is None else _fmt_hms_ms(value + delta_h * 3600.0)


def generate_json(res, lat, lon, alt, tz_offset, eclipse_key, output="todayeclipse.json"):
    label  = ECLIPSES[eclipse_key]["label"]
    tstart = shift_utc(res["C1_utc"], -1.0)
    tend   = shift_utc(res["C4_utc"], +1.0)
    tz_str = f"UTC{tz_offset:+g}"

    def hms(v):
        if not v:
            return None
        return _fmt_hms_ms(_parse_hms_seconds(v))

    def altitude(v):
        if v is None:
            return None
        return float(v)

    import re as _re
    m = _re.search(r"(Totale|Annulaire|Partielle|Hybride)", label, _re.IGNORECASE)
    type_global = m.group(1).capitalize() if m else "Totale"

    date_str = eclipse_key

    cfg = {
        "_comment":              "Calculé par eclipse_calculator_jubier.py — Algorithme JS Xavier Jubier",
        "_eclipse":              label,
        "_type_global":          type_global,
        "_type":                 res["eclipse_type"],
        "_magnitude":            res["magnitude"],
        "_moon_sun_ratio":       res["moon_sun_ratio"],
        "_duration":             res["duration_str"],
        "_sun_alt_tmax":         res["sun_alt_tmax"],
        "_generated_utc":        datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "_date":                 date_str,
        "_date_utc":             date_str,
        "_circumstances_location": {
            "latitude": float(lat),
            "longitude": float(lon),
            "altitude_m": float(alt),
            "comment": "Circonstances calculées pour cette position GPS et cette altitude.",
        },
        "_timezone":             tz_str,
        "title":                 label,
        "C1":                    hms(res["C1_utc"]),
        "C2":                    hms(res.get("C2_utc")),
        "C3":                    hms(res.get("C3_utc")),
        "C4":                    hms(res["C4_utc"]),
        "TMAX":                  hms(res["TMAX_utc"]),
        "TSTART":                hms(tstart),
        "TEND":                  hms(tend),
        # Heures locales
        "C1_local":              hms(res["C1_local"]),
        "C2_local":              hms(res.get("C2_local")),
        "C3_local":              hms(res.get("C3_local")),
        "C4_local":              hms(res["C4_local"]),
        "TMAX_local":            hms(res["TMAX_local"]),
        # Altitudes geometriques issues de c1[32], c2[32], mid[32], c3[32], c4[32]
        "C1_alt_deg":            altitude(res.get("C1_alt_deg")),
        "C2_alt_deg":            altitude(res.get("C2_alt_deg")),
        "TMAX_alt_deg":          altitude(res.get("TMAX_alt_deg")),
        "C3_alt_deg":            altitude(res.get("C3_alt_deg")),
        "C4_alt_deg":            altitude(res.get("C4_alt_deg")),
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
    print(f"╠══════════════════════════════════════════════════════════════╣{RE}")


def auto_eclipse():
    today = datetime.now(timezone.utc).date()
    future = [k for k in ECLIPSES
              if datetime.strptime(k, "%Y-%m-%d").date() >= today]
    return min(future) if future else list(ECLIPSES.keys())[0]


def main():
    ap = argparse.ArgumentParser(
        description="Calcul circonstances éclipse — JS Jubier via Playwright/Chromium headless",
        formatter_class=argparse.RawTextHelpFormatter,
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

    print(f"{B}[1/2] Vérification du calculateur JS Jubier...{RE}")
    setup_files()

    print(f"{B}[2/2] Calcul via Chromium headless + JS Jubier...{RE}")
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

    if not args.no_json:
        generate_json(result, args.lat, args.lon, args.alt, args.tz, eclipse_key, args.output)
        print(f"\n{G}✅ Fichier généré : {Y}{args.output}{RE}")


if __name__ == "__main__":
    main()
