from __future__ import annotations
from pathlib import Path
import json, os, signal, subprocess, sys, threading, time
from datetime import datetime, timezone
from backend.timeline import build_timeline, parse_date_from_config, sequence_seconds
from backend.execution_plan_runtime import load_execution_plan

class TriggerValidationError(RuntimeError):
    def __init__(self, message, code="TRIGGER_INVALID"):
        super().__init__(message); self.code = code

def validate_eclipse(ecl):
    ts, c1, c2, c3, c4, te = sequence_seconds(ecl)
    errors = []

    if c1 is None or c4 is None:
        errors.append("C1 ou C4 manquant")
    else:
        if ts is not None and ts >= c1:
            errors.append(
                f"TSTART ({ecl.get('TSTART')}) ≥ C1 ({ecl.get('C1')})"
            )

        if (c2 is None) != (c3 is None):
            errors.append(
                "C2 et C3 doivent être tous deux présents ou absents"
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
                            "C2 < TMAX < C3 non respecté"
                        )
                except Exception as exc:
                    errors.append(f"TMAX invalide: {exc}")

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
                            "C1 < TMAX < C4 non respecté"
                        )
                except Exception as exc:
                    errors.append(f"TMAX invalide: {exc}")

        if te is not None and c4 >= te:
            errors.append(
                f"C4 ({ecl.get('C4')}) ≥ TEND ({ecl.get('TEND')})"
            )

    if errors:
        raise TriggerValidationError(
            "❌ JSON incohérent : " + " | ".join(errors),
            "JSON_INVALID",
        )

def validate_execution_rigs(config):
    """Validate rig requirements only when real hardware execution starts."""
    if not isinstance(config, dict):
        raise TriggerValidationError(
            "Configuration RIG invalide.",
            "RIG_CONFIG_INVALID",
        )

    rigs = config.get("rigs")
    if not isinstance(rigs, list):
        raise TriggerValidationError(
            "Configuration RIG invalide.",
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
            "RIG 1 est obligatoire pour exécuter le trigger.",
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
                f"RIG {rig_id} nécessite une caméra configurée "
                "pour exécuter le trigger.",
                "RIG_CAMERA_REQUIRED",
            )

    return tuple(participating_ids)


def validate_execution_rig(config, rig_id):
    """Validate one RIG for an independent trigger execution."""
    if (
        not isinstance(rig_id, int)
        or isinstance(rig_id, bool)
        or not 1 <= rig_id <= 4
    ):
        raise TriggerValidationError(
            f"RIG invalide : {rig_id}",
            "RIG_ID_INVALID",
        )

    if not isinstance(config, dict):
        raise TriggerValidationError(
            "Configuration RIG invalide.",
            "RIG_CONFIG_INVALID",
        )

    rigs = config.get("rigs")
    if not isinstance(rigs, list):
        raise TriggerValidationError(
            "Configuration RIG invalide.",
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
            f"RIG {rig_id} introuvable.",
            "RIG_NOT_FOUND",
        )

    if rig.get("enabled") is not True:
        raise TriggerValidationError(
            f"RIG {rig_id} n'est pas actif.",
            "RIG_DISABLED",
        )

    devices = rig.get("devices")
    camera = devices.get("camera") if isinstance(devices, dict) else None
    backend = camera.get("backend") if isinstance(camera, dict) else None
    backend = backend.strip().lower() if isinstance(backend, str) else ""

    if not backend or backend in {"none", "external"}:
        raise TriggerValidationError(
            f"RIG {rig_id} nécessite une caméra configurée "
            "pour exécuter le trigger.",
            "RIG_CAMERA_REQUIRED",
        )

    return rig_id


