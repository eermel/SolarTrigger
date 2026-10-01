from __future__ import annotations
from pathlib import Path
import hashlib
import json, os, shutil, signal, subprocess, sys, threading, time, uuid
from threading import Thread as _HeartbeatThread
from datetime import datetime, timezone
from backend.timeline import build_timeline, sequence_seconds
from backend.phase_trigger import build_phase_schedule
from backend.trigger_heartbeat import (
    DEFAULT_HEARTBEAT_TIMEOUT_S,
    HEARTBEAT_ENV,
    HeartbeatSupervisor,
)

TOTALITY_START_PREEMPT_TIMEOUT_S = 8.0
TOTALITY_CHILD_RECOVERY_MAX_ATTEMPTS = 3

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

def validate_trigger_gps_state(gps, *, now_utc=None):
    """Validate the GPS state required before any Trigger hardware preflight.

    This is intentionally the first start-time validation: camera and mount
    I/O must never begin until the system clock synchronization is known-good.
    """
    gps = gps if isinstance(gps, dict) else {}

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

        current = now_utc or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        else:
            current = current.astimezone(timezone.utc)

        age = (
            current
            - sync_dt.astimezone(timezone.utc)
        ).total_seconds()
    except Exception as exc:
        raise TriggerValidationError(
            "⚠️ GPS synchronization timestamp is invalid. "
            "Synchronize again.",
            "GPS_SYNC_TIME_INVALID",
        ) from exc

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

    return gps


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
                 product_configs_dir=None, run_journal=None,
                 heartbeat_timeout_s=DEFAULT_HEARTBEAT_TIMEOUT_S,
                 failure_alert_fn=None):
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
        self._active_rig_config_paths = {}
        self._validated_input_bytes_by_rig = {}
        self._snapshot_ids_by_rig = {}
        self.snapshot_root = (
            self.project_dir / "var" / "state" / "trigger_inputs"
        )
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
        self._cancel_start_events = {
            rig_id: threading.Event()
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
        self.run_journal = run_journal
        self.heartbeat_timeout_s = max(0.05, float(heartbeat_timeout_s))
        self.failure_alert_fn = failure_alert_fn
        self._run_ids_by_rig = {rig_id: None for rig_id in range(1, 5)}

    def _active_selection(self, rig_id):
        circumstances = self._active_circumstances_paths.get(rig_id)
        photo = self._active_photo_paths.get(rig_id)
        exposure = self._active_exposure_opt_paths.get(rig_id)
        return {
            "circumstances_file": circumstances.name if circumstances else None,
            "photo_file": photo.name if photo else None,
            "exposure_opt_file": exposure.name if exposure else None,
        }

    def _active_input_fingerprints(self, rig_id):
        paths = {
            "circumstances": self._active_circumstances_paths.get(rig_id),
            "photo": self._active_photo_paths.get(rig_id),
            "exposure_opt": self._active_exposure_opt_paths.get(rig_id),
            "rig_config": self._active_rig_config_paths.get(rig_id),
        }
        fingerprints = {}
        for role, path in paths.items():
            if path is None:
                continue
            data = path.read_bytes()
            fingerprints[role] = {
                "name": path.name,
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        return fingerprints

    _SNAPSHOT_FILENAMES = {
        "circumstances": "circumstances.json",
        "photo": "photo.json",
        "exposure_opt": "exposure_opt.json",
        "rig_config": "rig_config.json",
    }

    def _snapshot_dir(self, snapshot_id):
        if (
            not isinstance(snapshot_id, str)
            or not snapshot_id
            or Path(snapshot_id).name != snapshot_id
            or any(
                not (char.isalnum() or char in "-_")
                for char in snapshot_id
            )
        ):
            raise TriggerValidationError(
                "Trigger snapshot identity is invalid.",
                "RECOVERY_INPUTS_UNVERIFIED",
            )
        return self.snapshot_root / snapshot_id

    @staticmethod
    def _write_snapshot_file(path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)

    def _freeze_run_inputs(self, rig_id, snapshot_id, rig_config=None):
        payloads = dict(
            self._validated_input_bytes_by_rig.get(rig_id) or {}
        )
        if not payloads:
            raise TriggerValidationError(
                "Validated trigger inputs are unavailable.",
                "TRIGGER_INPUTS_NOT_LOADED",
            )

        directory = self._snapshot_dir(snapshot_id)
        directory.mkdir(parents=True, exist_ok=True)
        frozen = {}
        for role in ("circumstances", "photo", "exposure_opt"):
            data = payloads.get(role)
            if data is None:
                continue
            path = directory / self._SNAPSHOT_FILENAMES[role]
            self._write_snapshot_file(path, data)
            frozen[role] = path

        if rig_config is not None:
            encoded = (
                json.dumps(
                    rig_config,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
            ).encode("utf-8")
            path = directory / self._SNAPSHOT_FILENAMES["rig_config"]
            self._write_snapshot_file(path, encoded)
            frozen["rig_config"] = path

        if "circumstances" in frozen:
            self._active_circumstances_paths[rig_id] = frozen["circumstances"]
        if "photo" in frozen:
            self._active_photo_paths[rig_id] = frozen["photo"]
        if "exposure_opt" in frozen:
            self._active_exposure_opt_paths[rig_id] = frozen["exposure_opt"]
        if "rig_config" in frozen:
            self._active_rig_config_paths[rig_id] = frozen["rig_config"]
        self._snapshot_ids_by_rig[rig_id] = snapshot_id
        return frozen

    def _restore_run_snapshot(self, rig_id, snapshot_id, expected):
        directory = self._snapshot_dir(snapshot_id)
        paths = {}
        for role, details in (expected or {}).items():
            if role not in self._SNAPSHOT_FILENAMES:
                continue
            if not isinstance(details, dict):
                continue
            path = directory / self._SNAPSHOT_FILENAMES[role]
            if path.is_file():
                paths[role] = path

        if "circumstances" in paths:
            self._active_circumstances_paths[rig_id] = paths["circumstances"]
        if "photo" in paths:
            self._active_photo_paths[rig_id] = paths["photo"]
        if "exposure_opt" in paths:
            self._active_exposure_opt_paths[rig_id] = paths["exposure_opt"]
        if "rig_config" in paths:
            self._active_rig_config_paths[rig_id] = paths["rig_config"]
        self._snapshot_ids_by_rig[rig_id] = snapshot_id

        self._verify_recovery_input_fingerprints(rig_id, expected)

        rig_config = None
        rig_path = paths.get("rig_config")
        if rig_path is not None:
            try:
                rig_config = json.loads(rig_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise TriggerValidationError(
                    "Frozen RIG configuration is unreadable.",
                    "RECOVERY_INPUTS_CHANGED",
                ) from exc
            if not isinstance(rig_config, dict):
                raise TriggerValidationError(
                    "Frozen RIG configuration is invalid.",
                    "RECOVERY_INPUTS_CHANGED",
                )
        return paths, rig_config

    def _discard_run_snapshot(self, snapshot_id):
        if not snapshot_id:
            return
        try:
            directory = self._snapshot_dir(snapshot_id)
            shutil.rmtree(directory, ignore_errors=True)
        except Exception as exc:
            self.log(
                f"Trigger snapshot cleanup warning: {exc}",
                "warning",
                "trigger",
            )

    def _verify_recovery_input_fingerprints(self, rig_id, expected):
        if not isinstance(expected, dict) or not expected:
            raise TriggerValidationError(
                "Persisted trigger inputs cannot be verified.",
                "RECOVERY_INPUTS_UNVERIFIED",
            )

        try:
            actual = self._active_input_fingerprints(rig_id)
        except OSError as exc:
            raise TriggerValidationError(
                "Persisted trigger inputs are unreadable during recovery.",
                "RECOVERY_INPUTS_CHANGED",
            ) from exc

        if actual != expected:
            raise TriggerValidationError(
                "Persisted trigger inputs changed after the original start.",
                "RECOVERY_INPUTS_CHANGED",
            )

    def _journal_begin(
        self,
        rig_id,
        mode,
        selected,
        *,
        totality_only=False,
        run_id=None,
        snapshot_id=None,
    ):
        if self.run_journal is None:
            return None
        try:
            entry = self.run_journal.begin_run(
                rig_id=rig_id,
                mode=mode,
                selected=selected,
                speed=1.0,
                totality_only=totality_only,
                input_fingerprints=self._active_input_fingerprints(rig_id),
                run_id=run_id,
                snapshot_id=snapshot_id,
            )
            return entry.get("run_id")
        except Exception as exc:
            # Persistence failure must not prevent an eclipse capture from
            # starting. It disables crash recovery for this run and is made
            # highly visible instead.
            self._log_rig(
                rig_id,
                f"Trigger recovery journal unavailable: {type(exc).__name__}: {exc}",
                "error",
            )
            return None

    def _journal_finish(self, rig_id, run_id, status, **kwargs):
        if self.run_journal is None or run_id is None:
            return
        try:
            self.run_journal.finish(
                rig_id=rig_id,
                run_id=run_id,
                status=status,
                **kwargs,
            )
        except Exception as exc:
            self._log_rig(
                rig_id,
                f"Trigger recovery journal finalization error: {exc}",
                "error",
            )

    def _clear_published_failure(self, rig_id):
        clear_fn = getattr(self.state, "clear_trigger_failure", None)
        if callable(clear_fn):
            clear_fn(rig_id)

    def publish_external_failure(
        self, rig_id, code, detail, *, exit_code=None, audible=False
    ):
        failure_state = {
            "running": False,
            "phase": "failed",
            "mode": None,
            "speed": None,
            "failure_code": str(code),
            "failure_detail": str(detail),
            "exit_code": exit_code,
        }
        self.state.update_trigger_rig(rig_id, failure_state)
        payload = {
            "rig_id": rig_id,
            "phase": "failed",
            "running": False,
            "code": str(code),
            "message": str(detail),
        }
        if exit_code is not None:
            payload["exit_code"] = exit_code
        self.emit("trigger_phase", payload)
        self.emit("trigger_failure", payload)
        self._log_rig(rig_id, f"TRIGGER FAILED [{code}] {detail}", "critical")
        if audible and callable(self.failure_alert_fn):
            try:
                self.failure_alert_fn(rig_id, str(code), str(detail))
            except Exception as exc:
                self._log_rig(
                    rig_id,
                    f"Trigger failure audio alert failed: {exc}",
                    "error",
                )

    def _recovery_window_open(self, rig_id):
        path = self._active_circumstances_paths.get(rig_id)
        if path is None:
            return False
        try:
            ecl = json.loads(path.read_text(encoding="utf-8"))
            timeline = build_timeline(
                ecl,
                fallback_date=datetime.now(timezone.utc).date(),
            )
            tend = timeline.get("TEND")
            return tend is not None and datetime.now(timezone.utc) < tend
        except Exception:
            return False

    def _child_recovery_safe(
        self,
        *,
        rig_id,
        totality_only,
        recovery_attempt,
        last_stage,
        heartbeat_timed_out,
        recovery_window_open=None,
    ):
        if recovery_attempt >= 1 or heartbeat_timed_out:
            return False
        stage = str(last_stage or "")
        safe = (
            stage == "startup"
            or stage == "ipc.ready"
            or stage == "runtime.begin"
            or stage == "runtime.end"
            or stage == "wait"
            or stage == "phase.ready"
            or stage == "capture.end"
        )
        if not safe:
            return False
        if totality_only:
            return True
        if recovery_window_open is None:
            recovery_window_open = self._recovery_window_open(rig_id)
        return bool(recovery_window_open)

    def _totality_window_open(
        self,
        rig_id,
        *,
        snapshot_id=None,
        now_utc=None,
    ):
        """Return true only while the authoritative C2-C3 window is open."""
        path = self._active_circumstances_paths.get(rig_id)
        if path is None and isinstance(snapshot_id, str) and snapshot_id:
            candidate = (
                self._snapshot_dir(snapshot_id)
                / self._SNAPSHOT_FILENAMES["circumstances"]
            )
            if candidate.is_file():
                path = candidate
        if path is None:
            return False
        try:
            ecl = json.loads(path.read_text(encoding="utf-8"))
            timeline = build_timeline(
                ecl,
                fallback_date=datetime.now(timezone.utc).date(),
            )
            c2 = timeline.get("C2")
            c3 = timeline.get("C3")
            current = now_utc or datetime.now(timezone.utc)
            if current.tzinfo is None:
                current = current.replace(tzinfo=timezone.utc)
            else:
                current = current.astimezone(timezone.utc)
            return (
                c2 is not None
                and c3 is not None
                and c2 <= current < c3
            )
        except Exception:
            return False

    def _child_recovery_plan(
        self,
        *,
        rig_id,
        totality_only,
        recovery_attempt,
        last_stage,
        heartbeat_timed_out,
        recovery_window_open,
        totality_window_open,
    ):
        # During true totality, preserving photography dominates stage-level
        # uncertainty. The old scheduler process is already dead before this
        # policy is evaluated, and the old IPC lease is revoked before restart.
        if (
            totality_window_open
            and recovery_attempt < TOTALITY_CHILD_RECOVERY_MAX_ATTEMPTS
        ):
            return "totality"

        if self._child_recovery_safe(
            rig_id=rig_id,
            totality_only=totality_only,
            recovery_attempt=recovery_attempt,
            last_stage=last_stage,
            heartbeat_timed_out=heartbeat_timed_out,
            recovery_window_open=recovery_window_open,
        ):
            return "same"

        return None

    def recover_persisted_run(self, entry):
        # Resume one same-boot persisted run without replaying past phases.
        if not isinstance(entry, dict):
            raise TriggerValidationError("Invalid recovery journal entry.", "RECOVERY_INVALID")
        rig_id = int(entry.get("rig_id"))
        run_id = entry.get("run_id")
        if not isinstance(run_id, str) or not run_id:
            raise TriggerValidationError("Recovery run_id is missing.", "RECOVERY_INVALID")
        input_fingerprints = entry.get("input_fingerprints")
        snapshot_id = entry.get("snapshot_id")
        if not isinstance(input_fingerprints, dict) or not input_fingerprints:
            raise TriggerValidationError(
                "Recovery journal has no verifiable trigger input fingerprints.",
                "RECOVERY_INPUTS_UNVERIFIED",
            )
        if not isinstance(snapshot_id, str) or not snapshot_id:
            raise TriggerValidationError(
                "Recovery journal has no frozen trigger snapshot.",
                "RECOVERY_INPUTS_UNVERIFIED",
            )
        if entry.get("totality_only") is True or entry.get("mode") == "totality_override":
            return self.start_totality_only(
                rig_id=rig_id,
                _recovery=True,
                _run_id=run_id,
                _recovery_snapshot_id=snapshot_id,
                _recovery_input_fingerprints=input_fingerprints,
            )
        if entry.get("mode") != "real":
            raise TriggerValidationError(
                "Only real eclipse runs can be recovered automatically.",
                "RECOVERY_MODE_UNSAFE",
            )
        selected = entry.get("selected")
        if not isinstance(selected, dict):
            raise TriggerValidationError("Recovery inputs are missing.", "RECOVERY_INVALID")
        # Restore and verify the immutable run snapshot before touching hardware.
        paths, _config = self._restore_run_snapshot(
            rig_id,
            snapshot_id,
            input_fingerprints,
        )
        if "circumstances" not in paths:
            self._clear_active_inputs(rig_id)
            raise TriggerValidationError(
                "Persisted eclipse snapshot is incomplete.",
                "RECOVERY_INPUTS_CHANGED",
            )
        if not self._recovery_window_open(rig_id):
            self._clear_active_inputs(rig_id)
            raise TriggerValidationError(
                "Persisted eclipse run is already outside its active timeline.",
                "RECOVERY_WINDOW_ENDED",
            )
        self._clear_active_inputs(rig_id)
        return self.start(
            rig_id=rig_id,
            selected=selected,
            _recovery=True,
            _run_id=run_id,
            _recovery_snapshot_id=snapshot_id,
            _recovery_input_fingerprints=input_fingerprints,
        )

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

    def _start_cancel_event(self, rig_id):
        events = getattr(self, "_cancel_start_events", None)
        if not isinstance(events, dict):
            events = {
                item_rig_id: threading.Event()
                for item_rig_id in range(1, 5)
            }
            self._cancel_start_events = events
        event = events.get(rig_id)
        if event is None:
            event = threading.Event()
            events[rig_id] = event
        return event

    def _request_start_cancel(self, rig_id):
        """Priority-safe cancellation channel independent of the lifecycle lock."""
        self._start_cancel_event(rig_id).set()
        cancel_map = getattr(self, "_cancel_start_requested_by_rig", None)
        if isinstance(cancel_map, dict):
            cancel_map[rig_id] = True

    def _clear_start_cancel_request(self, rig_id):
        self._start_cancel_event(rig_id).clear()
        cancel_map = getattr(self, "_cancel_start_requested_by_rig", None)
        if isinstance(cancel_map, dict):
            cancel_map[rig_id] = False

    def _start_cancel_requested(self, rig_id):
        if self._start_cancel_event(rig_id).is_set():
            return True
        cancel_map = getattr(self, "_cancel_start_requested_by_rig", None)
        return bool(
            isinstance(cancel_map, dict)
            and cancel_map.get(rig_id, False)
        )

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
        """Resolve and validate the fixed product configuration for emergency Totality."""
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
            data = path.read_bytes()
            photo = json.loads(data)
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
        self._validated_input_bytes_by_rig[rig_id] = {"photo": data}
        return path

    def _clear_active_inputs(self, rig_id):
        for attribute in (
            "_active_circumstances_paths",
            "_active_photo_paths",
            "_active_exposure_opt_paths",
            "_active_rig_config_paths",
            "_validated_input_bytes_by_rig",
            "_snapshot_ids_by_rig",
        ):
            mapping = getattr(self, attribute, None)
            if isinstance(mapping, dict):
                mapping.pop(rig_id, None)

    def validate_start(
        self,
        rig_id=1,
        require_gps=True,
        selected=None,
        strict_circumstances_date=True,
        _resolved_paths=None,
    ):
        if require_gps:
            validate_trigger_gps_state(self.state.snapshot("gps") or {})

        paths = (
            _resolved_paths
            if isinstance(_resolved_paths, dict)
            else self._resolve_trigger_inputs(rig_id, selected)
        )
        circumstances_path = paths["circumstances"]
        raw_inputs = {
            role: path.read_bytes()
            for role, path in paths.items()
            if role in {"circumstances", "photo", "exposure_opt"}
            and path is not None
        }
        filename = circumstances_path.name

        try:
            ecl = json.loads(raw_inputs["circumstances"])
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
            photo = json.loads(raw_inputs["photo"])
            exposure_opt = json.loads(raw_inputs["exposure_opt"])
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
        if not hasattr(self, "_validated_input_bytes_by_rig"):
            self._validated_input_bytes_by_rig = {}
        self._validated_input_bytes_by_rig[rig_id] = raw_inputs
        return ecl

    def start(self, rig_id=1, simulate=False, speed=60.0, dry_run=False,
              selected=None, _recovery=False, _run_id=None,
              _child_recovery_attempt=0, _recovery_input_fingerprints=None,
              _recovery_snapshot_id=None):
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
        mode = (
            "simulation"
            if simulate
            else "dryrun"
            if dry_run
            else "real"
        )
        run_id = _run_id
        snapshot_id = _recovery_snapshot_id
        if not _recovery:
            if mode == "real":
                run_id = uuid.uuid4().hex
            snapshot_id = run_id or uuid.uuid4().hex

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
            self._clear_start_cancel_request(rig_id)

            config = None
            try:
                if _recovery:
                    if (
                        not isinstance(snapshot_id, str)
                        or not snapshot_id
                        or not isinstance(_recovery_input_fingerprints, dict)
                        or not _recovery_input_fingerprints
                    ):
                        raise TriggerValidationError(
                            "Recovery snapshot metadata is missing.",
                            "RECOVERY_INPUTS_UNVERIFIED",
                        )
                    restored_paths, config = self._restore_run_snapshot(
                        rig_id,
                        snapshot_id,
                        _recovery_input_fingerprints,
                    )
                    ecl = self.validate_start(
                        rig_id=rig_id,
                        require_gps=False,
                        selected=selected,
                        strict_circumstances_date=not (simulate or dry_run),
                        _resolved_paths=restored_paths,
                    )
                    self._verify_recovery_input_fingerprints(
                        rig_id,
                        _recovery_input_fingerprints,
                    )
                else:
                    ecl = self.validate_start(
                        rig_id=rig_id,
                        require_gps=not simulate,
                        selected=selected,
                        strict_circumstances_date=not (simulate or dry_run),
                    )
            except Exception:
                self._starting_by_rig[rig_id] = False
                self._clear_active_inputs(rig_id)
                raise

            ipc_session = None
            if not simulate and self.rig_config_loader is not None:
                try:
                    if config is None:
                        config = self.rig_config_loader()
                    validate_execution_rig(config, rig_id)

                    exposure_data = json.loads(
                        self._validated_input_bytes_by_rig[rig_id]["exposure_opt"]
                    )
                    overrides = {
                        item.get("rig_id"): item.get("photo")
                        for item in exposure_data.get("rigs", ())
                        if isinstance(item, dict) and isinstance(item.get("photo"), dict)
                    }
                    for item in config.get("rigs", ()):
                        if item.get("rig_id") == rig_id and rig_id in overrides:
                            item.setdefault("photo", {}).update(overrides[rig_id])

                    if not _recovery:
                        self._freeze_run_inputs(
                            rig_id,
                            snapshot_id,
                            config,
                        )

                except TriggerValidationError:
                    self._starting_by_rig[rig_id] = False
                    self._clear_active_inputs(rig_id)
                    if not _recovery:
                        self._discard_run_snapshot(snapshot_id)
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
                    if not _recovery:
                        self._discard_run_snapshot(snapshot_id)
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
                    if not _recovery:
                        self._discard_run_snapshot(snapshot_id)
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
                        promote_session = getattr(
                            self.camera_runtime,
                            "mark_ipc_session_priority",
                            None,
                        )
                        if callable(promote_session):
                            promote_session(ipc_session.session_id, True)
                    except Exception as exc:
                        self._starting_by_rig[rig_id] = False
                        self._clear_active_inputs(rig_id)
                        if not _recovery:
                            self._discard_run_snapshot(snapshot_id)
                        self.log(
                            f"Trigger camera preparation ERROR: {type(exc).__name__}: {exc}",
                            "error",
                            "trigger",
                        )
                        raise
            if not _recovery and (
                simulate or self.rig_config_loader is None
            ):
                self._freeze_run_inputs(rig_id, snapshot_id, None)

            try:
                gen = ecl.get("_generated_utc", "")
                today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                if gen and today not in gen:
                    self.log(
                        f"⚠️ todayeclipse.json generated on {gen[:10]} — eclipse not today?",
                        "warning",
                        "trigger",
                    )
                if mode == "real" and not _recovery:
                    run_id = self._journal_begin(
                        rig_id,
                        mode,
                        selected,
                        run_id=run_id,
                        snapshot_id=snapshot_id,
                    )
                if run_id is not None:
                    self._run_ids_by_rig[rig_id] = run_id
                published_phase = "recovering" if _recovery else "starting"

                self._clear_published_failure(rig_id)
                self.state.update_trigger_rig(
                    rig_id,
                    {
                        "running": True,
                        "phase": published_phase,
                        "mode": mode,
                        "speed": speed if simulate else 1.0,
                    },
                )
                self.emit(
                    "trigger_phase",
                    {"rig_id": rig_id, "phase": published_phase},
                )
                thread = threading.Thread(
                    target=self._run,
                    kwargs={
                        "simulate": simulate,
                        "speed": speed,
                        "dry_run": dry_run,
                        "ipc_session": ipc_session,
                        "rig_id": rig_id,
                        "run_id": run_id,
                        "snapshot_id": snapshot_id,
                        "input_fingerprints": self._active_input_fingerprints(rig_id),
                        "recovery_attempt": _child_recovery_attempt,
                    },
                    name=f"eclipse-trigger-process-rig-{rig_id}",
                    daemon=True,
                )
                self._supervisor_threads[rig_id] = thread
                thread.start()
            except Exception as start_exc:
                self._starting_by_rig[rig_id] = False
                self._supervisor_threads[rig_id] = None
                self._journal_finish(
                    rig_id, run_id, "failed",
                    failure_code="START_FAILED", detail=str(start_exc),
                )
                self._run_ids_by_rig[rig_id] = None
                self._clear_active_inputs(rig_id)
                self._discard_run_snapshot(snapshot_id)
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
        run_id=None,
        snapshot_id=None,
        input_fingerprints=None,
        recovery_attempt=0,
    ):
        proc=None
        stdout_stream = None
        stdout_read_fd = None
        stdout_write_fd = None
        heartbeat_read_fd = None
        heartbeat_write_fd = None
        heartbeat = None
        heartbeat_last_stage = None
        heartbeat_timed_out = False
        supervisor_error = None
        recovery_selection = None
        if not hasattr(self, 'run_journal'):
            self.run_journal = None
        if not hasattr(self, 'heartbeat_timeout_s'):
            self.heartbeat_timeout_s = DEFAULT_HEARTBEAT_TIMEOUT_S
        if not hasattr(self, '_run_ids_by_rig'):
            self._run_ids_by_rig = {item: None for item in range(1, 5)}
        if not hasattr(self, '_stopping_by_rig'):
            self._stopping_by_rig = {item: False for item in range(1, 5)}
        try:
            # STOP may arrive immediately after start() released its lock but
            # before this supervisor thread has created the subprocess.
            if self._start_cancel_requested(rig_id):
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

            rig_config_path = getattr(
                self,
                "_active_rig_config_paths",
                {},
            ).get(rig_id)
            if rig_config_path is not None:
                cmd += ["--rig-config", str(rig_config_path)]

            if simulate:
                cmd += ["--simulate", "--speed", str(speed)]
            elif dry_run:
                cmd.append("--dry-run")

            env=self._subprocess_env(ipc_session)
            env["SET_TRIGGER_RIG_ID"] = str(rig_id)
            recovery_selection = self._active_selection(rig_id)
            heartbeat_read_fd, heartbeat_write_fd = os.pipe()
            # Heartbeat is observability only.  The child must never block its
            # real-time scheduler because the parent reader is delayed or has
            # stopped draining the pipe.
            os.set_blocking(heartbeat_write_fd, False)
            env[HEARTBEAT_ENV] = str(heartbeat_write_fd)

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
                    pass_fds=(heartbeat_write_fd,),
                )
                # Popen() is the hardware/runtime ownership boundary.  Publish
                # the child handle immediately, before heartbeat/stdout setup,
                # so any later supervision failure or concurrent STOP can
                # still target the exact scheduler process.
                with self._lock:
                    self._procs[rig_id] = proc
            finally:
                if stdout_write_fd is not None:
                    try:
                        os.close(stdout_write_fd)
                    except OSError:
                        pass
                    stdout_write_fd = None
                if heartbeat_write_fd is not None:
                    try:
                        os.close(heartbeat_write_fd)
                    except OSError:
                        pass
                    heartbeat_write_fd = None

            heartbeat = HeartbeatSupervisor(
                read_fd=heartbeat_read_fd,
                proc=proc,
                timeout_s=self.heartbeat_timeout_s,
                manual_stop_fn=lambda: self._manual_stop_requested_by_rig[rig_id],
                log_fn=lambda message: self._log_rig(rig_id, message, "critical"),
                thread_factory=_HeartbeatThread,
            ).start()
            heartbeat_read_fd = None

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
                cancel_start = self._start_cancel_requested(rig_id)
                if not cancel_start:
                    # Startup ownership is now fully supervised.  From this
                    # point the live process itself is sufficient to report
                    # activity, and Emergency Totality may safely preempt it.
                    self._starting_by_rig[rig_id] = False
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
            supervisor_error = exc
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
            if heartbeat is not None:
                heartbeat.stop()
                heartbeat_last_stage, heartbeat_timed_out = heartbeat.snapshot()
            if stdout_stream is not None:
                try:
                    stdout_stream.close()
                except Exception:
                    pass
                stdout_stream = None
            for fd_name in (
                "stdout_read_fd", "stdout_write_fd",
                "heartbeat_read_fd", "heartbeat_write_fd",
            ):
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
                    self._log_rig(
                        rig_id,
                        f"Camera IPC session close error: {exc}",
                        "error",
                    )

            process_still_alive = proc is not None and proc.poll() is None
            recovery_window_open = (
                True
                if totality_only
                else self._recovery_window_open(rig_id)
            )
            totality_window_open = self._totality_window_open(
                rig_id,
                snapshot_id=snapshot_id,
            )
            with self._lock:
                manual_stop_requested = self._manual_stop_requested_by_rig[rig_id]
                owns_process = (
            self._procs[rig_id] is proc
            or (
                proc is not None
                and self._procs[rig_id] is None
                and self._start_cancel_requested(rig_id)
            )
            or (
                proc is None
                and self._starting_by_rig[rig_id]
            )
        )
                if owns_process and not process_still_alive:
                    if proc is not None and self._procs[rig_id] is proc:
                        self._procs[rig_id] = None
                    self._starting_by_rig[rig_id] = False
                    self._clear_active_inputs(rig_id)
                    self._analysis_suppressed_by_rig[rig_id] = False
                    self._manual_stop_requested_by_rig[rig_id] = False
                    self._clear_start_cancel_request(rig_id)
                    self._stopping_by_rig[rig_id] = False
                elif owns_process and process_still_alive:
                    self._analysis_suppressed_by_rig[rig_id] = True
                    self._starting_by_rig[rig_id] = False
                if self._supervisor_threads[rig_id] is threading.current_thread():
                    self._supervisor_threads[rig_id] = None

            code = proc.returncode if proc is not None else None
            failed = bool(
                supervisor_error is not None
                or (code is not None and code != 0)
                or (proc is None and not manual_stop_requested)
            )
            recovery_plan = None
            if (
                owns_process
                and not process_still_alive
                and failed
                and not manual_stop_requested
                and not simulate
                and not dry_run
            ):
                recovery_plan = self._child_recovery_plan(
                    rig_id=rig_id,
                    totality_only=totality_only,
                    recovery_attempt=recovery_attempt,
                    last_stage=heartbeat_last_stage,
                    heartbeat_timed_out=heartbeat_timed_out,
                    recovery_window_open=recovery_window_open,
                    totality_window_open=totality_window_open,
                )
            can_recover = recovery_plan is not None
            if can_recover and self.run_journal is not None and run_id is not None:
                try:
                    can_recover = self.run_journal.note_child_recovery(
                        rig_id=rig_id,
                        run_id=run_id,
                        max_recoveries=(
                            TOTALITY_CHILD_RECOVERY_MAX_ATTEMPTS
                            if recovery_plan == "totality"
                            else 1
                        ),
                    ) is not None
                except Exception as exc:
                    can_recover = False
                    self._log_rig(
                        rig_id,
                        f"Child recovery journal error: {exc}",
                        "error",
                    )

            if can_recover:
                self._clear_published_failure(rig_id)
                self.state.update_trigger_rig(
                    rig_id,
                    {
                        "running": True,
                        "phase": "recovering",
                        "mode": "totality_override" if totality_only else "real",
                        "speed": 1.0,
                    },
                )
                self.emit(
                    "trigger_phase",
                    {"rig_id": rig_id, "phase": "recovering"},
                )
                self._log_rig(
                    rig_id,
                    (
                        "Unexpected trigger child exit during totality — "
                        f"starting emergency totality recovery "
                        f"{recovery_attempt + 1}/"
                        f"{TOTALITY_CHILD_RECOVERY_MAX_ATTEMPTS}."
                        if recovery_plan == "totality"
                        else
                        "Unexpected trigger child exit at a safe boundary — "
                        "starting the single permitted recovery attempt."
                    ),
                    "warning",
                )
                try:
                    if recovery_plan == "totality" or totality_only:
                        recovered = self.start_totality_only(
                            rig_id=rig_id,
                            _recovery=True,
                            _run_id=run_id,
                            _recovery_snapshot_id=snapshot_id,
                            _recovery_input_fingerprints=input_fingerprints,
                            _child_recovery_attempt=recovery_attempt + 1,
                        )
                        recovered = recovered == "started"
                    else:
                        recovered = self.start(
                            rig_id=rig_id,
                            selected=recovery_selection,
                            _recovery=True,
                            _run_id=run_id,
                            _recovery_snapshot_id=snapshot_id,
                            _recovery_input_fingerprints=input_fingerprints,
                            _child_recovery_attempt=recovery_attempt + 1,
                        )
                except Exception as exc:
                    recovered = False
                    supervisor_error = exc
                if recovered:
                    return

            if not owns_process:
                return

            if process_still_alive:
                detail = (
                    "child process is still alive after supervision failed; "
                    "camera IPC was revoked and STOP can still force termination"
                )
                self._journal_finish(
                    rig_id, run_id, "failed",
                    failure_code="SUPERVISOR_CHILD_STILL_ALIVE",
                    detail=detail,
                    exit_code=code,
                )
                self.publish_external_failure(
                    rig_id,
                    "SUPERVISOR_CHILD_STILL_ALIVE",
                    detail,
                    exit_code=code,
                    audible=True,
                )
                # Retain process ownership so STOP remains possible.
                self.state.update_trigger_rig(rig_id, {"running": True})
                self.emit(
                    "trigger_phase",
                    {"rig_id": rig_id, "phase": "failed", "running": True},
                )
                return

            self._run_ids_by_rig[rig_id] = None
            if code == 0 and supervisor_error is None:
                self.state.update_trigger_rig(
                    rig_id,
                    {"running": False, "phase": "idle", "mode": None, "speed": None},
                )
                self.emit(
                    "trigger_phase",
                    {"rig_id": rig_id, "phase": "idle"},
                )
                self._journal_finish(rig_id, run_id, "completed", exit_code=0)
                self._log_rig(rig_id, "■ Trigger finished (code 0).", "info")
            elif manual_stop_requested:
                self.state.update_trigger_rig(
                    rig_id,
                    {"running": False, "phase": "idle", "mode": None, "speed": None},
                )
                self.emit(
                    "trigger_phase",
                    {"rig_id": rig_id, "phase": "idle"},
                )
                self._journal_finish(
                    rig_id, run_id, "stopped", exit_code=code,
                )
                self._log_rig(
                    rig_id,
                    f"■ Trigger stopped by user (code {code}).",
                    "warning",
                )
            else:
                failure_code = (
                    "HEARTBEAT_TIMEOUT"
                    if heartbeat_timed_out
                    else "SUPERVISOR_ERROR"
                    if supervisor_error is not None
                    else "CHILD_EXIT"
                )
                detail = (
                    f"scheduler heartbeat timed out; last_stage={heartbeat_last_stage or 'none'}"
                    if heartbeat_timed_out
                    else f"{type(supervisor_error).__name__}: {supervisor_error}"
                    if supervisor_error is not None
                    else f"trigger child exited with code {code}"
                )
                self._journal_finish(
                    rig_id, run_id, "failed",
                    failure_code=failure_code,
                    detail=detail,
                    exit_code=code,
                )
                self.publish_external_failure(
                    rig_id,
                    failure_code,
                    detail,
                    exit_code=code,
                    audible=True,
                )

            self._discard_run_snapshot(snapshot_id)

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

    def start_totality_only(self, rig_id=1, _recovery=False, _run_id=None,
                            _child_recovery_attempt=0,
                            _recovery_input_fingerprints=None,
                            _recovery_snapshot_id=None):
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

        if not _recovery:
            # The lifecycle lock may be held by slow validation/reconcile work.
            # Cancellation therefore uses the independent Event channel first,
            # then waits outside that lock for startup ownership to unwind.
            deadline = time.monotonic() + TOTALITY_START_PREEMPT_TIMEOUT_S
            while bool(self._starting_by_rig.get(rig_id, False)):
                self._request_start_cancel(rig_id)
                self._analysis_suppressed_by_rig[rig_id] = True
                self._manual_stop_requested_by_rig[rig_id] = True
                stopping_map = getattr(self, "_stopping_by_rig", None)
                if isinstance(stopping_map, dict):
                    stopping_map[rig_id] = True
                if time.monotonic() >= deadline:
                    raise TriggerValidationError(
                        f"Emergency Totality could not preempt RIG {rig_id} startup.",
                        "TRIGGER_START_PREEMPT_TIMEOUT",
                    )
                time.sleep(0.02)

        with self._lock:
            proc = self._procs[rig_id]
            running = proc is not None and proc.poll() is None
            stopping = bool(
                getattr(self, "_stopping_by_rig", {}).get(rig_id, False)
            )
        if running and stopping:
            raise TriggerValidationError(
                f"RIG {rig_id} startup child did not stop cleanly.",
                "TRIGGER_START_PREEMPT_TIMEOUT",
            )
        if running:
            if not self.override_totality(rig_id=rig_id):
                raise TriggerValidationError(
                    f"Could not preempt RIG {rig_id}.",
                    "TOTALITY_OVERRIDE_FAILED",
                )
            return "preempted"

        with self._lock:
            if self._starting_by_rig[rig_id]:
                raise TriggerValidationError(
                    f"Emergency Totality could not claim RIG {rig_id} startup.",
                    "TRIGGER_START_PREEMPT_TIMEOUT",
                )
            self._starting_by_rig[rig_id] = True
            self._analysis_suppressed_by_rig[rig_id] = True
            self._manual_stop_requested_by_rig[rig_id] = False
            self._clear_start_cancel_request(rig_id)

        run_id = _run_id
        snapshot_id = _recovery_snapshot_id
        if not _recovery:
            run_id = uuid.uuid4().hex
            snapshot_id = run_id

        ipc_session = None
        try:
            config = None
            if _recovery:
                if (
                    not isinstance(snapshot_id, str)
                    or not snapshot_id
                    or not isinstance(_recovery_input_fingerprints, dict)
                    or not _recovery_input_fingerprints
                ):
                    raise TriggerValidationError(
                        "Recovery snapshot metadata is missing.",
                        "RECOVERY_INPUTS_UNVERIFIED",
                    )
                _paths, config = self._restore_run_snapshot(
                    rig_id,
                    snapshot_id,
                    _recovery_input_fingerprints,
                )
            else:
                self._resolve_totality_input(rig_id)

            if self.rig_config_loader is not None:
                if config is None:
                    config = self.rig_config_loader()
                validate_execution_rig(config, rig_id)
                if not _recovery:
                    self._freeze_run_inputs(rig_id, snapshot_id, config)
                if self.camera_runtime is not None:
                    self.camera_runtime.reconcile(config)
                    ipc_session = self.camera_runtime.open_ipc_session((rig_id,))
                    promote_session = getattr(
                        self.camera_runtime,
                        "mark_ipc_session_priority",
                        None,
                    )
                    if callable(promote_session):
                        promote_session(ipc_session.session_id, True)
            elif not _recovery:
                self._freeze_run_inputs(rig_id, snapshot_id, None)

            if not _recovery:
                run_id = self._journal_begin(
                    rig_id,
                    "totality_override",
                    {},
                    totality_only=True,
                    run_id=run_id,
                    snapshot_id=snapshot_id,
                )
            if run_id is not None:
                self._run_ids_by_rig[rig_id] = run_id
            published_phase = "recovering" if _recovery else "totality_override"
            self._clear_published_failure(rig_id)
            self.state.update_trigger_rig(
                rig_id,
                {
                    "running": True,
                    "phase": published_phase,
                    "mode": "totality_override",
                    "speed": 1.0,
                },
            )
            self.emit(
                "trigger_phase",
                {"rig_id": rig_id, "phase": published_phase},
            )
            thread = threading.Thread(
                target=self._run,
                kwargs={
                    "ipc_session": ipc_session,
                    "rig_id": rig_id,
                    "totality_only": True,
                    "run_id": run_id,
                    "snapshot_id": snapshot_id,
                    "input_fingerprints": self._active_input_fingerprints(rig_id),
                    "recovery_attempt": _child_recovery_attempt,
                },
                name=f"totality-only-process-rig-{rig_id}",
                daemon=True,
            )
            with self._lock:
                self._supervisor_threads[rig_id] = thread
            thread.start()
            return "started"
        except Exception as start_exc:
            self._journal_finish(
                rig_id, locals().get("run_id"), "failed",
                failure_code="START_FAILED", detail=str(start_exc),
            )
            self._run_ids_by_rig[rig_id] = None
            with self._lock:
                self._starting_by_rig[rig_id] = False
                self._analysis_suppressed_by_rig[rig_id] = False
                self._manual_stop_requested_by_rig[rig_id] = False
                self._clear_start_cancel_request(rig_id)
                self._supervisor_threads[rig_id] = None
                self._clear_active_inputs(rig_id)
                self._discard_run_snapshot(snapshot_id)

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

    def stop(self, rig_id=1, force=False):
        """Stop one Trigger RIG.

        The first request is always graceful: SIGTERM asks eclipse_trigger.py
        to stop at the next safe boundary and this method returns immediately.
        An atomic PHOTO group which is already on the camera is allowed to
        finish, regardless of duration.  SIGKILL is reserved for a second,
        explicit ``force=True`` operator request (or runtime service shutdown).
        """
        if (
            not isinstance(rig_id, int)
            or isinstance(rig_id, bool)
            or not 1 <= rig_id <= 4
        ):
            return {
                "status": "invalid_rig",
                "rig_id": rig_id,
            }
        if not isinstance(force, bool):
            return {
                "status": "invalid_force",
                "rig_id": rig_id,
            }

        # Priority path: start() deliberately holds the lifecycle lock across
        # several preparation operations. Do not wait for that lock merely to
        # request cancellation. The Event is safe to set immediately and the
        # supervisor checks it before entering the capture runtime.
        starting_map = getattr(self, "_starting_by_rig", None)
        proc_map = getattr(self, "_procs", None)
        proc_hint = (
            proc_map.get(rig_id)
            if isinstance(proc_map, dict)
            else None
        )
        live_proc_hint = (
            proc_hint is not None
            and proc_hint.poll() is None
        )
        if (
            isinstance(starting_map, dict)
            and bool(starting_map.get(rig_id, False))
            and not live_proc_hint
        ):
            self._request_start_cancel(rig_id)
            analysis_map = getattr(self, "_analysis_suppressed_by_rig", None)
            if isinstance(analysis_map, dict):
                analysis_map[rig_id] = True
            manual_map = getattr(self, "_manual_stop_requested_by_rig", None)
            if isinstance(manual_map, dict):
                manual_map[rig_id] = True
            stopping_map = getattr(self, "_stopping_by_rig", None)
            if not isinstance(stopping_map, dict):
                stopping_map = {
                    item_rig_id: False
                    for item_rig_id in range(1, 5)
                }
                self._stopping_by_rig = stopping_map
            stopping_map[rig_id] = True
            return {
                "status": "stopping",
                "rig_id": rig_id,
                "forced": bool(force),
                "still_running": True,
            }

        proc = None
        starting = False
        supervisor = None

        with self._lock:
            stopping_map = getattr(self, "_stopping_by_rig", None)
            if not isinstance(stopping_map, dict):
                stopping_map = {
                    item_rig_id: False
                    for item_rig_id in range(1, 5)
                }
                self._stopping_by_rig = stopping_map

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
            live_proc = proc is not None and proc.poll() is None
            already_stopping = bool(stopping_map.get(rig_id, False))

            if not live_proc and not starting:
                stopping_map[rig_id] = False
                return {
                    "status": "not_running",
                    "rig_id": rig_id,
                }

            if already_stopping and not force:
                return {
                    "status": "stopping",
                    "rig_id": rig_id,
                    "forced": False,
                    "still_running": bool(live_proc or starting),
                }

            stopping_map[rig_id] = True
            self._analysis_suppressed_by_rig[rig_id] = True
            self._manual_stop_requested_by_rig[rig_id] = True

            if starting and isinstance(cancel_map, dict):
                # STOP is authoritative even in the short window before Popen
                # has been published by the supervisor.
                cancel_map[rig_id] = True

        # A cancelled startup has no atomic camera operation to preserve. Give
        # its supervisor a bounded opportunity to observe cancellation and
        # avoid launching the child at all.
        if (proc is None or proc.poll() is not None) and starting:
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
                live_proc = proc is not None and proc.poll() is None
                if not live_proc and not starting:
                    self._stopping_by_rig[rig_id] = False
                    return {
                        "status": "stopped",
                        "rig_id": rig_id,
                        "forced": bool(force),
                        "still_running": False,
                    }
            if not live_proc:
                return {
                    "status": "stopping",
                    "rig_id": rig_id,
                    "forced": bool(force),
                    "still_running": True,
                }

        if proc is None or proc.poll() is not None:
            with self._lock:
                self._stopping_by_rig[rig_id] = False
            return {
                "status": "stopped",
                "rig_id": rig_id,
                "forced": bool(force),
                "still_running": False,
            }

        # Publish stopping before signalling the child. Buffered child phase
        # messages are already suppressed above and cannot overwrite it.
        try:
            state = getattr(self, "state", None)
            if state is not None:
                state.update_trigger_rig(
                    rig_id,
                    {"running": True, "phase": "stopping"},
                )
            emit = getattr(self, "emit", None)
            if callable(emit):
                emit(
                    "trigger_phase",
                    {"rig_id": rig_id, "phase": "stopping"},
                )
        except Exception:
            # STOP remains a hardware-safety action even if UI publication
            # fails. The periodic runtime snapshot will reconcile later.
            pass

        if force:
            try:
                proc.kill()
            except Exception:
                pass
            try:
                proc.wait(timeout=2.0)
            except Exception:
                pass

            still = proc.poll() is None
            self.log(
                (
                    f"⚠️ RIG {rig_id} — FORCE STOP requested (SIGKILL); "
                    "process still active."
                    if still
                    else f"■ RIG {rig_id} — FORCE STOP requested (SIGKILL)."
                ),
                "error" if still else "warning",
                "trigger",
            )
            return {
                "status": "stopping" if still else "stopped",
                "rig_id": rig_id,
                "forced": True,
                "still_running": still,
            }

        try:
            proc.terminate()
        except Exception as exc:
            self.log(
                f"⚠️ RIG {rig_id} — Graceful STOP signal failed: {exc}",
                "error",
                "trigger",
            )

        # Crucial P0 contract: never impose a wall-clock timeout on an atomic
        # PHOTO. The supervisor owns final cleanup when the child exits. The
        # operator can explicitly press FORCE STOP if aborting the group is
        # preferable to waiting for a safe boundary.
        still = proc.poll() is None
        self.log(
            (
                f"■ RIG {rig_id} — Graceful STOP requested; waiting for the "
                "current atomic PHOTO to finish."
                if still
                else f"■ RIG {rig_id} — Graceful STOP completed."
            ),
            "warning",
            "trigger",
        )
        return {
            "status": "stopping" if still else "stopped",
            "rig_id": rig_id,
            "forced": False,
            "still_running": still,
        }
