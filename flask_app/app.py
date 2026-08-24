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
  todayeclipse.json → généré par eclipse_calculator_jubier.py

À la reconnexion d'un client :
  - État GPS complet restauré
  - Éclipse calculée restaurée
  - 500 dernières lignes de log renvoyées
  - Heure locale + UTC en temps réel
"""

import json
import re

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
from pathlib import Path
from types import SimpleNamespace

import gphoto2 as gp
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
BASE_DIR       = Path(__file__).parent
TRIGGER_DIR    = Path.home() / "python_solareclipsetrigger"
TRIGGER_SCRIPT = TRIGGER_DIR / "eclipse_trigger.py"
TOTALITY_ONLY_SCRIPT = TRIGGER_DIR / "totality_only.py"
CALC_SCRIPT    = TRIGGER_DIR / "eclipse_calculator_jubier.py"
GPS_SCRIPT     = TRIGGER_DIR / "gps_sync.py"
GPS_CONFIG_FILE = TRIGGER_DIR / "configs" / "gps_default.json"
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
from backend.trigger_service import TriggerService, TriggerValidationError
from backend.timezone_service import calculate_timezone_from_coords as _backend_timezone
from services.camera_service import CameraService
from services.focuser_service import FocuserService
from services.mount_service import MountService

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
_state_store = StateStore(STATE_FILE)
_state = _state_store.data
_state_lock = _state_store.lock
_focuser_service = FocuserService(
    _state_store, log_fn=lambda message: log.info(message)
)
_mount_service = MountService(
    _state_store, log_fn=lambda message: log.info(message)
)
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


def _focuser_post_guard(movement=False):
    inactive = require_device_active("focuser")
    if inactive is not None:
        return inactive
    if movement:
        return _trigger_running_response()
    return None


def _focuser_motion_conflict():
    status_method = getattr(_focuser_service, "status", None)
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
    socketio.emit("focuser_update", status)
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
    return jsonify(_devices_snapshot())


@app.route("/api/devices/detect", methods=["POST"])
def api_devices_detect():
    return jsonify(_detect_devices())

# ══════════════════════════════════════════════════════════════════════════════
# API — STATUT
# ══════════════════════════════════════════════════════════════════════════════

@app.route("/api/status")
def api_status():
    camera_info = _get_camera_status()
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
    })


# ══════════════════════════════════════════════════════════════════════════════
# API — FOCUSER
# ══════════════════════════════════════════════════════════════════════════════

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


# ═════════════════════════════════════════════════════════════════════════════
# API — MOUNT
# ═══════════════════════════════════════════════════════════════════════════════

@app.route("/api/mount/status")
def api_mount_status():
    inactive = require_device_active("mount")
    if inactive is not None:
        return inactive
    status = dict(_mount_service.status())
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
    except (ValueError, RuntimeError) as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(result)


@app.route("/api/mount/tracking/stop", methods=["POST"])
def api_mount_tracking_stop():
    guarded = _mount_tracking_guard()
    if guarded is not None:
        return guarded
    try:
        result = _mount_service.stop_tracking()
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
        speed = _json_number(payload, "speed")
        result = _mount_service.set_speed(speed)
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
    except (ValueError, RuntimeError) as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(result)


@app.route("/api/mount/slew/stop", methods=["POST"])
def api_mount_slew_stop():
    inactive = require_device_active("mount")
    if inactive is not None:
        return inactive
    return jsonify(_mount_service.stop())


def _get_camera_model_info(camera):
    """Lit marque, modèle et batterie depuis la config gphoto2."""
    brand   = None
    model   = None
    battery = None
    try:
        from plugins.camera import get_camera_model
        from plugins.camera.nikon import NikonDSLRPlugin, NikonZPlugin
        from plugins.camera.sony import SonyPlugin
        full_model = get_camera_model(camera)
        if full_model:
            model = full_model
            if SonyPlugin.matches(model):
                brand = "SONY"
            elif NikonZPlugin.matches(model) or NikonDSLRPlugin.matches(model):
                brand = "NIKON"
            else:
                brand = model.split()[0].upper()
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
    try:
        camera = gp.Camera()
        camera.init()
        brand, model, battery = _get_camera_model_info(camera)
        camera.exit()
        info = {"connected": True, "brand": brand, "model": model, "battery": battery}
    except Exception:
        info = {"connected": False, "brand": None, "model": None, "battery": None}
    with _state_lock:
        _state.setdefault("camera", {}).update(info)
        info = dict(_state["camera"])
    _save_state()
    return info

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
        new_time = _time_payload()
        socketio.emit("status_update", {"time": new_time, "gps": payload}, namespace="/")
        socketio.emit("clock_reset", {"new_utc": new_time["utc"]["iso"]}, namespace="/")

def _sync_time_backend(gps_time, dry_run=False):
    from gps_sync import sync_system_time
    return sync_system_time(gps_time, dry_run=dry_run)

_gps_controller = GpsController(
    _state_store, GPS_CONFIG_FILE,
    timezone_fn=lambda lat, lon, eclipse_date=None: _backend_timezone(lat, lon, eclipse_date, log=log),
    time_sync_fn=_sync_time_backend, log_fn=_append_log, emit_fn=_emit_backend)

@app.route("/api/gps/sync", methods=["POST"])
def api_gps_sync():
    inactive = require_device_active("gps")
    if inactive is not None:
        return inactive
    trigger_state = _state_store.snapshot("trigger") or {}
    if trigger_state.get("running"):
        return jsonify({"error": "Synchronisation GPS interdite pendant un trigger actif.", "code": "TRIGGER_RUNNING"}), 409
    if not _gps_controller.start(timeout_s=60.0):
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

@app.route("/api/camera/usb", methods=["POST"])
def api_camera_usb():
    """Libère ou reconnecte l'appareil photo via le système USB du Pi."""
    inactive = require_device_active("camera")
    if inactive is not None:
        return inactive
    action = (request.json or {}).get("action", "release")
    try:
        if action == "release":
            # 1. Tuer tous les processus qui tiennent l'USB de l'appareil
            for proc in ["gvfsd-gphoto2", "gphoto2", "gvfs-gphoto2-volume-monitor"]:
                subprocess.run(["/usr/bin/sudo", "-n", "/usr/bin/pkill", "-f", proc],
                               capture_output=True)
            import time as _time
            _time.sleep(1)
            # 2. Désautoriser le device USB de l'appareil photo
            import glob as _glob
            released = False
            for dev_path in _glob.glob("/sys/bus/usb/devices/[0-9]*-[0-9]*"):
                vendor_file = os.path.join(dev_path, "idVendor")
                auth_file   = os.path.join(dev_path, "authorized")
                if not os.path.exists(vendor_file) or not os.path.exists(auth_file):
                    continue
                try:
                    with open(vendor_file) as f:
                        vid = f.read().strip()
                    if vid in ("04b0", "054c"):  # Nikon, Sony
                        subprocess.run(
                            ["/usr/bin/sudo", "-n", "/usr/bin/tee", auth_file],
                            input="0", text=True, capture_output=True
                        )
                        released = True
                        _append_log("📷 USB libéré — appareil photo indépendant", "info", "system")
                        break
                except Exception:
                    continue
            if not released:
                _append_log("📷 gphoto2 arrêté — appareil photo libéré", "info", "system")
            return jsonify({"status": "ok"})

        else:  # reconnect
            import glob as _glob
            import time as _time
            for dev_path in _glob.glob("/sys/bus/usb/devices/[0-9]*-[0-9]*"):
                vendor_file = os.path.join(dev_path, "idVendor")
                auth_file   = os.path.join(dev_path, "authorized")
                if not os.path.exists(vendor_file) or not os.path.exists(auth_file):
                    continue
                try:
                    with open(vendor_file) as f:
                        vid = f.read().strip()
                    if vid in ("04b0", "054c"):
                        subprocess.run(
                            ["/usr/bin/sudo", "-n", "/usr/bin/tee", auth_file],
                            input="1", text=True, capture_output=True
                        )
                        break
                except Exception:
                    continue
            _time.sleep(2)
            _append_log("🔌 USB reconnecté — Pi reprend le contrôle", "success", "system")
            return jsonify({"status": "ok"})

    except Exception as e:
        _append_log(f"❌ Erreur USB : {e}", "error", "system")
        return jsonify({"error": str(e)}), 500

