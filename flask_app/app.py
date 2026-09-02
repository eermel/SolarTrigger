"""
SolarEclipse Portal — app.py
Flask + Flask-SocketIO
Version : 6.0.0
Date    : 2026-08-19

Changelog :
  3.0.68 - eclipse_trigger v3.9.22 : countdown en alertes individuelles
  3.0.67 - eclipse_trigger v3.9.21 : fix pkill + camera UnboundLocalError
  2.53 - Suppression des routes /api/debug/logs et /api/debug/logs/clear

Persistance :
  state.json        → GPS, éclipse calculée, statuts — survit au reboot Pi
  logs_buffer.jsonl → ring buffer 500 dernières lignes de log
  todayeclipse.json → généré par eclipse_calculator_py.py

À la reconnexion d'un client :
  - État GPS complet restauré
  - Éclipse calculée restaurée
  - 500 dernières lignes de log renvoyées
  - Heure locale + UTC en temps réel
"""

import json
import re
from copy import deepcopy

# Détecte la couleur ANSI puis la supprime — mapping vers les niveaux CSS du portail
_ANSI_ESCAPE = re.compile(r'\x1b\[[0-9;]*m')

def _clean(line):
    """Supprime les codes couleur ANSI d'une ligne de log."""
    return _ANSI_ESCAPE.sub('', line)

def _ansi_to_level(line):
    """
    Lit le premier code ANSI de la ligne et retourne le niveau CSS correspondant.
    Mapping basé sur les Colors du trigger :
      \033[1;32m  vert     → success   (phases, connexion, init)
      \033[1;34m  bleu     → gps       (Camera Settings)
      \033[1;33m  jaune    → warning   (Start Capture, compteurs)
      \033[38;2;255;127;0m orange → orange  (sons, watchdog reprise)
      \033[38;5;198m pink  → pink     (messages temps C1/C2...)
      \033[1;31m  rouge    → error    (erreurs)
      (rien)                → info     (texte neutre)
    """
    m = re.search(r'\x1b\[([0-9;]*)m', line)
    if not m:
        return "info"
    code = m.group(1)
    if code in ("1;32", "32"):       return "success"   # vert
    if code in ("1;34", "34"):       return "gps"       # bleu
    if code in ("1;33", "33"):       return "warning"   # jaune
    if "255;127;0" in code:          return "orange"    # orange
    if code in ("38;5;198",):        return "pink"      # rose
    if code in ("1;31", "31"):       return "error"     # rouge
    return "info"
import logging
import os
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime, timezone, timedelta
from functools import wraps
from pathlib import Path
from types import SimpleNamespace

try:
    import gphoto2 as gp
except ModuleNotFoundError:
    gp = None
from flask import Flask, jsonify, request, send_from_directory
from flask_socketio import SocketIO, emit

def calculate_timezone_from_coords(lat, lon, eclipse_date=None):
    """
    Calcule le timezone courant (DST inclus) à partir des coordonnées GPS.
    eclipse_date : datetime ou str ISO (ex: '2026-08-12') — si fourni, le DST
                   est calculé à la DATE DE L'ÉCLIPSE et non à la date système.
                   Cela garantit que le JSON sauvegardé aujourd'hui sera correct
                   le jour J, même si l'heure d'été a changé entre les deux.
    Utilise timezonefinder si disponible (précis, DST correct).
    Sinon, fallback sur une approximation par longitude + règles DST européennes.
    Retourne l'offset total en heures (ex: 2 pour UTC+2, -5 pour UTC-5).
    """
    # Résoudre la date de référence pour le calcul DST
    if eclipse_date is not None:
        if isinstance(eclipse_date, str):
            try:
                ref_date = datetime.strptime(eclipse_date[:10], "%Y-%m-%d").replace(
                    tzinfo=timezone.utc)
            except ValueError:
                ref_date = datetime.now(timezone.utc)
        elif isinstance(eclipse_date, datetime):
            ref_date = eclipse_date if eclipse_date.tzinfo else \
                       eclipse_date.replace(tzinfo=timezone.utc)
        else:
            ref_date = datetime.now(timezone.utc)
    else:
        ref_date = datetime.now(timezone.utc)

    # ── Tentative avec timezonefinder (pip install timezonefinder) ────────────
    try:
        from timezonefinder import TimezoneFinder
        import pytz
        tf = TimezoneFinder()
        tz_name = tf.timezone_at(lat=lat, lng=lon)
        if tz_name:
            tz = pytz.timezone(tz_name)
            # Localiser à la DATE DE L'ÉCLIPSE pour le bon DST
            ref_local = ref_date.astimezone(tz)
            offset_h  = ref_local.utcoffset().total_seconds() / 3600
            src = "éclipse" if eclipse_date is not None else "système"
            log.info(f"Timezone : {tz_name} → UTC{offset_h:+.1f} "
                     f"(DST calculé à la date {src} : {ref_date.strftime('%Y-%m-%d')})")
            return offset_h
    except ImportError:
        pass  # timezonefinder non installé → fallback
    except Exception as e:
        log.warning(f"timezonefinder erreur : {e} → fallback longitude")

    # ── Fallback : approximation longitude + DST européen ────────────────────
    base_offset = round(lon / 15.0)
    base_offset = max(-12, min(14, base_offset))

    def _last_sunday(year, month):
        """Retourne la date du dernier dimanche du mois donné."""
        import calendar
        last_day = calendar.monthrange(year, month)[1]
        d = datetime(year, month, last_day, tzinfo=timezone.utc)
        offset = (d.weekday() + 1) % 7   # 0 = dimanche
        return d - timedelta(days=offset)

    # DST Europe : actif du dernier dimanche de mars 01:00 UTC
    #              au dernier dimanche d'octobre 01:00 UTC
    dst_start = _last_sunday(ref_date.year, 3).replace(hour=1)
    dst_end   = _last_sunday(ref_date.year, 10).replace(hour=1)
    dst_active = dst_start <= ref_date < dst_end

    src = "éclipse" if eclipse_date is not None else "système"
    log.info(f"Timezone fallback longitude : UTC{base_offset:+d}, "
             f"DST {'actif' if dst_active else 'inactif'} "
             f"(date {src} : {ref_date.strftime('%Y-%m-%d')})")

    # Europe (longitude -10° à +40°, latitude > 35°)
    if -10 <= lon <= 40 and lat > 35:
        winter_offset = 1 if lon < 22 else 2
        return winter_offset + (1 if dst_active else 0)

    # Amérique du Nord approximative
    if -130 <= lon <= -60 and 25 <= lat <= 70:
        return base_offset + (1 if dst_active else 0)

    # Reste du monde : pas de DST
    return base_offset

# ── Chemins ────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent

# Le package source place app.py dans flask_app/, tandis que l'installation
# de production place app.py directement à la racine applicative.
PROJECT_DIR = (
    BASE_DIR.parent
    if BASE_DIR.name == "flask_app"
    else BASE_DIR
)

TRIGGER_DIR    = PROJECT_DIR
SCRIPTS_DIR    = PROJECT_DIR / "scripts"
TRIGGER_SCRIPT = SCRIPTS_DIR / "eclipse_trigger.py"
TOTALITY_ONLY_SCRIPT = SCRIPTS_DIR / "totality_only.py"
CALC_SCRIPT    = SCRIPTS_DIR / "eclipse_calculator_py.py"
GPS_SCRIPT     = SCRIPTS_DIR / "gps_sync.py"
GPS_CONFIG_FILE = TRIGGER_DIR / "configs" / "gps_default.json"
MOUNT_CONFIG_FILE = TRIGGER_DIR / "configs" / "mount_default.json"
JSON_FILE      = TRIGGER_DIR / "todayeclipse.json"
EVENTS_FILE    = TRIGGER_DIR / "sound_events.jsonl"
SOUNDS_DIR     = TRIGGER_DIR / "Sounds"
STATIC_DIR     = BASE_DIR / "static"
STATIC_SOUNDS  = STATIC_DIR / "sounds"

# Fichiers de persistance
STATE_FILE      = BASE_DIR / "state.json"        # état GPS + éclipse + statuts
LOGS_BUFFER_FILE = BASE_DIR / "logs_buffer.jsonl" # ring buffer logs

LOG_BUFFER_SIZE = 500   # lignes conservées en mémoire et sur disque

# Backend v6 : Flask est un adaptateur HTTP/SocketIO.
if str(TRIGGER_DIR) not in sys.path:
    sys.path.insert(0, str(TRIGGER_DIR))
from backend.state_store import StateStore
from backend.event_log import EventLog
from backend.gps_controller import GpsController
from backend.devices import CATEGORIES as DEVICE_CATEGORIES
from backend.devices import detect_all, normalize_selection, ttl_expired
from backend.device_identity import identity_key
from backend import rig_trace
from backend.rig_trace_log import get_default_log
from backend.device_inventory import (
    build_display_labels,
    get_cached_inventory,
    refresh_inventory,
)
from backend.eclipse_engine import loader as eclipse_loader
from backend.preview_context import load_eclipse_context
from backend.atmo import interpolate_altitude
from backend.camera_model_resolution import resolve_sensor_entry
from backend.sensor_db import load_sensor_db
from backend.preview_materializer import (
    PreviewMaterializationError,
    apply_atmos_if_enabled,
    assemble_exposures_s,
    build_exposure_diff_lines,
    compute_iso_and_corrections,
    expand_executable_shutters,
    format_photo_shutter,
    normalize_intent_plan,
    resolve_policy,
)
from backend.motion_exposure_policy import (
    DEFAULT_SENSOR_DB_PATH,
    compute_motion_exposure_ceiling,
    materialize_exposure_plan,
)
from backend.preview_request import validate_payload as validate_preview_request
from backend.rig_config import (
    atmospheric_attenuation_enabled,
    canonical_rig_defaults,
    save as save_rig_config,
    validate as validate_rig_config,
)
from backend.rig_manager import RigManager
from backend.sequencer_plan_service import (
    SequencerCompileError,
    compile_execution_plan_from_files,
)

from backend.rig_runtime import (
    get_rig_manager,
    load_rig_configuration,
    normalize_rigs_for_ui,
    reload_rig_manager,
)
from backend.camera_worker_runtime import get_camera_worker_runtime
from backend.focuser_worker_runtime import get_focuser_worker_runtime
from backend.generic_worker import BusyDeviceError
from backend.mount_worker_runtime import get_mount_worker_runtime
from backend.trigger_service import TriggerService, TriggerValidationError
from backend.timezone_service import calculate_timezone_from_coords as _backend_timezone
from services.camera_service import CameraService, _normalized_speed_plan
from services.focuser_service import FocuserService
from services.mount_service import MountService
from plugins.mount.indi_client import IndiClientError

# ── Flask ──────────────────────────────────────────────────────────────────────
app = Flask(__name__, static_folder=str(STATIC_DIR),
            template_folder=str(BASE_DIR / "templates"))
app.config["SECRET_KEY"] = "solareclipse2026"
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading",
                    logger=False, engineio_logger=False)

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("solareclipse")

# ── Backend application services ───────────────────────────────────────────────
def _load_mount_plugin_config():
    """Load the optional mount plugin settings without blocking startup."""
    try:
        with MOUNT_CONFIG_FILE.open(encoding="utf-8") as config_file:
            mount_config = json.load(config_file)
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Impossible de charger mount_default.json : %s", exc)
        return {}

    if not isinstance(mount_config, dict):
        log.warning("Configuration mount_default.json invalide : objet JSON attendu")
        return {}
    if "plugin" in mount_config and not isinstance(mount_config["plugin"], str):
        log.warning("Configuration mount_default.json invalide : 'plugin' doit être une chaîne")
        return {}
    plugin_config = mount_config.get("plugin_config", {})
    if not isinstance(plugin_config, dict):
        log.warning("Configuration mount_default.json invalide : 'plugin_config' doit être un objet")
        return {}
    return plugin_config


def _create_mount_service():
    return MountService(
        _state_store,
        log_fn=lambda message: log.info(message),
        config=_load_mount_plugin_config(),
    )


_state_store = StateStore(STATE_FILE)
_state = _state_store.data
_state_lock = _state_store.lock
_focuser_service = FocuserService(
    _state_store, log_fn=lambda message: log.info(message)
)
_mount_service = _create_mount_service()
_event_log = EventLog(LOGS_BUFFER_FILE, LOG_BUFFER_SIZE,
                      emit_fn=lambda event, payload: socketio.emit(event, payload))
_log_buffer = _event_log.buffer
_log_lock = _event_log.lock
_calc_proc = None
_camera_sync_lock = threading.Lock()
_device_detection_lock = threading.Lock()
_device_detection_cache = {}
_DEVICE_DETECTION_TIMEOUTS = {
    "camera": 2.0,
    "gps": 2.0,
    "focuser": 2.0,
    "mount": 2.0,
}

def _load_state(): return _state_store.data
def _save_state():
    try: _state_store.save()
    except Exception as e: log.warning(f"Impossible de sauvegarder state.json : {e}")
def _load_log_buffer(): _event_log.reset()
def _append_log(text, level="info", source="system"): return _event_log.append(text, level, source)
def _trim_log_file(): _event_log.trim_forever()

# ══════════════════════════════════════════════════════════════════════════════
# ROUTES HTML
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/")
def index():
    return send_from_directory(str(BASE_DIR / "templates"), "index.html")

@app.route("/static/sounds/<path:filename>")
def serve_sound(filename):
    return send_from_directory(str(STATIC_SOUNDS), filename)

# ══════════════════════════════════════════════════════════════════════════════
# HELPERS TEMPS
# ══════════════════════════════════════════════════════════════════════════════

def _time_payload():
    """Retourne heure locale + UTC pour l'UI."""
    now_local = datetime.now()
    now_utc   = datetime.now(timezone.utc)
    return {
        "epoch_ms": int(now_utc.timestamp() * 1000),
        "backend_utc_epoch_ms": int(now_utc.timestamp() * 1000),
        "backend_local_epoch_ms": int(now_local.timestamp() * 1000),
        "local": {
            "time":     now_local.strftime("%H:%M:%S"),
            "date":     now_local.strftime("%Y-%m-%d"),
            "iso":      now_local.isoformat(),
            "label":    "Heure locale",
        },
        "utc": {
            "time":     now_utc.strftime("%H:%M:%S"),
            "date":     now_utc.strftime("%Y-%m-%d"),
            "iso":      now_utc.isoformat(),
            "label":    "UTC",
        },
    }


def _status_update_payload(base: dict) -> dict:
    """Ajoute les données communes à chaque mise à jour de statut."""
    try:
        rigs = normalize_rigs_for_ui(get_rig_manager())
    except Exception as exc:
        log.warning("Chargement des rigs impossible : %s", exc)
        rigs = [
            {"rig_id": rig_id, "name": f"RIG {rig_id}", "enabled": False}
            for rig_id in range(1, 5)
        ]
    return {
        **base,
        "time": _time_payload(),
        "rigs": rigs,
    }


def _devices_snapshot():
    """Return persisted selections enriched with the latest transient scan."""
    devices = _state_store.snapshot("devices") or {}
    with _device_detection_lock:
        detection = {name: dict(info)
                     for name, info in _device_detection_cache.items()}
    for name in DEVICE_CATEGORIES:
        selected = devices.setdefault(name, {"plugin": "none", "active": False})
        selected.update(detection.get(name, {}))
    return devices


def _detect_devices():
    """Refresh transient detection data without changing persisted state."""
    detected = detect_all(_DEVICE_DETECTION_TIMEOUTS)
    with _device_detection_lock:
        _device_detection_cache.clear()
        _device_detection_cache.update(
            {name: dict(detected.get(name, {})) for name in DEVICE_CATEGORIES}
        )
    return _devices_snapshot()


def _has_missing_device_selection(devices):
    """Return whether any category lacks a valid explicit selection."""
    for name in DEVICE_CATEGORIES:
        selection = devices.get(name)
        if not isinstance(selection, dict):
            return True
        if (selection.get("plugin") in (None, "", "none")
                or selection.get("active") is not True):
            return True
    return False


def require_device_active(category):
    """Return a conflict response when the selected device is inactive."""
    devices = _state_store.snapshot("devices") or {}
    device = devices.get(category) or {}
    if device.get("active") is not True:
        return jsonify({
            "error": f"Device category '{category}' is inactive.",
            "code": "DEVICE_INACTIVE",
            "category": category,
        }), 409
    return None


def _selected_device_plugin(category):
    """Return the configured plugin id for a device category."""
    devices = _state_store.snapshot("devices") or {}
    selection = devices.get(category) or {}
    return str(selection.get("plugin") or "none")


def _trigger_running_response():
    """Return a conflict response while hardware motion is unsafe."""
    trigger_state = _state_store.snapshot("trigger") or {}
    if trigger_state.get("running"):
        return jsonify({
            "error": "Mouvement du focuser interdit pendant un trigger actif.",
            "code": "TRIGGER_RUNNING",
        }), 409
    return None


def _focuser_post_guard(movement=False, require_active=True):
    if require_active:
        inactive = require_device_active("focuser")
        if inactive is not None:
            return inactive
    if movement:
        return _trigger_running_response()
    return None


