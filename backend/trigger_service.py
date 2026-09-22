from __future__ import annotations
from pathlib import Path
import json, os, signal, subprocess, sys, threading
from datetime import datetime, timezone
from backend.timeline import build_timeline, sequence_seconds
from backend.phase_trigger import build_phase_schedule

class TriggerValidationError(RuntimeError):
    def __init__(self, message, code="TRIGGER_INVALID"):
        super().__init__(message); self.code = code


def _utc_today():
    return datetime.now(timezone.utc).date()


def validate_eclipse(ecl):
    ts, c1, c2, c3, c4, te = sequence_seconds(ecl)
    errors = []

    if c1 is None or c4 is None:
        errors.append("C1 or C4 missing")
    else:
        if ts is not None and ts >= c1:
            errors.append(
                f"TSTART ({ecl.get('TSTART')}) ≥ C1 ({ecl.get('C1')})"
            )

        if (c2 is None) != (c3 is None):
            errors.append(
                "C2 and C3 must both be present or absent"
            )

        elif c2 is not None:
            # Eclipse centrale. TMAX reste facultatif pour compatibilité
            # avec les anciens fichiers.
            if c1 >= c2:
                errors.append(
                    f"C1 ({ecl.get('C1')}) ≥ C2 ({ecl.get('C2')})"
                )

            if c2 > c3:
                errors.append(
                    f"C2 ({ecl.get('C2')}) > C3 ({ecl.get('C3')})"
                )

            if c3 >= c4:
                errors.append(
                    f"C3 ({ecl.get('C3')}) ≥ C4 ({ecl.get('C4')})"
                )

            if ecl.get("TMAX"):
                try:
                    tl = build_timeline(
                        ecl,
                        fallback_date=datetime.now(timezone.utc).date(),
                    )
                    if not (
                        tl["C2"]
                        < tl["TMAX"]
                        < tl["C3"]
                    ):
                        errors.append(
                            "C2 < TMAX < C3 constraint not satisfied"
                        )
                except Exception as exc:
                    errors.append(f"Invalid TMAX: {exc}")

        else:
            # Eclipse partielle : C2 et C3 n'existent pas.
            if ecl.get("TMAX"):
                try:
                    tl = build_timeline(
                        ecl,
                        fallback_date=datetime.now(timezone.utc).date(),
                    )
                    if not (
                        tl["C1"]
                        < tl["TMAX"]
                        < tl["C4"]
                    ):
                        errors.append(
                            "C1 < TMAX < C4 constraint not satisfied"
                        )
                except Exception as exc:
                    errors.append(f"Invalid TMAX: {exc}")

        if te is not None and c4 >= te:
            errors.append(
                f"C4 ({ecl.get('C4')}) ≥ TEND ({ecl.get('TEND')})"
            )

    if errors:
        raise TriggerValidationError(
            "❌ Inconsistent JSON: " + " | ".join(errors),
            "JSON_INVALID",
        )

def validate_execution_rigs(config):
    """Validate rig requirements only when real hardware execution starts."""
    if not isinstance(config, dict):
        raise TriggerValidationError(
            "Invalid RIG configuration.",
            "RIG_CONFIG_INVALID",
        )

    rigs = config.get("rigs")
    if not isinstance(rigs, list):
        raise TriggerValidationError(
            "Invalid RIG configuration.",
            "RIG_CONFIG_INVALID",
        )

    by_id = {}
    for rig in rigs:
        if not isinstance(rig, dict):
            continue
        rig_id = rig.get("rig_id")
        if isinstance(rig_id, int) and not isinstance(rig_id, bool):
            by_id[rig_id] = rig

    # RIG 1 participe toujours, indépendamment de son ancien flag enabled.
    rig1 = by_id.get(1)
    if not isinstance(rig1, dict):
        raise TriggerValidationError(
            "RIG 1 is required to run the trigger.",
            "RIG1_REQUIRED",
        )

    participating_ids = [1]
    participating_ids.extend(
        rig_id
        for rig_id in range(2, 5)
        if isinstance(by_id.get(rig_id), dict)
        and by_id[rig_id].get("enabled") is True
    )

    for rig_id in participating_ids:
        rig = by_id[rig_id]
        devices = rig.get("devices")
        camera = devices.get("camera") if isinstance(devices, dict) else None
        backend = camera.get("backend") if isinstance(camera, dict) else None
        backend = backend.strip().lower() if isinstance(backend, str) else ""

        if not backend or backend in {"none", "external"}:
            raise TriggerValidationError(
                f"RIG {rig_id} requires a configured camera "
                "to run the trigger.",
                "RIG_CAMERA_REQUIRED",
            )

    return tuple(participating_ids)


def validate_execution_rig(config, rig_id):
    """Validate one RIG for an independent trigger execution.

    RIG 1 is the mandatory primary RIG and therefore participates regardless
    of its legacy ``enabled`` flag.  Only secondary RIGs are opt-in.
    """
    if (
        not isinstance(rig_id, int)
        or isinstance(rig_id, bool)
        or not 1 <= rig_id <= 4
    ):
        raise TriggerValidationError(
            f"Invalid RIG: {rig_id}",
            "RIG_ID_INVALID",
        )

    if not isinstance(config, dict):
        raise TriggerValidationError(
            "Invalid RIG configuration.",
            "RIG_CONFIG_INVALID",
        )

    rigs = config.get("rigs")
    if not isinstance(rigs, list):
        raise TriggerValidationError(
            "Invalid RIG configuration.",
            "RIG_CONFIG_INVALID",
        )

    rig = next(
        (
            item
            for item in rigs
            if isinstance(item, dict)
            and item.get("rig_id") == rig_id
        ),
        None,
    )

    if rig is None:
        raise TriggerValidationError(
            f"RIG {rig_id} not found.",
            "RIG_NOT_FOUND",
        )

    if rig_id != 1 and rig.get("enabled") is not True:
        raise TriggerValidationError(
            f"RIG {rig_id} is not active.",
            "RIG_DISABLED",
        )

    devices = rig.get("devices")
    camera = devices.get("camera") if isinstance(devices, dict) else None
    backend = camera.get("backend") if isinstance(camera, dict) else None
    backend = backend.strip().lower() if isinstance(backend, str) else ""

    if not backend or backend in {"none", "external"}:
        raise TriggerValidationError(
            f"RIG {rig_id} requires a configured camera "
            "to run the trigger.",
            "RIG_CAMERA_REQUIRED",
        )

    return rig_id


