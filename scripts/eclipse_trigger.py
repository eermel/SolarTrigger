#!/usr/bin/env python3
"""
Total Solar Eclipse Automatic Trigger
Version : 6.4.0
Date    : 2026-08-19

Changelog :
  3.9.27 - (app 3.0.73) bouton 🗑 supprime debug_*.json
  3.9.26 - Log : titre en 3 print PINK séparés (lisibles par Flask)
  3.9.25 - Log : titre en logging.info
  3.9.24 - Log : suppression heure dans messages photo
  3.9.23 - Log : suppression horodatage redondant (garde uniquement [ ] Flask)
  3.9.22 - Countdown : 5 alertes individuelles (5.wav..1.wav) au lieu de jouer_sequence
  3.9.21 - Fix : pkill au lieu de killall (PATH venv), camera=None avant unmount
  3.9.20 - get_battery_level : warning visible si champ absent

"""
# ─────────────────────────────────────────────────────────────────────────────
# Early --check handling: MUST run before any side-effectful import.
# Uses only stdlib and backend.timeline utilities.
# ─────────────────────────────────────────────────────────────────────────────
import sys as _early_sys
import os as _early_os
import json as _early_json

def _early_find_arg(argv, name):
    try:
        idx = argv.index(name)
    except ValueError:
        return None
    # Return next token if present and not another flag
    if idx + 1 < len(argv) and not argv[idx + 1].startswith("--"):
        return argv[idx + 1]
    return None

if "--check" in _early_sys.argv:
    errors = []

    file_path = _early_find_arg(_early_sys.argv, "--file")
    if not file_path:
        errors.append("Missing --file")
    else:
        # Ensure repository root on sys.path when invoked as a script
        _repo_root = _early_os.path.dirname(_early_os.path.dirname(_early_os.path.abspath(__file__)))
        if _repo_root not in _early_sys.path:
            _early_sys.path.insert(0, _repo_root)
        from backend.check_validation import validate_circumstances

        if _early_os.path.isfile(file_path):
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    cfg = _early_json.load(f)
            except Exception as exc:
                errors.append(f"JSON error: {exc}")
                cfg = None
        else:
            errors.append(f"File not found: {file_path}")
            cfg = None

        if cfg is not None:
            errors = validate_circumstances(cfg)

    if errors:
        for e in errors:
            print(e)
        _early_sys.exit(1)
    else:
        print("CHECK OK")
        _early_sys.exit(0)

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
import threading

_print_lock = threading.Lock()

def _log(msg):
    """Print thread-safe avec flush — remplace tous les print directs."""
    with _print_lock:
        print(msg, flush=True)

import gphoto2 as gp

# Supprimer les logs gphoto2 C-level qui écrivent directement sur stderr/stdout
# et peuvent entremêler les lignes avec le logging Python
gp.check_result(gp.use_python_logging(mapping={
    gp.GP_LOG_ERROR:   logging.WARNING,
    gp.GP_LOG_VERBOSE: logging.DEBUG,
    gp.GP_LOG_DEBUG:   logging.DEBUG,
    gp.GP_LOG_DATA:    logging.DEBUG,
}))
from fractions import Fraction
import subprocess
import math

# Configuration audio - méthode C par défaut pour sortie jack
SOUND_METHOD_DEFAULT = "C"  # C: Jack, A: Events, B: SocketIO

# Configuration du logging — DOIT être avant tout appel à logging.*
logging.basicConfig(
    level=logging.INFO,
    format='%(levelname)s %(message)s',
    datefmt='%H:%M:%S',
    stream=sys.stdout,   # stdout pour que Flask capture via stderr=subprocess.STDOUT
    force=True           # écrase tout handler précédent même si déjà initialisé
)

# ══════════════════════════════════════════════════════════════════════════════
# RUNTIME — horloge virtuelle + watchdog externalisés (backend v6)
# ══════════════════════════════════════════════════════════════════════════════
from pathlib import Path
from backend.trigger_runtime import RuntimeClock, TriggerWatchdog
from backend.timeline import build_timeline, rebase_timeline, format_hms_ms
from services.camera_service import CameraService
from services.camera_service import _normalized_speed_plan as _norm_plan
from backend.atmo import facteur_atmospherique, interpolate_altitude
from backend import audio_service

_runtime_clock = RuntimeClock()
_watchdog = TriggerWatchdog(Path.home() / "python_solareclipsetrigger" / "trigger_state.json", _runtime_clock)

def now(): return _runtime_clock.now()
def sleep_sim(seconds): return _runtime_clock.sleep(seconds)
def _watchdog_write(phase, next_shot_time=None): return _watchdog.write(phase, next_shot_time)
def _watchdog_read(): return _watchdog.read()
def _watchdog_clear(): return _watchdog.clear()

# Couleurs ANSI
class Colors:
    RED = "\033[1;31m"
    GREEN = "\033[1;32m"
    YELLOW = "\033[1;33m"
    JAUNE = "\033[1;33m"   # alias FR
    BLUE = "\033[1;34m"
    BLEU = "\033[1;34m"    # alias FR
    PINK = "\033[38;5;198m"
    ORANGE = "\033[38;2;255;127;0m"
    CYAN = "\033[1;36m"   # Ajouter cette ligne
    RESET = "\033[0m"

audio_service.init(log_fn=_log, colors=Colors, driver='alsa')

import json
import os

# Valeurs par défaut (utilisées si aucun fichier JSON ni argument CLI)
DEFAULTS = {
    "title":                    "12 AOUT 2026 - MADRID SUD",
    "C1":                       "19:36:01",
    "C2":                       "20:29:47",
    "C3":                       "20:31:17",
    "C4":                       "21:21:48",
    "TMAX":                     "20:30:32",
    "TSTART":                   "18:36:01",
    "TEND":                     "22:21:48",
    "interval_partial":         180,
    "interval_diamond_ring":    4,
    "duree_diamond_ring":       40,
    "shutterspeed_partial":     "1/500",
    "shutterspeed_diamondring": "1/500",
    "wake_up_time":             2.5,    # secondes — réveil anticipé caméra avant chaque photo (phases 1a/3b)
                                        # À calibrer avec measure_camera_wakeup.py --full
}

def load_config_file(filepath):
    """Charge un fichier JSON de configuration et retourne un dictionnaire de paramètres."""
    if not os.path.isfile(filepath):
        _log(f"{Colors.RED}Fichier de configuration introuvable : {filepath}{Colors.RESET}")
        raise SystemExit(1)
    with open(filepath, "r", encoding="utf-8") as f:
        try:
            config = json.load(f)
            _log(f"{Colors.GREEN}Configuration chargée depuis : {filepath}{Colors.RESET}")
            return config
        except json.JSONDecodeError as e:
            _log(f"{Colors.RED}Erreur de parsing JSON dans {filepath} : {e}{Colors.RESET}")
            raise SystemExit(1)

