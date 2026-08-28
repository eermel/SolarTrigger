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
import uuid
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
from services.camera_service import CameraService, CaptureIntent, PreparedCapture
from plugins.camera.base import CaptureResult
from services.camera_service import _normalized_speed_plan as _norm_plan
from scripts.camera_ipc_client import CameraIpcClient
from scripts.fanout_camera_adapter import FanoutCameraAdapter
from backend.atmo import facteur_atmospherique, interpolate_altitude
from backend import audio_service

_runtime_clock = RuntimeClock()
_watchdog = TriggerWatchdog(Path(__file__).resolve().parent.parent / "trigger_state.json", _runtime_clock)

C3_OVERFLOW_GRACE_S = 1.0
SHORT_EXPOSURE_MAX_S = 0.5


def _select_uniform_indices(exposures, target_size):
    """Return evenly distributed indices including both bracket endpoints."""
    total_size = len(exposures)
    if target_size < 2 or target_size > total_size:
        raise ValueError("target_size must satisfy 2 <= target_size <= len(exposures)")

    intervals = target_size - 1
    span = total_size - 1
    return [
        (2 * position * span + intervals) // (2 * intervals)
        for position in range(target_size)
    ]


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
    parser.add_argument("--dry-run",   action="store_true",  help="Dry-run : même moteur et même caméra, timeline translatée sur maintenant")
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

_ASTRONOMY_KEYS = {
    "C1", "C2", "C3", "C4", "TMAX", "TSTART", "TEND",
    "C1_alt_deg", "C2_alt_deg", "TMAX_alt_deg", "C3_alt_deg", "C4_alt_deg",
}
_CAPTURE_PHASES = ("partial", "diamond_ring", "totality")

def build_capture_canonical(capture):
    """Validate and copy a capture_execution v2 configuration."""
    if not isinstance(capture, dict):
        raise ValueError("configuration capture invalide : objet JSON attendu")

    has_v2_marker = "phases" in capture or "exposure_correction" in capture
    if has_v2_marker:
        phases = capture.get("phases")
        correction = capture.get("exposure_correction", {})

        if not isinstance(phases, dict):
            raise ValueError("capture v2 invalide : 'phases' doit être un objet")

        for phase in _CAPTURE_PHASES:
            if not isinstance(phases.get(phase), dict):
                raise ValueError(
                    f"capture v2 invalide : 'phases.{phase}' doit être un objet"
                )

        if not isinstance(correction, dict):
            raise ValueError(
                "capture v2 invalide : 'exposure_correction' doit être un objet"
            )

        canonical_correction = dict(correction)

        # Compatibilité avec la représentation transitoire utilisée avant
        # finalisation du schéma capture_execution v2.
        if ("atmospheric" in canonical_correction
                and "atmospheric_attenuation_enabled" not in canonical_correction):
            canonical_correction["atmospheric_attenuation_enabled"] = (
                canonical_correction.pop("atmospheric")
            )

        atmospheric = canonical_correction.get(
            "atmospheric_attenuation_enabled"
        )
        if atmospheric is not None and not isinstance(atmospheric, bool):
            raise ValueError(
                "capture v2 invalide : "
                "'exposure_correction.atmospheric_attenuation_enabled' "
                "doit être un booléen"
            )

        return {
            "phases": {phase: dict(phases[phase]) for phase in _CAPTURE_PHASES},
            "exposure_correction": canonical_correction,
        }

    raise ValueError("capture v2 invalide : marqueur 'phases' absent")


def build_legacy_capture_canonical(camera_profile, circumstances):
    """Adapt historical camera and eclipse fields to the in-memory v2 shape."""
    camera_profile = camera_profile if isinstance(camera_profile, dict) else {}
    circumstances = circumstances if isinstance(circumstances, dict) else {}

    def phase_settings(name):
        profile_phase = camera_profile.get(name, {})
        circumstance_phase = circumstances.get(name, {})
        profile_phase = profile_phase if isinstance(profile_phase, dict) else {}
        circumstance_phase = (
            circumstance_phase if isinstance(circumstance_phase, dict) else {}
        )
        settings = dict(profile_phase)
        settings.update(circumstance_phase)
        return settings

    partial = phase_settings("partial")
    diamond_ring = phase_settings("diamond_ring")
    totality_profile = camera_profile.get("totality", {})
    totality_profile = (
        totality_profile if isinstance(totality_profile, dict) else {}
    )
    totality_circumstances = circumstances.get("totality", {})
    totality_circumstances = (
        totality_circumstances
        if isinstance(totality_circumstances, dict) else {}
    )
    totality = dict(totality_profile)
    if totality_circumstances.get("speeds"):
        totality["speeds"] = totality_circumstances["speeds"]

    partial_circumstances = circumstances.get("partial", {})
    partial_circumstances = (
        partial_circumstances if isinstance(partial_circumstances, dict) else {}
    )
    diamond_circumstances = circumstances.get("diamond_ring", {})
    diamond_circumstances = (
        diamond_circumstances if isinstance(diamond_circumstances, dict) else {}
    )
    if ("shutterspeed_partial" in circumstances
            and not partial_circumstances.get("speeds")):
        partial["speeds"] = [circumstances["shutterspeed_partial"]]
    if ("shutterspeed_diamondring" in circumstances
            and not diamond_circumstances.get("speeds")):
        diamond_ring["speeds"] = [circumstances["shutterspeed_diamondring"]]

    partial_interval = circumstances.get("interval_partial")
    if partial_interval is None:
        phase1a = circumstances.get("phase1a", {})
        if isinstance(phase1a, dict):
            partial_interval = phase1a.get("interval_s")
    diamond_interval = circumstances.get("interval_diamond_ring")
    if diamond_interval is None:
        diamond_interval = diamond_circumstances.get("interval_s")
    diamond_duration = circumstances.get("duree_diamond_ring")
    if diamond_duration is None:
        diamond_duration = diamond_circumstances.get("duration_s")

    partial["interval_s"] = partial_interval
    partial["duration_s"] = None
    diamond_ring["interval_s"] = diamond_interval
    diamond_ring["duration_s"] = diamond_duration

    totality_interval = totality.get("interval_s")
    if totality_interval is None:
        phase2 = circumstances.get("phase2", {})
        if isinstance(phase2, dict):
            totality_interval = phase2.get("interval_s")
    totality["interval_s"] = totality_interval

    for phase in (partial, diamond_ring, totality):
        speeds = phase.get("speeds")
        if isinstance(speeds, list) and len(speeds) == 1:
            phase["shutter_min"] = speeds[0]
            phase["shutter_max"] = speeds[0]

    return {
        "phases": {
            "partial": partial,
            "diamond_ring": diamond_ring,
            "totality": totality,
        },
        "exposure_correction": {
            "atmospheric_attenuation_enabled": bool(
                circumstances.get(
                    "atmo_compensation",
                    camera_profile.get("atmo_compensation", False),
                )
            ),
        },
    }