def _focuser_motion_conflict(service=None):
    status_method = getattr(service or _focuser_service, "status", None)
    if not callable(status_method):
        return None
    status = status_method()
    if (status.get("motion_command") in ("go", "home", "jog")
            and status.get("moving") is True):
        return jsonify({
            "error": "Focuser motion already in progress.",
            "code": "FOCUSER_BUSY",
        }), 409
    return None


def _focuser_result(status):
    """Publish and return the latest focuser state after a successful action."""
    payload = {**status, "rig_id": 1, "device_type": "focuser"}
    socketio.emit("focuser_update", payload)
    return jsonify(status)


def _json_int(payload, name, required=True):
    """Read a JSON integer without accepting booleans as integers."""
    if name not in payload:
        if required:
            raise ValueError(f"Missing integer field '{name}'.")
        return None
    value = payload[name]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"Field '{name}' must be an integer.")
    return value


def _json_number(payload, name, required=True):
    """Read a JSON number without accepting booleans as numbers."""
    if name not in payload:
        if required:
            raise ValueError(f"Missing numeric field '{name}'.")
        return None
    value = payload[name]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"Field '{name}' must be numeric.")
    return value


# ══════════════════════════════════════════════════════════════════════════════
# API — DEVICES
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/api/devices", methods=["GET"])
def api_devices_get():
    devices = _state_store.snapshot("devices") or {}
    if (ttl_expired(devices.get("updated_at"))
            or _has_missing_device_selection(devices)):
        return jsonify(_detect_devices())
    return jsonify(_devices_snapshot())


@app.route("/api/devices", methods=["POST"])
def api_devices_set():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Invalid device selection payload."}), 400

    selections = {}
    for name in DEVICE_CATEGORIES:
        if name in payload:
            normalized = normalize_selection(payload[name])
            selections[name] = {
                "plugin": normalized.get("plugin"),
                "active": normalized["active"],
            }
    if not selections:
        return jsonify({"error": "No device category provided."}), 400

    previous_devices = _state_store.snapshot("devices") or {}

    previous_mount = previous_devices.get("mount") or {}
    new_mount = selections.get("mount")

    previous_focuser = previous_devices.get("focuser") or {}
    new_focuser = selections.get("focuser")
    if (new_focuser is not None
            and new_focuser.get("active") is not True
            and previous_focuser.get("active") is True):
        try:
            _focuser_service.stop_jog()
        except Exception as exc:
            log.warning("Impossible d'arrêter le jog du focuser : %s", exc)

    selections["updated_at"] = datetime.now(timezone.utc).isoformat()
    _state_store.update_section("devices", selections, persist=True)

    if new_mount is not None:
        mount_changed = (
            previous_mount.get("plugin") != new_mount.get("plugin")
            or previous_mount.get("active") is not new_mount.get("active")
        )

        if mount_changed:
            def warm_selected_mount():
                try:
                    if (
                        new_mount.get("active") is True
                        and new_mount.get("plugin") not in (None, "", "none")
                    ):
                        log.info(
                            "Pré-initialisation monture sélectionnée : %s",
                            new_mount.get("plugin"),
                        )
                        _mount_service.warmup()
                    else:
                        _mount_service.close()
                        log.info("Monture désactivée")
                except Exception as exc:
                    log.warning(
                        "Pré-initialisation monture impossible : %s",
                        exc,
                    )

            threading.Thread(
                target=warm_selected_mount,
                name="mount-selection-warmup",
                daemon=True,
            ).start()

    return jsonify(_devices_snapshot())


@app.route("/api/devices/detect", methods=["POST"])
def api_devices_detect():
    return jsonify(_detect_devices())


@app.route("/api/rigs/devices/inventory", methods=["GET"])
def api_rig_device_inventory():
    """Return the runtime inventory cache without probing hardware."""
    inventory = get_cached_inventory()
    build_display_labels([
        entry
        for entries in inventory.values()
        for entry in entries
    ])
    return jsonify(inventory)


@app.route("/api/rigs/devices", methods=["GET"])
def api_rig_devices_get():
    """Return persisted rig bindings enriched from the inventory cache."""
    manager = get_rig_manager()
    config = load_rig_configuration()
    config_rigs = {
        rig.get("rig_id"): rig
        for rig in config.get("rigs", [])
        if isinstance(rig, dict)
    }
    inventory = get_cached_inventory()
    categories = ("camera", "mount", "focuser")

    for category in categories:
        inventory.setdefault(category, [])

    rigs = []
    bindings_by_category = {category: [] for category in categories}
    for rig_id in range(1, 5):
        rig = manager.rigs.get(rig_id)
        devices = {}
        for category in categories:
            configured = rig.devices.get(category) if rig is not None else None
            binding = dict(configured) if isinstance(configured, dict) else None
            devices[category] = binding
            if binding is not None:
                bindings_by_category[category].append(binding)
        configured_rig = config_rigs.get(rig_id, {})
        rigs.append({
            "rig_id": rig_id,
            "name": rig.name if rig is not None else f"RIG {rig_id}",
            "enabled": rig.enabled if rig is not None else False,
            "devices": devices,
            "optics": deepcopy(configured_rig.get("optics", {})),
        })

    for category, bindings in bindings_by_category.items():
        build_display_labels([*inventory[category], *bindings])
        present_identities = {
            key
            for entry in inventory[category]
            if (key := identity_key(entry)) is not None
        }
        for binding in bindings:
            binding["present"] = identity_key(binding) in present_identities

    return jsonify({
        "rigs": rigs,
        "inventory": inventory,
        "identity_warnings": list(manager.identity_warnings),
    })


_RIG_PATCH_FIELDS = frozenset(("rig_id", "enabled", "name", "devices", "optics"))
_RIG_PHOTO_PATCH_FIELDS = frozenset(("rig_id", "photo"))
_RIG_DEVICE_CATEGORIES = frozenset(("camera", "mount", "focuser"))
_RUNTIME_DEVICE_FIELDS = frozenset(
    ("present", "pilotable", "transport_locator", "busnum", "devnum")
)


def _new_rig_scaffold(rig_id, *, atmos_enabled=False):
    return canonical_rig_defaults(
        rig_id,
        atmos_enabled=atmos_enabled,
    )


def _validate_positive_number(value, field, *, integer=False, nullable=False):
    """Validate a positive JSON number used by a per-rig photo patch."""
    if nullable and value is None:
        return
    expected = int if integer else (int, float)
    if (
        not isinstance(value, expected)
        or isinstance(value, bool)
        or value <= 0
    ):
        kind = "integer" if integer else "number"
        raise ValueError(f"{field} must be a {kind} strictly greater than 0")


def _preview_datetime(value):
    return value.isoformat(timespec="microseconds") + "Z" if value is not None else None


def _preview_rig_metadata(rig):
    devices = rig.get("devices") if isinstance(rig, dict) else {}
    devices = devices if isinstance(devices, dict) else {}

    camera = devices.get("camera")
    camera = camera if isinstance(camera, dict) else {}

    manufacturer = camera.get("manufacturer")
    model = camera.get("model") or camera.get("alias")

    camera_parts = [
        str(value).strip()
        for value in (manufacturer, model)
        if isinstance(value, str) and value.strip()
    ]

    camera_label = (
        " ".join(camera_parts)
        if camera_parts
        else "Not configured"
    )

    pixel_pitch_um = None

    if (
        isinstance(manufacturer, str)
        and manufacturer.strip()
        and isinstance(model, str)
        and model.strip()
    ):
        try:
            db = load_sensor_db(str(DEFAULT_SENSOR_DB_PATH))
            sensor = resolve_sensor_entry(
                manufacturer,
                model,
                db,
            )
            pixel_pitch_um = float(sensor["pixel_pitch_um"])
        except (KeyError, OSError, TypeError, ValueError):
            pixel_pitch_um = None

    mount = devices.get("mount")
    mount = mount if isinstance(mount, dict) else {}

    mount_parts = [
        str(value).strip()
        for value in (
            mount.get("manufacturer"),
            mount.get("model") or mount.get("alias"),
        )
        if isinstance(value, str) and value.strip()
    ]

    mount_label = (
        " ".join(mount_parts)
        if mount_parts
        else "None / fixed"
    )

    optics = rig.get("optics")
    optics = optics if isinstance(optics, dict) else {}

    photo = rig.get("photo")
    photo = photo if isinstance(photo, dict) else {}

    return {
        "camera": camera_label,
        "pixel_pitch_um": pixel_pitch_um,
        "mount": mount_label,
        "mount_geometry": mount.get("geometry"),
        "mount_tracking": mount.get("tracking"),
        "focal_length_mm": optics.get("focal_length_mm"),
        "motion_tolerance_px": photo.get("motion_tolerance_px"),
        "anti_trailing_enabled": (
            photo.get("anti_trailing_enabled") is True
        ),
    }


def _preview_atmospheric_summary(rig, context):
    photo = rig.get("photo") if isinstance(rig, dict) else {}

    enabled = (
        isinstance(photo, dict)
        and photo.get("atmos_enabled") is True
    )

    altitudes = context.get("altitudes", context)
    values = []

    if isinstance(altitudes, dict):
        for event in ("C1", "C2", "TMAX", "C3", "C4"):
            raw = altitudes.get(f"{event}_alt_deg")
            if raw is None:
                continue
            try:
                values.append(float(raw))
            except (TypeError, ValueError):
                pass

    return {
        "enabled": enabled,
        "sun_altitude_min_deg": min(values) if values else None,
        "sun_altitude_max_deg": max(values) if values else None,
    }


def _preview_solar_altitude(target_time, context):
    timeline = context.get("timeline")
    altitudes = context.get("altitudes", context)

    if not isinstance(timeline, dict):
        return None
    if not isinstance(altitudes, dict):
        return None

    try:
        return float(
            interpolate_altitude(
                target_time,
                dict(timeline),
                dict(altitudes),
            )
        )
    except (KeyError, TypeError, ValueError):
        return None


def _preview_atmos_added_lines(before, after, iso):
    if len(after) <= len(before):
        return []

    if after[:len(before)] != before:
        return []

    return [
        f"+ ({format_photo_shutter(speed)} ; {iso})"
        for speed in after[len(before):]
    ]