def parse_arguments():
    """Parse command-line arguments.
    Priorité : --file (JSON) < arguments CLI individuels < --interact
    """
    parser = argparse.ArgumentParser(
        description="Total Solar Eclipse Automatic Script",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog="Exemple : python3 monscript.py --file espagne.json\n"
               "          python3 monscript.py --file espagne.json --debug"
    )
    parser.add_argument("--file",   type=str, default=None, help="Fichier JSON circonstances éclipse")
    parser.add_argument("--camera", type=str, default=None, help="Fichier JSON configuration appareil photo")
    parser.add_argument("--interact",  action="store_true",  help="Enable interact mode")
    parser.add_argument("--debug",     action="store_true",  help="Enable debug mode (contacts dans 15s)")
    parser.add_argument("--simulate",  action="store_true",  help="Mode simulation accélérée sans déclenchement matériel")
    parser.add_argument("--speed",     type=float, default=60.0, help="Facteur d'accélération simulation (défaut: 60)")
    parser.add_argument("--dry-run",   action="store_true",  help="Dry-run réel : même moteur/caméra, timeline translatée sur maintenant")
    parser.add_argument("--dry-run-delay", type=float, default=30.0, help="Délai avant TSTART du dry-run, en secondes (défaut: 30)")
    # Arguments optionnels — surchargent le fichier JSON si fournis
    parser.add_argument("--title",                   type=str, default=None)
    parser.add_argument("--C1",                      type=str, default=None)
    parser.add_argument("--C2",                      type=str, default=None)
    parser.add_argument("--C3",                      type=str, default=None)
    parser.add_argument("--C4",                      type=str, default=None)
    parser.add_argument("--TMAX",                    type=str, default=None)
    parser.add_argument("--TSTART",                  type=str, default=None)
    parser.add_argument("--TEND",                    type=str, default=None)
    parser.add_argument("--interval_partial",        type=int, default=None)
    parser.add_argument("--interval_diamond_ring",   type=int, default=None)
    parser.add_argument("--duree_diamond_ring",      type=int, default=None)
    parser.add_argument("--shutterspeed_partial",    type=str, default=None)
    parser.add_argument("--shutterspeed_diamondring",type=str, default=None)
    return parser.parse_args()

args = parse_arguments()

# --- Résolution des paramètres ---
# Priorité v6.4 : DEFAULTS < profil caméra < fichier de séquence/éclipse < CLI.
# Le profil caméra ne possède PLUS les timings de phase : il fournit seulement
# des réglages d'exposition par défaut. Un todayeclipse.json de debug/test peut
# donc toujours surcharger les vitesses et les timings sans être écrasé par un
# ancien profil D850.
cfg = dict(DEFAULTS)

def _apply_camera_profile(target, cam_cfg):
    p = cam_cfg.get("partial", {})
    dr = cam_cfg.get("diamond_ring", {})
    t = cam_cfg.get("totality", {})
    if p.get("speeds"):
        target["speeds_partial"] = p["speeds"]
    if p.get("aperture"):
        target["aperture_partial"] = p["aperture"]
    if p.get("iso") is not None:
        target["iso_partial"] = str(p["iso"])
    if p.get("step_il") is not None:
        target["step_partial"] = float(p["step_il"])
    if dr.get("speeds"):
        target["speeds_diamond_ring"] = dr["speeds"]
    if dr.get("aperture"):
        target["aperture_diamond_ring"] = dr["aperture"]
    if dr.get("iso") is not None:
        target["iso_diamond_ring"] = str(dr["iso"])
    if dr.get("step_il") is not None:
        target["step_diamond_ring"] = float(dr["step_il"])
    if t.get("speeds"):
        target["totality"] = {"speeds": t["speeds"]}
    if t.get("aperture"):
        target["aperture_totality"] = t["aperture"]
    if t.get("iso") is not None:
        target["iso_totality"] = str(t["iso"])
    if t.get("step_il") is not None:
        target["step_totality"] = float(t["step_il"])

def _apply_eclipse_file(target, ecl):
    # Top-level keys are authoritative. Nested UI structures are only fallbacks.
    target.update(ecl)
    if "interval_partial" not in ecl:
        ph = ecl.get("phase1a", {})
        if ph.get("interval_s") is not None:
            target["interval_partial"] = ph["interval_s"]
    if "interval_diamond_ring" not in ecl:
        dr = ecl.get("diamond_ring", {})
        if dr.get("interval_s") is not None:
            target["interval_diamond_ring"] = dr["interval_s"]
    if "duree_diamond_ring" not in ecl:
        dr = ecl.get("diamond_ring", {})
        if dr.get("duration_s") is not None:
            target["duree_diamond_ring"] = dr["duration_s"]

    # Une vitesse explicitement fournie par la séquence remplace le profil caméra.
    if "shutterspeed_partial" in ecl and not ecl.get("partial", {}).get("speeds"):
        target["speeds_partial"] = [ecl["shutterspeed_partial"]]
    if "shutterspeed_diamondring" in ecl and not ecl.get("diamond_ring", {}).get("speeds"):
        target["speeds_diamond_ring"] = [ecl["shutterspeed_diamondring"]]

if args.camera:
    _apply_camera_profile(cfg, load_config_file(args.camera))

if args.file:
    _apply_eclipse_file(cfg, load_config_file(args.file))

# Arguments CLI individuels : priorité maximale.
cli_overrides = {
    "title":                    args.title,
    "C1":                       args.C1,
    "C2":                       args.C2,
    "C3":                       args.C3,
    "C4":                       args.C4,
    "TMAX":                     args.TMAX,
    "TSTART":                   args.TSTART,
    "TEND":                     args.TEND,
    "interval_partial":         args.interval_partial,
    "interval_diamond_ring":    args.interval_diamond_ring,
    "duree_diamond_ring":       args.duree_diamond_ring,
    "shutterspeed_partial":     args.shutterspeed_partial,
    "shutterspeed_diamondring": args.shutterspeed_diamondring,
}
cfg.update({k: v for k, v in cli_overrides.items() if v is not None})
if args.shutterspeed_partial is not None:
    cfg["speeds_partial"] = [args.shutterspeed_partial]
if args.shutterspeed_diamondring is not None:
    cfg["speeds_diamond_ring"] = [args.shutterspeed_diamondring]

# Alertes sonores — fichiers WAV dans le sous-dossier Sounds/ (relatif au script)
_SOUNDS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "Sounds")
audio_service.set_sounds_dir(_SOUNDS_DIR)

# Variables globales
debug    = args.debug
interact = args.interact

# ── Mode simulation ────────────────────────────────────────────────────────
_runtime_clock.configure(args.simulate, args.speed)
_sim_mode = _runtime_clock.sim_mode
_sim_speed = _runtime_clock.speed
if _sim_mode:
    _log(f"⚡ MODE SIMULATION ×{_sim_speed:.0f} activé")

titre                    = cfg["title"]
C1_str                   = cfg["C1"]
C2_str                   = cfg["C2"]
C3_str                   = cfg["C3"]
C4_str                   = cfg["C4"]
TMAX_str                 = cfg["TMAX"]
TSTART_str               = cfg["TSTART"]
TEND_str                 = cfg["TEND"]
interval_partial         = int(cfg["interval_partial"])
interval_diamond_ring    = int(cfg["interval_diamond_ring"])
duree_diamond_ring       = int(cfg["duree_diamond_ring"])
shutterspeed_partial     = cfg["shutterspeed_partial"]
shutterspeed_diamondring = cfg["shutterspeed_diamondring"]
wake_up_time             = float(cfg.get("wake_up_time", 2.5))  # secondes