class TriggerService:
    """Owns trigger process lifecycle; Flask is only an HTTP adapter."""
    def __init__(self, state_store, trigger_script, json_file, events_file, configs_dir,
                 log_fn, emit_fn, line_level_fn=None, line_clean_fn=None,
                 camera_runtime=None, rig_config_loader=None):
        self.state=state_store; self.trigger_script=trigger_script; self.json_file=json_file
        self.events_file=events_file; self.configs_dir=configs_dir; self.log=log_fn; self.emit=emit_fn
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
        for subdir in ("camera_cfg", "capture"):
            candidate = self.configs_dir / subdir / filename
            if candidate.exists():
                return candidate
        return None

    def _resolve_execution_plan(self, rig_id=1):
        if (
            not isinstance(rig_id, int)
            or isinstance(rig_id, bool)
            or not 1 <= rig_id <= 4
        ):
            raise TriggerValidationError(
                f"RIG invalide : {rig_id}",
                "RIG_ID_INVALID",
            )

        state_key = f"execution_plan_file_rig_{rig_id}"
        filename = self.state.get(state_key)

        if not isinstance(filename, str) or not filename.strip():
            raise TriggerValidationError(
                f"Aucun plan d'exécution sélectionné pour RIG {rig_id}.",
                "EXECUTION_PLAN_NOT_LOADED",
            )

        filename = filename.strip()

        if Path(filename).name != filename:
            raise TriggerValidationError(
                f"Nom de plan d'exécution invalide pour RIG {rig_id}.",
                "EXECUTION_PLAN_INVALID",
            )

        path = self.configs_dir / "execution_plan" / filename

        if not path.is_file():
            raise TriggerValidationError(
                f"Plan d'exécution introuvable pour RIG {rig_id} : {filename}",
                "EXECUTION_PLAN_NOT_FOUND",
            )

        try:
            plan = load_execution_plan(path)
        except Exception as exc:
            raise TriggerValidationError(
                f"Plan d'exécution illisible "
                f"pour RIG {rig_id} : {filename}",
                "EXECUTION_PLAN_INVALID",
            ) from exc

        plan_rig_ids = {
            command.get("rig_id")
            for command in plan.get(
                "commands",
                [],
            )
            if isinstance(command, dict)
        }

        plan_rig_ids.discard(None)

        if path.suffix.lower() == ".plan":
            incompatible_rig = (
                plan_rig_ids != {rig_id}
            )
        else:
            # Legacy schema-v2 JSON plans historically allowed an empty
            # command list. Preserve that behaviour during migration.
            #
            # If a legacy JSON plan does contain explicit RIG ids, they must
            # still all match the selected RIG.
            incompatible_rig = (
                bool(plan_rig_ids)
                and plan_rig_ids != {rig_id}
            )

        if incompatible_rig:
            raise TriggerValidationError(
                f"Plan d'exécution incompatible "
                f"pour RIG {rig_id} : {filename}",
                "EXECUTION_PLAN_INVALID",
            )

        return path

    def validate_start(self, rig_id=1, require_gps=True):
        if require_gps:
            gps = self.state.snapshot("gps") or {}
            if not gps.get("synced"):
                raise TriggerValidationError(
                    "⚠️ GPS non synchronisé. Synchronisez l'heure avant de démarrer.",
                    "GPS_NOT_SYNCED",
                )

            sync_time = gps.get("sync_time")
            if sync_time:
                try:
                    sync_dt = datetime.fromisoformat(
                        sync_time.replace("Z", "+00:00")
                    )
                    if sync_dt.tzinfo is None:
                        sync_dt = sync_dt.replace(tzinfo=timezone.utc)

                    age = (
                        datetime.now(timezone.utc)
                        - sync_dt.astimezone(timezone.utc)
                    ).total_seconds()

                    if age > 7200:
                        raise TriggerValidationError(
                            f"⚠️ Dernière synchro GPS il y a "
                            f"{int(age // 60)} min. Resynchronisez.",
                            "GPS_SYNC_STALE",
                        )
                except TriggerValidationError:
                    raise
                except Exception:
                    pass

        plan_path = self._resolve_execution_plan(rig_id)

        try:
            plan = load_execution_plan(
                plan_path
            )
        except Exception as exc:
            raise TriggerValidationError(
                "Plan d'exécution illisible.",
                "EXECUTION_PLAN_INVALID",
            ) from exc

        sources = plan.get("sources") or {}
        filename = sources.get("circumstances_file")

        if (
            not isinstance(filename, str)
            or not filename.strip()
            or Path(filename).name != filename.strip()
        ):
            raise TriggerValidationError(
                "Circumstances absentes du plan d'exécution.",
                "EXECUTION_PLAN_CIRCUMSTANCES_INVALID",
            )

        filename = filename.strip()

        circumstances_path = (
            self.configs_dir / "circumstances" / filename
        )

        if not circumstances_path.is_file():
            circumstances_path = None

        if circumstances_path is None:
            raise TriggerValidationError(
                f"Circumstances du plan introuvables : {filename}",
                "EXECUTION_PLAN_CIRCUMSTANCES_NOT_FOUND",
            )

        try:
            ecl = json.loads(
                circumstances_path.read_text(encoding="utf-8")
            )
            if not isinstance(ecl, dict):
                raise ValueError("invalid JSON root")
            validate_eclipse(ecl)
        except Exception as exc:
            raise TriggerValidationError(
                f"Circumstances du plan invalides : {filename}",
                "EXECUTION_PLAN_CIRCUMSTANCES_INVALID",
            ) from exc

        self._active_circumstances_paths[rig_id] = circumstances_path
        return ecl

    def start(self, rig_id=1, simulate=False, speed=60.0, dry_run=False, dry_run_delay=30.0):
        if (
            not isinstance(rig_id, int)
            or isinstance(rig_id, bool)
            or not 1 <= rig_id <= 4
        ):
            raise TriggerValidationError(
                f"RIG invalide : {rig_id}",
                "RIG_ID_INVALID",
            )

        try:
            speed=float(speed)
        except (TypeError, ValueError):
            raise TriggerValidationError("Facteur de simulation invalide.", "SIM_SPEED_INVALID")
        if simulate and dry_run:
            raise TriggerValidationError("Simulation et dry-run sont mutuellement exclusifs.", "TRIGGER_MODE_INVALID")
        if simulate and not (1.0 <= speed <= 1000.0):
            raise TriggerValidationError("Facteur de simulation hors limites (1 à 1000).", "SIM_SPEED_INVALID")
        try:
            dry_run_delay=float(dry_run_delay)
        except (TypeError, ValueError):
            raise TriggerValidationError("Délai dry-run invalide.", "DRYRUN_DELAY_INVALID")
        if dry_run and not (0.0 <= dry_run_delay <= 3600.0):
            raise TriggerValidationError("Délai dry-run hors limites (0 à 3600 s).", "DRYRUN_DELAY_INVALID")
        with self._lock:
            proc = self._procs[rig_id]
            if (
                self._starting_by_rig[rig_id]
                or (proc is not None and proc.poll() is None)
            ):
                return False

            self._starting_by_rig[rig_id] = True

            try:
                ecl = self.validate_start(
                    rig_id=rig_id,
                    require_gps=not simulate,
                )
            except Exception:
                self._starting_by_rig[rig_id] = False
                raise

            execution_plan_path = self._resolve_execution_plan(rig_id)

            ipc_session = None
            if not simulate and self.rig_config_loader is not None:
                try:
                    config = self.rig_config_loader()
                    validate_execution_rig(config, rig_id)

                    if self.camera_runtime is not None:
                        self.camera_runtime.reconcile(config)
                        ipc_session = self.camera_runtime.open_ipc_session(
                            (rig_id,)
                        )
                except Exception:
                    self._starting_by_rig[rig_id] = False
                    self._active_circumstances_paths.pop(rig_id, None)
                    raise
            gen=ecl.get("_generated_utc", ""); today=datetime.now(timezone.utc).strftime("%Y-%m-%d")
            if gen and today not in gen: self.log(f"⚠️ todayeclipse.json généré le {gen[:10]} — éclipse pas aujourd'hui ?", "warning", "trigger")
            try: self.events_file.write_text("", encoding="utf-8")
            except Exception: pass
            mode="simulation" if simulate else ("dryrun" if dry_run else "real")
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
                        dry_run_delay,
                        ipc_session,
                        execution_plan_path,
                        rig_id,
                    ),
                    name=f"eclipse-trigger-process-rig-{rig_id}",
                    daemon=True,
                )
                thread.start()
            except Exception:
                self._starting_by_rig[rig_id] = False
                self._active_circumstances_paths.pop(rig_id, None)
                if ipc_session is not None:
                    try:
                        self.camera_runtime.close_ipc_session(ipc_session.session_id)
                    except Exception as exc:
                        self.log(f"Erreur fermeture session IPC caméra : {exc}","error","trigger")
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

    def _run(self, simulate=False, speed=60.0, dry_run=False, dry_run_delay=30.0,
             ipc_session=None, execution_plan_path=None, rig_id=1):
        proc=None
        try:
            circumstances_path = self._active_circumstances_paths.get(rig_id)
            if circumstances_path is None:
                raise TriggerValidationError(
                    "Circumstances du plan non résolues.",
                    "EXECUTION_PLAN_CIRCUMSTANCES_INVALID",
                )

            cmd = [
                sys.executable,
                "-u",
                str(self.trigger_script),
                "--file",
                str(circumstances_path),
            ]

            if execution_plan_path is None:
                execution_plan_path = self._resolve_execution_plan(rig_id)

            cmd += ["--execution-plan", str(execution_plan_path)]

            if simulate:
                cmd += ["--simulate", "--speed", str(speed)]
            elif dry_run:
                cmd += [
                    "--dry-run",
                    "--dry-run-delay",
                    str(dry_run_delay),
                ]

            env=self._subprocess_env(ipc_session)
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
            mode="simulation" if simulate else ("dryrun" if dry_run else "real")
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
            label="► Trigger simulation démarré." if simulate else ("► Dry-run ×1 démarré." if dry_run else "► Trigger démarré.")
            self.log(label,"success","trigger")
            for raw in iter(proc.stdout.readline, ""):
                if not raw and proc.poll() is not None: break
                line=raw.rstrip()
                if not line: continue
                level=self.line_level_fn(line); line=self.line_clean_fn(line)
                if "PHASE 1a" in line: self._set_phase(rig_id, "partial")
                elif "PHASE 1b" in line or "DIAMOND RING" in line: self._set_phase(rig_id, "diamond_ring")
                elif "PHASE 2" in line: self._set_phase(rig_id, "totality")
                elif "PHASE 3a" in line or "PHASE 3b" in line: self._set_phase(rig_id, "partial_end")
                self.log(line,level,"trigger")
            proc.wait()
        except Exception as exc:
            self.log(f"ERREUR thread trigger : {exc}","error","trigger")
        finally:
            if ipc_session is not None:
                try:
                    self.camera_runtime.close_ipc_session(ipc_session.session_id)
                except Exception as exc:
                    self.log(f"Erreur fermeture session IPC caméra : {exc}","error","trigger")

            with self._lock:
                owns_process = self._procs[rig_id] is proc
                if owns_process:
                    self._procs[rig_id] = None
                    self._starting_by_rig[rig_id] = False
                    self._active_circumstances_paths.pop(rig_id, None)

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
                f"■ Trigger terminé (code {code}).",
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

        try:
            proc.send_signal(signal.SIGUSR1)
        except Exception as exc:
            self.log(
                f"Erreur override totalité : {exc}",
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
            f"🌑 RIG {rig_id} — Override totalité envoyé au scheduler photo — audio conservé.",
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
                f"■ RIG {rig_id} — Trigger tué (SIGKILL) après timeout.",
                "warning",
                "trigger",
            )

        still = proc.poll() is None

        self.log(
            (
                f"⚠️ RIG {rig_id} — processus toujours actif après SIGKILL."
                if still
                else f"■ RIG {rig_id} — Trigger arrêté manuellement."
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
