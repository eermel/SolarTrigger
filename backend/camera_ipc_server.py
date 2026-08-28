"""Local JSON-lines IPC server for persistent camera workers."""

from __future__ import annotations

import dataclasses
import json
import math
import os
import re
import secrets
import socket
import stat
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.camera_model_resolution import resolve_sensor_entry
from backend.exposure_selection import (
    DEFAULT_SUPPORTED_ISOS,
    DEFAULT_SUPPORTED_SHUTTERS,
    safe_shutter_and_iso,
    select_supported_shutter_at_or_below,
)
from backend.field_rotation import (
    FieldRotationSingularityError,
    field_rotation_rate_deg_s,
)
from backend.generic_worker import ExpiredJobError
from backend.motion_constraint_resolver import resolve_motion_constraint
from backend.sensor_db import load_sensor_db
from backend.solar_position import (
    greenwich_sidereal_deg_utc,
    local_hour_angle_deg,
    solar_apparent_ra_dec_deg_utc,
    solar_declination_deg_utc,
)
from backend.solar_trailing import max_exposure_time_fixed_mount
from backend.trigger_runtime import RuntimeClock
from services.camera_service import CaptureIntent


MAX_MESSAGE_BYTES = 65536
MAX_WORKERS = 8
_SENSOR_DB_PATH = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "camera_sensors"
    / "camera_sensors.v1.json"
)
_ISO_PATTERN = re.compile(r"[0-9]+")
_CORRECTION_ORDER = ("shutter_limited", "iso_compensated", "iso_rounded")
_WARNING_ORDER = ("iso_capped",)

_ENVELOPE_KEYS = {"operation", "params", "session_id"}
_REQUIRED_ENVELOPE_KEYS = {"operation"}
_REQUIRED_INTENT_KEYS = {
    "shutter_min",
    "shutter_max",
    "step_ev",
    "speeds",
    "phase",
    "target_time",
    "deadline",
    "overflow_policy",
}
_ALLOWED_INTENT_KEYS = _REQUIRED_INTENT_KEYS | {"origin", "request_id"}
_PARAM_KEYS = {
    "ping": set(),
    "list_active_camera_rigs": set(),
    "camera.initialize": {
        "rig_id",
        "aperture",
        "iso",
        "image_format",
        "white_balance",
    },
    "apply_phase_settings": {"rig_id", "aperture", "iso"},
    "prepare_capture": {"rig_id", "intent"},
    "trigger_prepared": {"rig_id", "token_id", "deadline"},
    "shoot_speed_list": {
        "rig_id",
        "speeds",
        "photo_num_start",
        "deadline",
        "slowest_override_seconds",
    },
}
_REQUIRED_PARAMS = {
    "camera.initialize": {"rig_id"},
    "apply_phase_settings": {"rig_id"},
    "prepare_capture": {"rig_id", "intent"},
    "trigger_prepared": {"rig_id", "token_id"},
    "shoot_speed_list": {"rig_id", "speeds"},
}