# Bracket vitesses — depuis camera config ou fallback ancienne clé
speeds_partial      = cfg.get("speeds_partial",      [shutterspeed_partial])
speeds_diamond_ring = cfg.get("speeds_diamond_ring", [shutterspeed_diamondring])
aperture_partial    = cfg.get("aperture_partial",    "f/8")
aperture_diamond    = cfg.get("aperture_diamond_ring","f/8")
aperture_totality   = cfg.get("aperture_totality",   "f/8")
iso_partial         = cfg.get("iso_partial",         "100")
iso_diamond_ring    = cfg.get("iso_diamond_ring",    "100")
iso_totality        = cfg.get("iso_totality",        "100")

if interact:
    C1_str                   = input("C1 (H:M:S) ? ")
    C2_str                   = input("C2 (H:M:S) ? ")
    C3_str                   = input("C3 (H:M:S) ? ")
    C4_str                   = input("C4 (H:M:S) ? ")
    TMAX_str                 = input("TMAX (H:M:S) ? ")
    TSTART_str               = input("TSTART (H:M:S) ? ")
    TEND_str                 = input("TEND (H:M:S) ? ")
    interval_partial         = int(input("Interval Partiality in s ? [180] ") or "180")
    interval_diamond_ring    = int(input("Interval Diamond Ring in s ? [4] ") or "4")
    duree_diamond_ring       = int(input("Duration of Diamond Ring in s ? [40] ") or "40")
    shutterspeed_partial     = input("Shutter speed for Partiality phase ? [1/500] ") or "1/500"
    shutterspeed_diamondring = input("Shutter speed for Diamond ring phase ? [1/500] ") or "1/500"

# Conversion des heures / timeline -------------------------------------------------
# v7.1 : les circonstances restent `_date` + heures UTC indépendantes.
# Le dry-run translate la timeline entière sans modifier un seul intervalle.
_timeline_cfg = dict(cfg)
_timeline_cfg.update({
    "C1": C1_str, "C2": C2_str, "C3": C3_str, "C4": C4_str,
    "TMAX": TMAX_str, "TSTART": TSTART_str, "TEND": TEND_str,
})
_timeline = build_timeline(_timeline_cfg, fallback_date=now().date())
if args.dry_run:
    _timeline = rebase_timeline(_timeline, now() + timedelta(seconds=float(args.dry_run_delay)))
    _log(f"🧪 DRY-RUN ×1 — timeline translatée, matériel réel, TSTART dans {args.dry_run_delay:g}s")

TSTART = _timeline["TSTART"]
C1 = _timeline["C1"]
C2 = _timeline["C2"]
TMAX = _timeline["TMAX"]
C3 = _timeline["C3"]
C4 = _timeline["C4"]
TEND = _timeline["TEND"]

## Détecter si l'éclipse est partielle
is_partial = False
if cfg.get("_type") and "partielle" in cfg.get("_type", "").lower():
    is_partial = True
elif not cfg.get("C2") or not cfg.get("C3"):
    # Si C2 ou C3 sont vides ou nuls, c'est une éclipse partielle
    is_partial = True

_log(f"{Colors.CYAN}Type d'éclipse : {'Partielle' if is_partial else 'Totale'}{Colors.RESET}")

# Alertes textuelles (adaptées selon le type d'éclipse)
messages_temps = [
    (C1 - timedelta(minutes=10), "C1 - PARTIALITY START T-10min"),
    (C1 - timedelta(minutes=5), "C1 - PARTIALITY START T-5min"),
    (C1 - timedelta(minutes=1), "C1 - PARTIALITY START T-1min"),
    (C1, "C1 - PARTIALITY START"),
]

# Ajouter les alertes de totalité seulement si ce n'est pas partiel
if not is_partial:
    messages_temps.extend([
        (C2 - timedelta(minutes=10), "C2 - TOTALITY START T-10min"),
        (C2 - timedelta(minutes=5), "C2 - TOTALITY START T-5min"),
        (C2 - timedelta(minutes=2), "C2 - TOTALITY START T-2min"),
        (C2 - timedelta(minutes=1), "C2 - TOTALITY START T-1min"),
        (C2, "C2 - TOTALITY START"),
        (C3 - timedelta(minutes=1), "C3 - TOTALITY END T-1min"),
        (C3, "C3 - TOTALITY END"),
    ])

messages_temps.extend([
    (C4 - timedelta(minutes=10), "C4 - PARTIALITY END T-10min"),
    (C4 - timedelta(minutes=5),  "C4 - PARTIALITY END T-5min"),
    (C4 - timedelta(minutes=2),  "C4 - PARTIALITY END T-2min"),
    (C4 - timedelta(minutes=1),  "C4 - PARTIALITY END T-1min"),
    (C4, "C4 - PARTIALITY END")
])

# Alertes sonores (adaptées selon le type d'éclipse)
def _alerte(t, son, t_min=None, t_max=None):
    """
    Retourne (t, son) seulement si t est dans [t_min, t_max] et dans le futur.
    t_min / t_max définissent la fenêtre valide de la phase.
    Marge minimale de 5s avant t_max pour éviter les alertes trop tardives.
    """
    if t_min is not None and t < t_min:
        return None
    if t_max is not None and t >= t_max - timedelta(seconds=5):
        return None
    return (t, son)

