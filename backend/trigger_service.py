from __future__ import annotations
from pathlib import Path
import json, os, signal, subprocess, sys, threading
from datetime import datetime, timezone
from backend.timeline import build_timeline, sequence_seconds
from backend.phase_trigger import build_phase_schedule

class TriggerValidationError(RuntimeError):
    def __init__(self, message, code="TRIGGER_INVALID"):
        super().__init__(message); self.code = code

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
                        fallback_date=datetime.now().date(),
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
                        fallback_date=datetime.now().date(),
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

    def _clear_active_inputs(self, rig_id):
        self._active_circumstances_paths.pop(rig_id, None)
        self._active_photo_paths.pop(rig_id, None)
        self._active_exposure_opt_paths.pop(rig_id, None)

    def validate_start(self, rig_id=1, require_gps=True, selected=None):
        if require_gps:
            gps = self.state.snapshot("gps") or {}
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
            if not any(
                isinstance(item, dict) and item.get("rig_id") == rig_id
                for item in exposure_opt.get("rigs", ())
            ):
                raise ValueError(f"Exposure Optimization has no RIG {rig_id}")
            timeline = build_timeline(
                ecl,
                fallback_date=datetime.now(timezone.utc).date(),
            )
            build_phase_schedule(timeline, photo)
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

            try:
                ecl = self.validate_start(
                    rig_id=rig_id,
                    require_gps=not simulate,
                    selected=selected,
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

                    if self.camera_runtime is not None:
                        self.camera_runtime.reconcile(config)
                        ipc_session = self.camera_runtime.open_ipc_session(
                            (rig_id,)
                        )
                except Exception:
                    self._starting_by_rig[rig_id] = False
                    self._clear_active_inputs(rig_id)
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
                thread.start()
            except Exception:
                self._starting_by_rig[rig_id] = False
                self._clear_active_inputs(rig_id)
                if ipc_session is not None:
                    try:
                        self.camera_runtime.close_ipc_session(ipc_session.session_id)
                    except Exception as exc:
                        self.log(f"Camera IPC session close error: {exc}","error","trigger")
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

    def _run(
        self,
        simulate=False,
        speed=60.0,
        dry_run=False,
        ipc_session=None,
        rig_id=1,
    ):
        proc=None
        try:
            circumstances_path = self._active_circumstances_paths.get(rig_id)
            if circumstances_path is None:
                raise TriggerValidationError(
                    "Trigger circumstances were not resolved.",
                    "TRIGGER_INPUTS_NOT_LOADED",
                )

            cmd = [
                sys.executable,
                "-u",
                str(self.trigger_script),
                "--file",
                str(circumstances_path),
            ]

            photo_path = self._active_photo_paths.get(rig_id)
            exposure_opt_path = self._active_exposure_opt_paths.get(rig_id)
            if photo_path is None or exposure_opt_path is None:
                raise TriggerValidationError(
                    "Trigger input files were not resolved.",
                    "TRIGGER_INPUTS_NOT_LOADED",
                )
            cmd += [
                "--camera", str(photo_path),
                "--exposure-opt", str(exposure_opt_path),
            ]

            if simulate:
                cmd += ["--simulate", "--speed", str(speed)]
            elif dry_run:
                cmd.append("--dry-run")

            env=self._subprocess_env(ipc_session)
            env["SET_TRIGGER_RIG_ID"] = str(rig_id)
            proc=subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                cwd=str(self.project_dir),
                env=env,
            )
            with self._lock:
                self._procs[rig_id] = proc
            mode = (
                "simulation"
                if simulate
                else "dryrun"
                if dry_run
                else "real"
            )
            self.state.update_trigger_rig(
                rig_id,
                {
                    "running": True,
                    "phase": "waiting",
                    "mode": mode,
                    "speed": speed if simulate else 1.0,
                },
            )
            self.emit(
                "trigger_phase",
                {"rig_id": rig_id, "phase": "waiting"},
            )
            label = (
                "► Trigger simulation started."
                if simulate
                else "► Dry-run ×1 started."
                if dry_run
                else "► Trigger started."
            )
            self.log(label,"success","trigger")
            for raw in iter(proc.stdout.readline, ""):
                if not raw and proc.poll() is not None: break
                line=raw.rstrip()
                if not line: continue
                level=self.line_level_fn(line); line=self.line_clean_fn(line)
                if line.startswith("TRIGGER_RUN_ANALYSIS "):
                    with self._lock:
                        suppress_analysis = self._analysis_suppressed_by_rig[rig_id]
                    if suppress_analysis:
                        continue
                if line.startswith("TRIGGER_PHASE "):
                    self._set_phase(rig_id, line.split(None, 1)[1])
                elif "PHASE 1a" in line: self._set_phase(rig_id, "partial")
                elif "PHASE 1b" in line or "DIAMOND RING" in line: self._set_phase(rig_id, "diamond_ring")
                elif "PHASE 2" in line: self._set_phase(rig_id, "totality")
                elif "PHASE 3a" in line or "PHASE 3b" in line: self._set_phase(rig_id, "partial_end")
                self.log(line,level,"trigger")
            proc.wait()
        except Exception as exc:
            self.log(f"Trigger thread ERROR: {exc}","error","trigger")
        finally:
            if ipc_session is not None:
                try:
                    self.camera_runtime.close_ipc_session(ipc_session.session_id)
                except Exception as exc:
                    self.log(f"Camera IPC session close error: {exc}","error","trigger")

            with self._lock:
                owns_process = self._procs[rig_id] is proc
                if owns_process:
                    self._procs[rig_id] = None
                    self._starting_by_rig[rig_id] = False
                    self._clear_active_inputs(rig_id)
                    self._analysis_suppressed_by_rig[rig_id] = False

            if owns_process:
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

            code = proc.returncode if proc else "?"
            self.log(
                f"■ Trigger finished (code {code}).",
                "info",
                "trigger",
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

        with self._lock:
            proc = self._procs[rig_id]

        if proc is None or proc.poll() is not None:
            return False

        with self._lock:
            self._analysis_suppressed_by_rig[rig_id] = True

        try:
            proc.send_signal(signal.SIGUSR1)
        except Exception as exc:
            self.log(
                f"Totality override error: {exc}",
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
            proc = self._procs[rig_id]

        if not proc or proc.poll() is not None:
            return {
                "status": "not_running",
                "rig_id": rig_id,
            }

        with self._lock:
            self._analysis_suppressed_by_rig[rig_id] = True

        try:
            proc.terminate()
        except Exception:
            pass

        forced = False

        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            forced = True
            try:
                proc.kill()
                proc.wait(timeout=2)
            except Exception:
                pass

            self.log(
                f"■ RIG {rig_id} — Trigger killed (SIGKILL) after timeout.",
                "warning",
                "trigger",
            )

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