@app.route("/api/rigs/preview", methods=["POST"])
def api_rigs_preview():
    """Materialize exposure intents from configuration without touching runtime state."""
    payload = request.get_json(silent=True)
    try:
        config = deepcopy(load_rig_configuration())
    except (OSError, json.JSONDecodeError, ValueError):
        return jsonify({"error": "rig configuration could not be loaded"}), 500

    # Preview compatibility only:
    # historical canonical RIG files may contain sequence.common without
    # phase objects.  Preview requires them, but this compatibility shape
    # must never be persisted back to the canonical RIG configuration.
    sequence = config.get("sequence")
    if isinstance(sequence, dict):
        common = sequence.get("common")
        if isinstance(common, dict):
            phases = common.setdefault("phases", {})
            if isinstance(phases, dict):
                for phase in ("partial", "diamond_ring", "totality"):
                    phases.setdefault(phase, {})

    try:
        intents, preview_rig_id, rig_override = validate_preview_request(
            payload,
            config,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    # Preview overrides are deliberately ephemeral. This configuration is
    # already a private copy and is never saved.

    if preview_rig_id is not None:
        target_rig = next(
            (
                rig
                for rig in config.get("rigs", [])
                if isinstance(rig, dict)
                and rig.get("rig_id") == preview_rig_id
            ),
            None,
        )
        if target_rig is None:
            return jsonify({
                "error": f"RIG {preview_rig_id} does not exist in configuration"
            }), 400

        target_rig.setdefault("optics", {}).update(
            deepcopy(rig_override["optics"])
        )
        target_rig.setdefault("photo", {}).update(
            deepcopy(rig_override["photo"])
        )

    eclipse_context = load_eclipse_context(JSON_FILE)
    materializer_context = {
        **eclipse_context,
        "altitude_m": eclipse_context.get("observer_alt_m"),
    }
    rigs = sorted(
        (
            rig for rig in config.get("rigs", [])
            if isinstance(rig, dict)
            and (
                preview_rig_id is None
                or rig.get("rig_id") == preview_rig_id
            )
        ),
        key=lambda rig: rig.get("rig_id"),
    )
    response = []
    for rig in rigs:
        items = []
        rig_metadata = _preview_rig_metadata(rig)
        atmospheric_summary = _preview_atmospheric_summary(
            rig,
            materializer_context,
        )

        for intent in intents:
            item = {
                "phase": intent["phase"],
                "target_time": _preview_datetime(intent["target_time"]),
                "deadline": _preview_datetime(intent["deadline"]),
                "origin": intent["origin"],
                "request_id": intent["request_id"],
            }
            try:
                original_plan = normalize_intent_plan(intent)
                plan = original_plan

                pre_atmos_shutters = expand_executable_shutters(
                    rig,
                    original_plan,
                )

                plan, atmos_applied, theoretical_slowest = apply_atmos_if_enabled(
                    rig, plan, intent["target_time"], materializer_context
                )

                atmos_shutters = expand_executable_shutters(
                    rig,
                    plan,
                )

                solar_altitude_deg = _preview_solar_altitude(
                    intent["target_time"],
                    materializer_context,
                )

                motion_policy = resolve_policy(rig)
                motion_ceiling_s = None
                iso_requested = (
                    int(intent["iso_target"])
                    if intent["iso_target"] is not None
                    else None
                )

                iso_applied = (
                    str(iso_requested)
                    if iso_requested is not None
                    else None
                )
                corrections = []
                warnings = []
                final_isos = None

                if motion_policy != "none":
                    if iso_requested is None:
                        raise PreviewMaterializationError(
                            "ISO target is missing"
                        )

                    policy = deepcopy(rig)
                    policy["eclipse"] = deepcopy(
                        config.get("eclipse", {})
                    )

                    t_max = compute_motion_exposure_ceiling(
                        policy,
                        intent["target_time"],
                    )
                    motion_ceiling_s = t_max

                    if t_max is not None:
                        regular, fastest, slowest, step, speeds = plan
                        physical_shutters = expand_executable_shutters(
                            rig,
                            plan,
                        )
                        materialized = materialize_exposure_plan(
                            speeds=physical_shutters,
                            shutter_min=None,
                            shutter_max=None,
                            step_ev=step,
                            iso_requested=iso_requested,
                            iso_max=int(
                                rig.get("photo", {}).get("iso_max", 6400)
                            ),
                            t_max=t_max,
                            iso_compensation_enabled=(
                                rig.get("photo", {}).get(
                                    "iso_compensation_enabled",
                                    True,
                                )
                            ),
                        )

                        plan = (
                            False,
                            materialized["speeds"][0],
                            materialized["speeds"][-1],
                            step,
                            materialized["speeds"],
                        )

                        iso_applied = str(
                            materialized["iso_applied"]
                        )
                        final_isos = [
                            exposure["iso"]
                            for exposure in materialized["exposure_plan"]
                        ]
                        corrections = materialized["corrections"]
                        warnings = materialized["warnings"]

                original_shutters = expand_executable_shutters(
                    rig,
                    original_plan,
                )
                final_shutters = expand_executable_shutters(
                    rig,
                    plan,
                )

                atmos_added_lines = _preview_atmos_added_lines(
                    pre_atmos_shutters,
                    atmos_shutters,
                    iso_requested,
                )

                anti_blur_final_iso = (
                    int(iso_applied)
                    if iso_applied is not None
                    else iso_requested
                )

                anti_blur_diff_lines = build_exposure_diff_lines(
                    atmos_shutters,
                    iso_requested,
                    final_shutters,
                    anti_blur_final_iso,
                    final_isos=final_isos,
                )

                final_iso = (
                    int(iso_applied)
                    if iso_applied is not None
                    else iso_requested
                )
                diff_lines = build_exposure_diff_lines(
                    original_shutters,
                    iso_requested,
                    final_shutters,
                    final_iso,
                    final_isos=final_isos,
                )

                item.update({
                    "exposures_s": assemble_exposures_s(plan),
                    "iso_applied": iso_applied,
                    "corrections": corrections,
                    "warnings": warnings,
                    "atmos_applied": atmos_applied,
                    "sun_altitude_deg": solar_altitude_deg,
                    "atmos_added_lines": atmos_added_lines,
                    "motion_policy": motion_policy,
                    "motion_ceiling_s": motion_ceiling_s,
                    "anti_blur_diff_lines": anti_blur_diff_lines,
                    "diff_lines": diff_lines,
                    "error": None,
                })
            except (
                PreviewMaterializationError,
                ArithmeticError,
                KeyError,
                TypeError,
                ValueError,
            ) as exc:
                code = getattr(exc, "code", "MATERIALIZATION_ERROR")
                item["error"] = {"code": code, "message": str(exc)}
            items.append(item)
        response.append({
            "rig_id": rig["rig_id"],
            "metadata": rig_metadata,
            "atmospheric": atmospheric_summary,
            "items": items,
        })

    return jsonify({"rigs": response})


@app.route("/api/rigs/devices", methods=["POST"])
def api_rig_devices_post():
    """Persist validated per-rig binding patches and reload runtime state."""
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or set(payload) != {"rigs"}:
        return jsonify({"error": "payload must contain only a rigs array"}), 400
    patches = payload["rigs"]
    if not isinstance(patches, list):
        return jsonify({"error": "rigs must be an array"}), 400

    try:
        config = deepcopy(load_rig_configuration())
        global_atmos = atmospheric_attenuation_enabled(config)
        rigs_by_id = {
            rig.get("rig_id"): rig
            for rig in config.get("rigs", [])
            if isinstance(rig, dict)
        }
        for rig_id in range(1, 5):
            rigs_by_id.setdefault(
                rig_id,
                _new_rig_scaffold(
                    rig_id,
                    atmos_enabled=global_atmos,
                ),
            )

        inventory = get_cached_inventory()
        non_pilotable_identities = {
            category: {
                key
                for entry in inventory.get(category, [])
                if entry.get("pilotable") is False
                and (key := identity_key(entry)) is not None
            }
            for category in _RIG_DEVICE_CATEGORIES
        }

        patched_ids = set()
        for index, patch in enumerate(patches):
            if not isinstance(patch, dict):
                raise ValueError(f"rigs[{index}] must be an object")
            unknown = set(patch) - _RIG_PATCH_FIELDS
            if unknown:
                raise ValueError(
                    f"rigs[{index}] contains unsupported fields: "
                    + ", ".join(sorted(unknown))
                )
            rig_id = patch.get("rig_id")
            if (
                not isinstance(rig_id, int)
                or isinstance(rig_id, bool)
                or not 1 <= rig_id <= 4
            ):
                raise ValueError(f"rigs[{index}].rig_id must be an integer from 1 to 4")
            if rig_id in patched_ids:
                raise ValueError(f"duplicate rig_id patch: {rig_id}")
            patched_ids.add(rig_id)

            target = rigs_by_id[rig_id]
            for field in ("enabled", "name"):
                if field in patch:
                    target[field] = patch[field]

            if "optics" in patch:
                optics_patch = patch["optics"]
                if not isinstance(optics_patch, dict):
                    raise ValueError(f"rigs[{index}].optics must be an object")

                unknown_optics = set(optics_patch) - {"focal_length_mm"}
                if unknown_optics:
                    raise ValueError(
                        f"rigs[{index}].optics contains unsupported fields: "
                        + ", ".join(sorted(unknown_optics))
                    )

                if "focal_length_mm" in optics_patch:
                    _validate_positive_number(
                        optics_patch["focal_length_mm"],
                        f"rigs[{index}].optics.focal_length_mm",
                        nullable=True,
                    )

                target.setdefault("optics", {}).update(
                    deepcopy(optics_patch)
                )

            if "devices" in patch:
                devices_patch = patch["devices"]
                if not isinstance(devices_patch, dict):
                    raise ValueError(f"rigs[{index}].devices must be an object")
                unknown_devices = set(devices_patch) - _RIG_DEVICE_CATEGORIES
                if unknown_devices:
                    raise ValueError(
                        f"rigs[{index}].devices contains unsupported categories: "
                        + ", ".join(sorted(unknown_devices))
                    )
                for category, binding in devices_patch.items():
                    if isinstance(binding, dict):
                        transient = set(binding) & _RUNTIME_DEVICE_FIELDS
                        if transient:
                            raise ValueError(
                                f"rigs[{index}].devices.{category} contains runtime-only "
                                f"fields: {', '.join(sorted(transient))}"
                            )
                        binding_identity = identity_key(binding)
                        if (
                            binding_identity is not None
                            and binding_identity
                            in non_pilotable_identities.get(category, set())
                        ):
                            raise ValueError(
                                f"rigs[{index}].devices.{category} device is not pilotable"
                            )
                    target.setdefault("devices", {})[category] = deepcopy(binding)

        config["rigs"] = [rigs_by_id[rig_id] for rig_id in range(1, 5)]
        validate_rig_config(config)
        manager = RigManager.from_config(config)
    except ValueError as exc:
        status = 409 if "duplicate device identity" in str(exc) else 400
        return jsonify({"error": str(exc)}), status
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Chargement de la configuration rigs impossible : %s", exc)
        return jsonify({"error": "rig configuration could not be loaded"}), 500

    config_path = TRIGGER_DIR / "configs" / "rig" / "default.json"
    try:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        save_rig_config(config_path, config)
        manager = reload_rig_manager(config)
    except (OSError, ValueError) as exc:
        log.error("Sauvegarde de la configuration rigs impossible : %s", exc)
        return jsonify({"error": "rig configuration could not be saved"}), 500

    rigs_summary = normalize_rigs_for_ui(manager)
    socketio.emit(
        "status_update",
        _status_update_payload({"rigs": rigs_summary}),
        namespace="/",
    )
    return jsonify({
        "rigs": rigs_summary,
        "identity_warnings": list(manager.identity_warnings),
    })


@app.route("/api/rigs/photo", methods=["GET", "POST"])
def api_rig_photo_post():
    """Read or persist per-RIG optics/photo configuration without hardware access."""
    if request.method == "GET":
        try:
            config = load_rig_configuration()
        except (OSError, json.JSONDecodeError, ValueError):
            return jsonify({"error": "rig configuration could not be loaded"}), 500

        global_atmos = atmospheric_attenuation_enabled(config)
        rigs_by_id = {
            rig.get("rig_id"): rig
            for rig in config.get("rigs", [])
            if isinstance(rig, dict)
        }

        rigs = []
        for rig_id in range(1, 5):
            rig = rigs_by_id.get(rig_id) or _new_rig_scaffold(
                rig_id,
                atmos_enabled=global_atmos,
            )
            rigs.append({
                "rig_id": rig_id,
                "photo": deepcopy(rig.get("photo", {})),
            })

        return jsonify({"rigs": rigs})

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or set(payload) != {"rigs"}:
        return jsonify({"error": "payload must contain only a rigs array"}), 400
    patches = payload["rigs"]
    if not isinstance(patches, list):
        return jsonify({"error": "rigs must be an array"}), 400

    try:
        config = deepcopy(load_rig_configuration())
        rigs_by_id = {
            rig.get("rig_id"): rig
            for rig in config.get("rigs", [])
            if isinstance(rig, dict)
        }
        for rig_id in range(1, 5):
            rigs_by_id.setdefault(rig_id, _new_rig_scaffold(rig_id))

        patched_ids = set()
        for index, patch in enumerate(patches):
            prefix = f"rigs[{index}]"
            if not isinstance(patch, dict):
                raise ValueError(f"{prefix} must be an object")
            unknown = set(patch) - _RIG_PHOTO_PATCH_FIELDS
            if unknown:
                raise ValueError(
                    f"{prefix} contains unsupported fields: "
                    + ", ".join(sorted(unknown))
                )
            rig_id = patch.get("rig_id")
            if (
                not isinstance(rig_id, int)
                or isinstance(rig_id, bool)
                or not 1 <= rig_id <= 4
            ):
                raise ValueError(f"{prefix}.rig_id must be an integer from 1 to 4")
            if rig_id in patched_ids:
                raise ValueError(f"duplicate rig_id patch: {rig_id}")
            patched_ids.add(rig_id)

            photo_patch = patch.get("photo", {})
            if not isinstance(photo_patch, dict):
                raise ValueError(f"{prefix}.photo must be an object")
            for field in (
                "atmos_enabled",
                "anti_trailing_enabled",
                "iso_compensation_enabled",
            ):
                if field in photo_patch and not isinstance(photo_patch[field], bool):
                    raise ValueError(f"{prefix}.photo.{field} must be a boolean")

            if "motion_tolerance_px" in photo_patch:
                _validate_positive_number(
                    photo_patch["motion_tolerance_px"],
                    f"{prefix}.photo.motion_tolerance_px",
                )

            if "iso_max" in photo_patch:
                _validate_positive_number(
                    photo_patch["iso_max"],
                    f"{prefix}.photo.iso_max",
                    integer=True,
                )

            target = rigs_by_id[rig_id]
            target.setdefault("photo", {}).update(deepcopy(photo_patch))

        config["rigs"] = [rigs_by_id[rig_id] for rig_id in range(1, 5)]
        validate_rig_config(config)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Chargement de la configuration rigs impossible : %s", exc)
        return jsonify({"error": "rig configuration could not be loaded"}), 500

    config_path = TRIGGER_DIR / "configs" / "rig" / "default.json"
    try:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        save_rig_config(config_path, config)
        manager = reload_rig_manager(config)
    except (OSError, ValueError) as exc:
        log.error("Sauvegarde de la configuration rigs impossible : %s", exc)
        return jsonify({"error": "rig configuration could not be saved"}), 500

    rigs_summary = normalize_rigs_for_ui(manager)
    socketio.emit(
        "status_update",
        _status_update_payload({"rigs": rigs_summary}),
        namespace="/",
    )
    return jsonify({
        "rigs": rigs_summary,
        "identity_warnings": list(manager.identity_warnings),
    })


@app.route("/api/rigs/devices/refresh", methods=["POST"])
def api_rig_device_inventory_refresh():
    """Run the operator-requested discovery pass and replace the cache."""
    return jsonify(refresh_inventory())

# ══════════════════════════════════════════════════════════════════════════════
# API — STATUT
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/api/status")
def api_status():
    camera_info = _state_store.snapshot("camera") or {}
    try:
        rigs = normalize_rigs_for_ui(get_rig_manager())
    except Exception as exc:
        log.warning("Chargement des rigs impossible : %s", exc)
        rigs = [
            {"rig_id": rig_id, "name": f"RIG {rig_id}", "enabled": False}
            for rig_id in range(1, 5)
        ]
    with _state_lock:
        gps     = dict(_state["gps"])
        trigger = dict(_state["trigger"])
    return jsonify({
        "time":    _time_payload(),
        "gps":     gps,
        "camera":  camera_info,
        "trigger": trigger,
        "eclipse": _load_eclipse_json(),
        "circumstances": _state_store.snapshot("circumstances"),
        "capture": _state_store.snapshot("capture"),
        "devices": _devices_snapshot(),
        "rigs": rigs,
    })


# ══════════════════════════════════════════════════════════════════════════════
# API — FOCUSER
# ══════════════════════════════════════════════════════════════════════════════

def _trace_rig_stop(device_type, fixed_rig_id=None):
    """Trace one STOP request at its Flask route boundary."""
    def decorator(route):
        @wraps(route)
        def traced(*args, **kwargs):
            if fixed_rig_id is None:
                rig_id = kwargs.get("rig_id", args[0] if args else None)
            else:
                rig_id = fixed_rig_id
            start_utc = datetime.now(timezone.utc)
            try:
                result = route(*args, **kwargs)
            except Exception as exc:
                end_utc = datetime.now(timezone.utc)
                rig_trace.trace_event(f"{device_type}.stop", {
                    "rig_id": rig_id,
                    "device_type": device_type,
                    "action": "stop",
                    "start_utc": start_utc.isoformat(),
                    "end_utc": end_utc.isoformat(),
                    "duration_ms": (
                        end_utc - start_utc
                    ).total_seconds() * 1000.0,
                    "status": "error",
                    "code": getattr(exc, "code", type(exc).__name__),
                    "message": str(exc),
                })
                raise

            end_utc = datetime.now(timezone.utc)
            response = app.make_response(result)
            trace_payload = {
                "rig_id": rig_id,
                "device_type": device_type,
                "action": "stop",
                "start_utc": start_utc.isoformat(),
                "end_utc": end_utc.isoformat(),
                "duration_ms": (
                    end_utc - start_utc
                ).total_seconds() * 1000.0,
                "status": "success" if response.status_code < 400 else "error",
            }
            if response.status_code >= 400:
                body = response.get_json(silent=True) or {}
                trace_payload["code"] = body.get(
                    "code", f"HTTP_{response.status_code}"
                )
                trace_payload["message"] = body.get(
                    "message", body.get("error", response.status)
                )
            rig_trace.trace_event(f"{device_type}.stop", trace_payload)
            return result

        return traced
    return decorator

@app.route("/api/focuser/status")
def api_focuser_status():
    inactive = require_device_active("focuser")
    if inactive is not None:
        return inactive
    status = dict(_focuser_service.status())
    status["plugin"] = _selected_device_plugin("focuser")
    return jsonify(status)


@app.route("/api/focuser/mode", methods=["POST"])
def api_focuser_mode():
    guarded = _focuser_post_guard()
    if guarded is not None:
        return guarded
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Invalid focuser payload."}), 400
    mode = payload.get("mode")
    if mode not in ("slow", "fast"):
        return jsonify({"error": "Field 'mode' must be 'slow' or 'fast'."}), 400
    return _focuser_result(_focuser_service.set_mode(mode))


@app.route("/api/focuser/home", methods=["POST"])
def api_focuser_home():
    guarded = _focuser_post_guard(movement=True)
    if guarded is not None:
        return guarded
    conflict = _focuser_motion_conflict()
    if conflict is not None:
        return conflict
    return _focuser_result(_focuser_service.home())


@app.route("/api/focuser/stop", methods=["POST"])
@_trace_rig_stop("focuser", fixed_rig_id=1)
def api_focuser_stop():
    guarded = _focuser_post_guard()
    if guarded is not None:
        return guarded
    return _focuser_result(_focuser_service.stop())


@app.route("/api/focuser/move_to", methods=["POST"])
def api_focuser_move_to():
    guarded = _focuser_post_guard(movement=True)
    if guarded is not None:
        return guarded
    conflict = _focuser_motion_conflict()
    if conflict is not None:
        return conflict
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Invalid focuser payload."}), 400
    try:
        position = _json_int(payload, "position")
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return _focuser_result(_focuser_service.move_to(position))


@app.route("/api/focuser/step", methods=["POST"])
def api_focuser_step():
    guarded = _focuser_post_guard(movement=True)
    if guarded is not None:
        return guarded
    conflict = _focuser_motion_conflict()
    if conflict is not None:
        return conflict
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Invalid focuser payload."}), 400
    direction = payload.get("direction")
    legacy_delta = None
    if "delta" in payload:
        try:
            legacy_delta = _json_int(payload, "delta")
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    if direction is None:
        if legacy_delta is None:
            return jsonify({"error": "Field 'direction' is required."}), 400
        if legacy_delta == 0:
            return jsonify({"error": "Field 'delta' must be non-zero."}), 400
        direction = "increase" if legacy_delta > 0 else "decrease"
    elif direction not in ("increase", "decrease"):
        return jsonify({
            "error": "Field 'direction' must be 'increase' or 'decrease'.",
            "code": "INVALID_DIRECTION",
        }), 400
    elif legacy_delta is not None:
        delta_matches_direction = (
            (direction == "increase" and legacy_delta > 0)
            or (direction == "decrease" and legacy_delta < 0)
        )
        if not delta_matches_direction:
            return jsonify({
                "error": "Fields 'direction' and 'delta' contradict each other.",
                "code": "INVALID_DIRECTION",
            }), 400

    sign = 1 if direction == "increase" else -1
    delta = sign * _focuser_service.active_step()
    return _focuser_result(_focuser_service.move_relative(delta))


@app.route("/api/focuser/jog/start", methods=["POST"])
def api_focuser_jog_start():
    guarded = _focuser_post_guard(movement=True)
    if guarded is not None:
        return guarded
    conflict = _focuser_motion_conflict()
    if conflict is not None:
        return conflict
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Invalid focuser payload."}), 400
    direction = payload.get("direction")
    if direction not in ("increase", "decrease", "in", "out"):
        return jsonify({
            "error": (
                "Field 'direction' must be 'increase', 'decrease', 'in' or 'out'."
            ),
            "code": "INVALID_DIRECTION",
        }), 400
    return _focuser_result(_focuser_service.start_jog(direction))


@app.route("/api/focuser/jog/stop", methods=["POST"])
def api_focuser_jog_stop():
    guarded = _focuser_post_guard()
    if guarded is not None:
        return guarded
    return _focuser_result(_focuser_service.stop_jog())


@app.route("/api/focuser/set_step", methods=["POST"])
def api_focuser_set_step():
    guarded = _focuser_post_guard()
    if guarded is not None:
        return guarded
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Invalid focuser payload."}), 400
    try:
        coarse = _json_int(payload, "coarse", required=False)
        fine = _json_int(payload, "fine", required=False)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if coarse is None and fine is None:
        return jsonify({"error": "At least one step value is required."}), 400
    return _focuser_result(_focuser_service.set_step(coarse=coarse, fine=fine))


def _focuser_service_factory_provider(binding):
    return lambda: FocuserService(
        _state_store,
        log_fn=log.info,
        config=dict(binding.focuser_entry),
        selected_plugin=binding.backend,
        persist_policy="volatile",
    )


def _rig_focuser_worker(rig_id):
    try:
        rig = get_rig_manager().get_rig(rig_id)
    except ValueError as exc:
        return None, (jsonify({"error": str(exc)}), 400)
    runtime = get_focuser_worker_runtime(
        service_factory_provider=_focuser_service_factory_provider,
        log_fn=log.info,
    )
    runtime.reconcile(load_rig_configuration())
    worker = runtime.get_for_rig(rig_id)
    if worker is None:
        return None, (jsonify({
            "error": f"focuser is not configured for rig {rig_id}",
            "code": "DEVICE_NOT_CONFIGURED",
            "rig_id": rig_id,
            "device_type": "focuser",
        }), 409)
    return worker, None


def _rig_focuser_guard(rig_id, *, movement=False):
    worker, error = _rig_focuser_worker(rig_id)
    if error is not None:
        return None, error
    guarded = _focuser_post_guard(movement=movement, require_active=False)
    return (None, guarded) if guarded is not None else (worker, None)


def _rig_focuser_result(rig_id, result):
    payload = dict(result)
    payload.update({"rig_id": rig_id, "device_type": "focuser"})
    socketio.emit("focuser_update", payload, namespace="/")
    return jsonify(result)


def _rig_focuser_service_call(worker, method, *args):
    operation = getattr(worker, method, None)
    if callable(operation):
        return operation(*args)
    return worker._call(method, *args)


@app.route("/api/rigs/<int:rig_id>/focuser/status")
def api_rig_focuser_status(rig_id):
    worker, error = _rig_focuser_worker(rig_id)
    if error is not None:
        return error
    return _rig_focuser_result(rig_id, worker.status())


@app.route("/api/rigs/<int:rig_id>/focuser/mode", methods=["POST"])
def api_rig_focuser_mode(rig_id):
    worker, error = _rig_focuser_guard(rig_id)
    if error is not None:
        return error
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Invalid focuser payload."}), 400
    mode = payload.get("mode")
    if mode not in ("slow", "fast"):
        return jsonify({"error": "Field 'mode' must be 'slow' or 'fast'."}), 400
    return _rig_focuser_result(
        rig_id, _rig_focuser_service_call(worker, "set_mode", mode)
    )


@app.route("/api/rigs/<int:rig_id>/focuser/home", methods=["POST"])
def api_rig_focuser_home(rig_id):
    worker, error = _rig_focuser_guard(rig_id, movement=True)
    if error is not None:
        return error
    conflict = _focuser_motion_conflict(worker)
    if conflict is not None:
        return conflict
    return _rig_focuser_result(rig_id, worker.home())


@app.route("/api/rigs/<int:rig_id>/focuser/stop", methods=["POST"])
@_trace_rig_stop("focuser")
def api_rig_focuser_stop(rig_id):
    worker, error = _rig_focuser_guard(rig_id)
    if error is not None:
        return error
    return _rig_focuser_result(rig_id, worker.stop())


@app.route("/api/rigs/<int:rig_id>/focuser/move_to", methods=["POST"])
def api_rig_focuser_move_to(rig_id):
    worker, error = _rig_focuser_guard(rig_id, movement=True)
    if error is not None:
        return error
    conflict = _focuser_motion_conflict(worker)
    if conflict is not None:
        return conflict
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Invalid focuser payload."}), 400
    try:
        position = _json_int(payload, "position")
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return _rig_focuser_result(rig_id, worker.move_to(position))


@app.route("/api/rigs/<int:rig_id>/focuser/step", methods=["POST"])
def api_rig_focuser_step(rig_id):
    worker, error = _rig_focuser_guard(rig_id, movement=True)
    if error is not None:
        return error
    conflict = _focuser_motion_conflict(worker)
    if conflict is not None:
        return conflict
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Invalid focuser payload."}), 400
    direction = payload.get("direction")
    legacy_delta = None
    if "delta" in payload:
        try:
            legacy_delta = _json_int(payload, "delta")
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
    if direction is None:
        if legacy_delta is None:
            return jsonify({"error": "Field 'direction' is required."}), 400
        if legacy_delta == 0:
            return jsonify({"error": "Field 'delta' must be non-zero."}), 400
        direction = "increase" if legacy_delta > 0 else "decrease"
    elif direction not in ("increase", "decrease"):
        return jsonify({
            "error": "Field 'direction' must be 'increase' or 'decrease'.",
            "code": "INVALID_DIRECTION",
        }), 400
    elif legacy_delta is not None:
        matches = ((direction == "increase" and legacy_delta > 0)
                   or (direction == "decrease" and legacy_delta < 0))
        if not matches:
            return jsonify({
                "error": "Fields 'direction' and 'delta' contradict each other.",
                "code": "INVALID_DIRECTION",
            }), 400
    sign = 1 if direction == "increase" else -1
    active_step = _rig_focuser_service_call(worker, "active_step")
    return _rig_focuser_result(rig_id, worker.move_relative(sign * active_step))


@app.route("/api/rigs/<int:rig_id>/focuser/jog/start", methods=["POST"])
def api_rig_focuser_jog_start(rig_id):
    worker, error = _rig_focuser_guard(rig_id, movement=True)
    if error is not None:
        return error
    conflict = _focuser_motion_conflict(worker)
    if conflict is not None:
        return conflict
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Invalid focuser payload."}), 400
    direction = payload.get("direction")
    if direction not in ("increase", "decrease", "in", "out"):
        return jsonify({
            "error": (
                "Field 'direction' must be 'increase', 'decrease', 'in' or 'out'."
            ),
            "code": "INVALID_DIRECTION",
        }), 400
    return _rig_focuser_result(rig_id, worker.start_jog(direction))


@app.route("/api/rigs/<int:rig_id>/focuser/jog/stop", methods=["POST"])
@_trace_rig_stop("focuser")
def api_rig_focuser_jog_stop(rig_id):
    worker, error = _rig_focuser_guard(rig_id)
    if error is not None:
        return error
    return _rig_focuser_result(rig_id, worker.stop_jog())


@app.route("/api/rigs/<int:rig_id>/focuser/set_step", methods=["POST"])
def api_rig_focuser_set_step(rig_id):
    worker, error = _rig_focuser_guard(rig_id)
    if error is not None:
        return error
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Invalid focuser payload."}), 400
    try:
        coarse = _json_int(payload, "coarse", required=False)
        fine = _json_int(payload, "fine", required=False)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if coarse is None and fine is None:
        return jsonify({"error": "At least one step value is required."}), 400
    return _rig_focuser_result(
        rig_id, worker.set_step(coarse=coarse, fine=fine)
    )


# ═════════════════════════════════════════════════════════════════════════════
# API — MOUNT
# ═══════════════════════════════════════════════════════════════════════════════

def _mount_indi_error(exc):
    return jsonify({"error": str(exc), "code": exc.code}), 400


def _mount_service_factory_provider(binding):
    return lambda: MountService(
        _state_store,
        log_fn=log.info,
        config=dict(binding.mount_entry),
        selected_plugin=binding.backend,
    )


def _rig_mount_worker(rig_id):
    try:
        rig = get_rig_manager().get_rig(rig_id)
    except ValueError as exc:
        return None, (jsonify({"error": str(exc)}), 400)

    runtime = get_mount_worker_runtime(
        service_factory_provider=_mount_service_factory_provider,
        log_fn=log.info,
    )
    runtime.reconcile(load_rig_configuration())
    worker = runtime.get_for_rig(rig_id)
    if worker is None:
        return None, (jsonify({
            "error": f"mount is not configured for rig {rig_id}",
            "code": "DEVICE_NOT_CONFIGURED",
            "rig_id": rig_id,
            "device_type": "mount",
        }), 409)
    return worker, None


def _rig_mount_emit(rig_id, result):
    payload = dict(result)
    payload.update({"rig_id": rig_id, "device_type": "mount"})
    socketio.emit("mount_update", payload, namespace="/")
    return jsonify(result)


def _rig_mount_error(rig_id, exc, *, status=400, code=None):
    payload = {
        "status": "error",
        "rig_id": rig_id,
        "device_type": "mount",
        "error": str(exc),
    }
    resolved_code = code if code is not None else getattr(exc, "code", None)
    if resolved_code is not None:
        payload["code"] = resolved_code
    socketio.emit("mount_update", payload, namespace="/")
    response = {"error": str(exc)}
    if resolved_code is not None:
        response["code"] = resolved_code
    return jsonify(response), status


def _rig_mount_tracking_guard(rig_id):
    worker, error = _rig_mount_worker(rig_id)
    if error is not None:
        return None, error
    trigger_state = _state_store.snapshot("trigger") or {}
    if trigger_state.get("running"):
        exc = RuntimeError(
            "Mount tracking changes are forbidden during an active trigger."
        )
        return None, _rig_mount_error(
            rig_id, exc, status=409, code="TRIGGER_RUNNING"
        )
    return worker, None


@app.route("/api/rigs/<int:rig_id>/mount/status")
def api_rig_mount_status(rig_id):
    worker, error = _rig_mount_worker(rig_id)
    if error is not None:
        return error
    try:
        result = worker.status()
    except IndiClientError as exc:
        return _rig_mount_error(rig_id, exc)
    return _rig_mount_emit(rig_id, result)


@app.route("/api/rigs/<int:rig_id>/mount/tracking/mode", methods=["POST"])
def api_rig_mount_tracking_mode(rig_id):
    worker, error = _rig_mount_tracking_guard(rig_id)
    if error is not None:
        return error
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or payload.get("mode") not in {
        "solar", "sidereal"
    }:
        return jsonify({
            "error": "Field 'mode' must be 'solar' or 'sidereal'."
        }), 400
    try:
        result = worker.set_tracking_mode(payload["mode"])
    except IndiClientError as exc:
        return _rig_mount_error(rig_id, exc)
    except (ValueError, RuntimeError) as exc:
        return _rig_mount_error(rig_id, exc)
    return _rig_mount_emit(rig_id, result)


@app.route("/api/rigs/<int:rig_id>/mount/tracking/start", methods=["POST"])
def api_rig_mount_tracking_start(rig_id):
    worker, error = _rig_mount_tracking_guard(rig_id)
    if error is not None:
        return error
    try:
        result = worker.start_tracking()
    except IndiClientError as exc:
        return _rig_mount_error(rig_id, exc)
    except (ValueError, RuntimeError) as exc:
        return _rig_mount_error(rig_id, exc)
    return _rig_mount_emit(rig_id, result)


@app.route("/api/rigs/<int:rig_id>/mount/tracking/stop", methods=["POST"])
@_trace_rig_stop("mount")
def api_rig_mount_tracking_stop(rig_id):
    worker, error = _rig_mount_tracking_guard(rig_id)
    if error is not None:
        return error
    try:
        result = worker.stop_tracking()
    except IndiClientError as exc:
        return _rig_mount_error(rig_id, exc)
    except (ValueError, RuntimeError) as exc:
        return _rig_mount_error(rig_id, exc)
    return _rig_mount_emit(rig_id, result)


@app.route("/api/rigs/<int:rig_id>/mount/speed", methods=["POST"])
def api_rig_mount_speed(rig_id):
    worker, error = _rig_mount_worker(rig_id)
    if error is not None:
        return error
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Invalid mount payload."}), 400
    try:
        if "speed" not in payload:
            raise ValueError("Missing field 'speed'.")
        result = worker.set_speed(payload["speed"])
    except IndiClientError as exc:
        return _rig_mount_error(rig_id, exc)
    except (ValueError, RuntimeError) as exc:
        return _rig_mount_error(rig_id, exc)
    return _rig_mount_emit(rig_id, result)


@app.route("/api/rigs/<int:rig_id>/mount/slew/start", methods=["POST"])
def api_rig_mount_slew_start(rig_id):
    worker, error = _rig_mount_worker(rig_id)
    if error is not None:
        return error
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Invalid mount payload."}), 400
    direction = payload.get("direction")
    if direction not in ("north", "south", "east", "west"):
        return jsonify({
            "error": (
                "Field 'direction' must be 'north', 'south', 'east' or 'west'."
            )
        }), 400
    try:
        result = worker.start_slew(direction)
    except IndiClientError as exc:
        return _rig_mount_error(rig_id, exc)
    except (ValueError, RuntimeError) as exc:
        if "homing" in str(exc).lower():
            return _rig_mount_error(rig_id, exc, status=409, code="MOUNT_HOMING")
        return _rig_mount_error(rig_id, exc)
    return _rig_mount_emit(rig_id, result)


@app.route("/api/rigs/<int:rig_id>/mount/home", methods=["POST"])
def api_rig_mount_home(rig_id):
    worker, error = _rig_mount_worker(rig_id)
    if error is not None:
        return error
    try:
        result = worker.home_start()
    except IndiClientError as exc:
        return _rig_mount_error(rig_id, exc)
    except (ValueError, RuntimeError) as exc:
        return _rig_mount_error(rig_id, exc)
    return _rig_mount_emit(rig_id, result)


@app.route("/api/rigs/<int:rig_id>/mount/slew/stop", methods=["POST"])
@_trace_rig_stop("mount")
def api_rig_mount_slew_stop(rig_id):
    worker, error = _rig_mount_worker(rig_id)
    if error is not None:
        return error
    try:
        result = worker.stop()
    except IndiClientError as exc:
        return _rig_mount_error(rig_id, exc)
    return _rig_mount_emit(rig_id, result)


@app.route("/api/mount/status")
def api_mount_status():
    inactive = require_device_active("mount")
    if inactive is not None:
        return inactive
    try:
        status = dict(_mount_service.status())
    except IndiClientError as exc:
        return _mount_indi_error(exc)
    status.setdefault("tracking_mode", "solar")
    status.setdefault("tracking_enabled", False)
    status.setdefault("tracking_caps", None)
    status["plugin"] = _selected_device_plugin("mount")
    return jsonify(status)


def _mount_tracking_guard():
    inactive = require_device_active("mount")
    if inactive is not None:
        return inactive
    trigger_state = _state_store.snapshot("trigger") or {}
    if trigger_state.get("running"):
        return jsonify({
            "error": "Mount tracking changes are forbidden during an active trigger.",
            "code": "TRIGGER_RUNNING",
        }), 409
    return None


@app.route("/api/mount/tracking/mode", methods=["POST"])
def api_mount_tracking_mode():
    guarded = _mount_tracking_guard()
    if guarded is not None:
        return guarded
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or payload.get("mode") not in {
        "solar", "sidereal"
    }:
        return jsonify({
            "error": "Field 'mode' must be 'solar' or 'sidereal'."
        }), 400
    try:
        result = _mount_service.set_tracking_mode(payload["mode"])
    except IndiClientError as exc:
        return _mount_indi_error(exc)
    except (ValueError, RuntimeError) as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(result)


@app.route("/api/mount/tracking/start", methods=["POST"])
def api_mount_tracking_start():
    guarded = _mount_tracking_guard()
    if guarded is not None:
        return guarded
    try:
        result = _mount_service.start_tracking()
    except IndiClientError as exc:
        return _mount_indi_error(exc)
    except (ValueError, RuntimeError) as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(result)


@app.route("/api/mount/tracking/stop", methods=["POST"])
@_trace_rig_stop("mount", fixed_rig_id=1)
def api_mount_tracking_stop():
    guarded = _mount_tracking_guard()
    if guarded is not None:
        return guarded
    try:
        result = _mount_service.stop_tracking()
    except IndiClientError as exc:
        return _mount_indi_error(exc)
    except (ValueError, RuntimeError) as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(result)


@app.route("/api/mount/speed", methods=["POST"])
def api_mount_speed():
    inactive = require_device_active("mount")
    if inactive is not None:
        return inactive
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Invalid mount payload."}), 400
    try:
        if "speed" not in payload:
            raise ValueError("Missing field 'speed'.")
        speed = payload["speed"]
        result = _mount_service.set_speed(speed)
    except IndiClientError as exc:
        return _mount_indi_error(exc)
    except (ValueError, RuntimeError) as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(result)


@app.route("/api/mount/slew/start", methods=["POST"])
def api_mount_slew_start():
    inactive = require_device_active("mount")
    if inactive is not None:
        return inactive
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "Invalid mount payload."}), 400
    direction = payload.get("direction")
    if direction not in ("north", "south", "east", "west"):
        return jsonify({
            "error": (
                "Field 'direction' must be 'north', 'south', 'east' or 'west'."
            )
        }), 400
    try:
        result = _mount_service.start_slew(direction)
    except IndiClientError as exc:
        return _mount_indi_error(exc)
    except (ValueError, RuntimeError) as exc:
        if "homing" in str(exc).lower():
            return jsonify({
                "error": str(exc),
                "code": "MOUNT_HOMING",
            }), 409
        return jsonify({"error": str(exc)}), 400
    return jsonify(result)