class TriggerService:
    """Owns trigger process lifecycle; Flask is only an HTTP adapter."""
    def __init__(self, state_store, trigger_script, json_file, configs_dir,
                 log_fn, emit_fn, line_level_fn=None, line_clean_fn=None,
                 camera_runtime=None, rig_config_loader=None,
                 product_configs_dir=None):
        self.state = state_store
        self.trigger_script = Path(trigger_script)
        self.json_file = Path(json_file)
        self.configs_dir = Path(configs_dir)
        self.product_configs_dir = (
            Path(product_configs_dir)
            if product_configs_dir is not None
            else self.configs_dir
        )
        self.log = log_fn
        self.emit = emit_fn
        self.project_dir=self.trigger_script.resolve().parent.parent
        self.line_level_fn=line_level_fn or (lambda _: "info")
        self.line_clean_fn=line_clean_fn or (lambda x:x)
        is_production_tree = self.project_dir == Path(__file__).resolve().parent.parent
        self.camera_runtime = camera_runtime
        self.rig_config_loader = rig_config_loader
        if is_production_tree:
            if self.camera_runtime is None:
                from backend.camera_worker_runtime import get_camera_worker_runtime

                self.camera_runtime = get_camera_worker_runtime(log_fn=log_fn)
            if self.rig_config_loader is None:
                from backend.rig_runtime import load_rig_configuration

                self.rig_config_loader = load_rig_configuration
        self._lock = threading.RLock()
        self._procs = {rig_id: None for rig_id in range(1, 5)}
        self._starting_by_rig = {
            rig_id: False
            for rig_id in range(1, 5)
        }
        self._active_circumstances_paths = {}
        self._active_photo_paths = {}
        self._active_exposure_opt_paths = {}
        self._analysis_suppressed_by_rig = {
            rig_id: False
            for rig_id in range(1, 5)
        }
        self._manual_stop_requested_by_rig = {
            rig_id: False
            for rig_id in range(1, 5)
        }
        self._cancel_start_requested_by_rig = {
            rig_id: False
            for rig_id in range(1, 5)
        }
        self._stopping_by_rig = {
            rig_id: False
            for rig_id in range(1, 5)
        }
        self._supervisor_threads = {
            rig_id: None
            for rig_id in range(1, 5)
        }

    def _log_rig(self, rig_id, text, level="info"):
        """Log one trigger event with explicit RIG ownership.

        Legacy/injected log functions used by tests may still accept only
        (text, level, source), so retain compatibility with that contract.
        """
        try:
            return self.log(
                text,
                level,
                "trigger",
                rig_id=rig_id,
            )
        except TypeError as exc:
            # Compatibility only for legacy log callbacks that do not accept
            # the new rig_id keyword. Do not hide TypeError raised internally
            # by a real logger implementation.
            if "rig_id" not in str(exc):
                raise
            return self.log(text, level, "trigger")

    @property
    def _proc(self):
        """Compatibility alias for legacy single-RIG tests/code."""
        return self._procs[1]

    @_proc.setter
    def _proc(self, value):
        self._procs[1] = value

    @property
    def _starting(self):
        """Compatibility alias for legacy single-RIG tests/code."""
        return self._starting_by_rig[1]

    @_starting.setter
    def _starting(self, value):
        self._starting_by_rig[1] = bool(value)

    def is_active_or_starting(self, rig_id: int) -> bool:
        if (
            not isinstance(rig_id, int)
            or isinstance(rig_id, bool)
            or not 1 <= rig_id <= 4
        ):
            return False
        with self._lock:
            proc = self._procs[rig_id]
            return bool(
                self._starting_by_rig[rig_id]
                or (proc is not None and proc.poll() is None)
            )

    def any_active_or_starting(self) -> bool:
        with self._lock:
            for rig_id in range(1, 5):
                proc = self._procs[rig_id]
                if self._starting_by_rig[rig_id]:
                    return True
                if proc is not None and proc.poll() is None:
                    return True
        return False

    def active_inputs_snapshot(self) -> dict:
        """Return the exact input filenames owned by each live/startup RIG."""
        with self._lock:
            snapshot = {}
            for rig_id in range(1, 5):
                key = str(rig_id)
                circumstances = self._active_circumstances_paths.get(rig_id)
                photo = self._active_photo_paths.get(rig_id)
                exposure_opt = self._active_exposure_opt_paths.get(rig_id)
                snapshot[key] = {
                    "circumstances_file": (
                        circumstances.name if circumstances is not None else None
                    ),
                    "photo_file": photo.name if photo is not None else None,
                    "exposure_opt_file": (
                        exposure_opt.name if exposure_opt is not None else None
                    ),
                }
            return snapshot

    def _subprocess_env(self, ipc_session=None):
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        existing = env.get("PYTHONPATH")
        env["PYTHONPATH"] = (
            str(self.project_dir)
            if not existing
            else str(self.project_dir) + os.pathsep + existing
        )
        if ipc_session is not None:
            env["SET_CAMERA_IPC_SOCKET"] = ipc_session.socket_path
            env["SET_CAMERA_IPC_SESSION"] = ipc_session.session_id
        return env

    def _resolve_camera_config(self, camera_config_file):
        if not camera_config_file:
            return None

        filename = Path(camera_config_file).name

        generated = (
            self.configs_dir
            / "camera_cfg"
            / filename
        )
        if generated.is_file():
            return generated

        bundled = (
            self.product_configs_dir
            / "capture"
            / filename
        )
        if bundled.is_file():
            return bundled

        return None

    def _resolve_named_config(self, filename, subdir, *, bundled=False):
        if not isinstance(filename, str) or not filename.strip():
            return None
        filename = filename.strip()
        if Path(filename).name != filename or Path(filename).suffix.lower() != ".json":
            return None
        path = self.configs_dir / subdir / filename
        if path.is_file():
            return path
        if bundled:
            path = self.product_configs_dir / subdir / filename
            if path.is_file():
                return path
        return None

    def _resolve_trigger_inputs(self, rig_id, selected=None):
        selected = selected if isinstance(selected, dict) else {}
        names = {
            "circumstances": selected.get("circumstances_file"),
            "photo": selected.get("photo_file"),
            "exposure_opt": selected.get("exposure_opt_file"),
        }

        paths = {
            "circumstances": self._resolve_named_config(names["circumstances"], "circumstances"),
            "photo": self._resolve_named_config(names["photo"], "photo_cfg", bundled=True),
            "exposure_opt": self._resolve_named_config(names["exposure_opt"], "exposure_opt"),
        }
        missing = [name for name, path in paths.items() if path is None]
        if missing:
            raise TriggerValidationError(
                "Select the circumstances, Photo Setup and Exposure Optimization files.",
                "TRIGGER_INPUTS_NOT_LOADED",
            )
        return paths

    def _resolve_totality_input(self, rig_id):
        """Resolve the fixed product configuration for emergency Totality."""
        path = (
            self.product_configs_dir
            / "emergency"
            / "photo_totality.json"
        )
        if not path.is_file():
            raise TriggerValidationError(
                "Emergency Totality Photo Setup is missing.",
                "EMERGENCY_PHOTO_CONFIG_MISSING",
            )

        try:
            photo = json.loads(path.read_text(encoding="utf-8"))
            if (
                not isinstance(photo, dict)
                or photo.get("config_type") != "emergency_totality_photo_setup"
                or not isinstance(photo.get("phases", {}).get("totality"), dict)
            ):
                raise ValueError("invalid emergency Totality Photo Setup")
        except Exception as exc:
            raise TriggerValidationError(
                f"Invalid emergency Totality Photo Setup: {exc}",
                "EMERGENCY_PHOTO_CONFIG_INVALID",
            ) from exc

        self._active_photo_paths[rig_id] = path
        return path

    def _clear_active_inputs(self, rig_id):
        self._active_circumstances_paths.pop(rig_id, None)
        self._active_photo_paths.pop(rig_id, None)
        self._active_exposure_opt_paths.pop(rig_id, None)

    def validate_start(
        self,
        rig_id=1,
        require_gps=True,
        selected=None,
        strict_circumstances_date=True,
    ):
        if require_gps:
            gps = self.state.snapshot("gps") or {}
            if gps.get("gps_sync_running") is True:
                raise TriggerValidationError(
                    "⚠️ GPS synchronization is still in progress. "
                    "Wait for it to finish before starting.",
                    "GPS_SYNC_IN_PROGRESS",
                )
            if not gps.get("synced"):
                raise TriggerValidationError(
                    "⚠️ GPS is not synchronized. Synchronize the clock before starting.",
                    "GPS_NOT_SYNCED",
                )

            sync_time = gps.get("sync_time")
            if not isinstance(sync_time, str) or not sync_time.strip():
                raise TriggerValidationError(
                    "⚠️ GPS synchronization timestamp is missing or invalid. "
                    "Synchronize again.",
                    "GPS_SYNC_TIME_INVALID",
                )

            try:
                sync_dt = datetime.fromisoformat(
                    sync_time.strip().replace("Z", "+00:00")
                )
                if sync_dt.tzinfo is None:
                    # Backward compatibility: historical state may contain a
                    # timezone-naive timestamp, which SolarTrigger treated as UTC.
                    sync_dt = sync_dt.replace(tzinfo=timezone.utc)

                age = (
                    datetime.now(timezone.utc)
                    - sync_dt.astimezone(timezone.utc)
                ).total_seconds()
            except Exception as exc:
                # START is safety-critical: an unreadable synchronization
                # timestamp must never bypass the freshness check.
                raise TriggerValidationError(
                    "⚠️ GPS synchronization timestamp is invalid. "
                    "Synchronize again.",
                    "GPS_SYNC_TIME_INVALID",
                ) from exc

            # A synchronization timestamp meaningfully ahead of the Pi clock
            # is inconsistent: accepting a negative age would bypass the
            # freshness guard entirely. Keep a small tolerance for scheduling
            # and serialization races around the synchronization operation.
            if age < -5:
                raise TriggerValidationError(
                    "⚠️ GPS synchronization timestamp is in the future. "
                    "Synchronize again.",
                    "GPS_SYNC_TIME_INVALID",
                )

            if age > 7200:
                raise TriggerValidationError(
                    f"⚠️ Last GPS synchronization was "
                    f"{int(age // 60)} min ago. Synchronize again.",
                    "GPS_SYNC_STALE",
                )

        paths = self._resolve_trigger_inputs(rig_id, selected)
        circumstances_path = paths["circumstances"]
        filename = circumstances_path.name

        try:
            ecl = json.loads(
                circumstances_path.read_text(encoding="utf-8")
            )
            if not isinstance(ecl, dict):
                raise ValueError("invalid JSON root")

            if strict_circumstances_date:
                raw_date = ecl.get("_date")
                if not isinstance(raw_date, str) or not raw_date.strip():
                    raise TriggerValidationError(
                        "Circumstances file is missing required _date (YYYY-MM-DD).",
                        "CIRCUMSTANCES_DATE_MISSING",
                    )
                normalized_date = raw_date.strip()
                try:
                    parsed_date = datetime.strptime(
                        normalized_date,
                        "%Y-%m-%d",
                    ).date()
                except ValueError as exc:
                    raise TriggerValidationError(
                        "Circumstances _date is invalid; expected YYYY-MM-DD.",
                        "CIRCUMSTANCES_DATE_INVALID",
                    ) from exc
                if parsed_date.isoformat() != normalized_date:
                    raise TriggerValidationError(
                        "Circumstances _date is invalid; expected YYYY-MM-DD.",
                        "CIRCUMSTANCES_DATE_INVALID",
                    )
                today_utc = _utc_today()
                if parsed_date != today_utc:
                    raise TriggerValidationError(
                        "Circumstances date "
                        f"{parsed_date.isoformat()} does not match current UTC date "
                        f"{today_utc.isoformat()}.",
                        "CIRCUMSTANCES_DATE_MISMATCH",
                    )

            validate_eclipse(ecl)
            photo = json.loads(paths["photo"].read_text(encoding="utf-8"))
            exposure_opt = json.loads(
                paths["exposure_opt"].read_text(encoding="utf-8")
            )
            if not isinstance(photo, dict) or photo.get("config_type") not in (None, "photo_setup"):
                raise ValueError("invalid Photo Setup")
            if (
                not isinstance(exposure_opt, dict)
                or exposure_opt.get("config_type") != "exposure_optimization"
            ):
                raise ValueError("invalid Exposure Optimization")
            for field in (
                "atmospheric_attenuation_enabled",
                "atmospheric_attenuation_replace_exposures",
            ):
                value = exposure_opt.get(field, False)
                if not isinstance(value, bool):
                    raise ValueError(f"{field} must be boolean")
            if not any(
                isinstance(item, dict) and item.get("rig_id") == rig_id
                for item in exposure_opt.get("rigs", ())
            ):
                raise ValueError(f"Exposure Optimization has no RIG {rig_id}")
            timeline = build_timeline(
                ecl,
                fallback_date=(
                    None
                    if strict_circumstances_date
                    else datetime.now(timezone.utc).date()
                ),
            )
            build_phase_schedule(
                timeline,
                photo,
                honor_timeline_bounds=ecl.get("_debug_scenario") is True,
            )
        except TriggerValidationError as exc:
            if exc.code.startswith("CIRCUMSTANCES_DATE_"):
                raise
            raise TriggerValidationError(
                f"Invalid trigger inputs: {exc}",
                "TRIGGER_INPUTS_INVALID",
            ) from exc
        except Exception as exc:
            raise TriggerValidationError(
                f"Invalid trigger inputs: {exc}",
                "TRIGGER_INPUTS_INVALID",
            ) from exc

        self._active_circumstances_paths[rig_id] = circumstances_path
        self._active_photo_paths[rig_id] = paths["photo"]
        self._active_exposure_opt_paths[rig_id] = paths["exposure_opt"]
        return ecl

    def start(self, rig_id=1, simulate=False, speed=60.0, dry_run=False,
              selected=None):
        if (
            not isinstance(rig_id, int)
            or isinstance(rig_id, bool)
            or not 1 <= rig_id <= 4
        ):
            raise TriggerValidationError(
                f"Invalid RIG: {rig_id}",
                "RIG_ID_INVALID",
            )

        try:
            speed=float(speed)
        except (TypeError, ValueError):
            raise TriggerValidationError("Invalid simulation factor.", "SIM_SPEED_INVALID")
        if simulate and dry_run:
            raise TriggerValidationError(
                "Simulation and dry-run are mutually exclusive.",
                "TRIGGER_MODE_INVALID",
            )
        if simulate and not (1.0 <= speed <= 1000.0):
            raise TriggerValidationError("Simulation factor out of range (1 to 1000).", "SIM_SPEED_INVALID")
        with self._lock:
            proc = self._procs[rig_id]
            if (
                self._starting_by_rig[rig_id]
                or (proc is not None and proc.poll() is None)
            ):
                return False

            self._starting_by_rig[rig_id] = True
            self._analysis_suppressed_by_rig[rig_id] = False
            self._manual_stop_requested_by_rig[rig_id] = False
            self._cancel_start_requested_by_rig[rig_id] = False

            try:
                ecl = self.validate_start(
                    rig_id=rig_id,
                    require_gps=not simulate,
                    selected=selected,
                    strict_circumstances_date=not (simulate or dry_run),
                )
            except Exception:
                self._starting_by_rig[rig_id] = False
                raise

            ipc_session = None
            if not simulate and self.rig_config_loader is not None:
                try:
                    config = self.rig_config_loader()
                    validate_execution_rig(config, rig_id)

                    exposure_data = json.loads(
                        self._active_exposure_opt_paths[rig_id].read_text(encoding="utf-8")
                    )
                    overrides = {
                        item.get("rig_id"): item.get("photo")
                        for item in exposure_data.get("rigs", ())
                        if isinstance(item, dict) and isinstance(item.get("photo"), dict)
                    }
                    for item in config.get("rigs", ()):
                        if item.get("rig_id") == rig_id and rig_id in overrides:
                            item.setdefault("photo", {}).update(overrides[rig_id])

                except TriggerValidationError:
                    self._starting_by_rig[rig_id] = False
                    self._clear_active_inputs(rig_id)
                    raise
                except (
                    json.JSONDecodeError,
                    OSError,
                    ValueError,
                    KeyError,
                    TypeError,
                ) as exc:
                    self._starting_by_rig[rig_id] = False
                    self._clear_active_inputs(rig_id)
                    self.log(
                        f"Trigger start configuration ERROR: {type(exc).__name__}: {exc}",
                        "error",
                        "trigger",
                    )
                    raise TriggerValidationError(
                        "RIG/capture configuration is invalid or unreadable.",
                        "RIG_CONFIG_INVALID",
                    ) from exc
                except Exception as exc:
                    self._starting_by_rig[rig_id] = False
                    self._clear_active_inputs(rig_id)
                    self.log(
                        f"Trigger start preparation ERROR: {type(exc).__name__}: {exc}",
                        "error",
                        "trigger",
                    )
                    raise

                if self.camera_runtime is not None:
                    try:
                        self.camera_runtime.reconcile(config)
                        ipc_session = self.camera_runtime.open_ipc_session(
                            (rig_id,)
                        )
                    except Exception as exc:
                        self._starting_by_rig[rig_id] = False
                        self._clear_active_inputs(rig_id)
                        self.log(
                            f"Trigger camera preparation ERROR: {type(exc).__name__}: {exc}",
                            "error",
                            "trigger",
                        )
                        raise
            gen=ecl.get("_generated_utc", ""); today=datetime.now(timezone.utc).strftime("%Y-%m-%d")
            if gen and today not in gen: self.log(f"⚠️ todayeclipse.json generated on {gen[:10]} — eclipse not today?", "warning", "trigger")
            mode = (
                "simulation"
                if simulate
                else "dryrun"
                if dry_run
                else "real"
            )
            try:
                self.state.update_trigger_rig(
                    rig_id,
                    {
                        "running": True,
                        "phase": "starting",
                        "mode": mode,
                        "speed": speed if simulate else 1.0,
                    },
                )
                self.emit(
                    "trigger_phase",
                    {"rig_id": rig_id, "phase": "starting"},
                )
                thread = threading.Thread(
                    target=self._run,
                    args=(
                        simulate,
                        speed,
                        dry_run,
                        ipc_session,
                        rig_id,
                    ),
                    name=f"eclipse-trigger-process-rig-{rig_id}",
                    daemon=True,
                )
                self._supervisor_threads[rig_id] = thread
                thread.start()
            except Exception:
                self._starting_by_rig[rig_id] = False
                self._supervisor_threads[rig_id] = None
                self._clear_active_inputs(rig_id)
                if ipc_session is not None:
                    try:
                        self.camera_runtime.close_ipc_session(ipc_session.session_id)
                    except Exception as exc:
                        self._log_rig(
                            rig_id,
                            f"Camera IPC session close error: {exc}",
                            "error",
                        )
                try:
                    self.state.update_trigger_rig(
                        rig_id,
                        {
                            "running": False,
                            "phase": "idle",
                            "mode": None,
                            "speed": None,
                        },
                    )
                    self.emit(
                        "trigger_phase",
                        {"rig_id": rig_id, "phase": "idle"},
                    )
                except Exception:
                    pass
                raise
            return True

    def _set_phase(self, rig_id, phase):
        self.state.update_trigger_rig(rig_id, {"phase": phase})
        self.emit(
            "trigger_phase",
            {"rig_id": rig_id, "phase": phase},
        )

    @staticmethod
    def _runtime_log_event(line):
        phase_events = {
            "partial_before": ("Phase 1 — Partial", "partial"),
            "diamond_ring_c2": ("Phase 2 — Diamond ring", "diamond_ring"),
            "totality": ("Phase 3 — Totality", "totality"),
            "diamond_ring_c3": ("Phase 4 — Diamond ring", "diamond_ring"),
            "partial_after": ("Phase 5 — Partial", "partial"),
        }
        if line == "TRIGGER_PHASE_BORDER":
            return "#" * 65, "phase", None

        if line.startswith("TRIGGER_PHASE "):
            phase_name = line[len("TRIGGER_PHASE "):].strip()
            if not phase_name:
                return "Malformed trigger event: TRIGGER_PHASE", "error", None
            event = phase_events.get(phase_name)
            if event is not None:
                label, public_phase = event
                return f"### {label}", "phase", public_phase

        if line.startswith("TRIGGER_CONFIG "):
            payload = line[len("TRIGGER_CONFIG "):].strip()
            if not payload:
                return "Malformed trigger event: TRIGGER_CONFIG", "error", None
            return payload, "gps", None

        if line.startswith("TRIGGER_PHOTO "):
            parts = line.split(" ", 2)
            if len(parts) == 3:
                level = parts[1]
                if level in {"warning", "orange", "purple", "totality"}:
                    return parts[2], level, None

        if line.startswith("TRIGGER_WAIT "):
            parts = line.split(" ", 2)
            if len(parts) == 3:
                level = parts[1]
                if level in {"warning", "orange", "purple", "totality"}:
                    return parts[2], level, None

        if line.startswith("TRIGGER_AUDIO "):
            filename = line[len("TRIGGER_AUDIO "):].strip()
            if not filename:
                return "Malformed trigger event: TRIGGER_AUDIO", "error", None
            return f"🔊 Sound played: {filename}", "audio", None

        if line == "TRIGGER_SUMMARY_BEGIN":
            return "### Capture summary", "phase", None

        if line == "TRIGGER_SUMMARY_END":
            return "#" * 65, "phase", None

        if line.startswith("TRIGGER_SUMMARY "):
            payload = line[len("TRIGGER_SUMMARY "):]

            try:
                phase_start = payload.index('phase="') + len('phase="')
                phase_end = payload.index('"', phase_start)
                phase = payload[phase_start:phase_end]

                fields = {}
                for item in payload[phase_end + 1:].strip().split():
                    if "=" in item:
                        key, value = item.split("=", 1)
                        fields[key] = value

                photos = int(fields.get("photos", "0"))
            except (ValueError, TypeError):
                return payload, "error", None

            return (
                f"{phase} — Photos: {photos}",
                "success",
                None,
            )

        return line, None, None

    def _run(
        self,
        simulate=False,
        speed=60.0,
        dry_run=False,
        ipc_session=None,
        rig_id=1,
        totality_only=False,
    ):
        proc=None
        stdout_stream = None
        stdout_read_fd = None
        stdout_write_fd = None
        try:
            # STOP may arrive immediately after start() released its lock but
            # before this supervisor thread has created the subprocess.
            with self._lock:
                if self._cancel_start_requested_by_rig[rig_id]:
                    return

            cmd = [
                sys.executable,
                "-u",
                str(self.trigger_script),
            ]

            if totality_only:
                cmd.append("--totality-only")
            else:
                circumstances_path = self._active_circumstances_paths.get(rig_id)
                if circumstances_path is None:
                    raise TriggerValidationError(
                        "Trigger circumstances were not resolved.",
                        "TRIGGER_INPUTS_NOT_LOADED",
                    )
                cmd += ["--file", str(circumstances_path)]

            photo_path = self._active_photo_paths.get(rig_id)
            exposure_opt_path = self._active_exposure_opt_paths.get(rig_id)
            if photo_path is None or (
                not totality_only and exposure_opt_path is None
            ):
                raise TriggerValidationError(
                    "Trigger input files were not resolved.",
                    "TRIGGER_INPUTS_NOT_LOADED",
                )
            cmd += ["--camera", str(photo_path)]
            if not totality_only:
                cmd += ["--exposure-opt", str(exposure_opt_path)]

            if simulate:
                cmd += ["--simulate", "--speed", str(speed)]
            elif dry_run:
                cmd.append("--dry-run")

            env=self._subprocess_env(ipc_session)
            env["SET_TRIGGER_RIG_ID"] = str(rig_id)

            # The real-time scheduler must never wait for the web portal to
            # consume logs.  Make only the child's pipe writer non-blocking:
            # eclipse_trigger.log() already treats BlockingIOError/OSError as
            # best-effort observability failures.  Camera scheduling therefore
            # keeps running even if this supervisor is temporarily unable to
            # drain stdout fast enough.
            stdout_read_fd, stdout_write_fd = os.pipe()
            os.set_blocking(stdout_write_fd, False)
            try:
                proc=subprocess.Popen(
                    cmd,
                    stdout=stdout_write_fd,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    cwd=str(self.project_dir),
                    env=env,
                )
            finally:
                if stdout_write_fd is not None:
                    os.close(stdout_write_fd)
                    stdout_write_fd = None

            # Test doubles historically expose their own .stdout even when the
            # supplied descriptor is not subprocess.PIPE. Preserve that
            # contract; real Popen objects use the non-blocking pipe above.
            if getattr(proc, "stdout", None) is not None:
                os.close(stdout_read_fd)
                stdout_read_fd = None
                stdout_stream = proc.stdout
            else:
                stdout_stream = os.fdopen(
                    stdout_read_fd,
                    "r",
                    encoding="utf-8",
                    errors="replace",
                    buffering=1,
                )
                stdout_read_fd = None
            with self._lock:
                cancel_start = self._cancel_start_requested_by_rig[rig_id]
                if not cancel_start:
                    self._procs[rig_id] = proc
            if cancel_start:
                # The STOP raced with Popen().  Do not publish this process as
                # active; terminate it before it can enter the capture runtime.
                try:
                    proc.terminate()
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    try:
                        proc.kill()
                        proc.wait(timeout=2)
                    except Exception:
                        pass
                except Exception:
                    try:
                        proc.kill()
                        proc.wait(timeout=2)
                    except Exception:
                        pass
                return
            mode = (
                "totality_override"
                if totality_only
                else "simulation"
                if simulate
                else "dryrun"
                if dry_run
                else "real"
            )
            self.state.update_trigger_rig(
                rig_id,
                {
                    "running": True,
                    "phase": (
                        "totality_override"
                        if totality_only
                        else "waiting"
                    ),
                    "mode": mode,
                    "speed": speed if simulate else 1.0,
                },
            )
            self.emit(
                "trigger_phase",
                {
                    "rig_id": rig_id,
                    "phase": (
                        "totality_override"
                        if totality_only
                        else "waiting"
                    ),
                },
            )
            label = (
                f"🌑 RIG {rig_id} — Emergency Totality sequence started."
                if totality_only
                else "► Trigger simulation started."
                if simulate
                else "► Dry-run ×1 started."
                if dry_run
                else "► Trigger started."
            )
            self._log_rig(rig_id, label, "success")
            for raw in iter(stdout_stream.readline, ""):
                if not raw and proc.poll() is not None:
                    break
                raw_line = raw.rstrip()
                if not raw_line:
                    continue
                try:
                    level = self.line_level_fn(raw_line)
                    line = self.line_clean_fn(raw_line)

                    # The Pi has just started this sound locally. Mirror the same
                    # WAV to every connected browser without making Pi audio
                    # dependent on Socket.IO or browser availability.
                    if line.startswith("TRIGGER_AUDIO "):
                        filename = line[len("TRIGGER_AUDIO "):].strip()
                        if filename:
                            self.emit(
                                "audio_play",
                                {
                                    "filename": filename,
                                    "source": "trigger",
                                    "rig_id": rig_id,
                                },
                            )

                    line, event_level, public_phase = self._runtime_log_event(line)
                    if event_level is not None:
                        level = event_level
                    with self._lock:
                        suppress_runtime_updates = (
                            self._analysis_suppressed_by_rig[rig_id]
                        )
                    if (
                        line.startswith("TRIGGER_RUN_ANALYSIS ")
                        and suppress_runtime_updates
                    ):
                        continue

                    # Emergency Totality and STOP make the parent-side phase
                    # authoritative immediately.  Keep draining/logging the
                    # old child stdout, but never let buffered TRIGGER_PHASE
                    # lines overwrite totality_override (or a stopping state)
                    # after suppression has been armed.
                    if not suppress_runtime_updates:
                        if public_phase is not None:
                            self._set_phase(rig_id, public_phase)
                        elif "PHASE 1a" in line:
                            self._set_phase(rig_id, "partial")
                        elif "PHASE 1b" in line or "DIAMOND RING" in line:
                            self._set_phase(rig_id, "diamond_ring")
                        elif "PHASE 2" in line:
                            self._set_phase(rig_id, "totality")
                        elif "PHASE 3a" in line or "PHASE 3b" in line:
                            self._set_phase(rig_id, "partial_end")
                    self._log_rig(rig_id, line, level)
                except Exception as line_exc:
                    # Observability must never terminate supervision of the
                    # real-time child process. Preserve the raw line and keep
                    # draining stdout even if parsing/UI emission fails.
                    try:
                        self._log_rig(
                            rig_id,
                            "Trigger output processing ERROR: "
                            f"{type(line_exc).__name__}: {line_exc}; "
                            f"raw={raw_line!r}",
                            "error",
                        )
                    except Exception:
                        pass
            proc.wait()
        except Exception as exc:
            self._log_rig(
                rig_id,
                f"Trigger thread ERROR: {exc}",
                "error",
            )
            # Never forget a still-running child if supervision itself fails.
            if proc is not None and proc.poll() is None:
                try:
                    proc.terminate()
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    try:
                        proc.kill()
                        proc.wait(timeout=2)
                    except Exception:
                        pass
                except Exception:
                    try:
                        proc.kill()
                        proc.wait(timeout=2)
                    except Exception:
                        pass
        finally:
            if stdout_stream is not None:
                try:
                    stdout_stream.close()
                except Exception:
                    pass
                stdout_stream = None
            for fd_name in ("stdout_read_fd", "stdout_write_fd"):
                fd = locals().get(fd_name)
                if fd is not None:
                    try:
                        os.close(fd)
                    except OSError:
                        pass

            if ipc_session is not None:
                try:
                    self.camera_runtime.close_ipc_session(ipc_session.session_id)
                except Exception as exc:
                    self.log(f"Camera IPC session close error: {exc}","error","trigger")

            process_still_alive = (
                proc is not None and proc.poll() is None
            )

            with self._lock:
                # If STOP arrived after Popen() but before this supervisor
                # published the process into _procs, this thread still owns
                # the startup lifecycle. Treat that cancelled, unpublished
                # child as ours so _starting/state/inputs are released.
                owns_process = (
                    self._procs[rig_id] is proc
                    or (
                        proc is not None
                        and self._procs[rig_id] is None
                        and self._cancel_start_requested_by_rig[rig_id]
                    )
                )
                manual_stop_requested = self._manual_stop_requested_by_rig[rig_id]
                if owns_process and not process_still_alive:
                    self._procs[rig_id] = None
                    self._starting_by_rig[rig_id] = False
                    self._clear_active_inputs(rig_id)
                    self._analysis_suppressed_by_rig[rig_id] = False
                    self._manual_stop_requested_by_rig[rig_id] = False
                    self._cancel_start_requested_by_rig[rig_id] = False
                elif owns_process and process_still_alive:
                    # Supervision is ending but the child resisted every
                    # terminate/kill attempt. Never publish a false idle state
                    # or discard the only process reference. Camera IPC has
                    # already been revoked above, so the orphan cannot keep
                    # controlling hardware; STOP can still retry termination.
                    self._analysis_suppressed_by_rig[rig_id] = True
                    self._starting_by_rig[rig_id] = False
                if self._supervisor_threads[rig_id] is threading.current_thread():
                    self._supervisor_threads[rig_id] = None

            if owns_process and not process_still_alive:
                self.state.update_trigger_rig(
                    rig_id,
                    {
                        "running": False,
                        "phase": "idle",
                        "mode": None,
                        "speed": None,
                    },
                )
                self.emit(
                    "trigger_phase",
                    {"rig_id": rig_id, "phase": "idle"},
                )
            elif owns_process and process_still_alive:
                self._log_rig(
                    rig_id,
                    "TRIGGER SUPERVISION FAILED: child process is still alive; "
                    "camera IPC revoked and process retained for STOP.",
                    "error",
                )

            code = proc.returncode if proc else "?"
            if code == 0:
                self._log_rig(
                    rig_id,
                    "■ Trigger finished (code 0).",
                    "info",
                )
            elif manual_stop_requested:
                self._log_rig(
                    rig_id,
                    f"■ Trigger stopped by user (code {code}).",
                    "warning",
                )
            else:
                self._log_rig(
                    rig_id,
                    f"■ TRIGGER FAILED (code {code}).",
                    "error",
                )

    def override_totality(self, rig_id=1):
        """Interrupt one RIG photo scheduler; preserve global audio."""

        if (
            not isinstance(rig_id, int)
            or isinstance(rig_id, bool)
            or not 1 <= rig_id <= 4
        ):
            return False

        if not hasattr(signal, "SIGUSR1"):
            return False

        signal_error = None
        with self._lock:
            proc = self._procs[rig_id]
            if proc is None or proc.poll() is not None:
                return False

            previous_suppression = self._analysis_suppressed_by_rig[rig_id]
            self._analysis_suppressed_by_rig[rig_id] = True

            try:
                # Keep this short syscall inside the lock so STOP and another
                # override cannot race a rollback after failed signal delivery.
                proc.send_signal(signal.SIGUSR1)
            except Exception as exc:
                self._analysis_suppressed_by_rig[rig_id] = previous_suppression
                signal_error = exc

        if signal_error is not None:
            self.log(
                f"Totality override error: {signal_error}",
                "error",
                "trigger",
            )
            return False

        self.state.update_trigger_rig(
            rig_id,
            {"phase": "totality_override"},
        )

        self.emit(
            "trigger_phase",
            {
                "rig_id": rig_id,
                "phase": "totality_override",
            },
        )

        self.log(
            f"🌑 RIG {rig_id} — Totality override sent to the photo scheduler — audio preserved.",
            "warning",
            "trigger",
        )

        return True

    def start_totality_only(self, rig_id=1):
        """Start emergency Totality now, or preempt an existing photo run."""
        if (
            not isinstance(rig_id, int)
            or isinstance(rig_id, bool)
            or not 1 <= rig_id <= 4
        ):
            raise TriggerValidationError(
                f"Invalid RIG: {rig_id}",
                "RIG_ID_INVALID",
            )

        with self._lock:
            proc = self._procs[rig_id]
            running = proc is not None and proc.poll() is None
        if running:
            if not self.override_totality(rig_id=rig_id):
                raise TriggerValidationError(
                    f"Could not preempt RIG {rig_id}.",
                    "TOTALITY_OVERRIDE_FAILED",
                )
            return "preempted"

        with self._lock:
            if self._starting_by_rig[rig_id]:
                return False
            self._starting_by_rig[rig_id] = True
            self._analysis_suppressed_by_rig[rig_id] = True
            self._manual_stop_requested_by_rig[rig_id] = False
            self._cancel_start_requested_by_rig[rig_id] = False

        ipc_session = None
        try:
            self._resolve_totality_input(rig_id)
            if self.rig_config_loader is not None:
                config = self.rig_config_loader()
                validate_execution_rig(config, rig_id)
                if self.camera_runtime is not None:
                    self.camera_runtime.reconcile(config)
                    ipc_session = self.camera_runtime.open_ipc_session((rig_id,))

            self.state.update_trigger_rig(
                rig_id,
                {
                    "running": True,
                    "phase": "totality_override",
                    "mode": "totality_override",
                    "speed": 1.0,
                },
            )
            self.emit(
                "trigger_phase",
                {"rig_id": rig_id, "phase": "totality_override"},
            )
            thread = threading.Thread(
                target=self._run,
                kwargs={
                    "ipc_session": ipc_session,
                    "rig_id": rig_id,
                    "totality_only": True,
                },
                name=f"totality-only-process-rig-{rig_id}",
                daemon=True,
            )
            with self._lock:
                self._supervisor_threads[rig_id] = thread
            thread.start()
            return "started"
        except Exception:
            with self._lock:
                self._starting_by_rig[rig_id] = False
                self._analysis_suppressed_by_rig[rig_id] = False
                self._manual_stop_requested_by_rig[rig_id] = False
                self._cancel_start_requested_by_rig[rig_id] = False
                self._supervisor_threads[rig_id] = None
                self._clear_active_inputs(rig_id)

            if ipc_session is not None and self.camera_runtime is not None:
                try:
                    self.camera_runtime.close_ipc_session(
                        ipc_session.session_id
                    )
                except Exception as close_exc:
                    self._log_rig(
                        rig_id,
                        f"Camera IPC session close error: {close_exc}",
                        "error",
                    )

            # Emergency Totality publishes running/totality_override before
            # starting its supervisor thread.  If thread creation/start fails,
            # roll that externally visible state back exactly like normal
            # start() does; otherwise the UI can remain stuck on a run that
            # never existed.
            try:
                self.state.update_trigger_rig(
                    rig_id,
                    {
                        "running": False,
                        "phase": "idle",
                        "mode": None,
                        "speed": None,
                    },
                )
                self.emit(
                    "trigger_phase",
                    {"rig_id": rig_id, "phase": "idle"},
                )
            except Exception:
                pass

            raise

    def stop(self, rig_id=1):
        if (
            not isinstance(rig_id, int)
            or isinstance(rig_id, bool)
            or not 1 <= rig_id <= 4
        ):
            return {
                "status": "invalid_rig",
                "rig_id": rig_id,
            }

        with self._lock:
            stopping_map = getattr(self, "_stopping_by_rig", None)
            if not isinstance(stopping_map, dict):
                stopping_map = {
                    item_rig_id: False
                    for item_rig_id in range(1, 5)
                }
                self._stopping_by_rig = stopping_map

            if stopping_map.get(rig_id, False):
                return {
                    "status": "stopping",
                    "rig_id": rig_id,
                    "forced": False,
                    "still_running": True,
                }

            proc = self._procs[rig_id]
            starting_map = getattr(self, "_starting_by_rig", None)
            supervisor_map = getattr(self, "_supervisor_threads", None)
            cancel_map = getattr(self, "_cancel_start_requested_by_rig", None)

            starting = (
                bool(starting_map.get(rig_id, False))
                if isinstance(starting_map, dict)
                else False
            )
            supervisor = (
                supervisor_map.get(rig_id)
                if isinstance(supervisor_map, dict)
                else None
            )

            if starting:
                # A start request can have released start() while _run() has
                # not yet published its Popen object. Make STOP authoritative
                # across that gap instead of returning a false not_running.
                if isinstance(cancel_map, dict):
                    cancel_map[rig_id] = True
                self._analysis_suppressed_by_rig[rig_id] = True
                self._manual_stop_requested_by_rig[rig_id] = True

        if (not proc or proc.poll() is not None) and starting:
            if (
                supervisor is not None
                and supervisor is not threading.current_thread()
            ):
                supervisor.join(timeout=5.0)
            with self._lock:
                proc = self._procs[rig_id]
                starting_map = getattr(self, "_starting_by_rig", None)
                starting = (
                    bool(starting_map.get(rig_id, False))
                    if isinstance(starting_map, dict)
                    else False
                )
            if not proc or proc.poll() is not None:
                return {
                    "status": "stopped" if not starting else "stopping",
                    "rig_id": rig_id,
                    "forced": False,
                    "still_running": bool(starting),
                }

        if not proc or proc.poll() is not None:
            return {
                "status": "not_running",
                "rig_id": rig_id,
            }

        with self._lock:
            if self._stopping_by_rig.get(rig_id, False):
                return {
                    "status": "stopping",
                    "rig_id": rig_id,
                    "forced": False,
                    "still_running": True,
                }
            self._stopping_by_rig[rig_id] = True
            self._analysis_suppressed_by_rig[rig_id] = True
            self._manual_stop_requested_by_rig[rig_id] = True

        try:
            try:
                proc.terminate()
            except Exception:
                pass

            forced = False

            # SIGTERM only sets the trigger stop flag. An atomic camera PHOTO
            # group already in progress may finish before the process observes
            # that flag. Keep the existing 30 s emergency bound, but coalesce
            # duplicate STOP requests so only one terminate/kill sequence owns
            # the process.
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                forced = True
            else:
                forced = proc.poll() is None

            if forced:
                try:
                    proc.kill()
                    proc.wait(timeout=2)
                except Exception:
                    pass

                self.log(
                    f"■ RIG {rig_id} — Trigger killed (SIGKILL) after 30 s graceful-stop timeout.",
                    "warning",
                    "trigger",
                )

            with self._lock:
                supervisor_map = getattr(self, "_supervisor_threads", None)
                supervisor = (
                    supervisor_map.get(rig_id)
                    if isinstance(supervisor_map, dict)
                    else None
                )
            if (
                supervisor is not None
                and supervisor is not threading.current_thread()
            ):
                supervisor.join(timeout=5.0)

            still = proc.poll() is None

            self.log(
                (
                    f"⚠️ RIG {rig_id} — process still active after SIGKILL."
                    if still
                    else f"■ RIG {rig_id} — Trigger stopped manually."
                ),
                "error" if still else "warning",
                "trigger",
            )

            return {
                "status": "stopped",
                "rig_id": rig_id,
                "forced": forced,
                "still_running": still,
            }
        finally:
            with self._lock:
                self._stopping_by_rig[rig_id] = False