class IpcError(Exception):
    """An error which is safe to return across the IPC boundary."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class CameraIpcServer:
    """Serve one JSON request and one JSON response per AF_UNIX connection."""

    MAX_MESSAGE_BYTES = MAX_MESSAGE_BYTES
    MAX_WORKERS = MAX_WORKERS

    def __init__(
        self,
        runtime,
        *,
        clock=None,
        endpoint_dir: str | os.PathLike[str] | None = None,
        parent_pid: int | None = None,
        log_fn=print,
    ) -> None:
        self._runtime = runtime
        self._clock = clock or RuntimeClock()
        self._log = log_fn
        self._parent_pid = os.getppid() if parent_pid is None else int(parent_pid)
        self._endpoint_dir = self._select_endpoint_dir(endpoint_dir)
        self._socket_path = self._endpoint_dir / (
            f"camera-ipc-{self._parent_pid}.sock"
        )
        self._socket: socket.socket | None = None
        self._accept_thread: threading.Thread | None = None
        self._pool: ThreadPoolExecutor | None = None
        self._stopping = threading.Event()
        self._state_lock = threading.RLock()
        self._active_session: str | None = None
        self._tokens: dict[str, tuple[str | None, int, Any]] = {}
        self._rig_iso_targets: dict[int, int] = {}

    @property
    def socket_path(self) -> Path:
        return self._socket_path

    @property
    def endpoint(self) -> Path:
        """Compatibility name for callers which treat the path as an endpoint."""

        return self._socket_path

    @staticmethod
    def _select_endpoint_dir(value) -> Path:
        if value is not None:
            path = Path(value)
        else:
            runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
            path = Path(runtime_dir) if runtime_dir else Path(tempfile.gettempdir()) / (
                f"solar-eclipse-trigger-{os.getuid()}"
            )
        if path.exists():
            info = path.stat()
            if not stat.S_ISDIR(info.st_mode):
                raise IpcError("UNSAFE_ENDPOINT", "IPC endpoint is not a directory")
            if info.st_uid != os.getuid() or info.st_mode & 0o022:
                raise IpcError(
                    "UNSAFE_ENDPOINT",
                    "IPC endpoint directory has unsafe ownership or permissions",
                )
        else:
            path.mkdir(mode=0o700, parents=True)
        return path

    def activate_session(self, session_id: str | None = None) -> str:
        """Activate the sole client session, returning its opaque identifier."""

        candidate = session_id or secrets.token_urlsafe(24)
        if not isinstance(candidate, str) or not candidate:
            raise IpcError("INVALID_SESSION", "session_id must be a non-empty string")
        with self._state_lock:
            if self._active_session not in (None, candidate):
                raise IpcError("SESSION_ACTIVE", "another camera IPC session is active")
            self._active_session = candidate
        return candidate

    def revoke_session(self, session_id: str | None = None) -> None:
        """Revoke a session and purge every prepared capture it owns."""

        with self._state_lock:
            target = self._active_session if session_id is None else session_id
            if target is None or target != self._active_session:
                raise IpcError("INVALID_SESSION", "camera IPC session is not active")
            self._active_session = None
            self._tokens = {
                key: value for key, value in self._tokens.items() if value[0] != target
            }
            self._rig_iso_targets.clear()

    def start(self) -> Path:
        with self._state_lock:
            if self._socket is not None:
                return self._socket_path
            self._remove_stale_socket()
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                listener.bind(str(self._socket_path))
                os.chmod(self._socket_path, 0o600)
                listener.listen(MAX_WORKERS)
                listener.settimeout(0.25)
            except BaseException:
                listener.close()
                raise
            self._stopping.clear()
            self._socket = listener
            self._pool = ThreadPoolExecutor(
                max_workers=MAX_WORKERS, thread_name_prefix="camera-ipc"
            )
            self._accept_thread = threading.Thread(
                target=self._accept_loop, name="camera-ipc-accept", daemon=True
            )
            self._accept_thread.start()
        return self._socket_path

    def stop(self, timeout: float | None = 5.0) -> None:
        self._stopping.set()
        with self._state_lock:
            listener, self._socket = self._socket, None
            thread, self._accept_thread = self._accept_thread, None
            pool, self._pool = self._pool, None
        if listener is not None:
            listener.close()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)
        if pool is not None:
            pool.shutdown(wait=True, cancel_futures=True)
        self._unlink_own_socket()
        with self._state_lock:
            self._active_session = None
            self._tokens.clear()
            self._rig_iso_targets.clear()

    def _remove_stale_socket(self) -> None:
        try:
            info = self._socket_path.lstat()
        except FileNotFoundError:
            return
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISSOCK(info.st_mode):
            raise IpcError("UNSAFE_ENDPOINT", "refusing to replace non-socket endpoint")
        if info.st_uid != os.getuid():
            raise IpcError("UNSAFE_ENDPOINT", "refusing to replace foreign socket")
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            probe.settimeout(0.1)
            probe.connect(str(self._socket_path))
        except (ConnectionRefusedError, FileNotFoundError):
            self._socket_path.unlink(missing_ok=True)
        else:
            raise IpcError("ENDPOINT_IN_USE", "camera IPC endpoint is already active")
        finally:
            probe.close()

    def _unlink_own_socket(self) -> None:
        try:
            info = self._socket_path.lstat()
            if stat.S_ISSOCK(info.st_mode) and info.st_uid == os.getuid():
                self._socket_path.unlink()
        except FileNotFoundError:
            pass

    def _accept_loop(self) -> None:
        while not self._stopping.is_set():
            listener = self._socket
            if listener is None:
                break
            try:
                connection, _ = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                if self._stopping.is_set():
                    break
                continue
            pool = self._pool
            if pool is None:
                connection.close()
            else:
                pool.submit(self._serve_connection, connection)

    def _serve_connection(self, connection: socket.socket) -> None:
        with connection:
            try:
                request = self._read_request(connection)
                result = self.handle_request(request)
                response = {"ok": True, "result": self._json_value(result)}
            except IpcError as exc:
                response = self._error(exc.code, exc.message)
            except Exception as exc:
                self._safe_log("camera IPC request failed", exc)
                response = self._error("INTERNAL_ERROR", "camera operation failed")
            try:
                connection.sendall(
                    json.dumps(response, separators=(",", ":")).encode("utf-8") + b"\n"
                )
            except OSError:
                pass

    @staticmethod
    def _read_request(connection: socket.socket) -> dict[str, Any]:
        data = bytearray()
        while True:
            chunk = connection.recv(min(4096, MAX_MESSAGE_BYTES + 1 - len(data)))
            if not chunk:
                raise IpcError("INVALID_REQUEST", "request must end with a newline")
            data.extend(chunk)
            newline = data.find(b"\n")
            if newline >= 0:
                if newline > MAX_MESSAGE_BYTES:
                    raise IpcError("MESSAGE_TOO_LARGE", "request exceeds size limit")
                if data[newline + 1 :]:
                    raise IpcError("INVALID_REQUEST", "only one request is allowed")
                payload = bytes(data[:newline])
                break
            if len(data) > MAX_MESSAGE_BYTES:
                raise IpcError("MESSAGE_TOO_LARGE", "request exceeds size limit")
        try:
            value = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise IpcError("INVALID_JSON", "request is not valid JSON") from exc
        if not isinstance(value, dict):
            raise IpcError("INVALID_REQUEST", "request must be a JSON object")
        return value

    def handle_request(self, request: dict[str, Any]) -> Any:
        if not isinstance(request, dict):
            raise IpcError("INVALID_REQUEST", "request must be a JSON object")
        self._validate_keys(
            request, _ENVELOPE_KEYS, _REQUIRED_ENVELOPE_KEYS, "request"
        )
        operation = request["operation"]
        if not isinstance(operation, str) or not operation:
            raise IpcError("INVALID_REQUEST", "operation must be a string")
        if operation not in _PARAM_KEYS:
            raise IpcError("UNKNOWN_OPERATION", "operation is not allowed")
        params = request.get("params", {})
        if not isinstance(params, dict):
            raise IpcError("INVALID_REQUEST", "params must be an object")
        self._validate_keys(
            params,
            _PARAM_KEYS[operation],
            _REQUIRED_PARAMS.get(operation, set()),
            "params",
        )
        session = request.get("session_id")
        if "session_id" in request and (not isinstance(session, str) or not session):
            raise IpcError("INVALID_REQUEST", "session_id must be a non-empty string")
        self._validate_session(session)

        if operation == "ping":
            return {"ok": True}
        if operation == "list_active_camera_rigs":
            return {"rig_ids": list(self._runtime.active_camera_rig_ids())}
        if operation == "camera.initialize":
            self._optional_strings(params, "aperture", "iso")
            self._optional_strings(
                params, "image_format", "white_balance", nullable=False
            )
            rig_id, worker = self._worker(params)
            plugin = worker.connect()
            self._call_worker(
                worker.init_settings,
                aperture=params.get("aperture"),
                iso=params.get("iso"),
                image_format=params.get("image_format", "RAW"),
                white_balance=params.get("white_balance", "Daylight"),
            )
            return {
                "rig_id": rig_id,
                "initialized": True,
                "plugin_name": getattr(plugin, "name", None),
            }
        if operation == "apply_phase_settings":
            self._optional_strings(params, "aperture", "iso")
            iso = params.get("iso")
            if iso is not None and not self._valid_iso_string(iso):
                raise IpcError(
                    "INVALID_REQUEST", "iso must be a positive base-10 integer string"
                )
            rig_id, worker = self._worker(params)
            result = self._call_worker(
                worker.apply_phase_settings,
                aperture=params.get("aperture"), iso=iso
            )
            if iso is not None:
                with self._state_lock:
                    self._rig_iso_targets[rig_id] = int(iso)
            return result
        if operation == "prepare_capture":
            intent_data = params.get("intent")
            if not isinstance(intent_data, dict):
                raise IpcError("INVALID_REQUEST", "intent must be an object")
            self._validate_intent(intent_data)
            origin = intent_data.get("origin")
            request_id = intent_data.get("request_id")
            try:
                intent_values = dict(intent_data)
                intent_values["origin"] = origin
                intent_values["request_id"] = request_id
                intent_values["target_time"] = self._intent_datetime(
                    intent_values.get("target_time"), "target_time", required=True
                )
                intent_values["deadline"] = self._intent_datetime(
                    intent_values.get("deadline"), "deadline", required=False
                )
                intent = CaptureIntent(**intent_values)
            except (TypeError, ValueError) as exc:
                raise IpcError("INVALID_REQUEST", "invalid capture intent") from exc
            rig_id, worker = self._worker(params)
            policy_getter = getattr(self._runtime, "get_policy_config_for_rig", None)
            policy = policy_getter(rig_id) if policy_getter is not None else None
            augmented = None
            if isinstance(policy, dict):
                constraint = resolve_motion_constraint(policy)
                materialized = None
                if constraint == "fixed_trailing":
                    materialized = self._policy_intent(rig_id, intent, policy)
                elif constraint == "field_rotation":
                    materialized = self._field_rotation_policy_intent(
                        rig_id, intent, policy
                    )
                if materialized is not None:
                    intent, iso_applied, corrections, warnings = materialized
                    self._call_worker(worker.apply_phase_settings, iso=str(iso_applied))
                    with self._state_lock:
                        self._rig_iso_targets[rig_id] = iso_applied
                    augmented = {
                        "iso_applied": str(iso_applied),
                        "corrections": corrections,
                        "warnings": warnings,
                    }
            prepared = self._call_worker(worker.prepare_capture, intent)
            token_id = secrets.token_urlsafe(24)
            with self._state_lock:
                self._tokens[token_id] = (session, rig_id, prepared.token)
            response = {
                "token_id": token_id,
                "estimated_total_s": prepared.estimated_total_s,
                "exposures_s": prepared.exposures_s,
                "planned_count": prepared.planned_count,
                "plugin_name": prepared.plugin_name,
                "request_id": request_id,
            }
            if augmented is not None:
                response.update(augmented)
            return response
        if operation == "trigger_prepared":
            token_id = params.get("token_id")
            if not isinstance(token_id, str) or not token_id:
                raise IpcError("INVALID_REQUEST", "token_id must be a non-empty string")
            deadline = self._deadline(params.get("deadline"))
            rig_id, worker = self._worker(params)
            with self._state_lock:
                token = self._tokens.get(token_id)
                if token is None or token[0] != session or token[1] != rig_id:
                    raise IpcError(
                        "UNKNOWN_TOKEN", "prepared capture token is not valid"
                    )
                del self._tokens[token_id]
            return self._call_worker(
                worker.trigger_prepared, token[2], deadline=deadline
            )
        if operation == "shoot_speed_list":
            speeds = params.get("speeds")
            if not isinstance(speeds, list) or any(
                not isinstance(item, str) for item in speeds
            ):
                raise IpcError("INVALID_REQUEST", "speeds must be an array of strings")
            photo_num_start = params.get("photo_num_start", 0)
            if isinstance(photo_num_start, bool) or not isinstance(photo_num_start, int):
                raise IpcError("INVALID_REQUEST", "photo_num_start must be an integer")
            override = params.get("slowest_override_seconds")
            if override is not None and not self._is_number(override):
                raise IpcError(
                    "INVALID_REQUEST", "slowest_override_seconds must be a number"
                )
            deadline = self._deadline(params.get("deadline"))
            _, worker = self._worker(params)
            return self._call_worker(
                worker.shoot_speed_list,
                speeds,
                photo_num_start=photo_num_start,
                deadline=deadline,
                slowest_override_seconds=override,
            )
        raise AssertionError("validated operation was not dispatched")

    @staticmethod
    def _call_worker(method, *args, **kwargs):
        try:
            return method(*args, **kwargs)
        except ExpiredJobError as exc:
            raise IpcError("EXPIRED", "camera worker job expired") from exc

    @staticmethod
    def _valid_iso_string(value: Any) -> bool:
        return (
            isinstance(value, str)
            and _ISO_PATTERN.fullmatch(value) is not None
            and int(value) > 0
        )

    @staticmethod
    def _positive_number(value: Any, field: str) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise IpcError("POLICY_INVALID", f"{field} must be a positive number")
        converted = float(value)
        if not math.isfinite(converted) or converted <= 0:
            raise IpcError("POLICY_INVALID", f"{field} must be a positive number")
        return converted

    @staticmethod
    def _positive_integer(value: Any, field: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise IpcError("POLICY_INVALID", f"{field} must be a positive integer")
        return value

    def _policy_intent(
        self, rig_id: int, intent: CaptureIntent, policy: dict
    ) -> tuple[CaptureIntent, int, list[str], list[str]]:
        optics = policy.get("optics")
        photo = policy.get("photo")
        devices = policy.get("devices")
        camera = devices.get("camera") if isinstance(devices, dict) else None
        if not isinstance(optics, dict) or not isinstance(photo, dict):
            raise IpcError("POLICY_INVALID", "RIG policy snapshot is incomplete")

        focal_length = self._positive_number(
            optics.get("focal_length_mm"), "focal_length_mm"
        )
        tolerance = self._positive_number(
            photo.get("motion_tolerance_px"), "motion_tolerance_px"
        )
        iso_max = self._positive_integer(photo.get("iso_max"), "iso_max")
        with self._state_lock:
            iso_requested = self._rig_iso_targets.get(rig_id)
        if iso_requested is None:
            raise IpcError("POLICY_INVALID", "ISO target is missing for RIG")

        manufacturer = camera.get("manufacturer") if isinstance(camera, dict) else None
        model = camera.get("model") if isinstance(camera, dict) else None
        alias = camera.get("alias") if isinstance(camera, dict) else None
        model_or_alias = model if isinstance(model, str) and model.strip() else alias
        if not isinstance(manufacturer, str) or not manufacturer.strip():
            raise IpcError("POLICY_INVALID", "camera manufacturer is missing")
        if not isinstance(model_or_alias, str) or not model_or_alias.strip():
            raise IpcError("POLICY_INVALID", "camera model or alias is missing")

        try:
            sensor_db = load_sensor_db(str(_SENSOR_DB_PATH))
            sensor = resolve_sensor_entry(manufacturer, model_or_alias, sensor_db)
            pixel_pitch = self._positive_number(
                sensor.get("pixel_pitch_um"), "pixel_pitch_um"
            )
            declination = solar_declination_deg_utc(intent.target_time)
            t_max = max_exposure_time_fixed_mount(
                pixel_pitch, focal_length, tolerance, declination
            )
            return self._materialize_policy_intent(
                intent, iso_requested, iso_max, t_max
            )
        except IpcError:
            raise
        except (KeyError, OSError, TypeError, ValueError) as exc:
            raise IpcError("POLICY_INVALID", f"anti-trailing policy failed: {exc}") from exc

    def _field_rotation_policy_intent(
        self, rig_id: int, intent: CaptureIntent, policy: dict
    ) -> tuple[CaptureIntent, int, list[str], list[str]] | None:
        try:
            optics = policy.get("optics")
            photo = policy.get("photo")
            devices = policy.get("devices")
            camera = devices.get("camera") if isinstance(devices, dict) else None
            eclipse = policy.get("eclipse")
            reference_site = (
                eclipse.get("reference_site") if isinstance(eclipse, dict) else None
            )
            if not isinstance(optics, dict) or not isinstance(photo, dict):
                raise ValueError("RIG policy snapshot is incomplete")

            focal_length = self._positive_number(
                optics.get("focal_length_mm"), "focal_length_mm"
            )
            tolerance = self._positive_number(
                photo.get("motion_tolerance_px"), "motion_tolerance_px"
            )
            iso_max = self._positive_integer(photo.get("iso_max"), "iso_max")
            radius = self._field_rotation_coordinate(
                photo.get("field_rotation_radius_deg"),
                "field_rotation_radius_deg",
                minimum=0.0,
                maximum=90.0,
                maximum_inclusive=False,
            )
            latitude = self._field_rotation_coordinate(
                reference_site.get("lat") if isinstance(reference_site, dict) else None,
                "reference_site.lat",
                minimum=-90.0,
                maximum=90.0,
                minimum_inclusive=False,
                maximum_inclusive=False,
            )
            longitude = self._field_rotation_coordinate(
                reference_site.get("lon") if isinstance(reference_site, dict) else None,
                "reference_site.lon",
                minimum=-180.0,
                maximum=180.0,
            )
            with self._state_lock:
                iso_requested = self._rig_iso_targets.get(rig_id)
            if iso_requested is None:
                raise ValueError("ISO target is missing for RIG")

            manufacturer = (
                camera.get("manufacturer") if isinstance(camera, dict) else None
            )
            model = camera.get("model") if isinstance(camera, dict) else None
            alias = camera.get("alias") if isinstance(camera, dict) else None
            model_or_alias = model if isinstance(model, str) and model.strip() else alias
            if not isinstance(manufacturer, str) or not manufacturer.strip():
                raise ValueError("camera manufacturer is missing")
            if not isinstance(model_or_alias, str) or not model_or_alias.strip():
                raise ValueError("camera model or alias is missing")

            sensor_db = load_sensor_db(str(_SENSOR_DB_PATH))
            sensor = resolve_sensor_entry(manufacturer, model_or_alias, sensor_db)
            pixel_pitch = self._positive_number(
                sensor.get("pixel_pitch_um"), "pixel_pitch_um"
            )
            alpha, declination = solar_apparent_ra_dec_deg_utc(intent.target_time)
            sidereal = greenwich_sidereal_deg_utc(intent.target_time)
            hour_angle = local_hour_angle_deg(alpha, sidereal, longitude)
            omega = field_rotation_rate_deg_s(latitude, declination, hour_angle)
            if not all(
                math.isfinite(value)
                for value in (alpha, declination, sidereal, hour_angle, omega)
            ):
                raise ValueError("field-rotation calculation must be finite")
            if omega == 0.0 or radius == 0.0:
                return None

            radius_mm = focal_length * math.tan(math.radians(radius))
            radius_px = radius_mm * 1000.0 / pixel_pitch
            t_max = tolerance / (abs(omega) * math.pi / 180.0 * radius_px)
            if not all(
                math.isfinite(value) and value > 0
                for value in (radius_mm, radius_px, t_max)
            ):
                raise ValueError(
                    "field-rotation exposure ceiling must be finite and positive"
                )
            return self._materialize_policy_intent(
                intent, iso_requested, iso_max, t_max
            )
        except IpcError as exc:
            reason = exc.message.removeprefix("anti-trailing policy failed: ")
            raise IpcError(
                "POLICY_INVALID", f"anti-trailing policy failed: {reason}"
            ) from exc
        except (
            FieldRotationSingularityError,
            KeyError,
            OSError,
            OverflowError,
            TypeError,
            ValueError,
        ) as exc:
            raise IpcError(
                "POLICY_INVALID", f"anti-trailing policy failed: {exc}"
            ) from exc

    @staticmethod
    def _field_rotation_coordinate(
        value: Any,
        field: str,
        *,
        minimum: float,
        maximum: float,
        minimum_inclusive: bool = True,
        maximum_inclusive: bool = True,
    ) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{field} must be a finite number")
        converted = float(value)
        lower_valid = (
            converted >= minimum if minimum_inclusive else converted > minimum
        )
        upper_valid = (
            converted <= maximum if maximum_inclusive else converted < maximum
        )
        if not math.isfinite(converted) or not lower_valid or not upper_valid:
            left = "[" if minimum_inclusive else "("
            right = "]" if maximum_inclusive else ")"
            raise ValueError(f"{field} must be in {left}{minimum}, {maximum}{right}")
        return converted

    @staticmethod
    def _materialize_policy_intent(
        intent: CaptureIntent, iso_requested: int, iso_max: int, t_max: float
    ) -> tuple[CaptureIntent, int, list[str], list[str]]:
        results = []
        if intent.speeds is not None:
            speeds = [str(speed) for speed in intent.speeds]
            if not speeds:
                raise IpcError("POLICY_INVALID", "explicit shutter list must not be empty")
            for speed in speeds:
                results.append(
                    safe_shutter_and_iso(
                        t_requested=speed,
                        iso_requested=iso_requested,
                        t_max=str(t_max),
                        supported_shutters=DEFAULT_SUPPORTED_SHUTTERS,
                        supported_isos=DEFAULT_SUPPORTED_ISOS,
                        iso_max=iso_max,
                    )
                )
            replacement = dataclasses.replace(
                intent,
                shutter_min=None,
                shutter_max=None,
                speeds=[result["shutter"] for result in results],
            )
        else:
            if intent.shutter_min is None:
                raise IpcError("POLICY_INVALID", "slowest shutter bound is missing")
            applied_slowest = select_supported_shutter_at_or_below(
                t_max, DEFAULT_SUPPORTED_SHUTTERS
            )
            results.append(
                safe_shutter_and_iso(
                    t_requested=intent.shutter_min,
                    iso_requested=iso_requested,
                    t_max=applied_slowest,
                    supported_shutters=DEFAULT_SUPPORTED_SHUTTERS,
                    supported_isos=DEFAULT_SUPPORTED_ISOS,
                    iso_max=iso_max,
                )
            )
            replacement = dataclasses.replace(
                intent,
                shutter_min=applied_slowest,
                shutter_max=intent.shutter_max,
                step_ev=float(intent.step_ev) if intent.step_ev is not None else 1.0,
                speeds=None,
            )

        iso_applied = max(result["iso"] for result in results)
        corrections = [
            item
            for item in _CORRECTION_ORDER
            if any(item in result["corrections"] for result in results)
        ]
        warnings = [
            item
            for item in _WARNING_ORDER
            if any(item in result["warnings"] for result in results)
        ]
        return replacement, iso_applied, corrections, warnings

    @staticmethod
    def _validate_keys(
        value: dict[str, Any], allowed: set[str], required: set[str], label: str
    ) -> None:
        if any(not isinstance(key, str) for key in value):
            raise IpcError("INVALID_REQUEST", f"{label} keys must be strings")
        if set(value) - allowed:
            raise IpcError("INVALID_REQUEST", f"{label} contains unknown keys")
        if required - set(value):
            raise IpcError("INVALID_REQUEST", f"{label} is missing required keys")

    @staticmethod
    def _is_number(value: Any) -> bool:
        return isinstance(value, (int, float)) and not isinstance(value, bool)

    @classmethod
    def _optional_strings(
        cls, params: dict[str, Any], *fields: str, nullable: bool = True
    ) -> None:
        for field in fields:
            value = params.get(field)
            if (
                field in params
                and not isinstance(value, str)
                and not (nullable and value is None)
            ):
                raise IpcError("INVALID_REQUEST", f"{field} must be a string")

    @classmethod
    def _validate_intent(cls, intent: dict[str, Any]) -> None:
        cls._validate_keys(
            intent, _ALLOWED_INTENT_KEYS, _REQUIRED_INTENT_KEYS, "intent"
        )
        for field in ("shutter_min", "shutter_max", "overflow_policy"):
            if intent[field] is not None and not isinstance(intent[field], str):
                raise IpcError("INVALID_REQUEST", f"{field} must be a string or null")
        if intent["step_ev"] is not None and not cls._is_number(intent["step_ev"]):
            raise IpcError("INVALID_REQUEST", "step_ev must be a number or null")
        speeds = intent["speeds"]
        if speeds is not None and (
            not isinstance(speeds, list) or any(not isinstance(item, str) for item in speeds)
        ):
            raise IpcError("INVALID_REQUEST", "speeds must be an array of strings or null")
        if not isinstance(intent["phase"], str):
            raise IpcError("INVALID_REQUEST", "phase must be a string")

    def _validate_session(self, session: Any) -> None:
        with self._state_lock:
            active = self._active_session
        if active is not None and session != active:
            raise IpcError("INVALID_SESSION", "camera IPC session is not active")

    def _worker(self, params: dict[str, Any]):
        rig_id = params.get("rig_id")
        if not isinstance(rig_id, int) or isinstance(rig_id, bool) or not 1 <= rig_id <= 4:
            raise IpcError("INVALID_RIG", "rig_id must be an integer from 1 to 4")
        worker = self._runtime.get_for_rig(rig_id)
        if worker is None:
            raise IpcError("UNKNOWN_RIG", "camera rig is not active")
        return rig_id, worker

    def _deadline(self, raw: Any) -> datetime | None:
        if raw is None:
            return None
        return self._utc_datetime(raw, "deadline", required=True)

    @staticmethod
    def _intent_datetime(raw: Any, field: str, *, required: bool) -> datetime | None:
        if raw is None and not required:
            return None
        if not isinstance(raw, str):
            raise IpcError("INVALID_REQUEST", f"{field} must be an ISO-8601 string")
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError as exc:
            raise IpcError("INVALID_REQUEST", f"{field} is not valid ISO-8601") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise IpcError("INVALID_REQUEST", f"{field} must include a UTC offset")
        return parsed.astimezone(timezone.utc).replace(tzinfo=None)

    @staticmethod
    def _utc_datetime(raw: Any, field: str, *, required: bool) -> datetime | None:
        if raw is None and not required:
            return None
        if not isinstance(raw, str):
            raise IpcError(
                "INVALID_DEADLINE", f"{field} must be an ISO-8601 string"
            )
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError as exc:
            raise IpcError(
                "INVALID_DEADLINE", f"{field} is not valid ISO-8601"
            ) from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise IpcError(
                "INVALID_DEADLINE", f"{field} must include a UTC offset"
            )
        return parsed.astimezone(timezone.utc).replace(tzinfo=None)

    def _safe_log(self, message: str, exc: BaseException) -> None:
        try:
            self._log(f"{message}: {type(exc).__name__}")
        except Exception:
            pass

    @staticmethod
    def _error(code: str, message: str) -> dict[str, Any]:
        return {"ok": False, "error": {"code": code, "message": message}}

    @classmethod
    def _json_value(cls, value: Any) -> Any:
        if dataclasses.is_dataclass(value):
            return cls._json_value(dataclasses.asdict(value))
        if isinstance(value, dict):
            return {str(key): cls._json_value(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [cls._json_value(item) for item in value]
        if isinstance(value, datetime):
            return value.isoformat()
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        raise IpcError("INVALID_RESPONSE", "camera operation returned unsupported data")


__all__ = ["CameraIpcServer", "IpcError", "MAX_MESSAGE_BYTES"]