@app.route("/api/mount/home", methods=["POST"])
def api_mount_home():
    inactive = require_device_active("mount")
    if inactive is not None:
        return inactive
    try:
        result = _mount_service.home_start()
    except IndiClientError as exc:
        return _mount_indi_error(exc)
    except (ValueError, RuntimeError) as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(result)


@app.route("/api/mount/slew/stop", methods=["POST"])
@_trace_rig_stop("mount", fixed_rig_id=1)
def api_mount_slew_stop():
    inactive = require_device_active("mount")
    if inactive is not None:
        return inactive
    try:
        result = _mount_service.stop()
    except IndiClientError as exc:
        return _mount_indi_error(exc)
    return jsonify(result)


def _brand_from_model(model):
    """Déduit la marque à partir du modèle déclaré par gphoto2."""
    from plugins.camera.nikon import NikonDSLRPlugin, NikonZPlugin
    from plugins.camera.sony import SonyPlugin

    if SonyPlugin.matches(model):
        return "SONY"
    if NikonZPlugin.matches(model) or NikonDSLRPlugin.matches(model):
        return "NIKON"
    if model and model.split():
        return model.split()[0].upper()
    return "Inconnu"


def _get_camera_model_info(camera):
    """Lit marque, modèle et batterie depuis la config gphoto2."""
    brand   = None
    model   = None
    battery = None
    try:
        from plugins.camera import get_camera_model
        full_model = get_camera_model(camera)
        model = full_model
        brand = _brand_from_model(model)
    except Exception:
        pass
    try:
        config  = camera.get_config()
        bat     = config.get_child_by_name("batterylevel")
        battery = bat.get_value()
    except Exception:
        pass
    return brand, model, battery