@app.route("/api/eclipse/calculate", methods=["POST"])
def api_eclipse_calculate():
    global _calc_proc
    if _calc_proc and _calc_proc.poll() is None:
        return jsonify({"error": "Calcul déjà en cours."}), 409

    data    = request.json or {}
    lat     = data.get("lat")
    lon     = data.get("lon")
    alt     = data.get("alt", 0)
    tz      = data.get("tz", 0)
    eclipse = data.get("eclipse", "auto")
    dst     = data.get("dst", False)   # True = calcul DST automatique

    if lat is None or lon is None:
        return jsonify({"error": "lat et lon requis"}), 400

    # Si DST activé : calculer la vraie timezone à la date de l'éclipse
    tz_used = tz
    tz_str_dst = None
    if dst:
        try:
            # Résoudre la date réelle de l'éclipse
            if eclipse == "auto" or not eclipse:
                sys.path.insert(0, str(TRIGGER_DIR))
                from eclipse_calculator_jubier import auto_eclipse
                eclipse_date = auto_eclipse()  # retourne "2026-08-12"
            else:
                eclipse_date = eclipse
            tz_info = calculate_timezone_from_coords(lat, lon, eclipse_date=eclipse_date)
            if tz_info is not None:
                tz_used = tz_info
                sign = '+' if tz_used >= 0 else ''
                val  = int(tz_used) if tz_used == int(tz_used) else tz_used
                tz_str_dst = f"UTC{sign}{val}"
                _append_log(
                    f"DST activé → timezone éclipse : {tz_str_dst} "
                    f"(date éclipse : {eclipse_date or 'auto'})",
                    "info", "calculator"
                )
                # Ne PAS écraser _state["gps"]["timezone"] — c'est la timezone système réelle
                # tz_str_dst est uniquement pour l'affichage des heures locales dans l'onglet Éclipse
        except Exception as e:
            _append_log(f"DST calcul erreur : {e} — timezone manuel utilisé", "warning", "calculator")

    def _run():
        global _calc_proc
        _append_log(f"▶ Calcul éclipse : lat={lat} lon={lon} alt={alt} tz=+{tz_used}{' (DST auto)' if dst else ''}", "info", "calculator")
        with _state_lock:
            _state["calc_running"] = True

        # Émettre la timezone DST au client avant le calcul
        if tz_str_dst:
            socketio.emit("state_update", {"timezone_override": tz_str_dst})

        cmd = [sys.executable, str(CALC_SCRIPT),
               "--lat", str(lat), "--lon", str(lon),
               "--alt", str(alt), "--tz",  str(tz_used),
               "--output", str(JSON_FILE)]
        if eclipse != "auto":
            cmd += ["--eclipse", eclipse]

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
            if tz_str_dst:
                payload["timezone_override"] = tz_str_dst
            socketio.emit("eclipse_calculated", payload)
            _append_log("✅ Calcul terminé — todayeclipse.json généré.", "success", "calculator")
        else:
            socketio.emit("eclipse_calculated", {"status": "error", "data": None})
            _append_log(f"❌ Calcul échoué (code {rc}).", "error", "calculator")

    threading.Thread(target=_run, daemon=True).start()
    return jsonify({"status": "started"})