def _build_alertes():
    """Construit la liste des alertes sonores en respectant les fenêtres de phase."""
    alertes = []

    def add(t, son, t_min=None, t_max=None):
        a = _alerte(t, son, t_min, t_max)
        if a:
            alertes.append(a)

    # ── Avant C1 (fenêtre : TSTART → C1) ─────────────────────────────────────
    add(TSTART - timedelta(seconds=30), "filters_on.wav",  t_min=TSTART - timedelta(minutes=1))
    add(C1 - timedelta(minutes=10),     "10minutes.wav",   t_min=TSTART,   t_max=C1)
    add(C1 - timedelta(minutes=5),      "5minutes.wav",    t_min=TSTART,   t_max=C1)
    add(C1 - timedelta(seconds=60),     "60seconds.wav",   t_min=TSTART,   t_max=C1)
    add(C1 - timedelta(seconds=5), "5.wav", t_min=TSTART)
    add(C1 - timedelta(seconds=4), "4.wav", t_min=TSTART)
    add(C1 - timedelta(seconds=3), "3.wav", t_min=TSTART)
    add(C1 - timedelta(seconds=2), "2.wav", t_min=TSTART)
    add(C1 - timedelta(seconds=1), "1.wav", t_min=TSTART)
    add(C1,                             "contact.wav",     t_min=TSTART)

    if not is_partial:
        totalite_s = (C3 - C2).total_seconds()

        # ── Avant C2 (fenêtre : C1 → C2) ─────────────────────────────────────
        add(C2 - timedelta(minutes=10),             "10minutes.wav",  t_min=C1, t_max=C2)
        add(C2 - timedelta(minutes=5),              "5minutes.wav",   t_min=C1, t_max=C2)
        add(C2 - timedelta(minutes=2),              "2minutes.wav",   t_min=C1, t_max=C2)
        add(C2 - timedelta(seconds=60),             "60seconds.wav",  t_min=C1, t_max=C2)
        add(C2 - timedelta(seconds=duree_diamond_ring), "filters_off.wav", t_min=C1, t_max=C2)
        add(C2 - timedelta(seconds=30),             "30seconds.wav",  t_min=C1, t_max=C2)
        add(C2 - timedelta(seconds=10),             "10seconds.wav",  t_min=C1, t_max=C2)
        add(C2 - timedelta(seconds=5), "5.wav", t_min=C1)
        add(C2 - timedelta(seconds=4), "4.wav", t_min=C1)
        add(C2 - timedelta(seconds=3), "3.wav", t_min=C1)
        add(C2 - timedelta(seconds=2), "2.wav", t_min=C1)
        add(C2 - timedelta(seconds=1), "1.wav", t_min=C1)
        add(C2,                                     "contact.wav",    t_min=C1)

        # ── Avant C3 (fenêtre : C2 → C3) — seulement si totalité assez longue ─
        add(C3 - timedelta(seconds=60),             "60seconds.wav",  t_min=C2, t_max=C3)
        add(C3 - timedelta(seconds=30),             "30seconds.wav",  t_min=C2, t_max=C3)
        add(C3 - timedelta(seconds=10),             "10seconds.wav",  t_min=C2, t_max=C3)
        add(C3 - timedelta(seconds=5), "5.wav", t_min=C2)
        add(C3 - timedelta(seconds=4), "4.wav", t_min=C2)
        add(C3 - timedelta(seconds=3), "3.wav", t_min=C2)
        add(C3 - timedelta(seconds=2), "2.wav", t_min=C2)
        add(C3 - timedelta(seconds=1), "1.wav", t_min=C2)
        add(C3,                                     "contact.wav",    t_min=C2)

        # ── Après C3 (diamond ring retour) ───────────────────────────────────
        add(C3 + timedelta(seconds=duree_diamond_ring), "filters_on.wav",
            t_min=C3, t_max=C4)

        _log(f"INFO {Colors.CYAN}Totalité : {totalite_s:.0f}s — alertes filtrées selon fenêtres de phase{Colors.RESET}")

    # ── Avant C4 (fenêtre : C3 → C4) ─────────────────────────────────────────
    add(C4 - timedelta(minutes=10), "10minutes.wav",  t_min=C3, t_max=C4)
    add(C4 - timedelta(minutes=5),  "5minutes.wav",   t_min=C3, t_max=C4)
    add(C4 - timedelta(seconds=60), "60seconds.wav",  t_min=C3, t_max=C4)
    add(C4 - timedelta(seconds=5), "5.wav", t_min=C3)
    add(C4 - timedelta(seconds=4), "4.wav", t_min=C3)
    add(C4 - timedelta(seconds=3), "3.wav", t_min=C3)
    add(C4 - timedelta(seconds=2), "2.wav", t_min=C3)
    add(C4 - timedelta(seconds=1), "1.wav", t_min=C3)
    add(C4,                         "contact.wav",    t_min=C3)

    # Trier par heure croissante et dédupliquer (même heure = même son)
    seen = set()
    result = []
    for t, son in sorted(alertes, key=lambda x: x[0]):
        key = (t.isoformat(timespec="milliseconds"), son)
        if key not in seen:
            seen.add(key)
            result.append((t, son))

    _log(f"INFO {Colors.CYAN}{len(result)} alertes sonores programmées (sur {len(alertes)} candidates){Colors.RESET}")
    return result

alertes_sons = _build_alertes()


if debug:
    TSTART = now() + timedelta(seconds=15)
    C1 = TSTART + timedelta(minutes=0.2)
    C2 = C1 + timedelta(minutes=1)
    C3 = C2 + timedelta(minutes=0.5)
    C4 = C3 + timedelta(minutes=1)
    TEND = C4 + timedelta(minutes=1)
    TMAX = C2 + timedelta(seconds=((C3-C2).total_seconds()/2))
    interval_partial = 17
    interval_diamond_ring = 4
    titre = "DEBUGGGGG SPAIN"


# Liste des vitesses d'obturation — lue depuis cfg["totality"]["speeds"] si présent,
# sinon liste complète par défaut.
_DEFAULT_SPEEDS = ["1/4000", "1/2000", "1/1000", "1/500", "1/250",
                   "1/125",  "1/60",   "1/30",   "1/15",  "1/8",
                   "1/4",    "1/2",    "1",      "2",     "4"]

if cfg.get("totality") and cfg["totality"].get("speeds"):
    shutter_speeds = cfg["totality"]["speeds"]
    _log(f"{Colors.CYAN}Vitesses totalité depuis JSON ({len(shutter_speeds)} vitesses){Colors.RESET}")
else:
    shutter_speeds = _DEFAULT_SPEEDS
    _log(f"{Colors.CYAN}Vitesses totalité : liste par défaut ({len(shutter_speeds)} vitesses){Colors.RESET}")

def parse_shutterspeed(speed_str):
    """Convertit une vitesse d'obturation en secondes.
    '1/500' → 0.002  |  '1/2' → 0.5  |  '2' → 2.0  |  '4' → 4.0
    """
    s = str(speed_str).strip()
    if '/' in s:
        num, den = s.split('/')
        return float(num) / float(den)
    return float(s)

def _set_phase_exposure(camera_service, aperture=None, iso=None):
    """Apply only phase-dependent exposure settings through the plugin."""
    if _sim_mode or camera_service is None:
        return
    camera_service.set_exposure_settings(aperture=aperture, iso=iso)

def _sim_capture_speed_list(
    speeds,
    photo_num_start,
    next_shot_time,
    deadline=None,
    slowest_override_seconds=None,
):
    """Simulation-only capture path. No gphoto2 call is allowed here.

    If slowest_override_seconds is provided for a regular EV bracket,
    extend the simulated bracket toward longer exposures using the same
    logical EV step.

    Hardware-specific snapping remains the responsibility of the real
    camera plugins.
    """
    sim_speeds = [str(s) for s in speeds]

    if slowest_override_seconds is not None:
        fastest, slowest, step_il, regular = _norm_plan(sim_speeds)

        if not regular:
            raise RuntimeError(
                "slowest_override_seconds fourni pour une liste irrégulière"
            )

        try:
            target_slowest = float(slowest_override_seconds)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                "slowest_override_seconds invalide en simulation"
            ) from exc

        current_slowest = parse_shutterspeed(slowest)

        if target_slowest < current_slowest:
            raise RuntimeError(
                "slowest_override_seconds ne peut pas raccourcir "
                "la borne lente en simulation"
            )

        if step_il <= 0.0:
            raise RuntimeError(
                "step_IL invalide pour extension atmosphérique en simulation"
            )

        next_exposure = current_slowest * (2.0 ** step_il)

        while next_exposure < target_slowest:
            sim_speeds.append(
                _format_seconds_as_speed(next_exposure)
            )
            next_exposure *= 2.0 ** step_il

        if target_slowest > current_slowest:
            sim_speeds.append(
                _format_seconds_as_speed(next_exposure)
            )

    count = 0

    for speed in sim_speeds:
        if deadline is not None:
            exposure = parse_shutterspeed(speed)
            end_exp = now() + timedelta(seconds=exposure)

            if end_exp > deadline and exposure > 0.5:
                _log(
                    f"INFO {Colors.ORANGE}"
                    f"⚠ Sécurité deadline : {speed} sautée"
                    f"{Colors.RESET}"
                )
                continue

        _log(
            f"{Colors.PINK}"
            f"⚡ [SIM] Photo #{photo_num_start + count} — {speed}"
            f"{Colors.RESET}"
        )
        count += 1

    if count:
        _watchdog_write("shooting", next_shot_time)

    return count

def _format_seconds_as_speed(sec: float) -> str:
    # Prefer fraction when possible for readability; fall back to decimal.
    if sec <= 0:
        return "0"
    if sec >= 1.0:
        return f"{sec:g}"
    frac = 1.0 / sec
    return f"1/{frac:g}"