def _get_camera_status():
    """Return already-known camera state without touching camera hardware."""
    with _state_lock:
        camera = _state.get("camera")
        return dict(camera) if isinstance(camera, dict) else {}

def _load_eclipse_json():
    try:
        if JSON_FILE.exists():
            with open(JSON_FILE, encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return None

# ══════════════════════════════════════════════════════════════════════════════
# API — GPS (acquisition ponctuelle opérateur)
# ══════════════════════════════════════════════════════════════════════════════

def _emit_backend(event, payload):
    socketio.emit(event, payload, namespace="/")
    if event == "gps_update" and payload.get("synced"):
        status_payload = _status_update_payload({"gps": payload})
        new_time = status_payload["time"]
        socketio.emit("status_update", status_payload, namespace="/")
        socketio.emit("clock_reset", {
            "new_utc": new_time["utc"]["iso"],
            "new_utc_epoch_ms": new_time["backend_utc_epoch_ms"],
            "new_local_epoch_ms": new_time["backend_local_epoch_ms"],
        }, namespace="/")

def _sync_time_backend(gps_time, dry_run=False):
    from scripts.gps_sync import sync_system_time
    return sync_system_time(gps_time, dry_run=dry_run)

_gps_controller = GpsController(
    _state_store, GPS_CONFIG_FILE,
    timezone_fn=lambda lat, lon, eclipse_date=None: _backend_timezone(lat, lon, eclipse_date, log=log),
    time_sync_fn=_sync_time_backend, log_fn=_append_log, emit_fn=_emit_backend)

@app.route("/api/gps/sync", methods=["POST"])
@app.route("/api/gps/sync_time_location", methods=["POST"])
def api_gps_sync():
    return _start_gps_sync("time_location")

@app.route("/api/gps/sync_time", methods=["POST"])
def api_gps_sync_time():
    return _start_gps_sync("time_only")

@app.route("/api/gps/get_location", methods=["POST"])
def api_gps_get_location():
    return _start_gps_sync("location_only")

def _start_gps_sync(mode):
    inactive = require_device_active("gps")
    if inactive is not None:
        return inactive
    trigger_state = _state_store.snapshot("trigger") or {}
    if trigger_state.get("running"):
        return jsonify({"error": "Synchronisation GPS interdite pendant un trigger actif.", "code": "TRIGGER_RUNNING"}), 409
    if not _gps_controller.start(timeout_s=60.0, mode=mode):
        return jsonify({"error": "Synchronisation GPS déjà en cours."}), 409
    return jsonify({"status": "started"})

@app.route("/api/gps/state")
def api_gps_state_get():
    return jsonify(_state_store.snapshot("gps"))

@app.route("/api/gps/state", methods=["POST"])
def api_gps_state_set():
    data = request.json or {}; values = {}
    for key in ("lat", "lon", "alt"):
        if key in data: values[key] = float(data[key])
    current = _state_store.snapshot("gps")
    lat = values.get("lat", current.get("lat")); lon = values.get("lon", current.get("lon"))
    values.update({"synced": True, "sync_time": datetime.now(timezone.utc).isoformat()})
    if lat is not None and lon is not None:
        values["timezone"] = f"UTC{_backend_timezone(lat, lon, None, log=log):+g}"
    snap = _state_store.update_section("gps", values, persist=True)
    socketio.emit("gps_update", snap)
    return jsonify({"status": "ok"})

@app.route("/api/camera/probe", methods=["POST"])
def api_camera_probe():
    """
    Teste la connexion USB, lit marque/modèle/batterie, coupe immédiatement la connexion.
    N'enregistre pas de connexion persistante pour économiser la batterie.
    """
    try:
        camera = gp.Camera()
        camera.init()
        brand, model, battery = _get_camera_model_info(camera)
        camera.exit()   # Couper immédiatement — économie batterie

        info = {"brand": brand or "Inconnu", "model": model or "Inconnu", "battery": battery}
        with _state_lock:
            _state["camera"]["connected"] = False   # déconnecté volontairement
            _state["camera"]["brand"]     = brand
            _state["camera"]["model"]     = model
            _state["camera"]["battery"]   = battery
        _save_state()
        _append_log(
            f"📷 Caméra détectée : {brand or '?'} {model or '?'}"
            + (f" — Batterie {battery}" if battery else "")
            + " — connexion coupée.",
            "success", "system"
        )
        return jsonify(info)
    except Exception as e:
        _append_log(f"❌ Caméra non détectée : {e}", "error", "system")
        return jsonify({"error": str(e)}), 404


@app.route("/api/rigs/<int:rig_id>/camera/probe", methods=["POST"])
def api_rig_camera_probe(rig_id):
    """Probe the camera worker belonging to one enabled rig."""
    try:
        rig = get_rig_manager().get_rig(rig_id)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    try:
        runtime = get_camera_worker_runtime(log_fn=log.info)
        runtime.reconcile(load_rig_configuration())
        worker = runtime.get_for_rig(rig_id)
        if worker is None:
            raise RuntimeError("camera worker is unavailable")
        info = worker.probe_info()
    except Exception as exc:
        log.warning("Camera probe unavailable for rig %s: %s", rig_id, exc)
        return jsonify({
            "error": "camera unavailable",
            "code": "CAMERA_UNAVAILABLE",
            "rig_id": rig_id,
        }), 404

    model = info.get("model")
    return jsonify({
        "brand": _brand_from_model(model),
        "model": model,
        "battery": info.get("battery"),
    })


@app.route("/api/rigs/<int:rig_id>/camera/read_info", methods=["POST"])
def api_rig_camera_read_info(rig_id):
    """Read and cache camera information for one enabled rig."""
    try:
        rig = get_rig_manager().get_rig(rig_id)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    camera_identity = rig.devices.get("camera", {})
    trace_identity = {
        field: camera_identity[field]
        for field in ("serial", "fallback_physical_path")
        if isinstance(camera_identity, dict) and camera_identity.get(field)
    }

    runtime = get_camera_worker_runtime(log_fn=log.info)
    runtime.reconcile(load_rig_configuration())
    worker = runtime.get_for_rig(rig_id)
    if worker is None:
        return jsonify({
            "error": f"camera is not configured for rig {rig_id}",
            "code": "DEVICE_NOT_CONFIGURED",
            "rig_id": rig_id,
            "device_type": "camera",
        }), 409

    start_utc = datetime.now(timezone.utc)
    try:
        result = worker.read_info()
    except BusyDeviceError as exc:
        end_utc = datetime.now(timezone.utc)
        get_default_log().append({
            "kind": "camera.read_info",
            "rig_id": rig_id,
            **trace_identity,
            "start_utc": start_utc.isoformat(),
            "end_utc": end_utc.isoformat(),
            "duration_ms": (end_utc - start_utc).total_seconds() * 1000.0,
            "status": "error",
            "error": str(exc),
            "code": "CAMERA_BUSY",
        })
        return jsonify({
            "error": str(exc),
            "code": "CAMERA_BUSY",
            "rig_id": rig_id,
        }), 409
    except Exception as exc:
        end_utc = datetime.now(timezone.utc)
        get_default_log().append({
            "kind": "camera.read_info",
            "rig_id": rig_id,
            **trace_identity,
            "start_utc": start_utc.isoformat(),
            "end_utc": end_utc.isoformat(),
            "duration_ms": (end_utc - start_utc).total_seconds() * 1000.0,
            "status": "error",
            "error": str(exc),
        })
        raise

    end_utc = datetime.now(timezone.utc)
    trace_payload = {
        "rig_id": rig_id,
        **trace_identity,
        "start_utc": start_utc.isoformat(),
        "end_utc": end_utc.isoformat(),
        "duration_ms": (end_utc - start_utc).total_seconds() * 1000.0,
        "status": "success",
    }
    if isinstance(result, dict):
        for field in ("model", "battery"):
            if result.get(field) is not None:
                trace_payload[field] = result[field]
    trace_payload["kind"] = "camera.read_info"
    get_default_log().append(trace_payload)

    _state_store.update_section(
        "camera_info",
        {
            str(rig_id): {
                "last_read": datetime.now(timezone.utc).isoformat(),
                "data": result,
            }
        },
        persist=False,
    )
    return jsonify(result)


@app.route("/api/rigs/<int:rig_id>/camera/test_photo", methods=["POST"])
def api_rig_camera_test_photo(rig_id):
    """Capture one diagnostic photo with the camera for an enabled rig."""
    payload = request.get_json(silent=True)
    speed = payload.get("speed") if isinstance(payload, dict) else None
    if not isinstance(speed, str) or not speed.strip():
        return jsonify({
            "error": "speed must be a non-empty string",
            "code": "INVALID_TEST_PHOTO_SPEED",
        }), 400
    try:
        _normalized_speed_plan([speed])
    except (TypeError, ValueError, ZeroDivisionError, OverflowError):
        return jsonify({
            "error": "speed is not a valid camera exposure speed",
            "code": "INVALID_TEST_PHOTO_SPEED",
        }), 400

    try:
        rig = get_rig_manager().get_rig(rig_id)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    camera_identity = rig.devices.get("camera", {})
    trace_identity = {
        field: camera_identity[field]
        for field in ("serial", "fallback_physical_path")
        if isinstance(camera_identity, dict) and camera_identity.get(field)
    }

    runtime = get_camera_worker_runtime(log_fn=log.info)
    runtime.reconcile(load_rig_configuration())
    worker = runtime.get_for_rig(rig_id)
    if worker is None:
        return jsonify({
            "error": f"camera is not configured for rig {rig_id}",
            "code": "DEVICE_NOT_CONFIGURED",
            "rig_id": rig_id,
            "device_type": "camera",
        }), 409

    start_utc = datetime.now(timezone.utc)
    started_at = start_utc.isoformat()
    t0 = time.monotonic()
    try:
        result = worker.test_photo(
            [speed], photo_num_start=0, deadline=None
        )
    except BusyDeviceError as exc:
        end_utc = datetime.now(timezone.utc)
        get_default_log().append({
            "kind": "camera.test_photo",
            "rig_id": rig_id,
            **trace_identity,
            "start_utc": start_utc.isoformat(),
            "end_utc": end_utc.isoformat(),
            "duration_ms": (end_utc - start_utc).total_seconds() * 1000.0,
            "status": "error",
            "error": str(exc),
            "code": "CAMERA_BUSY",
        })
        return jsonify({
            "error": str(exc),
            "code": "CAMERA_BUSY",
            "rig_id": rig_id,
        }), 409
    except Exception as exc:
        end_utc = datetime.now(timezone.utc)
        get_default_log().append({
            "kind": "camera.test_photo",
            "rig_id": rig_id,
            **trace_identity,
            "start_utc": start_utc.isoformat(),
            "end_utc": end_utc.isoformat(),
            "duration_ms": (end_utc - start_utc).total_seconds() * 1000.0,
            "status": "error",
            "error": str(exc),
            "code": "CAMERA_UNAVAILABLE",
        })
        log.warning("Camera test photo unavailable for rig %s: %s", rig_id, exc)
        return jsonify({
            "error": "camera unavailable",
            "code": "CAMERA_UNAVAILABLE",
            "rig_id": rig_id,
        }), 404
    t1 = time.monotonic()

    end_utc = datetime.now(timezone.utc)
    trace_payload = {
        "rig_id": rig_id,
        **trace_identity,
        "start_utc": start_utc.isoformat(),
        "end_utc": end_utc.isoformat(),
        "duration_ms": (end_utc - start_utc).total_seconds() * 1000.0,
        "status": "success",
    }
    for field in ("frames", "planned", "detail"):
        if hasattr(result, field):
            trace_payload[field] = getattr(result, field)
    trace_payload["kind"] = "camera.test_photo"
    get_default_log().append(trace_payload)

    response = {
        "status": "ok",
        "rig_id": rig_id,
        "speed": str(speed),
        "started_at": started_at,
        "duration_s": round(t1 - t0, 6),
    }
    for field in ("frames", "planned", "detail"):
        if hasattr(result, field):
            response[field] = getattr(result, field)
    return jsonify(response)


@app.route("/api/rigs/<int:rig_id>/camera/sync_time", methods=["POST"])
def api_rig_camera_sync_time(rig_id):
    """Synchronize the camera worker belonging to one enabled rig."""
    try:
        rig = get_rig_manager().get_rig(rig_id)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    gps_state = _state_store.snapshot("gps") or {}
    utc_offset_minutes = gps_state.get("utc_offset_minutes")
    if utc_offset_minutes is None:
        return jsonify({
            "error": "Synchronisation GPS requise avant la synchronisation caméra."
        }), 409

    attempted = datetime.now(timezone.utc)
    reference = SimpleNamespace(
        datetime_utc=attempted,
        datetime_local=attempted + timedelta(minutes=utc_offset_minutes),
        timezone_name=gps_state.get("timezone_name"),
        utc_offset_minutes=utc_offset_minutes,
    )

    try:
        runtime = get_camera_worker_runtime(log_fn=log.info)
        runtime.reconcile(load_rig_configuration())
        worker = runtime.get_for_rig(rig_id)
        if worker is None:
            raise RuntimeError("camera worker is unavailable")
        result = worker.sync_datetime(reference)
    except Exception as exc:
        log.warning("Camera time sync unavailable for rig %s: %s", rig_id, exc)
        return jsonify({
            "error": "camera unavailable",
            "code": "CAMERA_UNAVAILABLE",
            "rig_id": rig_id,
        }), 404

    return jsonify(result)

@app.route("/api/camera/sync_time", methods=["POST"])
def api_camera_sync_time():
    inactive = require_device_active("camera")
    if inactive is not None:
        return inactive
    trigger_state = _state_store.snapshot("trigger") or {}
    if trigger_state.get("running"):
        return jsonify({
            "error": "Synchronisation caméra interdite pendant un trigger actif.",
            "code": "TRIGGER_RUNNING",
        }), 409

    if not _camera_sync_lock.acquire(blocking=False):
        return jsonify({"error": "Synchronisation caméra déjà en cours."}), 409

    camera_service = None
    try:
        gps_state = _state_store.snapshot("gps") or {}
        utc_offset_minutes = gps_state.get("utc_offset_minutes")
        if utc_offset_minutes is None:
            return jsonify({
                "error": "Synchronisation GPS requise avant la synchronisation caméra."
            }), 409

        attempted = datetime.now(timezone.utc)
        reference = SimpleNamespace(
            datetime_utc=attempted,
            datetime_local=attempted + timedelta(minutes=utc_offset_minutes),
            timezone_name=gps_state.get("timezone_name"),
            utc_offset_minutes=utc_offset_minutes,
        )
        camera_service = CameraService(log_fn=lambda message: log.info(message))
        try:
            result = camera_service.sync_datetime(reference)
        except Exception as exc:
            return jsonify({"error": f"Aucune caméra connectée : {exc}"}), 404

        persisted_result = dict(result)
        persisted_result.update({
            "attempted_at": attempted.isoformat(),
            "completed_at": datetime.now(timezone.utc).isoformat(),
        })
        _state_store.update_section(
            "camera", {"time_sync": persisted_result}, persist=True
        )
        return jsonify(result)
    finally:
        if camera_service is not None:
            camera_service.close()
        _camera_sync_lock.release()

@app.route("/api/eclipse/supported")
def api_eclipse_supported():
    """Liste des dates d'éclipse présentes dans le registre canonique."""
    return jsonify({"dates": eclipse_loader.list_supported_eclipses()})


@app.route("/api/eclipse/calculate", methods=["POST"])
def api_eclipse_calculate():
    global _calc_proc
    if _calc_proc and _calc_proc.poll() is None:
        return jsonify({"error": "Calcul déjà en cours."}), 409

    data    = request.json or {}
    lat     = data.get("lat")
    lon     = data.get("lon")
    alt     = data.get("alt", 0)
    eclipse = data.get("eclipse", "auto")

    if lat is None or lon is None:
        return jsonify({"error": "lat et lon requis"}), 400

    if eclipse == "auto" or not eclipse:
        try:
            today = datetime.now(timezone.utc).date()
            supported = eclipse_loader.list_supported_eclipses()
            eclipse_dates = sorted(
                datetime.strptime(date_iso, "%Y-%m-%d").date()
                for date_iso in supported
            )
            future = [date_value for date_value in eclipse_dates if date_value >= today]
            eclipse_date = (future[0] if future else eclipse_dates[-1]).isoformat()
        except (IndexError, TypeError, ValueError) as e:
            _append_log(
                f"calculateur Python : sélection auto impossible : {e}",
                "error", "calculator"
            )
            return jsonify({"error": "Aucune date d'éclipse supportée"}), 500
    else:
        eclipse_date = eclipse

    tz_used = calculate_timezone_from_coords(lat, lon, eclipse_date=eclipse_date)
    sign = '+' if tz_used >= 0 else ''
    val = int(tz_used) if tz_used == int(tz_used) else tz_used
    tz_str_dst = f"UTC{sign}{val}"
    _append_log(
        f"Timezone éclipse auto : {tz_str_dst} "
        f"(date éclipse : {eclipse_date})",
        "info", "calculator"
    )

    def _run():
        global _calc_proc
        _append_log(f"▶ calculateur Python : lat={lat} lon={lon} alt={alt} tz=+{tz_used} date={eclipse_date} (timezone auto)", "info", "calculator")
        with _state_lock:
            _state["calc_running"] = True

        # Émettre la timezone calculée au client avant le calcul
        socketio.emit("state_update", {"timezone_override": tz_str_dst})

        cmd = [sys.executable, str(CALC_SCRIPT),
               "--lat", str(lat), "--lon", str(lon),
               "--alt", str(alt), "--tz",  str(tz_used),
               "--date", eclipse_date,
               "--output", str(JSON_FILE)]

        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True,
                                bufsize=1, cwd=str(TRIGGER_DIR))
        _calc_proc = proc

        for line in proc.stdout:
            line = line.rstrip()
            if not line:
                continue
            level = _ansi_to_level(line)
            line  = _clean(line)
            if line:  # peut être vide après suppression des codes ANSI seuls
                _append_log(line, level, "calculator")

        proc.wait()
        rc = proc.returncode

        with _state_lock:
            _state["calc_running"] = False

        if rc == 0 and JSON_FILE.exists():
            result = _load_eclipse_json()
            with _state_lock:
                _state["eclipse"] = result
            _save_state()
            payload = {"status": "success", "data": result}
            payload["timezone_override"] = tz_str_dst
            socketio.emit("eclipse_calculated", payload)
            _append_log("✅ Calcul terminé — todayeclipse.json généré.", "success", "calculator")
        else:
            socketio.emit("eclipse_calculated", {"status": "error", "data": None})
            _append_log(f"❌ Calcul échoué (code {rc}).", "error", "calculator")

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"status": "started"})