@app.route("/api/eclipse/current")
def api_eclipse_current():
    # Si _state["eclipse"] est explicitement None (réinitialisé au boot),
    # ne pas charger depuis le disque — l'utilisateur doit recalculer.
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
        socketio.emit("status_update", {
            "circumstances": circumstances,
            "time": _time_payload(),
        })
        return jsonify({"status": "ok", "circumstances": circumstances})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ── Routes configs JSON ──────────────────────────────────────────────────────
CONFIGS_DIR = Path(__file__).parent.parent / "configs"
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
    """Résout une config à la racine, puis dans son sous-répertoire dédié."""
    root_path = CONFIGS_DIR / filename
    if root_path.is_file():
        return root_path
    subdirectory_path = CONFIGS_DIR / subdirectory / filename
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
        candidates = _unique_config_files("*.json", "circumstances/*.json")
        files = [f.name for f in candidates if _is_circumstances_config(f)]
        return jsonify({"files": files})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/configs/list_camera", methods=["GET"])
def api_configs_list_camera():
    """Retourne les fichiers de configuration appareil photo (camera_*)."""
    try:
        files = [f.name for f in _unique_config_files("camera_*.json", "capture/*.json")]
        return jsonify({"files": files})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

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
        path = _resolve_config_file(filename, "capture")
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
    try:
        CONFIGS_DIR.mkdir(parents=True, exist_ok=True)
        destination = CONFIGS_DIR / filename
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
            socketio.emit("status_update", {
                "capture": capture,
                "time": _time_payload(),
            })
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
    """Sauvegarde le contenu courant de todayeclipse.json dans configs/ sous un nouveau nom."""
    body = request.json or {}
    filename = body.get("filename", "").strip()
    if not filename:
        return jsonify({"error": "Nom de fichier manquant"}), 400
    if not filename.endswith(".json"):
        filename += ".json"
    # Sécurité : pas de chemin traversal
    filename = Path(filename).name
    data = _load_eclipse_json()
    if not data:
        return jsonify({"error": "Aucune configuration active"}), 400
    try:
        CONFIGS_DIR.mkdir(parents=True, exist_ok=True)
        destination = CONFIGS_DIR / filename
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
            socketio.emit("status_update", {
                "circumstances": circumstances,
                "time": _time_payload(),
            })
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
        socketio.emit("status_update", {
            "circumstances": circumstances,
            "time": _time_payload(),
        })
        return jsonify({"status": "ok", "circumstances": circumstances})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ══════════════════════════════════════════════════════════════════════════════