def capture_speed_list(camera_service, speeds, photo_num_start, next_shot_time, deadline=None):
    """Execute a requested speed list through the selected camera plugin."""
    try:
        use_atmo = bool(cfg.get("atmo_compensation", False))
        slowest_override_seconds = None

        fastest, slowest, step_il, regular = _norm_plan(
            [str(s) for s in speeds]
        )

        if use_atmo and regular:
            loc = cfg.get("_circumstances_location", {})

            if loc is None or loc.get("altitude_m") is None:
                raise RuntimeError(
                    "atmo_compensation actif : altitude observateur manquante"
                )

            alts = {
                "C1_alt_deg": cfg.get("C1_alt_deg"),
                "C2_alt_deg": cfg.get("C2_alt_deg"),
                "TMAX_alt_deg": cfg.get("TMAX_alt_deg"),
                "C3_alt_deg": cfg.get("C3_alt_deg"),
                "C4_alt_deg": cfg.get("C4_alt_deg"),
            }

            if any(v is None for v in alts.values()):
                raise RuntimeError(
                    "atmo_compensation actif : "
                    "altitude C1/C2/TMAX/C3/C4 manquante"
                )

            try:
                tl = {
                    k: _timeline[k]
                    for k in ("C1", "C2", "TMAX", "C3", "C4")
                }
            except KeyError as exc:
                raise RuntimeError(
                    f"atmo_compensation actif : timestamp {exc.args[0]} manquant"
                ) from exc

            if next_shot_time is None:
                raise RuntimeError(
                    "atmo_compensation actif : timestamp capture manquant"
                )

            h = interpolate_altitude(
                next_shot_time,
                tl,
                alts,
            )

            facteur = facteur_atmospherique(
                h,
                float(loc["altitude_m"]),
            )

            slowest_seconds = parse_shutterspeed(slowest)

            slowest_override_seconds = (
                slowest_seconds * float(facteur)
            )

        if _sim_mode:
            return _sim_capture_speed_list(
                speeds,
                photo_num_start,
                next_shot_time,
                deadline,
                slowest_override_seconds=slowest_override_seconds,
            )

        if camera_service is None:
            _log(
                f"{Colors.RED}Caméra indisponible : capture annulée{Colors.RESET}"
            )
            return 0

        result = camera_service.shoot_speed_list(
            speeds,
            photo_num_start=photo_num_start,
            deadline=deadline,
            slowest_override_seconds=slowest_override_seconds,
        )

        if result.frames:
            _watchdog_write("shooting", next_shot_time)

        if result.frames != result.planned:
            _log(
                f"{Colors.YELLOW}"
                f"Capture plugin : {result.frames}/{result.planned} vues "
                f"({result.detail})"
                f"{Colors.RESET}"
            )
        else:
            _log(
                f"{Colors.GREEN}"
                f"Capture plugin : {result.frames}/{result.planned} vues "
                f"({result.detail})"
                f"{Colors.RESET}"
            )

        return result.frames

    except Exception as exc:
        _log(
            f"{Colors.RED}Erreur plugin caméra : {exc}{Colors.RESET}"
        )
        return 0

def attendre_heure(heure_cible):
    """
    Attend jusqu'à une heure précise.

    Args:
        heure_cible (datetime): L'heure à laquelle le script doit attendre.
    """
    _log(f"{Colors.GREEN}# WAITING TSTART{Colors.RESET}")
    last_log = now()
    while now() < heure_cible:
        sleep_sim(1)
        # Émettre une ligne toutes les 10s pour débloquer le pipe Flask
        if (now() - last_log).total_seconds() >= 10:
            remaining = int((heure_cible - now()).total_seconds())
            _log(f"{Colors.GREEN}# Attente TSTART — {remaining}s restantes{Colors.RESET}")
            last_log = now()

def afficher_messages_temps():
    """Affiche un message à une heure donnée."""
    messages_restants = sorted(messages_temps, key=lambda x: x[0])
    while messages_restants and not audio_service.is_stopped():
        heure_actuelle = now()
        for message in messages_restants[:]:
            if heure_actuelle >= message[0]:
                _log(f"{Colors.PINK}INFO - {message[1]}{Colors.RESET}")
                messages_restants.remove(message)
        audio_service.wait_stop(0.1 if _sim_mode else 0.5)

def _shutdown_audio_threads(timeout=5.0):
    """Arrête les alertes audio et attend les lectures en cours."""
    audio_service.shutdown(timeout)

def _emettre_evenement_batterie(pct):
    """Conservé pour compatibilité — Jack uniquement."""
    pass
def _emettre_evenement_alerte_batterie(pct, niveau):
    """Conservé pour compatibilité — Jack uniquement."""
    pass
def jouer_son_en_thread(nom_fichier):
    """Déclenche un son via Jack (pygame/ALSA) dans un thread daemon."""
    def _run():
        _log(f"INFO {Colors.ORANGE}♪ Son : {nom_fichier}{Colors.RESET}")
        audio_service.play(nom_fichier)
    if audio_service.is_stopped():
        return
    t = threading.Thread(target=_run, daemon=True, name=f"audio-{nom_fichier}")
    audio_service.register_thread(t)
    t.start()

def ecouter_alertes():
    """Thread : surveille alertes_sons et déclenche chaque WAV à l'heure prévue.
    Les alertes dont l'heure est déjà passée au démarrage sont ignorées silencieusement.
    """
    demarrage = now()
    # Filtrer les alertes déjà passées au moment du démarrage
    restantes = sorted(
        [a for a in alertes_sons if a[0] > demarrage],
        key=lambda x: x[0]
    )
    ignored = len(alertes_sons) - len(restantes)
    if ignored:
        _log(f"{Colors.YELLOW}{ignored} alerte(s) son passée(s) ignorée(s).{Colors.RESET}")
    while restantes and not audio_service.is_stopped():
        _now = now()
        for alerte in restantes[:]:
            if _now >= alerte[0]:
                son = alerte[1]
                jouer_son_en_thread(son)
                restantes.remove(alerte)
        audio_service.wait_stop(0.1 if _sim_mode else 0.5)

def get_battery_level(camera_service):
    """Read battery through the active camera plugin and emit UI events."""
    if _sim_mode or camera_service is None:
        return None
    try:
        pct = camera_service.get_battery_level()
        if pct is None:
            _log(f"{Colors.YELLOW}Batterie : niveau indisponible via plugin{Colors.RESET}")
            return None
        if pct > 70:
            _log(f"{Colors.GREEN}Batterie : {pct}%{Colors.RESET}")
        elif pct > 40:
            _log(f"{Colors.BLEU}Batterie : {pct}%{Colors.RESET}")
        elif pct > 20:
            _log(f"{Colors.JAUNE}Batterie : {pct}% avertissement{Colors.RESET}")
        else:
            _log(f"{Colors.RED}!!! BATTERIE FAIBLE : {pct}% CHANGER !!{Colors.RESET}")
            t = now()
            if not (C2 - timedelta(minutes=3) <= t <= C3 + timedelta(minutes=3)):
                jouer_son_en_thread("battery_low.wav")
        _emettre_evenement_batterie(pct)
        return pct
    except Exception as exc:
        _log(f"{Colors.YELLOW}Batterie plugin : {exc}{Colors.RESET}")
        return None