def astronomy(name):
    """Read an astronomical circumstance, never a capture setting."""
    if name not in _ASTRONOMY_KEYS:
        raise KeyError(f"champ astronomy inconnu : {name}")
    return circumstances.get(name)

def capture_phase(name):
    """Read one phase exclusively from the injected canonical capture."""
    if name not in _CAPTURE_PHASES:
        raise KeyError(f"phase capture inconnue : {name}")
    return capture_canonical["phases"][name]

def exposure_correction(name, default=None):
    return capture_canonical["exposure_correction"].get(name, default)

def _observer_location():
    return circumstances.get(
        "_circumstances_location", cfg.get("_circumstances_location", {})
    )

def _apply_eclipse_file(target, ecl):
    # Preserve historical UI-only timing fallbacks before canonical adaptation.
    target.update(ecl)
    if "interval_partial" not in ecl:
        phase1a = ecl.get("phase1a", {})
        if phase1a.get("interval_s") is not None:
            target["interval_partial"] = phase1a["interval_s"]
    if "interval_diamond_ring" not in ecl:
        diamond = ecl.get("diamond_ring", {})
        if diamond.get("interval_s") is not None:
            target["interval_diamond_ring"] = diamond["interval_s"]
    if "duree_diamond_ring" not in ecl:
        diamond = ecl.get("diamond_ring", {})
        if diamond.get("duration_s") is not None:
            target["duree_diamond_ring"] = diamond["duration_s"]

circumstances = load_config_file(args.file) if args.file else {}
capture_source = {}
if args.camera:
    capture_source = load_config_file(args.camera)
capture_is_v2 = "phases" in capture_source or "exposure_correction" in capture_source
if capture_is_v2:
    try:
        capture_canonical = build_capture_canonical(capture_source)
    except ValueError as exc:
        _log(f"{Colors.RED}{exc}{Colors.RESET}")
        raise SystemExit(1) from exc
if args.file:
    _apply_eclipse_file(cfg, circumstances)
if capture_is_v2:
    _log(f"{Colors.GREEN}Stratégie photo dérivée de capture v2{Colors.RESET}")

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
circumstances.update({
    k: v for k, v in cli_overrides.items()
    if k in _ASTRONOMY_KEYS and v is not None
})
if args.shutterspeed_partial is not None:
    if capture_is_v2:
        capture_canonical["phases"]["partial"]["speeds"] = [args.shutterspeed_partial]
if args.shutterspeed_diamondring is not None:
    if capture_is_v2:
        capture_canonical["phases"]["diamond_ring"]["speeds"] = [args.shutterspeed_diamondring]
if capture_is_v2:
    legacy_timings = build_legacy_capture_canonical({}, cfg)["phases"]
    for phase_name in ("partial", "diamond_ring"):
        for timing_name in ("interval_s", "duration_s"):
            capture_canonical["phases"][phase_name].setdefault(
                timing_name, legacy_timings[phase_name][timing_name]
            )
else:
    capture_canonical = build_legacy_capture_canonical(capture_source, cfg)

# Alertes sonores — fichiers WAV dans le sous-dossier Sounds/ (relatif au script)
_SOUNDS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Sounds")
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
C1_str                   = astronomy("C1") if circumstances else cfg["C1"]
C2_str                   = astronomy("C2") if circumstances else cfg["C2"]
C3_str                   = astronomy("C3") if circumstances else cfg["C3"]
C4_str                   = astronomy("C4") if circumstances else cfg["C4"]
TMAX_str                 = astronomy("TMAX") if circumstances else cfg["TMAX"]
TSTART_str               = astronomy("TSTART") if circumstances else cfg["TSTART"]
TEND_str                 = astronomy("TEND") if circumstances else cfg["TEND"]
wake_up_time             = float(cfg.get("wake_up_time", 2.5))  # secondes

# Paramètres d'exécution exclusivement issus de la capture canonique.
_partial_capture = capture_phase("partial")
_diamond_capture = capture_phase("diamond_ring")
_totality_capture = capture_phase("totality")

def _capture_speed_summary(capture, default):
    speeds = capture.get("speeds")
    if isinstance(speeds, dict):
        values = [speeds.get("fastest"), speeds.get("slowest")]
        return list(dict.fromkeys(str(value) for value in values if value is not None))
    if speeds:
        return list(speeds)
    values = [capture.get("shutter_max"), capture.get("shutter_min")]
    summary = list(dict.fromkeys(str(value) for value in values if value is not None))
    return summary or list(default)

interval_partial         = int(_partial_capture["interval_s"])
interval_diamond_ring    = int(_diamond_capture["interval_s"])
interval_totality        = _totality_capture.get("interval_s")
speeds_partial           = _capture_speed_summary(_partial_capture, ["1/500"])
speeds_diamond_ring      = _capture_speed_summary(_diamond_capture, ["1/500"])
shutterspeed_partial     = speeds_partial[0]
shutterspeed_diamondring = speeds_diamond_ring[0]
aperture_partial         = _partial_capture.get("aperture", "f/8")
aperture_diamond         = _diamond_capture.get("aperture", "f/8")
aperture_totality        = _totality_capture.get("aperture", "f/8")
iso_partial              = str(_partial_capture.get("iso", "100"))
iso_diamond_ring         = str(_diamond_capture.get("iso", "100"))
iso_totality             = str(_totality_capture.get("iso", "100"))

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
    _diamond_capture["duration_s"] = int(
        input("Duration of Diamond Ring in s ? [40] ") or "40"
    )
    shutterspeed_partial     = input("Shutter speed for Partiality phase ? [1/500] ") or "1/500"
    shutterspeed_diamondring = input("Shutter speed for Diamond ring phase ? [1/500] ") or "1/500"