@app.route("/api/configs/clear_debug", methods=["POST"])
def api_configs_clear_debug():
    """Supprime tous les fichiers JSON dont le nom contient 'debug' dans configs/."""
    deleted = []
    try:
        for f in CONFIGS_DIR.glob("*.json"):
            if "debug" in f.name.lower():
                f.unlink()
                deleted.append(f.name)
        _append_log(f"🗑 {len(deleted)} fichier(s) debug supprimé(s).", "warning", "trigger")
        return jsonify({"status": "ok", "deleted": deleted})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

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
    socketio.emit("status_update", {
        "circumstances": circumstances,
        "time": _time_payload(),
    })
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
    path = _resolve_config_file(filename, "capture")
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
    socketio.emit("status_update", {
        "capture": capture,
        "time": _time_payload(),
    })
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

@app.route("/api/debug/generate", methods=["POST"])
def api_debug_generate():
    """Génère un fichier debug horodaté dans configs/ et le charge comme config active."""
    debug_script = TRIGGER_DIR / "generate_debug_total.py"
    if not debug_script.exists():
        return jsonify({"error": f"Script introuvable : {debug_script}"}), 404
    try:
        # Supprimer le watchdog pour éviter une fausse reprise
        (TRIGGER_DIR / "trigger_state.json").unlink(missing_ok=True)
        result = subprocess.run(
            [sys.executable, str(debug_script)],
            capture_output=True, text=True, cwd=str(TRIGGER_DIR)
        )
        if result.returncode != 0:
            return jsonify({"error": result.stderr.strip() or "Erreur génération"}), 500

        # Lire le JSON généré (todayeclipse.json dans TRIGGER_DIR)
        data = _load_eclipse_json()
        if not data:
            return jsonify({"error": "JSON généré mais illisible"}), 500

        # Sauvegarder une copie horodatée dans configs/
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        debug_filename = f"debug_{ts}.json"
        CONFIGS_DIR.mkdir(parents=True, exist_ok=True)
        debug_path = CONFIGS_DIR / debug_filename
        with open(debug_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)

        # Charger comme config active
        with _state_lock:
            _state["eclipse"] = data
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
            {"loaded": True, "active_file": debug_filename, "meta": meta},
            persist=True,
        )
        socketio.emit("eclipse_calculated", {"status": "success", "data": data})
        socketio.emit("status_update", {
            "circumstances": circumstances,
            "time": _time_payload(),
        })
        _append_log(f"🛠 DEBUG : {debug_filename} généré — éclipse totale dans ~4 min", "warning", "trigger")
        return jsonify({
            "status": "ok", "filename": debug_filename, "data": data,
            "circumstances": circumstances,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/debug/generate_realistic", methods=["POST"])
def api_debug_generate_realistic():
    """Génère un fichier debug réaliste (proportions vraie éclipse — 180s/photo)."""
    debug_script = TRIGGER_DIR / "generate_debug_realistic.py"
    if not debug_script.exists():
        return jsonify({"error": f"Script introuvable : {debug_script}"}), 404
    try:
        # Supprimer le watchdog pour éviter une fausse reprise
        (TRIGGER_DIR / "trigger_state.json").unlink(missing_ok=True)
        result = subprocess.run(
            [sys.executable, str(debug_script)],
            capture_output=True, text=True, cwd=str(TRIGGER_DIR)
        )
        if result.returncode != 0:
            return jsonify({"error": result.stderr.strip() or "Erreur génération"}), 500

        data = _load_eclipse_json()
        if not data:
            return jsonify({"error": "JSON généré mais illisible"}), 500

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        debug_filename = f"debug_realistic_{ts}.json"
        CONFIGS_DIR.mkdir(parents=True, exist_ok=True)
        debug_path = CONFIGS_DIR / debug_filename
        with open(debug_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)

        with _state_lock:
            _state["eclipse"] = data
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
            {"loaded": True, "active_file": debug_filename, "meta": meta},
            persist=True,
        )
        socketio.emit("eclipse_calculated", {"status": "success", "data": data})
        socketio.emit("status_update", {
            "circumstances": circumstances,
            "time": _time_payload(),
        })
        _append_log(f"🌍 DEBUG RÉALISTE : {debug_filename} généré — séquence ~3h44m", "warning", "trigger")
        return jsonify({
            "status": "ok", "filename": debug_filename, "data": data,
            "circumstances": circumstances,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

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
def on_connect():
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
    emit("status_update", {
        "time":             _time_payload(),
        "gps":              gps,
        "trigger":          trigger,
        "eclipse":          eclipse,
        "circumstances":    _state_store.snapshot("circumstances"),
        "capture":          _state_store.snapshot("capture"),
        "camera_config_file": _state.get("camera_config_file"),
    })

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
            socketio.emit("status_update", {
                "time":    _time_payload(),
                "gps":     gps,
                "trigger": trigger,
            })
        except Exception:
            pass
        time.sleep(1)

def _thread_camera_poll():
    """Poll caméra désactivé — connexion à la demande uniquement (bouton 'Tester').
    Maintenu pour compatibilité structurelle mais ne fait rien."""
    pass

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

_append_log("🚀 SolarEclipse Portal démarré.", "success", "system")
_append_log(f"🐍 Python : {sys.executable}", "info", "system")

if __name__ == "__main__":
    start_background_threads()
    socketio.run(app, host="0.0.0.0", port=5000, debug=False)