def calculer_temps_debut_sequence(T0, T1, intervalle):
    """Calcule la date T1 - Tdeb où Tdeb = ( (T1 - T0) // intervalle ) * intervalle"""
    delta = (T1 - T0).total_seconds()  # Différence en secondes
    n = delta // intervalle  # Nombre d'intervalles entiers
    Tdeb = math.floor(n) * intervalle  # Temps total ajusté en secondes
    return T1 - timedelta(seconds=Tdeb)  # Nouvelle date T1 - Tdeb

def unmount_camera():
    """Libère l'USB de la caméra pour gphoto2.
    Tue les processus GVFS qui monopolisent le device USB,
    sans dépendre du point de montage /run/user/1000/gvfs
    (non disponible depuis une session systemd).
    """
    # 1. Tuer les processus GVFS (pkill est dans /usr/bin, toujours dans le PATH)
    for proc in ("gvfsd-gphoto2", "gvfs-gphoto2-volume-monitor", "gvfsd"):
        subprocess.run(["/usr/bin/pkill", "-f", proc],
                       stderr=subprocess.DEVNULL, check=False)

    # 2. Tenter de démonter GVFS si le point existe (session graphique)
    fusermount = None
    for candidate in ("/bin/fusermount", "/usr/bin/fusermount", "/usr/bin/fusermount3"):
        if os.path.exists(candidate):
            fusermount = candidate
            break

    if fusermount:
        for mount in (f"/run/user/1000/gvfs", f"/run/user/{os.getuid()}/gvfs"):
            if os.path.exists(mount):
                try:
                    subprocess.run([fusermount, "-u", mount], stderr=subprocess.DEVNULL)
                except Exception:
                    pass

    # 3. Laisser udev se stabiliser avant que gphoto2 réclame le device
    time.sleep(1)

def estimatedPhoto(T0, T1, intervalle):
    """Estime le nombre d'instants de tir possibles dans [T0, T1).

    Une première photo peut partir immédiatement à T0. Le comptage est donc
    ceil((T1-T0)/intervalle), et non floor(...). Cela évite notamment
    d'afficher ``Bracket 1/0`` lorsqu'il reste moins d'un intervalle avant T1.
    """
    delta = (T1 - T0).total_seconds()
    if delta <= 0:
        return 0
    if intervalle <= 0:
        raise ValueError("intervalle doit être > 0")
    return math.ceil(delta / intervalle)




def _usb_wait_or_hold(camera_service, next_shot_time, deadline=None):
    """Attend la prochaine échéance en maintenant la caméra connectée.

    Depuis v6.5, l'économie USB héritée du D850 est supprimée : aucune
    libération/reconnexion volontaire de la session PTP entre deux photos.
    """
    while now() < next_shot_time:
        if deadline and now() >= deadline:
            return
        if _sim_mode:
            sleep_sim(0.1)
        else:
            time.sleep(0.5)