def _erase_all_persistent_data():
    """Erase user/runtime persistent data while preserving bundled defaults."""
    removed = []

    files = [
        STATE_FILE,
        STATE_FILE.with_suffix(STATE_FILE.suffix + ".tmp"),
        LOGS_BUFFER_FILE,
        JSON_FILE,
        EVENTS_FILE,
        TRIGGER_DIR / "configs" / "rig" / "default.json",
    ]

    for path in files:
        try:
            if path.exists():
                path.unlink()
                removed.append(str(path))
        except OSError as exc:
            log.error("Unable to remove persistent file %s: %s", path, exc)
            raise

    camera_cfg_dir = TRIGGER_DIR / "configs" / "camera_cfg"
    if camera_cfg_dir.exists():
        for path in camera_cfg_dir.glob("*.json"):
            path.unlink()
            removed.append(str(path))

    circumstances_dir = TRIGGER_DIR / "configs" / "circumstances"
    if circumstances_dir.exists():
        for path in circumstances_dir.glob("*.json"):
            # Bundled dry-run fixture, not user persistence.
            if path.name == "dryrun_short.json":
                continue
            path.unlink()
            removed.append(str(path))

    return removed


@app.route("/api/system/erase-persistent-data-and-reboot", methods=["POST"])
def api_system_erase_persistent_data_and_reboot():
    payload = request.get_json(silent=True) or {}

    if payload.get("confirmation") != "ERASE ALL PERSISTANT DATA & REBOOT":
        return jsonify({
            "error": "Explicit confirmation is required.",
            "code": "CONFIRMATION_REQUIRED",
        }), 400

    trigger_state = _state_store.snapshot("trigger") or {}
    if trigger_state.get("running"):
        return jsonify({
            "error": "Persistent data cannot be erased while a trigger is running.",
            "code": "TRIGGER_RUNNING",
        }), 409

    def erase_and_reboot():
        import os
        import subprocess
        import threading
        import time

        # Allow the HTTP response to reach the browser first.
        time.sleep(0.5)

        try:
            removed = _erase_all_persistent_data()
            log.warning(
                "Persistent data erased before reboot: %s",
                ", ".join(removed) if removed else "nothing to remove",
            )

            try:
                os.sync()
            except AttributeError:
                pass

            subprocess.run(
                ["sudo", "-n", "/usr/bin/systemctl", "reboot"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            log.exception("Unable to erase persistent data and reboot")

    import threading
    threading.Thread(
        target=erase_and_reboot,
        name="erase-persistent-data-and-reboot",
        daemon=True,
    ).start()

    return jsonify({
        "status": "rebooting",
        "message": "Persistent data erase and reboot scheduled.",
    })


@app.route("/api/eclipse/current")
def api_eclipse_current():
    # Priorité à l'éclipse restaurée depuis l'état persistant.
    with _state_lock:
        mem = _state.get("eclipse", "unset")
    if mem is None:
        return jsonify({"error": "Aucun calcul disponible"}), 404
    # Priorité : état mémoire → fichier todayeclipse.json
    if mem and mem != "unset":
        return jsonify(mem)
    data = _load_eclipse_json()
    if data:
        return jsonify(data)
    return jsonify({"error": "Aucun calcul disponible"}), 404

@app.route("/api/eclipse/override", methods=["POST"])
def api_eclipse_override():
    """
    Met à jour todayeclipse.json avec les valeurs saisies dans l'UI.
    Seuls les champs fournis sont mis à jour (merge partiel).
    """
    updates = request.json or {}
    if not updates:
        return jsonify({"error": "Aucune donnée"}), 400

    # Charger le fichier existant
    data = _load_eclipse_json()
    if not data:
        with _state_lock:
            data = _state.get("eclipse") or {}

    # Appliquer les mises à jour (merge profond pour les sous-objets)
    for key, val in updates.items():
        if isinstance(val, dict) and key in data and isinstance(data[key], dict):
            data[key].update(val)
        else:
            data[key] = val

    # Synchroniser les clés plates lues par eclipse_trigger.py
    # depuis les sous-objets mis à jour par l'UI
    if "phase1a" in data:
        p1a = data["phase1a"]
        if "interval_s"   in p1a: data["interval_partial"]         = int(p1a["interval_s"])
        if "speed_denom"  in p1a: data["shutterspeed_partial"]      = f"1/{p1a['speed_denom']}"
    if "diamond_ring" in data:
        dr = data["diamond_ring"]
        if "interval_s"   in dr:  data["interval_diamond_ring"]     = int(dr["interval_s"])
        if "duration_s"   in dr:  data["duree_diamond_ring"]        = int(dr["duration_s"])
        if "speed_denom"  in dr:  data["shutterspeed_diamondring"]  = f"1/{dr['speed_denom']}"
    if "phase3b" in data:
        p3b = data["phase3b"]
        if "interval_s"   in p3b: data["interval_partial"]         = int(p3b["interval_s"])
        if "speed_denom"  in p3b: data["shutterspeed_partial"]     = f"1/{p3b['speed_denom']}"

    # Sauvegarder
    try:
        with open(JSON_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        with _state_lock:
            _state["eclipse"] = data
        _save_state()
        _append_log(f"⚙ Paramètres mis à jour manuellement.", "info", "override")
        socketio.emit("eclipse_calculated", {"status": "success", "data": data})
        meta = {
            key: data[key]
            for key in ("_date", "_date_utc", "title", "_type")
            if key in data
        }
        phases_local = {
            phase: data[f"{phase}_local"]
            for phase in ("C1", "C2", "TMAX", "C3", "C4")
            if f"{phase}_local" in data
        }
        if phases_local:
            meta["phases_local"] = phases_local
        circumstances = _state_store.update_section(
            "circumstances",
            {"loaded": True, "active_file": JSON_FILE.name, "meta": meta},
            persist=True,
        )
        socketio.emit("status_update", _status_update_payload({
            "circumstances": circumstances,
        }))
        return jsonify({"status": "ok", "circumstances": circumstances})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ── Routes configs JSON ──────────────────────────────────────────────────────
CONFIGS_DIR = PROJECT_DIR / "configs"
CONFIGS_DIR.mkdir(parents=True, exist_ok=True)  # créer si absent

def _unique_config_files(*patterns):
    """Retourne, par nom, les JSON trouvés par les motifs indiqués."""
    files = {}
    for pattern in patterns:
        for path in CONFIGS_DIR.glob(pattern):
            if path.is_file():
                files.setdefault(path.name, path)
    return [files[name] for name in sorted(files)]

def _is_circumstances_config(path):
    """Écarte les fichiers réservés à la capture, au debug ou aux modèles."""
    name = path.name.lower()
    stem = path.stem.lower()
    return (not name.startswith("camera_")
            and "debug" not in name
            and not stem.startswith("template")
            and not stem.startswith("_"))

def _resolve_config_file(filename, subdirectory):
    """Résout une config dans les emplacements indiqués puis à la racine."""
    root_path = CONFIGS_DIR / filename
    if isinstance(subdirectory, str):
        if root_path.is_file():
            return root_path
        subdirectories = (subdirectory,)
    else:
        subdirectories = subdirectory
    for directory in subdirectories:
        subdirectory_path = CONFIGS_DIR / directory / filename
        if subdirectory_path.is_file():
            return subdirectory_path
    return root_path

@app.route("/api/configs/list", methods=["GET"])
def api_configs_list():
    """Retourne la liste des fichiers .json dans le dossier configs/."""
    try:
        files = sorted([f.name for f in CONFIGS_DIR.glob("*.json")])
        return jsonify({"files": files})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/configs/list_eclipse", methods=["GET"])
def api_configs_list_eclipse():
    """Retourne uniquement les fichiers de circonstances éclipse."""
    try:
        active_file = _state_store.snapshot("circumstances").get("active_file")
        circumstances_root = (CONFIGS_DIR / "circumstances").resolve()
        files_by_name = {}

        def add_file(path, source_dir, allowed_root):
            try:
                resolved = path.resolve()
                if (resolved.parent != allowed_root
                        or resolved.suffix.lower() != ".json"):
                    return
                with open(resolved, encoding="utf-8") as f:
                    json.load(f)
            except (OSError, ValueError, TypeError):
                return
            files_by_name.setdefault(path.name, {
                "name": path.name,
                "dir": source_dir,
                "active": path.name == active_file,
            })

        if JSON_FILE.is_file():
            add_file(JSON_FILE, "trigger", TRIGGER_DIR.resolve())

        for path in _unique_config_files("circumstances/*.json"):
            if not _is_circumstances_config(path):
                continue
            add_file(path, "circumstances", circumstances_root)

        return jsonify({"files": [files_by_name[name]
                                  for name in sorted(files_by_name)]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/configs/circumstances/clean", methods=["POST"])
def api_configs_circumstances_clean():
    """Supprime les fichiers JSON de premier niveau des circonstances."""
    base_dir = CONFIGS_DIR / "circumstances"
    deleted = 0
    errors = []

    if not base_dir.exists():
        return jsonify({"status": "ok", "deleted": deleted, "errors": errors})

    for entry in base_dir.iterdir():
        if (entry.is_symlink()
                or not entry.is_file()
                or entry.suffix.lower() != ".json"):
            continue
        try:
            entry.unlink()
            deleted += 1
        except OSError as err:
            errors.append({"file": entry.name, "error": str(err)})

    return jsonify({"status": "ok", "deleted": deleted, "errors": errors})


@app.route("/api/configs/list_photo", methods=["GET"])
def api_configs_list_photo():
    """List Photo Setup configurations stored in photo_cfg/."""
    try:
        base_dir = CONFIGS_DIR / "photo_cfg"
        files = sorted(
            path.name
            for path in base_dir.glob("*.json")
            if path.is_file()
        )
        return jsonify({"files": files})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/configs/load_photo/<filename>", methods=["GET"])
def api_configs_load_photo(filename):
    """Load one Photo Setup configuration."""
    try:
        filename = Path(filename).name
        path = CONFIGS_DIR / "photo_cfg" / filename
        if not path.exists() or not path.is_file() or path.suffix.lower() != ".json":
            return jsonify({"error": "Fichier introuvable"}), 404

        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)

        if not isinstance(data, dict):
            return jsonify({"error": "Invalid Photo Setup configuration"}), 400

        config_type = data.get("config_type")
        if config_type not in (None, "photo_setup"):
            return jsonify({"error": "Invalid Photo Setup configuration type"}), 400

        return jsonify(data)

    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/configs/save_photo", methods=["POST"])
def api_configs_save_photo():
    """Save one Photo Setup configuration into photo_cfg/."""
    body = request.get_json(silent=True) or {}
    requested = str(body.get("filename", "")).strip()
    data = body.get("data")

    if not requested:
        return jsonify({"error": "Nom de fichier manquant"}), 400
    if not isinstance(data, dict):
        return jsonify({"error": "Configuration invalide"}), 400

    data = deepcopy(data)
    data["config_type"] = "photo_setup"

    # Exposure optimization must never leak into Photo Setup files.
    data.pop("exposure_correction", None)
    data.pop("atmospheric_attenuation_enabled", None)
    data.pop("rigs", None)

    shutter_speeds = [
        "8", "4", "2", "1", "1/2", "1/4", "1/8", "1/15", "1/30",
        "1/60", "1/125", "1/250", "1/500", "1/1000", "1/2000",
        "1/4000", "1/8000",
    ]
    shutter_indices = {
        speed: index for index, speed in enumerate(shutter_speeds)
    }

    phases = data.get("phases")
    if not isinstance(phases, dict):
        return jsonify({"error": "Phases invalides ou manquantes"}), 400

    for phase_name in ("partial", "diamond_ring", "totality"):
        phase = phases.get(phase_name)
        if not isinstance(phase, dict):
            return jsonify({
                "error": f"Phase invalide ou manquante : {phase_name}"
            }), 400

        shutter_min = phase.get("shutter_min")
        shutter_max = phase.get("shutter_max")

        if shutter_min not in shutter_indices or shutter_max not in shutter_indices:
            return jsonify({
                "error": f"Vitesse d'obturation invalide : {phase_name}"
            }), 400

        if shutter_indices[shutter_min] > shutter_indices[shutter_max]:
            return jsonify({
                "error": f"Plage d'obturation inversée : {phase_name}"
            }), 400

        if "step_ev" in phase and phase["step_ev"] != 1.0:
            return jsonify({
                "error": f"step_ev invalide : {phase_name}"
            }), 400

        phase.setdefault("step_ev", 1.0)

    filename = requested
    if not filename.endswith(".json"):
        filename += ".json"
    if not filename.startswith("photo_"):
        filename = "photo_" + filename
    filename = Path(filename).name

    try:
        base_dir = CONFIGS_DIR / "photo_cfg"
        base_dir.mkdir(parents=True, exist_ok=True)

        destination = base_dir / filename
        if destination.exists() and body.get("overwrite") is not True:
            return jsonify({
                "error": "Le fichier existe déjà",
                "filename": filename,
            }), 409

        with open(destination, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=4, ensure_ascii=False)

        return jsonify({
            "status": "ok",
            "filename": filename,
            "saved": {"filename": filename, "data": data},
        })

    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/configs/photo_cfg/clean", methods=["POST"])
def api_configs_photo_clean():
    """Delete all Photo Setup JSON files."""
    base_dir = CONFIGS_DIR / "photo_cfg"
    deleted = 0
    errors = []

    if not base_dir.exists():
        return jsonify({
            "status": "ok",
            "deleted": 0,
            "errors": [],
        })

    for entry in base_dir.iterdir():
        if (
            entry.is_symlink()
            or not entry.is_file()
            or entry.suffix.lower() != ".json"
        ):
            continue

        try:
            entry.unlink()
            deleted += 1
        except OSError as exc:
            errors.append({
                "file": entry.name,
                "error": str(exc),
            })

    return jsonify({
        "status": "ok",
        "deleted": deleted,
        "errors": errors,
    })


@app.route("/api/configs/list_exposure_opt", methods=["GET"])
def api_configs_list_exposure_opt():
    """List Exposure Optimization configurations."""
    try:
        base_dir = CONFIGS_DIR / "exposure_opt"
        files = sorted(
            path.name
            for path in base_dir.glob("*.json")
            if path.is_file()
        )
        return jsonify({"files": files})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/configs/load_exposure_opt/<filename>", methods=["GET"])
def api_configs_load_exposure_opt(filename):
    """Load one Exposure Optimization configuration."""
    try:
        filename = Path(filename).name
        path = CONFIGS_DIR / "exposure_opt" / filename

        if not path.exists() or not path.is_file() or path.suffix.lower() != ".json":
            return jsonify({"error": "Fichier introuvable"}), 404

        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)

        if (
            not isinstance(data, dict)
            or data.get("config_type") != "exposure_optimization"
        ):
            return jsonify({
                "error": "Invalid Exposure Optimization configuration"
            }), 400

        return jsonify(data)

    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/configs/save_exposure_opt", methods=["POST"])
def api_configs_save_exposure_opt():
    """Save one Exposure Optimization configuration."""
    body = request.get_json(silent=True) or {}
    requested = str(body.get("filename", "")).strip()
    data = body.get("data")

    if not requested:
        return jsonify({"error": "Nom de fichier manquant"}), 400
    if not isinstance(data, dict):
        return jsonify({"error": "Configuration invalide"}), 400

    data = deepcopy(data)
    data["schema_version"] = 1
    data["config_type"] = "exposure_optimization"

    rigs = data.get("rigs")
    if not isinstance(rigs, list) or len(rigs) != 4:
        return jsonify({
            "error": "Exposure Optimization must contain exactly 4 RIGs"
        }), 400

    seen = set()

    try:
        for rig in rigs:
            if not isinstance(rig, dict):
                raise ValueError("Invalid RIG entry")

            rig_id = rig.get("rig_id")
            if (
                not isinstance(rig_id, int)
                or isinstance(rig_id, bool)
                or not 1 <= rig_id <= 4
                or rig_id in seen
            ):
                raise ValueError("Invalid or duplicate rig_id")

            seen.add(rig_id)

            # Optics belongs to the canonical RIG configuration.
            # Accept legacy Exposure Optimization payloads but never
            # persist their optics section.
            rig.pop("optics", None)

            photo = rig.get("photo", {})

            if not isinstance(photo, dict):
                raise ValueError(f"Invalid RIG {rig_id} configuration")

            tolerance = photo.get("motion_tolerance_px")
            if tolerance is not None:
                _validate_positive_number(
                    tolerance,
                    f"rigs[{rig_id}].photo.motion_tolerance_px",
                )

            iso_max = photo.get("iso_max")
            if iso_max is not None:
                _validate_positive_number(
                    iso_max,
                    f"rigs[{rig_id}].photo.iso_max",
                    integer=True,
                )

            for field in (
                "anti_trailing_enabled",
                "iso_compensation_enabled",
            ):
                if field in photo and not isinstance(photo[field], bool):
                    raise ValueError(
                        f"rigs[{rig_id}].photo.{field} must be a boolean"
                    )

        if not isinstance(
            data.get("atmospheric_attenuation_enabled"),
            bool,
        ):
            raise ValueError(
                "atmospheric_attenuation_enabled must be a boolean"
            )

    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    filename = requested
    if not filename.endswith(".json"):
        filename += ".json"
    if not filename.startswith("expo_"):
        filename = "expo_" + filename
    filename = Path(filename).name

    try:
        base_dir = CONFIGS_DIR / "exposure_opt"
        base_dir.mkdir(parents=True, exist_ok=True)

        destination = base_dir / filename

        if destination.exists() and body.get("overwrite") is not True:
            return jsonify({
                "error": "Le fichier existe déjà",
                "filename": filename,
            }), 409

        with open(destination, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=4, ensure_ascii=False)

        return jsonify({
            "status": "ok",
            "filename": filename,
            "saved": {"filename": filename, "data": data},
        })

    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/configs/exposure_opt/clean", methods=["POST"])
def api_configs_exposure_opt_clean():
    """Delete all Exposure Optimization JSON files."""
    base_dir = CONFIGS_DIR / "exposure_opt"
    deleted = 0
    errors = []

    if not base_dir.exists():
        return jsonify({
            "status": "ok",
            "deleted": 0,
            "errors": [],
        })

    for entry in base_dir.iterdir():
        if (
            entry.is_symlink()
            or not entry.is_file()
            or entry.suffix.lower() != ".json"
        ):
            continue

        try:
            entry.unlink()
            deleted += 1
        except OSError as exc:
            errors.append({
                "file": entry.name,
                "error": str(exc),
            })

    return jsonify({
        "status": "ok",
        "deleted": deleted,
        "errors": errors,
    })


@app.route("/api/configs/load_circumstances/<filename>", methods=["GET"])
def api_configs_load_circumstances(filename):
    """Load one circumstances file without changing the active eclipse."""
    try:
        filename = Path(filename).name
        path = _resolve_config_file(filename, "circumstances")

        if (
            not path.exists()
            or not path.is_file()
            or path.suffix.lower() != ".json"
        ):
            return jsonify({"error": "Fichier introuvable"}), 404

        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)

        if not isinstance(data, dict):
            return jsonify({"error": "Invalid circumstances configuration"}), 400

        required = ("TSTART", "C1", "C2", "C3", "C4", "TEND")
        missing = [field for field in required if not data.get(field)]

        if missing:
            return jsonify({
                "error": "Invalid circumstances configuration",
                "missing": missing,
            }), 400

        return jsonify(data)

    except Exception as exc:
        return jsonify({"error": str(exc)}), 500



@app.route("/api/sequencer/compile", methods=["POST"])
def api_sequencer_compile():
    """Compile and persist a deterministic execution plan.

    This route never initializes or triggers camera hardware.
    """
    body = request.get_json(silent=True) or {}

    raw_sequence_file = body.get("sequence_file")

    if (
        not isinstance(raw_sequence_file, str)
        or not raw_sequence_file.strip()
    ):
        return jsonify({
            "error": "Missing Sequencer input: sequence_file",
        }), 400

    sequence_file = raw_sequence_file.strip()

    if Path(sequence_file).name != sequence_file:
        return jsonify({
            "error": "Invalid Sequencer input filename: sequence_file",
        }), 400

    raw_timing_files = body.get("camera_timing_files")

    if not isinstance(raw_timing_files, dict):
        return jsonify({
            "error": "camera_timing_files must be an object",
        }), 400

    timing_files = {}

    for raw_rig_id, raw_filename in raw_timing_files.items():
        try:
            rig_id = int(raw_rig_id)
        except (TypeError, ValueError):
            return jsonify({
                "error": f"Invalid RIG id in camera_timing_files: {raw_rig_id}",
            }), 400

        if rig_id < 1 or rig_id > 4:
            return jsonify({
                "error": f"Invalid RIG id in camera_timing_files: {rig_id}",
            }), 400

        if (
            not isinstance(raw_filename, str)
            or not raw_filename.strip()
        ):
            return jsonify({
                "error": f"Missing camera timing file for RIG {rig_id}",
            }), 400

        filename = raw_filename.strip()

        if Path(filename).name != filename:
            return jsonify({
                "error": f"Invalid camera timing filename for RIG {rig_id}",
            }), 400

        timing_files[rig_id] = filename

    try:
        plan, lines = compile_execution_plan_from_files(
            configs_dir=CONFIGS_DIR,
            sequence_file=sequence_file,
            camera_timing_files=timing_files,
        )
    except SequencerCompileError as exc:
        return jsonify({
            "error": str(exc),
            "code": "SEQUENCER_COMPILE_FAILED",
        }), 400
    except Exception as exc:
        return jsonify({
            "error": str(exc),
            "code": "SEQUENCER_COMPILE_ERROR",
        }), 500

    output_dir = CONFIGS_DIR / "execution_plan"
    output_dir.mkdir(parents=True, exist_ok=True)

    source_stem = Path(sequence_file).stem

    output_name = (
        f"execution_plan_{source_stem}.json"
    )

    destination = output_dir / output_name

    try:
        with destination.open(
            "w",
            encoding="utf-8",
        ) as handle:
            json.dump(
                plan,
                handle,
                indent=2,
                ensure_ascii=False,
            )
            handle.write("\n")
    except OSError as exc:
        return jsonify({
            "error": f"execution plan could not be saved: {exc}",
            "code": "SEQUENCER_SAVE_FAILED",
        }), 500

    return jsonify({
        "status": "ok",
        "filename": output_name,
        "plan": plan,
        "lines": lines,
    })


@app.route("/api/configs/list_camera_timing", methods=["GET"])
def api_configs_list_camera_timing():
    """List calibrated camera timing profiles."""
    try:
        base_dir = CONFIGS_DIR / "camera_timing"
        files = sorted(
            path.name
            for path in base_dir.glob("*.json")
            if path.is_file()
        )
        return jsonify({"files": files})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/configs/list_sequence", methods=["GET"])
def api_configs_list_sequence():
    """List saved Sequencer configurations."""
    try:
        base_dir = CONFIGS_DIR / "sequence"
        files = sorted(
            path.name
            for path in base_dir.glob("*.json")
            if path.is_file()
        )
        return jsonify({"files": files})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/configs/load_sequence/<filename>", methods=["GET"])
def api_configs_load_sequence(filename):
    """Load one Sequencer configuration."""
    try:
        filename = Path(filename).name
        path = CONFIGS_DIR / "sequence" / filename

        if (
            not path.exists()
            or not path.is_file()
            or path.suffix.lower() != ".json"
        ):
            return jsonify({"error": "Fichier introuvable"}), 404

        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)

        if (
            not isinstance(data, dict)
            or data.get("config_type") != "sequence"
        ):
            return jsonify({
                "error": "Invalid Sequencer configuration"
            }), 400

        return jsonify(data)

    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/configs/save_sequence", methods=["POST"])
def api_configs_save_sequence():
    """Save one Sequencer configuration."""
    body = request.get_json(silent=True) or {}
    requested = str(body.get("filename", "")).strip()
    data = body.get("data")

    if not requested:
        return jsonify({"error": "Nom de fichier manquant"}), 400

    if not isinstance(data, dict):
        return jsonify({"error": "Configuration invalide"}), 400

    requested_path = Path(requested)

    if (
        requested_path.is_absolute()
        or "/" in requested
        or "\\" in requested
        or ".." in requested_path.parts
    ):
        return jsonify({"error": "Nom de fichier invalide"}), 400

    filename = requested
    if not filename.endswith(".json"):
        filename += ".json"

    filename = Path(filename).name

    data = deepcopy(data)
    data["schema_version"] = 1
    data["config_type"] = "sequence"

    required_fields = (
        "circumstances_file",
        "photo_setup_file",
        "exposure_opt_file",
    )

    for field in required_fields:
        value = data.get(field)

        if not isinstance(value, str) or not value.strip():
            return jsonify({
                "error": f"Missing Sequencer input: {field}"
            }), 400

        data[field] = Path(value.strip()).name

    margin_min = data.get("sequence_margin_min", 60)

    if (
        isinstance(margin_min, bool)
        or not isinstance(margin_min, (int, float))
        or margin_min < 0
    ):
        return jsonify({
            "error": "Invalid sequence_margin_min"
        }), 400

    data["sequence_margin_min"] = margin_min

    base_dir = CONFIGS_DIR / "sequence"
    base_dir.mkdir(parents=True, exist_ok=True)

    destination = base_dir / filename

    try:
        if destination.resolve().parent != base_dir.resolve():
            return jsonify({"error": "Nom de fichier invalide"}), 400

        if destination.exists() and body.get("overwrite") is not True:
            return jsonify({
                "error": "Le fichier existe déjà",
                "filename": filename,
            }), 409

        with open(destination, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False)
            handle.write("\n")

        return jsonify({
            "status": "ok",
            "filename": filename,
            "saved": {
                "filename": filename,
                "data": data,
            },
        })

    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@app.route("/api/configs/sequence/clean", methods=["POST"])
def api_configs_sequence_clean():
    """Delete all saved Sequencer JSON files."""
    base_dir = CONFIGS_DIR / "sequence"
    deleted = 0
    errors = []

    if not base_dir.exists():
        return jsonify({
            "status": "ok",
            "deleted": 0,
            "errors": [],
        })

    for entry in base_dir.iterdir():
        if (
            entry.is_symlink()
            or not entry.is_file()
            or entry.suffix.lower() != ".json"
        ):
            continue

        try:
            entry.unlink()
            deleted += 1
        except OSError as exc:
            errors.append({
                "file": entry.name,
                "error": str(exc),
            })

    return jsonify({
        "status": "ok",
        "deleted": deleted,
        "errors": errors,
    })


@app.route("/api/configs/list_camera", methods=["GET"])
def api_configs_list_camera():
    """Retourne les configurations appareil photo de camera_cfg/."""
    try:
        camera_configs_dir = CONFIGS_DIR / "camera_cfg"
        files = sorted(
            path.name for path in camera_configs_dir.glob("*.json")
            if path.is_file()
        )
        return jsonify({"files": files})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/configs/camera_cfg/clean", methods=["POST"])
def api_configs_camera_clean():
    """Supprime les fichiers JSON de premier niveau de camera_cfg/."""
    base_dir = CONFIGS_DIR / "camera_cfg"
    deleted = 0
    errors = []

    if not base_dir.exists():
        return jsonify({"status": "ok", "deleted": deleted, "errors": errors})

    for entry in base_dir.iterdir():
        if (entry.is_symlink()
                or not entry.is_file()
                or entry.suffix.lower() != ".json"):
            continue
        try:
            entry.unlink()
            deleted += 1
        except OSError as err:
            errors.append({"file": entry.name, "error": str(err)})

    return jsonify({"status": "ok", "deleted": deleted, "errors": errors})

@app.route("/api/configs/load/<filename>", methods=["GET"])
def api_configs_load(filename):
    """Charge un fichier config JSON et le retourne."""
    try:
        path = CONFIGS_DIR / filename
        if not path.exists() or path.suffix != ".json":
            return jsonify({"error": "Fichier introuvable"}), 404
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/configs/load_camera/<filename>", methods=["GET"])
def api_configs_load_camera(filename):
    """Charge un fichier de configuration appareil photo."""
    try:
        filename = Path(filename).name
        path = _resolve_config_file(filename, ("camera_cfg", "capture"))
        if not path.exists() or path.suffix != ".json":
            return jsonify({"error": "Fichier introuvable"}), 404
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/configs/save_camera", methods=["POST"])
def api_configs_save_camera():
    """Sauvegarde un fichier de configuration appareil photo."""
    body = request.json or {}
    filename = body.get("filename", "").strip()
    data     = body.get("data", {})
    if not filename:
        return jsonify({"error": "Nom de fichier manquant"}), 400
    if not filename.endswith(".json"):
        filename += ".json"
    if not filename.startswith("camera_"):
        filename = "camera_" + filename
    filename = Path(filename).name

    shutter_speeds = [
        "8", "4", "2", "1", "1/2", "1/4", "1/8", "1/15", "1/30",
        "1/60", "1/125", "1/250", "1/500", "1/1000", "1/2000",
        "1/4000", "1/8000",
    ]
    shutter_indices = {speed: index for index, speed in enumerate(shutter_speeds)}
    phases = data.get("phases") if isinstance(data, dict) else None
    for phase_name in ("partial", "diamond_ring", "totality"):
        phase = phases.get(phase_name) if isinstance(phases, dict) else None
        if not isinstance(phase, dict):
            return jsonify({"error": f"Phase invalide ou manquante : {phase_name}"}), 400
        shutter_min = phase.get("shutter_min")
        shutter_max = phase.get("shutter_max")
        if shutter_min not in shutter_indices or shutter_max not in shutter_indices:
            return jsonify({"error": f"Vitesse d'obturation invalide : {phase_name}"}), 400
        # The canonical list is slowest to fastest, so min must precede max.
        if shutter_indices[shutter_min] > shutter_indices[shutter_max]:
            return jsonify({"error": f"Plage d'obturation inversée : {phase_name}"}), 400
        if "step_ev" in phase and phase["step_ev"] != 1.0:
            return jsonify({"error": f"step_ev invalide : {phase_name}"}), 400
        phase.setdefault("step_ev", 1.0)

    try:
        destination_dir = CONFIGS_DIR / "camera_cfg"
        destination_dir.mkdir(parents=True, exist_ok=True)
        destination = destination_dir / filename
        overwriting = destination.exists()
        if overwriting and body.get("overwrite") is not True:
            return jsonify({"error": "Le fichier existe déjà", "filename": filename}), 409
        with open(destination, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        _append_log(f"💾 Config appareil sauvegardée : {filename}", "success", "system")

        capture = _state_store.snapshot("capture")
        if (overwriting and body.get("overwrite") is True
                and capture.get("active_file") == filename):
            meta = {
                key: data[key]
                for key in ("_type", "_comment")
                if key in data
            }
            capture = _state_store.update_section(
                "capture",
                {"loaded": True, "active_file": filename, "meta": meta},
                persist=True,
            )
            socketio.emit("status_update", _status_update_payload({
                "capture": capture,
            }))
            return jsonify({"status": "ok", "filename": filename, "capture": capture})

        return jsonify({
            "status": "ok",
            "filename": filename,
            "saved": {"filename": filename, "data": data},
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/configs/save", methods=["POST"])
def api_configs_save():
    """Sauvegarde le contenu courant de todayeclipse.json sous un nouveau nom."""
    body = request.json or {}
    requested = body.get("filename", "").strip()
    if not requested:
        return jsonify({"error": "Nom de fichier manquant"}), 400
    requested_path = Path(requested)
    if (requested_path.is_absolute()
            or "/" in requested
            or os.sep in requested
            or (os.altsep and os.altsep in requested)
            or ".." in requested_path.parts):
        return jsonify({"error": "Nom de fichier invalide"}), 400
    filename = requested
    if not filename.endswith(".json"):
        filename += ".json"
    data = _load_eclipse_json()
    if not data:
        return jsonify({"error": "Aucune configuration active"}), 400
    try:
        CONFIGS_DIR.mkdir(parents=True, exist_ok=True)
        destination_dir = (
            CONFIGS_DIR if filename == "todayeclipse.json"
            else CONFIGS_DIR / "circumstances"
        )
        destination_dir.mkdir(parents=True, exist_ok=True)
        destination = destination_dir / filename
        if destination.resolve().parent != destination_dir.resolve():
            return jsonify({"error": "Nom de fichier invalide"}), 400
        filename = destination.resolve().name
        overwriting = destination.exists()
        if overwriting and body.get("overwrite") is not True:
            return jsonify({"error": "Le fichier existe déjà", "filename": filename}), 409
        with open(destination, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
        _append_log(f"💾 Config sauvegardée : {filename}", "success", "system")

        circumstances = _state_store.snapshot("circumstances")
        if (overwriting and body.get("overwrite") is True
                and circumstances.get("active_file") == filename):
            meta = {
                key: data[key]
                for key in ("_date", "_date_utc", "title", "_type")
                if key in data
            }
            phases_local = {
                phase: data[f"{phase}_local"]
                for phase in ("C1", "C2", "TMAX", "C3", "C4")
                if f"{phase}_local" in data
            }
            if phases_local:
                meta["phases_local"] = phases_local
            circumstances = _state_store.update_section(
                "circumstances",
                {"loaded": True, "active_file": filename, "meta": meta},
                persist=True,
            )
            socketio.emit("status_update", _status_update_payload({
                "circumstances": circumstances,
            }))
            return jsonify({
                "status": "ok",
                "filename": filename,
                "circumstances": circumstances,
            })

        return jsonify({
            "status": "ok",
            "filename": filename,
            "saved": {"filename": filename, "data": data},
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/eclipse/reset", methods=["POST"])
def api_eclipse_reset():
    try:
        if JSON_FILE.exists():
            JSON_FILE.unlink()
        with _state_lock:
            _state["eclipse"] = None
        circumstances = _state_store.update_section(
            "circumstances",
            {"loaded": False, "active_file": None, "meta": {}},
            persist=True,
        )
        _append_log("🗑 todayeclipse.json supprimé.", "warning", "debug")
        socketio.emit("status_update", _status_update_payload({
            "circumstances": circumstances,
        }))
        return jsonify({"status": "ok", "circumstances": circumstances})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ══════════════════════════════════════════════════════════════════════════════

@app.route("/api/configs/list_trigger", methods=["GET"])
def api_configs_list_trigger():
    """Retourne la liste des JSON de circonstances disponibles pour le trigger :
    - todayeclipse.json (actif dans TRIGGER_DIR)
    - tous les *.json de CONFIGS_DIR qui ne sont pas camera_* ni debug_* ni template*
    """
    files = []
    # Fichier actif en premier
    if JSON_FILE.exists():
        files.append({"name": "todayeclipse.json", "active": True, "dir": "trigger"})
    # Fichiers configs circonstances uniquement
    try:
        for f in _unique_config_files("*.json", "circumstances/*.json"):
            if not _is_circumstances_config(f) or f.name in {item["name"] for item in files}:
                continue
            source_dir = "circumstances" if f.parent == CONFIGS_DIR / "circumstances" else "configs"
            files.append({"name": f.name, "active": False, "dir": source_dir})
    except Exception:
        pass
    return jsonify({"files": files})

@app.route("/api/trigger/select", methods=["POST"])
def api_trigger_select():
    """Charge un fichier JSON et l'applique comme todayeclipse.json actif."""
    body = request.json or {}
    filename = body.get("filename", "").strip()
    source_dir = body.get("dir", "configs")

    if not filename or not filename.endswith(".json"):
        return jsonify({"error": "Nom de fichier invalide"}), 400
    filename = Path(filename).name  # sécurité anti-traversal

    if source_dir == "trigger":
        src = JSON_FILE
    elif source_dir == "circumstances":
        src = CONFIGS_DIR / "circumstances" / filename
    else:
        src = _resolve_config_file(filename, "circumstances")

    if not src.exists():
        return jsonify({"error": f"Fichier introuvable : {filename}"}), 404

    try:
        with open(src, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        return jsonify({"error": f"JSON illisible : {e}"}), 400

    # Copier comme todayeclipse.json si ce n'est pas déjà lui
    if src != JSON_FILE:
        import shutil as _shutil
        _shutil.copy2(src, JSON_FILE)

    with _state_lock:
        _state["eclipse"] = data
    _save_state()

    meta = {
        key: data[key]
        for key in ("_date", "_date_utc", "title", "_type")
        if key in data
    }
    phases_local = {
        phase: data[f"{phase}_local"]
        for phase in ("C1", "C2", "TMAX", "C3", "C4")
        if f"{phase}_local" in data
    }
    if phases_local:
        meta["phases_local"] = phases_local

    circumstances = _state_store.update_section(
        "circumstances",
        {"loaded": True, "active_file": filename, "meta": meta},
        persist=True,
    )
    socketio.emit("eclipse_calculated", {"status": "success", "data": data})
    socketio.emit("status_update", _status_update_payload({
        "circumstances": circumstances,
    }))
    _append_log(f"📂 Config chargée : {filename}", "info", "trigger")
    return jsonify({"status": "ok", "data": data, "circumstances": circumstances})

@app.route("/api/trigger/select_camera", methods=["POST"])
def api_trigger_select_camera():
    """Sélectionne un fichier de configuration appareil photo."""
    body = request.json or {}
    filename = body.get("filename", "").strip()
    if not filename or not filename.endswith(".json"):
        return jsonify({"error": "Nom de fichier invalide"}), 400
    filename = Path(filename).name
    path = _resolve_config_file(filename, ("camera_cfg", "capture"))
    if not path.exists():
        return jsonify({"error": f"Fichier introuvable : {filename}"}), 404
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        return jsonify({"error": f"JSON illisible : {e}"}), 400

    meta = {
        key: data[key]
        for key in ("_type", "_comment")
        if key in data
    }
    with _state_lock:
        _state["camera_config_file"] = filename
    capture = _state_store.update_section(
        "capture",
        {"loaded": True, "active_file": filename, "meta": meta},
        persist=True,
    )
    socketio.emit("status_update", _status_update_payload({
        "capture": capture,
    }))
    _append_log(f"📷 Config appareil : {filename}", "info", "trigger")
    return jsonify({"status": "ok", "filename": filename, "capture": capture})

@app.route("/api/trigger/totality_only", methods=["POST"])
def api_trigger_totality_only():
    """Lance la séquence de secours via TriggerService."""
    if not TOTALITY_ONLY_SCRIPT.exists():
        return jsonify({"error": f"Script introuvable : {TOTALITY_ONLY_SCRIPT}"}), 404
    if not _trigger_service.start_totality_only(TOTALITY_ONLY_SCRIPT):
        return jsonify({"error": "Trigger déjà en cours — arrêtez-le d'abord"}), 409
    return jsonify({"status": "ok"})

def _emit_trigger(event, payload):
    socketio.emit(event, payload, namespace="/")

_trigger_service = TriggerService(
    _state_store, TRIGGER_SCRIPT, JSON_FILE, EVENTS_FILE, CONFIGS_DIR,
    log_fn=_append_log, emit_fn=_emit_trigger,
    line_level_fn=_ansi_to_level, line_clean_fn=_clean)

@app.route("/api/trigger/start", methods=["POST"])
def api_trigger_start():
    """Démarrage RÉEL uniquement. La simulation possède une route séparée."""
    try:
        if not _trigger_service.start(simulate=False):
            return jsonify({"error": "Trigger déjà en cours."}), 409
        return jsonify({"status": "started", "mode": "real"})
    except TriggerValidationError as exc:
        if exc.code in ("CIRCUMSTANCES_NOT_LOADED", "CAPTURE_NOT_LOADED", "CIRCUMSTANCES_DATE_INVALID"):
            return jsonify({"error": exc.code, "message": str(exc)}), 409
        return jsonify({"error": str(exc), "code": exc.code}), 400

@app.route("/api/trigger/simulate", methods=["POST"])
def api_trigger_simulate():
    """Simulation explicite : le moteur reçoit --simulate et n'accède à aucun matériel caméra."""
    payload = request.get_json(silent=True) or {}
    speed = payload.get("speed", 60.0)
    try:
        if not _trigger_service.start(simulate=True, speed=speed):
            return jsonify({"error": "Trigger déjà en cours."}), 409
        return jsonify({"status": "started", "mode": "simulation", "speed": float(speed)})
    except TriggerValidationError as exc:
        if exc.code in ("CIRCUMSTANCES_NOT_LOADED", "CAPTURE_NOT_LOADED", "CIRCUMSTANCES_DATE_INVALID"):
            return jsonify({"error": exc.code, "message": str(exc)}), 409
        return jsonify({"error": str(exc), "code": exc.code}), 400

@app.route("/api/trigger/dryrun", methods=["POST"])
def api_trigger_dryrun():
    """Dry-run ×1 : même moteur et même matériel que le réel, seule la timeline est translatée."""
    payload = request.get_json(silent=True) or {}
    delay = payload.get("delay_s", 30.0)
    try:
        if not _trigger_service.start(dry_run=True, dry_run_delay=delay):
            return jsonify({"error": "Trigger déjà en cours."}), 409
        return jsonify({"status": "started", "mode": "dryrun", "speed": 1.0, "delay_s": float(delay)})
    except TriggerValidationError as exc:
        if exc.code in ("CIRCUMSTANCES_NOT_LOADED", "CAPTURE_NOT_LOADED", "CIRCUMSTANCES_DATE_INVALID"):
            return jsonify({"error": exc.code, "message": str(exc)}), 409
        return jsonify({"error": str(exc), "code": exc.code}), 400

@app.route("/api/trigger/stop", methods=["POST"])
def api_trigger_stop():
    return jsonify(_trigger_service.stop())

@app.route("/api/trigger/status")
def api_trigger_status():
    return jsonify(_state_store.snapshot("trigger"))

# ══════════════════════════════════════════════════════════════════════════════
# SOCKETIO — CONNEXION CLIENT
# ══════════════════════════════════════════════════════════════════════════════

@socketio.on("connect")
def on_connect(auth=None):
    """
    À chaque (re)connexion d'un client :
    1. Envoie l'état complet (GPS, éclipse, trigger, heure)
    2. Envoie les N dernières lignes de log (historique)
    """
    with _state_lock:
        gps     = dict(_state["gps"])
        trigger = dict(_state["trigger"])
        eclipse = _state.get("eclipse")

    # Si eclipse pas en mémoire, tenter le fichier
    if not eclipse:
        eclipse = _load_eclipse_json()

    # État complet
    emit("status_update", _status_update_payload({
        "gps":              gps,
        "trigger":          trigger,
        "eclipse":          eclipse,
        "circumstances":    _state_store.snapshot("circumstances"),
        "capture":          _state_store.snapshot("capture"),
        "rigs":             normalize_rigs_for_ui(get_rig_manager()),
        "camera_config_file": _state.get("camera_config_file"),
    }))

    # Éclipse séparément pour forcer le rendu des contacts
    if eclipse:
        emit("eclipse_calculated", {"status": "success", "data": eclipse})

    # Historique des logs
    with _log_lock:
        history = list(_log_buffer)
    if history:
        emit("log_history", history)

@socketio.on("trigger_sound")
def on_trigger_sound(data):
    """Mode B : reçu du trigger, relayé à tous les clients web."""
    socketio.emit("play_sound", {
        "file":      data.get("file"),
        "timestamp": data.get("timestamp"),
    })

@socketio.on("trigger_battery")
def on_trigger_battery(data):
    """Mode B : niveau batterie reçu du trigger."""
    pct = data.get("percent")
    with _state_lock:
        _state["camera"]["battery"] = f"{pct}%"
    _save_state()
    socketio.emit("battery_update", {
        "percent":   pct,
        "timestamp": data.get("timestamp"),
    })

@socketio.on("trigger_battery_alert")
def on_trigger_battery_alert(data):
    """Mode B : alerte batterie reçue du trigger."""
    pct   = data.get("percent")
    level = data.get("level")
    socketio.emit("battery_alert", {"percent": pct, "level": level})
    msg = (f"⚠⚠⚠ BATTERIE CRITIQUE {pct}% — CHANGEZ MAINTENANT"
           if level == "critical"
           else f"⚠ Batterie faible {pct}% — changez avant C2-10min")
    _append_log(msg, "error" if level == "critical" else "warning", "batterie")

# ══════════════════════════════════════════════════════════════════════════════
# THREADS DE FOND
# ══════════════════════════════════════════════════════════════════════════════

def _thread_mode_a_watcher():
    """Mode A : surveille sound_events.jsonl et relaie sons + batterie aux clients."""
    log.info("Mode A watcher démarré.")
    # Initialiser à la fin du fichier pour ne pas rejouer les anciens événements au reboot
    last_pos = 0
    if EVENTS_FILE.exists():
        last_pos = EVENTS_FILE.stat().st_size
    while True:
        try:
            if EVENTS_FILE.exists():
                with open(EVENTS_FILE, encoding="utf-8") as f:
                    f.seek(last_pos)
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            event = json.loads(line)
                            etype = event.get("type")

                            if etype == "sound":
                                socketio.emit("play_sound", {
                                    "file":      event["file"],
                                    "timestamp": event.get("timestamp"),
                                })

                            elif etype == "battery":
                                pct = event.get("percent")
                                with _state_lock:
                                    _state["camera"]["battery"] = f"{pct}%"
                                _save_state()
                                socketio.emit("battery_update", {
                                    "percent":   pct,
                                    "timestamp": event.get("timestamp"),
                                })

                            elif etype == "battery_alert":
                                pct   = event.get("percent")
                                level = event.get("level")   # "warning" ou "critical"
                                socketio.emit("battery_alert", {
                                    "percent":   pct,
                                    "level":     level,
                                    "timestamp": event.get("timestamp"),
                                })
                                msg = (f"⚠⚠⚠ BATTERIE CRITIQUE {pct}% — CHANGEZ MAINTENANT"
                                       if level == "critical"
                                       else f"⚠ Batterie faible {pct}% — changez avant C2-10min")
                                lvl = "error" if level == "critical" else "warning"
                                _append_log(msg, lvl, "batterie")

                        except json.JSONDecodeError:
                            pass
                    last_pos = f.tell()
        except Exception:
            pass
        time.sleep(0.3)

def _thread_status_broadcast():
    """Diffuse heure locale + UTC + état système toutes les secondes."""
    while True:
        try:
            with _state_lock:
                gps     = dict(_state["gps"])
                trigger = dict(_state["trigger"])
            socketio.emit("status_update", _status_update_payload({
                "gps":     gps,
                "trigger": trigger,
            }))
        except Exception:
            pass
        time.sleep(1)

def _thread_camera_poll():
    """Poll caméra désactivé — connexion à la demande uniquement (bouton 'Tester').
    Maintenu pour compatibilité structurelle mais ne fait rien."""
    pass

def _restore_persisted_trigger_selections():
    """Réactive au boot les sélections persistées uniquement si elles sont valides."""

    circumstances = _state_store.snapshot("circumstances") or {}
    circumstances_loaded = False

    if circumstances.get("active_file") and JSON_FILE.exists():
        try:
            data = json.loads(JSON_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                circumstances_loaded = True
        except Exception:
            pass

    _state_store.update_section(
        "circumstances",
        {"loaded": circumstances_loaded},
        persist=False,
    )

    capture = _state_store.snapshot("capture") or {}
    camera_config_file = _state_store.get("camera_config_file")
    capture_loaded = False

    if camera_config_file:
        filename = Path(camera_config_file).name
        path = _resolve_config_file(filename, ("camera_cfg", "capture"))

        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    capture_loaded = True
            except Exception:
                pass

    _state_store.update_section(
        "capture",
        {"loaded": capture_loaded},
        persist=False,
    )


# ══════════════════════════════════════════════════════════════════════════════
# DÉMARRAGE
# ══════════════════════════════════════════════════════════════════════════════


def start_background_threads():
    threading.Thread(target=_thread_mode_a_watcher,  daemon=True).start()
    threading.Thread(target=_thread_status_broadcast, daemon=True).start()
    threading.Thread(target=_thread_camera_poll,      daemon=True).start()
    threading.Thread(target=_trim_log_file,           daemon=True).start()
    log.info("Threads de fond démarrés.")

# Init au démarrage
_state = _load_state()
_load_log_buffer()
_state_store.reset_boot_sensitive()
_restore_persisted_trigger_selections()

_append_log("🚀 SolarEclipse Portal démarré.", "success", "system")
_append_log(f"🐍 Python : {sys.executable}", "info", "system")

if __name__ == "__main__":
    start_background_threads()
    socketio.run(app, host="0.0.0.0", port=5000, debug=False)