diamond_ring_duration_s = int(
    capture_canonical["phases"]["diamond_ring"]["duration_s"]
)

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
    _log(f"🧪 DRY-RUN ×1 — même moteur et même caméra, timeline translatée, TSTART dans {args.dry_run_delay:g}s")

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
        add(C2 - timedelta(seconds=diamond_ring_duration_s), "filters_off.wav", t_min=C1, t_max=C2)
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
        add(C3 + timedelta(seconds=diamond_ring_duration_s), "filters_on.wav",
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


# Liste des vitesses d'obturation de totalité injectée depuis la capture canonique.
_DEFAULT_SPEEDS = ["1/4000", "1/2000", "1/1000", "1/500", "1/250",
                   "1/125",  "1/60",   "1/30",   "1/15",  "1/8",
                   "1/4",    "1/2",    "1",      "2",     "4"]

_configured_totality_speeds = _capture_speed_summary(_totality_capture, [])
if _configured_totality_speeds:
    shutter_speeds = _configured_totality_speeds
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

if interval_totality is None:
    interval_totality = max(
        0.001, sum(parse_shutterspeed(s) for s in shutter_speeds)
    )

def _set_phase_exposure(camera_service, aperture=None, iso=None):
    """Apply phase-dependent settings once, through the service contract."""
    camera_service.apply_phase_settings(aperture=aperture, iso=iso)

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

def _extend_regular_ev_for_atmosphere(
    speeds,
    shutter_min,
    shutter_max,
    step_ev,
    target_time,
    timeline,
    altitudes,
    observer_altitude,
):
    """Extend a regular EV bracket for atmospheric attenuation."""
    if observer_altitude is None:
        raise RuntimeError(
            "atmo_compensation actif : altitude observateur manquante"
        )

    if any(value is None for value in altitudes.values()):
        raise RuntimeError(
            "atmo_compensation actif : "
            "altitude C1/C2/TMAX/C3/C4 manquante"
        )

    try:
        tl = {
            key: timeline[key]
            for key in ("C1", "C2", "TMAX", "C3", "C4")
        }
    except KeyError as exc:
        raise RuntimeError(
            f"atmo_compensation actif : timestamp {exc.args[0]} manquant"
        ) from exc

    if target_time is None:
        raise RuntimeError(
            "atmo_compensation actif : timestamp capture manquant"
        )

    h = interpolate_altitude(target_time, tl, altitudes)
    facteur = facteur_atmospherique(h, float(observer_altitude))
    updated_speeds = None if speeds is None else list(speeds)
    slowest = shutter_min
    slowest_seconds = parse_shutterspeed(slowest)
    target_slowest = slowest_seconds * float(facteur)
    next_exposure = slowest_seconds * (2.0 ** step_ev)

    while next_exposure < target_slowest:
        if updated_speeds is not None:
            updated_speeds.append(_format_seconds_as_speed(next_exposure))
        next_exposure *= 2.0 ** step_ev

    added = target_slowest > slowest_seconds
    if added:
        if updated_speeds is not None:
            updated_speeds.append(_format_seconds_as_speed(next_exposure))
        else:
            shutter_min = _format_seconds_as_speed(next_exposure)

    return updated_speeds, (shutter_min, shutter_max, step_ev), added

def _capture_intent(speeds, phase, target_time, deadline=None):
    """Build the brand-neutral intent for one absolute scheduler slot."""
    capture = speeds if isinstance(speeds, dict) else {"speeds": speeds}
    configured_speeds = capture.get("speeds")
    if isinstance(configured_speeds, dict):
        shutter_max = configured_speeds.get("fastest")
        shutter_min = configured_speeds.get("slowest")
        step_ev = configured_speeds.get("step_il")
        intent_speeds = None
    elif configured_speeds:
        shutter_min = shutter_max = step_ev = None
        intent_speeds = [str(speed) for speed in configured_speeds]
    else:
        shutter_min = capture.get("shutter_min")
        shutter_max = capture.get("shutter_max")
        step_ev = capture.get("step_ev", capture.get("step_il"))
        intent_speeds = None

    if intent_speeds is None and (shutter_min is None or shutter_max is None):
        raise RuntimeError(
            "construction CaptureIntent impossible: bornes shutter_min/shutter_max manquantes"
        )
    if intent_speeds is None and step_ev is None:
        step_ev = 1.0
    try:
        use_atmo = bool(
            capture_canonical["exposure_correction"].get(
                "atmospheric_attenuation_enabled", False
            )
        )
        slowest_override_seconds = None

        if intent_speeds is not None:
            fastest, slowest, step_il, regular = _norm_plan(intent_speeds)
        else:
            fastest, slowest, step_il, regular = (
                str(shutter_max), str(shutter_min), float(step_ev), True
            )

        if use_atmo and regular:
            loc = _observer_location()
            if loc is None or loc.get("altitude_m") is None:
                raise RuntimeError(
                    "atmo_compensation actif : altitude observateur manquante"
                )
            alts = {
                name: astronomy(name) if circumstances else cfg.get(name)
                for name in (
                    "C1_alt_deg", "C2_alt_deg", "TMAX_alt_deg",
                    "C3_alt_deg", "C4_alt_deg",
                )
            }
            has_explicit_speeds = intent_speeds is not None
            intent_speeds, bounds, added = _extend_regular_ev_for_atmosphere(
                intent_speeds,
                slowest,
                fastest,
                step_il,
                target_time,
                _timeline,
                alts,
                loc.get("altitude_m"),
            )
            if not has_explicit_speeds and added:
                shutter_min = bounds[0]

    except Exception as exc:
        raise RuntimeError(f"construction CaptureIntent impossible: {exc}") from exc

    return CaptureIntent(
        shutter_min=shutter_min, shutter_max=shutter_max, step_ev=step_ev,
        speeds=intent_speeds, phase=phase, target_time=target_time,
        deadline=deadline, overflow_policy="truncate",
        origin=phase, request_id=uuid.uuid4().hex,
    )


class _SimulationCameraService:
    """Shutter-free service implementing the same engine contract in simulation."""
    def apply_phase_settings(self, aperture=None, iso=None):
        _log(f"INFO scheduler phase_settings aperture={aperture} iso={iso}")

    def prepare_capture(self, intent):
        speeds = intent.speeds
        if speeds is None:
            fastest_s = parse_shutterspeed(intent.shutter_max)
            slowest_s = parse_shutterspeed(intent.shutter_min)
            step_ev = float(intent.step_ev if intent.step_ev is not None else 1.0)
            exposures = []
            current = fastest_s
            while current < slowest_s:
                exposures.append(current)
                current *= 2.0 ** step_ev
            exposures.append(slowest_s)
            speeds = [_format_seconds_as_speed(value) for value in exposures]
        return PreparedCapture(
            token=(intent, speeds), estimated_total_s=sum(parse_shutterspeed(s) for s in speeds),
            exposures_s=[parse_shutterspeed(s) for s in speeds],
            planned_count=len(speeds), plugin_name="simulation",
        )

    def trigger_prepared(self, prepared, deadline=None):
        intent, speeds = prepared.token
        frames = _sim_capture_speed_list(
            speeds, 1, intent.target_time, deadline,
        )
        return CaptureResult(frames=frames, planned=len(speeds), detail="simulation")

    def close(self):
        pass


def _c3_trigger_deadline(prepared, target, c3):
    """Return the hard deadline for an admissible sequence, else ``None``."""
    estimated_total_s = getattr(prepared, "estimated_total_s", None)
    exposures_s = getattr(prepared, "exposures_s", None)

    # Plugins legacy/non instrumentés : conserver le comportement historique.
    # Aucune grâce après C3 n'est accordée sans estimations fiables, mais la
    # séquence reste autorisée avec une deadline stricte à C3.
    if estimated_total_s is None or exposures_s is None:
        return c3

    try:
        estimated_total_s = float(estimated_total_s)
        exposures_s = [float(exposure) for exposure in exposures_s]
    except (TypeError, ValueError):
        _log("c3_adaptation=refused reason=invalid_estimate")
        return None
    exposure_total_s = sum(exposures_s)
    if (not math.isfinite(estimated_total_s)
            or any(not math.isfinite(exposure) for exposure in exposures_s)
            or estimated_total_s < exposure_total_s
            or any(exposure < 0.0 for exposure in exposures_s)):
        _log("c3_adaptation=refused reason=invalid_estimate")
        return None

    estimated_end = target + timedelta(seconds=estimated_total_s)
    if estimated_end <= c3:
        return c3

    grace_deadline = c3 + timedelta(seconds=C3_OVERFLOW_GRACE_S)
    if estimated_end > grace_deadline:
        return None

    # Treat non-exposure time in the estimate as preceding the exposures. This
    # conservatively identifies every exposure that may still be active at C3.
    elapsed_s = estimated_total_s - exposure_total_s
    crossing_exposures = []
    seconds_to_c3 = (c3 - target).total_seconds()
    for exposure_s in exposures_s:
        exposure_end_s = elapsed_s + exposure_s
        if exposure_end_s > seconds_to_c3:
            crossing_exposures.append(exposure_s)
        elapsed_s = exposure_end_s

    if (not crossing_exposures
            or any(exposure > SHORT_EXPOSURE_MAX_S
                   for exposure in crossing_exposures)):
        return None
    return grace_deadline


def _prepare_totality_sub_bracket(camera_service, speeds, target, c3):
    """Select the largest plugin-accepted sub-bracket admissible near C3."""
    capture = speeds if isinstance(speeds, dict) else {"speeds": speeds}
    configured = capture.get("speeds")

    # Ranged plans cannot be subset before the plugin expands them. Preserve
    # their existing preparation path; explicit plans use DEV-001 selection.
    if not isinstance(configured, (list, tuple)) or len(configured) < 2:
        intent = _capture_intent(speeds, "phase2", target, c3)
        prepared = camera_service.prepare_capture(intent)
        trigger_deadline = _c3_trigger_deadline(prepared, target, c3)
        if isinstance(configured, (list, tuple)) and trigger_deadline is not None:
            _log(f"c3_adaptation=full planned={len(configured)} "
                 f"selected={len(configured)}")
        return prepared, trigger_deadline

    total_size = len(configured)
    for candidate_size in range(total_size, 1, -1):
        indices = _select_uniform_indices(configured, candidate_size)
        candidate = dict(capture)
        candidate["speeds"] = [configured[index] for index in indices]
        try:
            intent = _capture_intent(candidate, "phase2", target, c3)
            prepared = camera_service.prepare_capture(intent)
        except Exception as exc:
            _log(f"INFO scheduler phase=phase2 target={target.isoformat()} "
                 f"candidate_m={candidate_size} accepted=false "
                 f"reason=prepare error={type(exc).__name__}: {exc}")
            continue

        trigger_deadline = _c3_trigger_deadline(prepared, target, c3)
        if trigger_deadline is not None:
            _log(f"INFO scheduler phase=phase2 target={target.isoformat()} "
                 f"candidate_m={candidate_size} accepted=true")
            outcome = "full" if candidate_size == total_size else "reduced"
            _log(f"c3_adaptation={outcome} planned={total_size} "
                 f"selected={candidate_size}")
            return prepared, trigger_deadline

        _log(f"INFO scheduler phase=phase2 target={target.isoformat()} "
             f"candidate_m={candidate_size} accepted=false "
             "reason=duration_or_exposure_policy")

    # M=1: consider the original exposures from longest to shortest. Account
    # for a late slot when applying the same C3 hard-deadline policy used for
    # every prepared capture.
    singles = sorted(configured, key=parse_shutterspeed, reverse=True)
    for speed in singles:
        candidate = dict(capture)
        candidate["speeds"] = [speed]
        try:
            intent = _capture_intent(candidate, "phase2", target, c3)
            prepared = camera_service.prepare_capture(intent)
        except Exception as exc:
            _log(f"INFO scheduler phase=phase2 target={target.isoformat()} "
                 f"candidate_m=1 speed={speed} accepted=false "
                 f"reason=prepare error={type(exc).__name__}: {exc}")
            continue

        effective_start = max(target, now())
        trigger_deadline = _c3_trigger_deadline(prepared, effective_start, c3)
        if trigger_deadline is not None:
            _log(f"INFO scheduler phase=phase2 target={target.isoformat()} "
                 f"candidate_m=1 speed={speed} accepted=true")
            _log(f"c3_adaptation=reduced planned={total_size} selected=1")
            return prepared, trigger_deadline

        _log(f"INFO scheduler phase=phase2 target={target.isoformat()} "
             f"candidate_m=1 speed={speed} accepted=false "
             "reason=duration_or_exposure_policy")

    _log("c3_adaptation=refused reason=no_admissible_subset")
    return None, None


def _run_continuous_totality(
    camera_service,
    capture,
    phase_start,
    phase_end,
    aperture=None,
    iso=None,
    photo_num_start=1,
):
    """Capture totality brackets back-to-back using the modern prepared contract."""
    _log("INFO scheduler phase=phase2 mode=continuous")

    camera_service.apply_phase_settings(aperture=aperture, iso=iso)

    photo_num = photo_num_start
    bracket = 1

    while now() < phase_end:
        target = now()

        try:
            prepared, trigger_deadline = _prepare_totality_sub_bracket(
                camera_service,
                capture,
                target,
                phase_end,
            )
        except Exception as exc:
            _log(
                f"ERROR scheduler phase=phase2 mode=continuous "
                f"stage=prepare error={type(exc).__name__}: {exc}"
            )
            break

        if prepared is None:
            _usb_wait_or_hold(
                camera_service,
                phase_end,
                deadline=phase_end,
            )
            break

        trigger_started = now()

        try:
            result = camera_service.trigger_prepared(
                prepared,
                deadline=trigger_deadline,
            )
        except Exception as exc:
            _log(
                f"ERROR scheduler phase=phase2 mode=continuous "
                f"stage=trigger error={type(exc).__name__}: {exc}"
            )
            break

        frames = int(getattr(result, "frames", 0) or 0)

        if frames > 0:
            _log(
                f"{Colors.YELLOW}Bracket {bracket} "
                f"[{frames} photos]{Colors.RESET}"
            )
            photo_num += frames
            bracket += 1
            _watchdog_write("shooting", target)

        trigger_finished = now()

        # Sécurité anti busy-loop uniquement.
        # En fonctionnement normal la capture matérielle fait progresser le temps,
        # donc aucun délai artificiel n'est ajouté entre deux brackets.
        if frames <= 0 or trigger_finished <= trigger_started:
            if now() < phase_end:
                sleep_sim(0.05)

    return photo_num


def _run_absolute_grid(camera_service, phase, speeds, first_target, phase_end,
                       interval_s, aperture=None, iso=None, deadline=None,
                       photo_num_start=1):
    """Prepare early and trigger slots on an immutable absolute time grid."""
    camera_service.apply_phase_settings(aperture=aperture, iso=iso)
    target = first_target
    bracket = 1
    photo_num = photo_num_start
    total = estimatedPhoto(first_target, phase_end, interval_s)

    while target < phase_end:
        skipped = 0
        while target + timedelta(seconds=interval_s) <= now() and target < phase_end:
            target += timedelta(seconds=interval_s)
            skipped += 1
        if skipped:
            _log(f"WARNING scheduler phase={phase} missed_slots={skipped} next_target={target.isoformat()}")
        if target >= phase_end:
            break
        prep_start = time.perf_counter()
        try:
            if phase == "phase2" and deadline is not None:
                prepared, trigger_deadline = _prepare_totality_sub_bracket(
                    camera_service, speeds, target, deadline,
                )
                if prepared is None:
                    _log(f"WARNING scheduler phase={phase} target={target.isoformat()} "
                         "c3_overflow=refused reason=no_admissible_subset")
                    target += timedelta(seconds=interval_s)
                    continue
            else:
                intent = _capture_intent(speeds, phase, target, deadline)
                prepared = camera_service.prepare_capture(intent)
                trigger_deadline = deadline
        except Exception as exc:
            _log(f"ERROR scheduler phase={phase} target={target.isoformat()} "
                 f"stage=prepare error={type(exc).__name__}: {exc}")
            target += timedelta(seconds=interval_s)
            continue
        if phase == "phase2" and deadline is not None:
            estimated_total_s = getattr(prepared, "estimated_total_s", None)
            exposures_s = getattr(prepared, "exposures_s", None)

            if trigger_deadline is None:
                _log(f"WARNING scheduler phase={phase} target={target.isoformat()} "
                     "c3_overflow=refused reason=duration_or_exposure_policy")
                target += timedelta(seconds=interval_s)
                continue

            if estimated_total_s is None or exposures_s is None:
                _log(f"INFO scheduler phase={phase} target={target.isoformat()} "
                     "c3_overflow=legacy_strict "
                     f"hard_deadline={trigger_deadline.isoformat()}")
            else:
                estimated_end = target + timedelta(
                    seconds=float(estimated_total_s)
                )
                if estimated_end > deadline:
                    _log(f"INFO scheduler phase={phase} target={target.isoformat()} "
                         f"c3_overflow=accepted estimated_end={estimated_end.isoformat()} "
                         f"hard_deadline={trigger_deadline.isoformat()}")
        prep_end = time.perf_counter()
        wait_start = prep_end
        _log(f"INFO scheduler phase={phase} target={target.isoformat()} "
             f"prep_start={prep_start:.6f} prep_end={prep_end:.6f} wait_start={wait_start:.6f}")
        _usb_wait_or_hold(camera_service, target, deadline=phase_end)
        if now() >= phase_end:
            break

        shutter_cmd = time.perf_counter()
        delay_s = max(0.0, (now() - target).total_seconds())
        if delay_s > 0:
            _log(f"WARNING scheduler phase={phase} target_delay_s={delay_s:.6f}")
        try:
            result = camera_service.trigger_prepared(
                prepared, deadline=trigger_deadline,
            )
        except Exception as exc:
            shutter_return = time.perf_counter()
            _log(f"ERROR scheduler phase={phase} target={target.isoformat()} "
                 f"stage=trigger shutter_cmd={shutter_cmd:.6f} "
                 f"shutter_return={shutter_return:.6f} "
                 f"trigger_minus_target_s={delay_s:.6f} "
                 f"error={type(exc).__name__}: {exc}")
            target += timedelta(seconds=interval_s)
            continue
        shutter_return = time.perf_counter()
        # The plugin owns event retrieval and settling; its return is the closest
        # engine-observable point for both completions.
        events_retrieval_complete = settle_complete = shutter_return
        total_duration = settle_complete - prep_start
        _log(f"INFO scheduler phase={phase} shutter_cmd={shutter_cmd:.6f} "
             f"shutter_return={shutter_return:.6f} "
             f"events_retrieval_complete={events_retrieval_complete:.6f} "
             f"settle_complete={settle_complete:.6f} total_duration_s={total_duration:.6f} "
             f"trigger_minus_target_s={delay_s:.6f}")

        if result.frames:
            _watchdog_write("shooting", target)
            _log(f"{Colors.YELLOW}Bracket {bracket}/{total} [{result.frames} photos]{Colors.RESET}")
            bracket += 1
            photo_num += result.frames

        target += timedelta(seconds=interval_s)
        skipped = 0
        while target + timedelta(seconds=interval_s) <= now() and target < phase_end:
            target += timedelta(seconds=interval_s)
            skipped += 1
        if skipped:
            _log(f"WARNING scheduler phase={phase} missed_slots={skipped} next_target={target.isoformat()}")
        if target < phase_end:
            _log(f"{Colors.CYAN}⏱ Prochaine photo : {target.strftime('%H:%M:%S')}{Colors.RESET}")
    return photo_num


def capture_speed_list(camera_service, speeds, photo_num_start, next_shot_time, deadline=None):
    """Compatibility adapter for callers outside the refactored runtime loops."""
    try:
        if _sim_mode:
            return _sim_capture_speed_list(
                speeds, photo_num_start, next_shot_time, deadline,
            )
        slowest_override_seconds = None
        _, slowest, _, regular = _norm_plan([str(speed) for speed in speeds])
        use_atmo = bool(
            capture_canonical["exposure_correction"].get(
                "atmospheric_attenuation_enabled", False
            )
        )
        if use_atmo and regular:
            loc = _observer_location()
            if not loc or loc.get("altitude_m") is None:
                raise RuntimeError("altitude observateur manquante")
            alts = {name: astronomy(name) if circumstances else cfg.get(name) for name in (
                "C1_alt_deg", "C2_alt_deg", "TMAX_alt_deg", "C3_alt_deg", "C4_alt_deg"
            )}
            if any(value is None for value in alts.values()):
                raise RuntimeError("altitudes de contact manquantes")
            altitude = interpolate_altitude(next_shot_time, _timeline, alts)
            slowest_override_seconds = (
                parse_shutterspeed(slowest)
                * facteur_atmospherique(altitude, float(loc["altitude_m"]))
            )
        result = camera_service.shoot_speed_list(
            speeds, photo_num_start=photo_num_start, deadline=deadline,
            slowest_override_seconds=slowest_override_seconds,
        )
        return result.frames if result is not None else 0
    except Exception as exc:
        _log(f"{Colors.RED}Erreur plugin caméra : {exc}{Colors.RESET}")
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


def _phase_is_future(phase_end):
    """True tant que la phase possède encore du temps futur à exécuter."""
    return now() < phase_end


def _first_future_grid_slot(first_target, interval_s, phase_end):
    """Retourne le premier slot de la grille qui n'est pas déjà passé.

    La grille absolue originale est conservée : aucune action passée
    n'est rattrapée et aucun décalage de timeline n'est introduit.
    """
    target = first_target
    current = now()

    while target < current and target < phase_end:
        target += timedelta(seconds=interval_s)

    return target


def main():
    """Main function to execute the eclipse photography sequence."""
    camera_service = None
    ipc_adapter = None
    try:
        _log(f"{Colors.PINK}#{Colors.RESET}")
        print(f"{Colors.PINK}# TOTAL SOLAR ECLIPSE AUTOMATIC SCRIPT - {titre}{Colors.RESET}")
        print(f"{Colors.PINK}#{Colors.RESET}")

        # ── Init horloge simulation ────────────────────────────────────────
        if _sim_mode:
            _runtime_clock.start_simulation(TSTART - timedelta(seconds=30))
            print(f"WARNING {Colors.PINK}⚡ SIMULATION ×{_sim_speed:.0f} | Heure virtuelle départ : {_runtime_clock.virt_start.strftime('%H:%M:%S')} | 1 seconde réelle = {_sim_speed:.0f}s virtuelles{Colors.RESET}")

        # ── Watchdog : diagnostic uniquement ─────────────────────────────
        # Un nouveau START ne reprend jamais à partir de trigger_state.json.
        # La position dans l'éclipse est déterminée exclusivement par l'heure
        # absolue courante. Toutes les actions passées sont abandonnées.
        prev_state = _watchdog_read()
        if prev_state and not _sim_mode:
            phase_prev = prev_state.get("phase")
            next_shot_prev = prev_state.get("next_shot_time")
            written_at = (
                prev_state.get("written_at_utc")
                or prev_state.get("written_at")
                or ""
            )
            _log(
                f"WARNING {Colors.ORANGE}⚠ WATCHDOG : ancien état détecté "
                f"(phase={phase_prev}, next_shot={next_shot_prev}, "
                f"écrit={str(written_at)[:19]}) — ignoré pour la reprise"
                f"{Colors.RESET}"
            )

        # ── Connexion caméra via CameraService / CameraPlugin ─────────────
        _log(f"{Colors.GREEN}### CLEAR CONNEXION TO CAMERA{Colors.RESET}")
        camera_service = None
        ipc_socket = os.environ.get("SET_CAMERA_IPC_SOCKET")
        if _sim_mode:
            _log(f"{Colors.PINK}⚡ SIM : accès matériel caméra totalement désactivé{Colors.RESET}")
            camera_service = _SimulationCameraService()
        elif ipc_socket:
            ipc_client = CameraIpcClient(
                ipc_socket,
                os.environ.get("SET_CAMERA_IPC_SESSION", ""),
                log_fn=_log,
            )
            ipc_client.ping()
            rig_snapshot = ipc_client.list_active_camera_rigs()
            _log(
                f"{Colors.GREEN}### CAMERA IPC RIGS "
                f"{rig_snapshot['rig_ids']}{Colors.RESET}"
            )
            ipc_adapter = FanoutCameraAdapter(ipc_client, log_fn=_log)
            camera_service = ipc_adapter
            camera_service.initialize(
                aperture=aperture_partial,
                iso=iso_partial,
            )
        else:
            if args.dry_run:
                _log(f"{Colors.PINK}🧪 DRY-RUN : chemin matériel caméra identique au mode réel{Colors.RESET}")
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

            fin_phase_1a = C2 - timedelta(seconds=diamond_ring_duration_s)
            fin_phase_3a = C3 + timedelta(seconds=diamond_ring_duration_s)

            # Un nouveau START se positionne uniquement d'après l'heure absolue.
            # Aucune phase passée et aucune photo passée ne sont rejouées.
            current = now()
            if current >= TEND:
                _log(
                    f"{Colors.ORANGE}⚠ START après TEND : "
                    f"séquence déjà terminée à {format_hms_ms(TEND)}"
                    f"{Colors.RESET}"
                )
            else:
                if current > TSTART:
                    _log(
                        f"{Colors.ORANGE}⚡ REPRISE TEMPORELLE : "
                        f"heure courante {format_hms_ms(current)} — "
                        f"toutes les actions antérieures sont ignorées."
                        f"{Colors.RESET}"
                    )

                ###
                ### PHASE 1a : START -> C1 -> C2-duree_diamond_ring
                ###
                if _phase_is_future(fin_phase_1a):
                    _log(f"{Colors.GREEN}# PHASE 1a : Start to C1 to C2-{diamond_ring_duration_s}s{Colors.RESET}")
                    _log(f"{Colors.BLUE}Camera Settings : Interval : {interval_partial}{Colors.RESET}")
                    _log(f"{Colors.BLUE}Camera Settings : Bracket vitesses : {speeds_partial}{Colors.RESET}")
                    _log(f"{Colors.BLUE}Camera Settings : Ouverture : {aperture_partial}{Colors.RESET}")

                    first_grid = calculer_temps_debut_sequence(
                        TSTART, TMAX, interval_partial
                    )
                    next_shot_time = _first_future_grid_slot(
                        first_grid,
                        interval_partial,
                        fin_phase_1a,
                    )

                    if next_shot_time < fin_phase_1a:
                        nbTotalBracket = estimatedPhoto(
                            next_shot_time,
                            fin_phase_1a,
                            interval_partial,
                        )
                        _log(f"{Colors.YELLOW}Start Capture (estimated number of brackets: {nbTotalBracket}){Colors.RESET}")
                        _log(f"{Colors.CYAN}⏱ Prochaine photo : {next_shot_time.strftime('%H:%M:%S')}{Colors.RESET}")

                        _run_absolute_grid(
                            camera_service,
                            "phase1a",
                            _partial_capture,
                            next_shot_time,
                            fin_phase_1a,
                            interval_partial,
                            aperture_partial,
                            iso_partial,
                            deadline=fin_phase_1a,
                        )

                ###
                ### PHASE 1b : DIAMOND RING -- C2-duree_diamond_ring -> C2
                ###
                if _phase_is_future(C2):
                    _log(f"{Colors.GREEN}# PHASE 1b : DIAMOND RING -- C2-{diamond_ring_duration_s}s -> C2{Colors.RESET}")
                    _log(f"{Colors.BLUE}Camera Settings : Interval : {interval_diamond_ring}{Colors.RESET}")
                    _log(f"{Colors.BLUE}Camera Settings : Bracket vitesses : {speeds_diamond_ring}{Colors.RESET}")
                    _log(f"{Colors.BLUE}Camera Settings : Ouverture : {aperture_diamond}{Colors.RESET}")

                    first_grid = calculer_temps_debut_sequence(
                        fin_phase_1a,
                        C2,
                        interval_diamond_ring,
                    )
                    next_shot_time = _first_future_grid_slot(
                        first_grid,
                        interval_diamond_ring,
                        C2,
                    )

                    if next_shot_time < C2:
                        nbTotalBracket = estimatedPhoto(
                            next_shot_time,
                            C2,
                            interval_diamond_ring,
                        )
                        _log(f"{Colors.YELLOW}Start Capture (estimated number of brackets: {nbTotalBracket}){Colors.RESET}")
                        _log(f"{Colors.CYAN}⏱ Prochaine photo : {next_shot_time.strftime('%H:%M:%S')}{Colors.RESET}")

                        _run_absolute_grid(
                            camera_service,
                            "phase1b",
                            _diamond_capture,
                            next_shot_time,
                            C2,
                            interval_diamond_ring,
                            aperture_diamond,
                            iso_diamond_ring,
                            deadline=C2,
                        )

                ###
                ### PHASE 2 : TOTALITY -- C2 -> C3
                ###
                if _phase_is_future(C3):
                    _log(f"{Colors.GREEN}# PHASE 2 - TOTALITY -- C2 -> C3{Colors.RESET}")
                    _log(f"{Colors.YELLOW}Capture{Colors.RESET}")

                    _log(
                        f"{Colors.BLUE}Sécurité C3 : débordement court autorisé "
                        f"jusqu'à +{C3_OVERFLOW_GRACE_S:g}s pour les poses "
                        f"≤ {SHORT_EXPOSURE_MAX_S:g}s ({format_hms_ms(C3)})"
                        f"{Colors.RESET}"
                    )

                    if interval_totality < 0:
                        _log(
                            f"{Colors.RED}Intervalle totalité invalide : "
                            f"{interval_totality}{Colors.RESET}"
                        )
                    elif interval_totality == 0:
                        _run_continuous_totality(
                            camera_service,
                            _totality_capture,
                            C2,
                            C3,
                            aperture_totality,
                            iso_totality,
                        )
                    else:
                        first_totality = _first_future_grid_slot(
                            C2,
                            float(interval_totality),
                            C3,
                        )
                        if first_totality < C3:
                            _run_absolute_grid(
                                camera_service,
                                "phase2",
                                _totality_capture,
                                first_totality,
                                C3,
                                float(interval_totality),
                                aperture_totality,
                                iso_totality,
                                deadline=C3,
                            )

                ###
                ### PHASE 3a : DIAMOND RING -- C3 -> C3+duree_diamond_ring
                ###
                if _phase_is_future(fin_phase_3a):
                    _log(f"{Colors.GREEN}# PHASE 3a : DIAMOND RING -- C3 -> C3+{diamond_ring_duration_s}s{Colors.RESET}")
                    _log(f"{Colors.BLUE}Camera Settings : Interval : {interval_diamond_ring}{Colors.RESET}")
                    _log(f"{Colors.BLUE}Camera Settings : Bracket vitesses : {speeds_diamond_ring}{Colors.RESET}")
                    _log(f"{Colors.BLUE}Camera Settings : Ouverture : {aperture_diamond}{Colors.RESET}")

                    next_shot_time = _first_future_grid_slot(
                        C3,
                        interval_diamond_ring,
                        fin_phase_3a,
                    )

                    if next_shot_time < fin_phase_3a:
                        nbTotalBracket = estimatedPhoto(
                            next_shot_time,
                            fin_phase_3a,
                            interval_diamond_ring,
                        )
                        _log(f"{Colors.YELLOW}Start Capture (estimated number of brackets: {nbTotalBracket}){Colors.RESET}")
                        _log(f"{Colors.CYAN}⏱ Prochaine photo : {next_shot_time.strftime('%H:%M:%S')}{Colors.RESET}")

                        _run_absolute_grid(
                            camera_service,
                            "phase3a",
                            _diamond_capture,
                            next_shot_time,
                            fin_phase_3a,
                            interval_diamond_ring,
                            aperture_diamond,
                            iso_diamond_ring,
                            deadline=fin_phase_3a,
                        )

                ###
                ### PHASE 3b : C3+duree_diamond_ring -> C4 -> TEND
                ###
                if _phase_is_future(TEND):
                    _log(f"{Colors.GREEN}# Phase 3b - C3+{diamond_ring_duration_s}s -> C4 -> TEND{Colors.RESET}")
                    _log(f"{Colors.BLUE}Camera Settings : Interval : {interval_partial}{Colors.RESET}")
                    _log(f"{Colors.BLUE}Camera Settings : Bracket vitesses : {speeds_partial}{Colors.RESET}")

                    first_grid = TMAX + timedelta(seconds=interval_partial)
                    while first_grid < fin_phase_3a:
                        first_grid += timedelta(seconds=interval_partial)

                    next_shot_time = _first_future_grid_slot(
                        first_grid,
                        interval_partial,
                        TEND,
                    )

                    if next_shot_time < TEND:
                        nbTotalBracket = estimatedPhoto(
                            next_shot_time,
                            TEND,
                            interval_partial,
                        )
                        _log(f"{Colors.YELLOW}Start Capture (estimated number of brackets: {nbTotalBracket}){Colors.RESET}")
                        _log(f"{Colors.CYAN}⏱ Prochaine photo : {next_shot_time.strftime('%H:%M:%S')}{Colors.RESET}")

                        _run_absolute_grid(
                            camera_service,
                            "phase3b",
                            _partial_capture,
                            next_shot_time,
                            TEND,
                            interval_partial,
                            aperture_partial,
                            iso_partial,
                            deadline=TEND,
                        )

            _watchdog_clear()
            _log(f"{Colors.GREEN}✅ Séquence terminée normalement.{Colors.RESET}")

        else:
            # ECLIPSE PARTIELLE DE SOLEIL
            #
            # Même politique : l'heure absolue décide de la reprise et les
            # slots passés sont définitivement abandonnés.

            if now() >= TEND:
                _log(
                    f"{Colors.ORANGE}⚠ START après TEND : "
                    f"séquence déjà terminée à {format_hms_ms(TEND)}"
                    f"{Colors.RESET}"
                )
            else:
                if now() > TSTART:
                    _log(
                        f"{Colors.ORANGE}⚡ REPRISE TEMPORELLE : "
                        f"heure courante {format_hms_ms(now())} — "
                        f"toutes les actions antérieures sont ignorées."
                        f"{Colors.RESET}"
                    )

                _log(f"{Colors.GREEN}# PHASE UNIQUE : Start to C1 to C4 to END{Colors.RESET}")
                _log(f"{Colors.BLUE}Camera Settings : Interval : {interval_partial}{Colors.RESET}")
                _log(f"{Colors.BLUE}Camera Settings : Shutterspeed : {shutterspeed_partial}{Colors.RESET}")

                first_grid = calculer_temps_debut_sequence(
                    TSTART,
                    TMAX,
                    interval_partial,
                )
                next_shot_time = _first_future_grid_slot(
                    first_grid,
                    interval_partial,
                    TEND,
                )

                if next_shot_time < TEND:
                    nbTotalBracket = estimatedPhoto(
                        next_shot_time,
                        TEND,
                        interval_partial,
                    )
                    _log(f"{Colors.YELLOW}Start Capture (estimated number of brackets: {nbTotalBracket}){Colors.RESET}")
                    _log(f"{Colors.CYAN}⏱ Prochaine photo : {next_shot_time.strftime('%H:%M:%S')}{Colors.RESET}")

                    _run_absolute_grid(
                        camera_service,
                        "partial",
                        _partial_capture,
                        next_shot_time,
                        TEND,
                        interval_partial,
                        aperture_partial,
                        iso_partial,
                        deadline=TEND,
                    )

            _watchdog_clear()
    except KeyboardInterrupt:
        _log("INFO Script stopped by user.")
    except Exception as e:
        _log(f"{Colors.RED}Unexpected error: {e}{Colors.RESET}")
    finally:
        _shutdown_audio_threads()
        if ipc_adapter is not None:
            ipc_adapter.close()
        elif camera_service is not None:
            camera_service.close()
        _log(f"{Colors.GREEN}End of the script.{Colors.RESET}")

if __name__ == "__main__":
    main()