def main():
    """Main function to execute the eclipse photography sequence."""
    camera_service = None
    try:
        _log(f"{Colors.PINK}#{Colors.RESET}")
        print(f"{Colors.PINK}# TOTAL SOLAR ECLIPSE AUTOMATIC SCRIPT - {titre}{Colors.RESET}")
        print(f"{Colors.PINK}#{Colors.RESET}")

        # ── Init horloge simulation ────────────────────────────────────────
        if _sim_mode:
            _runtime_clock.start_simulation(TSTART - timedelta(seconds=30))
            print(f"WARNING {Colors.PINK}⚡ SIMULATION ×{_sim_speed:.0f} | Heure virtuelle départ : {_runtime_clock.virt_start.strftime('%H:%M:%S')} | 1 seconde réelle = {_sim_speed:.0f}s virtuelles{Colors.RESET}")

        # ── Watchdog : reprise après crash ? ──────────────────────────────
        prev_state = _watchdog_read()
        resume_from = None
        if prev_state and not _sim_mode:
            phase_prev      = prev_state.get("phase")
            next_shot_prev  = prev_state.get("next_shot_time")
            written_at      = prev_state.get("written_at", "")
            _log(f"WARNING {Colors.ORANGE}⚠ WATCHDOG : état précédent détecté (phase={phase_prev}, next_shot={next_shot_prev}, écrit={written_at[:19]}){Colors.RESET}")
            if next_shot_prev:
                try:
                    # v7.1 : le watchdog stocke un ISO UTC complet, fractions incluses.
                    resume_from = datetime.fromisoformat(str(next_shot_prev).replace("Z", "+00:00"))
                    if resume_from.tzinfo is not None:
                        resume_from = resume_from.astimezone(timezone.utc).replace(tzinfo=None)
                    interval = interval_partial
                    while resume_from <= now():
                        resume_from += timedelta(seconds=interval)
                    _log(f"WARNING {Colors.ORANGE}⚠ REPRISE : prochaine photo à {format_hms_ms(resume_from)}{Colors.RESET}")
                except Exception as e:
                    _log(f"{Colors.YELLOW}Watchdog : impossible de parser next_shot_time : {e}{Colors.RESET}")

        # ── Connexion caméra via CameraService / CameraPlugin ─────────────
        _log(f"{Colors.GREEN}### CLEAR CONNEXION TO CAMERA{Colors.RESET}")
        camera_service = None
        if _sim_mode:
            _log(f"{Colors.PINK}⚡ SIM : accès matériel caméra totalement désactivé{Colors.RESET}")
        else:
            unmount_camera()
            camera_service = CameraService(log_fn=_log, clock=_runtime_clock)
            try:
                plugin = camera_service.connect()
            except Exception as exc:
                _log(f"{Colors.RED}Caméra/plugin non initialisé : {exc}{Colors.RESET}")
                return
            _log(f"{Colors.GREEN}### INIT - CAMERA CONFIGURATION ({plugin.name}){Colors.RESET}")
            camera_service.init_settings(aperture=aperture_partial, iso=iso_partial)
            time.sleep(1)
            get_battery_level(camera_service)
    
        if is_partial:
            _log(f"{Colors.GREEN}### ECLIPSE PARIELLE{Colors.RESET}")
        else:
             _log(f"{Colors.GREEN}### ECLIPSE TOTALE{Colors.RESET}")          
        
        _log(f"{Colors.GREEN}### SETUP - CONTACTS{Colors.RESET}")
        _log(f"C1 : {format_hms_ms(C1)}")
        if is_partial:
            _log(f"C2 : {format_hms_ms(C2)}")
            _log(f"C3 : {format_hms_ms(C3)}")
        _log(f"C4 : {format_hms_ms(C4)}")

        _log(f"{Colors.GREEN}### INIT - TSTART-TEND{Colors.RESET}")
        _log(f"START: {format_hms_ms(TSTART)}")
        _log(f"END  : {format_hms_ms(TEND)}")


        # Lancer l'écoute des alertes sonores
        _log(f"{Colors.BLUE}Sons — Jack (pygame ALSA) : {audio_service.SOUNDS_ENABLED}{Colors.RESET}")
        thread_alertes = threading.Thread(target=ecouter_alertes, daemon=True, name="audio-alert-scheduler")
        audio_service.register_thread(thread_alertes)
        thread_alertes.start()

        # Lancer alerte textuelle
        thread_messages = threading.Thread(target=afficher_messages_temps, daemon=True, name="message-scheduler")
        audio_service.register_thread(thread_messages)
        thread_messages.start()

        # Attente du début des prises de vue
        attendre_heure(TSTART)

        if not is_partial:
            ### ECLIPSE TOTALE DE SOLEIL

            ###
            ### PHASE 1a : START -> C1 -> C2-duree_diamond_ring
            ###
            _log(f"{Colors.GREEN}# PHASE 1a : Start to C1 to C2-{duree_diamond_ring}s{Colors.RESET}")
            _log(f"{Colors.BLUE}Camera Settings : Interval : {interval_partial}{Colors.RESET}")
            _log(f"{Colors.BLUE}Camera Settings : Bracket vitesses : {speeds_partial}{Colors.RESET}")
            _log(f"{Colors.BLUE}Camera Settings : Ouverture : {aperture_partial}{Colors.RESET}")
            next_shot_time = calculer_temps_debut_sequence(TSTART, TMAX, interval_partial)

            # Reprise watchdog : sauter jusqu'à la prochaine photo calculée
            if resume_from and resume_from > next_shot_time:
                while next_shot_time < resume_from:
                    next_shot_time += timedelta(seconds=interval_partial)
                _log(f"{Colors.ORANGE}⚠ REPRISE 1a : première photo à {next_shot_time.strftime('%H:%M:%S')}{Colors.RESET}")

            nbTotalBracket = estimatedPhoto(next_shot_time, (C2 - timedelta(seconds=duree_diamond_ring)), interval_partial)
            _log(f"{Colors.YELLOW}Start Capture (estimated number of brackets: {nbTotalBracket}){Colors.RESET}")
            _log(f"{Colors.CYAN}⏱ Prochaine photo : {next_shot_time.strftime('%H:%M:%S')}{Colors.RESET}")

            nbBracket = 1
            nbPhoto   = 1
            fin_phase_1a = C2 - timedelta(seconds=duree_diamond_ring)
            while now() < fin_phase_1a:
                try:
                    if now() >= next_shot_time:
                        n = capture_speed_list(camera_service, speeds_partial, nbPhoto, next_shot_time, deadline=fin_phase_1a)
                        if n > 0:
                            _log(f"{Colors.YELLOW}Bracket {nbBracket}/{nbTotalBracket} [{n} photos]{Colors.RESET}")
                            nbBracket += 1
                            nbPhoto   += n
                            next_shot_time = now() + timedelta(seconds=interval_partial)
                            if now() < fin_phase_1a:
                                _log(f"{Colors.CYAN}⏱ Prochaine photo : {next_shot_time.strftime('%H:%M:%S')}{Colors.RESET}")
                            # Ne pas attendre si la prochaine photo est après fin_phase_1a
                            # → sortir immédiatement pour enchaîner sur le diamond ring
                            if next_shot_time >= fin_phase_1a:
                                break
                            _usb_wait_or_hold(camera_service, next_shot_time, deadline=fin_phase_1a)
                except gp.GPhoto2Error as e:
                    _log(f"{Colors.YELLOW}Erreur USB : {e} — retry au prochain intervalle{Colors.RESET}")
                time.sleep(0.1)

            ###
            ### PHASE 1b : DIAMOND RING -- C2-duree_diamond_ring -> C2
            ###
            _log(f"{Colors.GREEN}# PHASE 1b : DIAMOND RING -- C2-{duree_diamond_ring}s -> C2{Colors.RESET}")
            _log(f"{Colors.BLUE}Camera Settings : Interval : {interval_diamond_ring}{Colors.RESET}")
            _log(f"{Colors.BLUE}Camera Settings : Bracket vitesses : {speeds_diamond_ring}{Colors.RESET}")
            _log(f"{Colors.BLUE}Camera Settings : Ouverture : {aperture_diamond}{Colors.RESET}")
            # Reconfigurer uniquement si différent de la phase précédente
            if aperture_diamond != aperture_partial or iso_diamond_ring != iso_partial:
                _set_phase_exposure(camera_service, aperture_diamond, iso_diamond_ring)
            next_shot_time = calculer_temps_debut_sequence(C2 - timedelta(seconds=duree_diamond_ring), C2, interval_diamond_ring)
            # Si on arrive tardivement dans la fenêtre, démarrer immédiatement
            if next_shot_time < now():
                next_shot_time = now()
            nbTotalBracket = estimatedPhoto(next_shot_time, C2, interval_diamond_ring)
            _log(f"{Colors.YELLOW}Start Capture (estimated number of brackets: {nbTotalBracket}){Colors.RESET}")
            _log(f"{Colors.CYAN}⏱ Prochaine photo : {next_shot_time.strftime('%H:%M:%S')}{Colors.RESET}")
            nbBracket = 1
            nbPhoto   = 1
            while now() < C2:
                try:
                    if now() >= next_shot_time:
                        n = capture_speed_list(camera_service, speeds_diamond_ring, nbPhoto, next_shot_time, deadline=C2)
                        if n > 0:
                            _log(f"{Colors.YELLOW}Bracket {nbBracket}/{nbTotalBracket} [{n} photos]{Colors.RESET}")
                            nbBracket += 1
                            nbPhoto   += n
                        next_shot_time = now() + timedelta(seconds=interval_diamond_ring)
                except gp.GPhoto2Error as e:
                    _log(f"{Colors.YELLOW}Erreur USB : {e} — retry au prochain intervalle{Colors.RESET}")
                time.sleep(0.1)

            ###
            ### PHASE 2 : TOTALITY -- C2 -> C3
            ###
            _log(f"{Colors.GREEN}# PHASE 2 - TOTALITY -- C2 -> C3{Colors.RESET}")
            if aperture_totality != aperture_diamond or iso_totality != iso_diamond_ring:
                _set_phase_exposure(camera_service, aperture_totality, iso_totality)
            _log(f"{Colors.YELLOW}Capture{Colors.RESET}")

            # La sécurité C3 est désormais appliquée par le plugin via deadline.
            # Le moteur ne règle plus jamais directement shutterspeed/shutterspeed2.
            _log(f"{Colors.BLUE}Sécurité C3 : aucune séquence ne doit déborder sur C3 ({format_hms_ms(C3)}){Colors.RESET}")

            nbPhoto = 1
            cycle_totality = 1
            while now() < C3:
                n = capture_speed_list(camera_service, shutter_speeds, nbPhoto, now(), deadline=C3)
                if n <= 0:
                    # Plus assez de temps pour une séquence complète : attendre C3 sans boucle CPU.
                    if now() < C3:
                        sleep_sim(0.05) if _sim_mode else time.sleep(0.05)
                    continue
                _log(f"{Colors.YELLOW}Cycle totalité {cycle_totality} [{n} photos]{Colors.RESET}")
                nbPhoto += n
                cycle_totality += 1

            ###
            ### PHASE 3a : DIAMOND RING -- C3 -> C3+duree_diamond_ring
            ###
            _log(f"{Colors.GREEN}# PHASE 3a : DIAMOND RING -- C3 -> C3+{duree_diamond_ring}s{Colors.RESET}")
            _log(f"{Colors.BLUE}Camera Settings : Interval : {interval_diamond_ring}{Colors.RESET}")
            _log(f"{Colors.BLUE}Camera Settings : Bracket vitesses : {speeds_diamond_ring}{Colors.RESET}")
            _log(f"{Colors.BLUE}Camera Settings : Ouverture : {aperture_diamond}{Colors.RESET}")
            # Reconfigurer uniquement si différent de la totalité
            if aperture_diamond != aperture_totality or iso_diamond_ring != iso_totality:
                _set_phase_exposure(camera_service, aperture_diamond, iso_diamond_ring)
            next_shot_time = now()
            nbTotalBracket = estimatedPhoto(next_shot_time, C3 + timedelta(seconds=duree_diamond_ring), interval_diamond_ring)
            _log(f"{Colors.YELLOW}Start Capture (estimated number of brackets: {nbTotalBracket}){Colors.RESET}")
            _log(f"{Colors.CYAN}⏱ Prochaine photo : {next_shot_time.strftime('%H:%M:%S')}{Colors.RESET}")
            nbBracket = 1
            nbPhoto   = 1
            while now() < C3 + timedelta(seconds=duree_diamond_ring):
                try:
                    if now() >= next_shot_time:
                        n = capture_speed_list(camera_service, speeds_diamond_ring, nbPhoto, next_shot_time, deadline=C3 + timedelta(seconds=duree_diamond_ring))
                        if n > 0:
                            _log(f"{Colors.YELLOW}Bracket {nbBracket}/{nbTotalBracket} [{n} photos]{Colors.RESET}")
                            nbBracket += 1
                            nbPhoto   += n
                        next_shot_time = now() + timedelta(seconds=interval_diamond_ring)
                except gp.GPhoto2Error as e:
                    _log(f"{Colors.YELLOW}Erreur USB : {e} — retry au prochain intervalle{Colors.RESET}")
                time.sleep(0.1)

            ###
            ### PHASE 3b : C3+duree_diamond_ring -> C4 -> TEND
            ###
            _log(f"{Colors.GREEN}# Phase 3b - C3+{duree_diamond_ring}s -> C4 -> TEND{Colors.RESET}")
            # Reconfigurer uniquement si différent du diamond ring
            if aperture_partial != aperture_diamond or iso_partial != iso_diamond_ring:
                _set_phase_exposure(camera_service, aperture_partial, iso_partial)
            _log(f"{Colors.BLUE}Camera Settings : Interval : {interval_partial}{Colors.RESET}")
            _log(f"{Colors.BLUE}Camera Settings : Bracket vitesses : {speeds_partial}{Colors.RESET}")

            next_shot_time = TMAX + timedelta(seconds=interval_partial)
            debut_3b = C3 + timedelta(seconds=duree_diamond_ring)
            while next_shot_time < debut_3b:
                next_shot_time += timedelta(seconds=interval_partial)

            # Reprise watchdog phase 3b
            if resume_from and resume_from > next_shot_time:
                while next_shot_time < resume_from:
                    next_shot_time += timedelta(seconds=interval_partial)
                _log(f"{Colors.ORANGE}⚠ REPRISE 3b : première photo à {next_shot_time.strftime('%H:%M:%S')}{Colors.RESET}")

            nbTotalBracket = estimatedPhoto(next_shot_time, TEND, interval_partial)
            _log(f"{Colors.YELLOW}Start Capture (estimated number of brackets: {nbTotalBracket}){Colors.RESET}")
            _log(f"{Colors.CYAN}⏱ Prochaine photo : {next_shot_time.strftime('%H:%M:%S')}{Colors.RESET}")

            nbBracket = 1
            nbPhoto   = 1
            while now() < TEND:
                try:
                    if now() >= next_shot_time:
                        n = capture_speed_list(camera_service, speeds_partial, nbPhoto, next_shot_time, deadline=TEND)
                        if n > 0:
                            _log(f"{Colors.YELLOW}Bracket {nbBracket}/{nbTotalBracket} [{n} photos]{Colors.RESET}")
                            nbBracket += 1
                            nbPhoto   += n
                            next_shot_time = now() + timedelta(seconds=interval_partial)
                            if next_shot_time >= TEND:
                                _log(f"{Colors.GREEN}⏱ Prochaine photo après TEND — séquence terminée.{Colors.RESET}")
                                break
                            _log(f"{Colors.CYAN}⏱ Prochaine photo : {next_shot_time.strftime('%H:%M:%S')}{Colors.RESET}")
                            _usb_wait_or_hold(camera_service, next_shot_time)
                except gp.GPhoto2Error as e:
                    _log(f"{Colors.YELLOW}Erreur USB : {e} — retry au prochain intervalle{Colors.RESET}")
                time.sleep(0.1)

            _watchdog_clear()
            _log(f"{Colors.GREEN}✅ Séquence terminée normalement.{Colors.RESET}")
        else:
            # ECLIPSE PARTIELLE DE SOLEIL
            
            ###
            ### PHASE UNIQUE : Start to C1 to C4 to END
            ###
            _log(f"{Colors.GREEN}# PHASE UNIQUE : Start to C1 to C4 to END{Colors.RESET}")
            _log(f"{Colors.BLUE}Camera Settings : Interval : {interval_partial}{Colors.RESET}")
            _log(f"{Colors.BLUE}Camera Settings : Shutterspeed : {shutterspeed_partial}{Colors.RESET}")
            next_shot_time = calculer_temps_debut_sequence(TSTART, TMAX, interval_partial)

            # Reprise watchdog : sauter jusqu'à la prochaine photo calculée
            if resume_from and resume_from > next_shot_time:
                while next_shot_time < resume_from:
                    next_shot_time += timedelta(seconds=interval_partial)
                _log(f"{Colors.ORANGE}⚠ REPRISE 1a : première photo à {next_shot_time.strftime('%H:%M:%S')}{Colors.RESET}")

            nbTotalBracket = estimatedPhoto(next_shot_time, TEND, interval_partial)
            _log(f"{Colors.YELLOW}Start Capture (estimated number of brackets: {nbTotalBracket}){Colors.RESET}")
            _log(f"{Colors.CYAN}⏱ Prochaine photo : {next_shot_time.strftime('%H:%M:%S')}{Colors.RESET}")

            nbBracket = 1
            nbPhoto   = 1
            fin_phase = TEND
            while now() < fin_phase:
                try:
                    if now() >= next_shot_time:
                        n = capture_speed_list(camera_service, speeds_partial, nbPhoto, next_shot_time, deadline=TEND)
                        if n > 0:
                            _log(f"{Colors.YELLOW}Bracket {nbBracket}/{nbTotalBracket} [{n} photos]{Colors.RESET}")
                            nbBracket += 1
                            nbPhoto   += n
                            next_shot_time = now() + timedelta(seconds=interval_partial)
                            if next_shot_time >= TEND:
                                _log(f"{Colors.GREEN}⏱ Prochaine photo après TEND — séquence terminée.{Colors.RESET}")
                                break
                            _log(f"{Colors.CYAN}⏱ Prochaine photo : {next_shot_time.strftime('%H:%M:%S')}{Colors.RESET}")
                            _usb_wait_or_hold(camera_service, next_shot_time)
                except gp.GPhoto2Error as e:
                    _log(f"{Colors.YELLOW}Erreur USB : {e} — retry au prochain intervalle{Colors.RESET}")
                time.sleep(0.1)

    except KeyboardInterrupt:
        _log("INFO Script stopped by user.")
    except Exception as e:
        _log(f"{Colors.RED}Unexpected error: {e}{Colors.RESET}")
    finally:
        _shutdown_audio_threads()
        if camera_service is not None:
            camera_service.close()
        _log(f"{Colors.GREEN}End of the script.{Colors.RESET}")

if __name__ == "__main__":
    main()
