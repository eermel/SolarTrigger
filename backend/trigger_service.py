from __future__ import annotations
from pathlib import Path
import json, os, subprocess, sys, threading, time
from datetime import datetime, timezone
from backend.timeline import parse_date_from_config, sequence_seconds

class TriggerValidationError(RuntimeError):
    def __init__(self, message, code="TRIGGER_INVALID"):
        super().__init__(message); self.code = code

def validate_eclipse(ecl):
    ts,c1,c2,c3,c4,te=sequence_seconds(ecl); errors=[]
    if c1 is None or c4 is None: errors.append("C1 ou C4 manquant")
    else:
        if ts is not None and ts >= c1: errors.append(f"TSTART ({ecl.get('TSTART')}) ≥ C1 ({ecl.get('C1')})")
        if c2 is not None and c1 >= c2: errors.append(f"C1 ({ecl.get('C1')}) ≥ C2 ({ecl.get('C2')})")
        if c2 is not None and c3 is not None and c2 > c3: errors.append(f"C2 ({ecl.get('C2')}) > C3 ({ecl.get('C3')})")
        if c3 is not None and c3 >= c4: errors.append(f"C3 ({ecl.get('C3')}) ≥ C4 ({ecl.get('C4')})")
        if te is not None and c4 >= te: errors.append(f"C4 ({ecl.get('C4')}) ≥ TEND ({ecl.get('TEND')})")
    if errors: raise TriggerValidationError("❌ JSON incohérent : " + " | ".join(errors), "JSON_INVALID")

class TriggerService:
    """Owns trigger process lifecycle; Flask is only an HTTP adapter."""
    def __init__(self, state_store, trigger_script, json_file, events_file, configs_dir,
                 log_fn, emit_fn, line_level_fn=None, line_clean_fn=None):
        self.state=state_store; self.trigger_script=trigger_script; self.json_file=json_file
        self.events_file=events_file; self.configs_dir=configs_dir; self.log=log_fn; self.emit=emit_fn
        self.project_dir=self.trigger_script.resolve().parent.parent
        self.line_level_fn=line_level_fn or (lambda _: "info")
        self.line_clean_fn=line_clean_fn or (lambda x:x); self._proc=None; self._lock=threading.RLock(); self._starting=False

    def _subprocess_env(self):
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        existing = env.get("PYTHONPATH")
        env["PYTHONPATH"] = (
            str(self.project_dir)
            if not existing
            else str(self.project_dir) + os.pathsep + existing
        )
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

    def validate_start(self, require_gps=True):
        # Une simulation ne pilote aucun matériel et utilise sa propre horloge
        # virtuelle : elle ne doit donc pas être bloquée par l'état GPS.
        if require_gps:
            gps=self.state.snapshot("gps") or {}
            if not gps.get("synced"):
                raise TriggerValidationError("⚠️ GPS non synchronisé. Synchronisez l'heure avant de démarrer.", "GPS_NOT_SYNCED")
            sync_time=gps.get("sync_time")
            if sync_time:
                try:
                    sync_dt=datetime.fromisoformat(sync_time.replace("Z", "+00:00"))
                    if sync_dt.tzinfo is None:
                        sync_dt=sync_dt.replace(tzinfo=timezone.utc)
                    age=(datetime.now(timezone.utc)-sync_dt.astimezone(timezone.utc)).total_seconds()
                    if age>7200:
                        raise TriggerValidationError(f"⚠️ Dernière synchro GPS il y a {int(age//60)} min. Resynchronisez.", "GPS_SYNC_STALE")
                except TriggerValidationError:
                    raise
                except Exception:
                    pass
        circumstances=self.state.snapshot("circumstances") or {}
        if not circumstances.get("loaded") or not self.json_file.exists():
            raise TriggerValidationError("Aucune circonstance d’éclipse sélectionnée", "CIRCUMSTANCES_NOT_LOADED")
        try:
            ecl=json.loads(self.json_file.read_text(encoding="utf-8"))
            if not isinstance(ecl, dict):
                raise ValueError("la racine JSON doit être un objet")
        except Exception:
            raise TriggerValidationError("Aucune circonstance d’éclipse sélectionnée", "CIRCUMSTANCES_NOT_LOADED")

        capture=self.state.snapshot("capture") or {}
        camera_config_file=self.state.get("camera_config_file")
        camera_config_path=self._resolve_camera_config(camera_config_file)

        if not capture.get("loaded") or camera_config_path is None:
            raise TriggerValidationError("Aucune configuration de capture sélectionnée", "CAPTURE_NOT_LOADED")
        try:
            json.loads(camera_config_path.read_text(encoding="utf-8"))
        except Exception:
            raise TriggerValidationError("Aucune configuration de capture sélectionnée", "CAPTURE_NOT_LOADED")

        validate_eclipse(ecl); return ecl

    def start(self, simulate=False, speed=60.0, dry_run=False, dry_run_delay=30.0):
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
            if self._starting or self.state.snapshot("trigger").get("running") or (self._proc and self._proc.poll() is None): return False
            self._starting=True
            try:
                ecl=self.validate_start(require_gps=not simulate)
            except Exception:
                self._starting=False
                raise
            gen=ecl.get("_generated_utc", ""); today=datetime.now(timezone.utc).strftime("%Y-%m-%d")
            if gen and today not in gen: self.log(f"⚠️ todayeclipse.json généré le {gen[:10]} — éclipse pas aujourd'hui ?", "warning", "trigger")
            try: self.events_file.write_text("", encoding="utf-8")
            except Exception: pass
            mode="simulation" if simulate else ("dryrun" if dry_run else "real")
            self.state.set("trigger", {"running":True,"phase":"starting","mode":mode,"speed":speed if simulate else 1.0})
            self.emit("trigger_phase", {"phase":"starting"})
            threading.Thread(target=self._run, args=(simulate,speed,dry_run,dry_run_delay), name="eclipse-trigger-process", daemon=True).start(); return True

    def _set_phase(self, phase):
        self.state.update_section("trigger", {"phase":phase}); self.emit("trigger_phase", {"phase":phase})

    def _run(self, simulate=False, speed=60.0, dry_run=False, dry_run_delay=30.0):
        proc=None
        try:
            cmd=[sys.executable,"-u",str(self.trigger_script),"--file",str(self.json_file)]
            if simulate:
                cmd += ["--simulate","--speed",str(speed)]
            elif dry_run:
                cmd += ["--dry-run","--dry-run-delay",str(dry_run_delay)]
            cfg=self.state.get("camera_config_file")
            camera_config_path=self._resolve_camera_config(cfg)
            if camera_config_path is not None:
                cmd += ["--camera",str(camera_config_path)]
            env=self._subprocess_env()
            proc=subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                cwd=str(self.project_dir),
                env=env,
            )
            with self._lock: self._proc=proc
            mode="simulation" if simulate else ("dryrun" if dry_run else "real")
            self.state.set("trigger", {"running":True,"phase":"waiting","mode":mode,"speed":speed if simulate else 1.0}); self.emit("trigger_phase",{"phase":"waiting"})
            label="► Trigger simulation démarré." if simulate else ("► Dry-run ×1 démarré." if dry_run else "► Trigger démarré.")
            self.log(label,"success","trigger")
            for raw in iter(proc.stdout.readline, ""):
                if not raw and proc.poll() is not None: break
                line=raw.rstrip()
                if not line: continue
                level=self.line_level_fn(line); line=self.line_clean_fn(line)
                if "PHASE 1a" in line: self._set_phase("partial")
                elif "PHASE 1b" in line or "DIAMOND RING" in line: self._set_phase("diamond_ring")
                elif "PHASE 2" in line: self._set_phase("totality")
                elif "PHASE 3a" in line or "PHASE 3b" in line: self._set_phase("partial_end")
                self.log(line,level,"trigger")
            proc.wait()
        except Exception as exc:
            self.log(f"ERREUR thread trigger : {exc}","error","trigger")
        finally:
            with self._lock:
                owns_process = self._proc is proc
                if owns_process:
                    self._proc = None
                    self._starting = False

            if owns_process:
                self.state.set(
                    "trigger",
                    {"running":False,"phase":"idle","mode":None,"speed":None},
                )
                self.emit("trigger_phase", {"phase":"idle"})

            code = proc.returncode if proc else "?"
            self.log(
                f"■ Trigger terminé (code {code}).",
                "info",
                "trigger",
            )

    def start_totality_only(self, script_path):
        """Préempte le trigger courant puis démarre le mode secours totalité."""

        with self._lock:
            old_proc = self._proc

        if old_proc is not None and old_proc.poll() is None:
            self.log(
                "🌑 Totalité secours demandée — arrêt du trigger en cours.",
                "warning",
                "trigger",
            )

            try:
                old_proc.terminate()
            except Exception:
                pass

            try:
                old_proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.log(
                    "⚠️ Trigger précédent non arrêté après 3 s — SIGKILL.",
                    "warning",
                    "trigger",
                )
                try:
                    old_proc.kill()
                    old_proc.wait(timeout=2)
                except Exception:
                    pass

            if old_proc.poll() is None:
                self.log(
                    "❌ Impossible d'arrêter le trigger courant — totalité non lancée.",
                    "error",
                    "trigger",
                )
                return False

        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            with self._lock:
                if self._proc is None:
                    break
            time.sleep(0.02)

        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                return False

            self._proc = None
            self._starting = True

            threading.Thread(
                target=self._run_totality,
                args=(script_path,),
                name="totality-only-process",
                daemon=True,
            ).start()

        return True

    def _run_totality(self, script_path):
        proc=None
        try:
            cmd=[sys.executable,"-u",str(script_path)]
            cfg=self.state.get("camera_config_file")
            camera_config_path=self._resolve_camera_config(cfg)
            if camera_config_path is not None:
                cmd += ["--camera",str(camera_config_path)]
            env=self._subprocess_env()
            proc=subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                cwd=str(self.project_dir),
                env=env,
            )
            with self._lock: self._proc=proc
            self.state.set("trigger", {"running":True,"phase":"totality"}); self.emit("trigger_phase",{"phase":"totality"})
            self.log("🌑 Totalité uniquement — démarrage","info","trigger")
            for raw in iter(proc.stdout.readline, ""):
                line=raw.rstrip()
                if line: self.log(self.line_clean_fn(line),self.line_level_fn(line),"trigger")
            proc.wait()
        except Exception as exc:
            self.log(f"Erreur totality_only : {exc}","error","trigger")
        finally:
            with self._lock:
                owns_process = self._proc is proc
                if owns_process:
                    self._proc = None
                    self._starting = False

            if owns_process:
                self.state.set(
                    "trigger",
                    {"running":False,"phase":"idle","mode":None,"speed":None},
                )
                self.emit("trigger_phase", {"phase":"idle"})

            self.log(
                "■ Totalité uniquement terminée.",
                "info",
                "trigger",
            )

    def stop(self):
        with self._lock: proc=self._proc
        if not proc or proc.poll() is not None: return {"status":"not_running"}
        try: proc.terminate()
        except Exception: pass
        forced=False
        try: proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            forced=True
            try: proc.kill(); proc.wait(timeout=2)
            except Exception: pass
            self.log("■ Trigger tué (SIGKILL) après timeout.","warning","trigger")
        still=proc.poll() is None
        self.state.set("trigger", {"running":False,"phase":"idle","mode":None,"speed":None}); self.emit("trigger_phase",{"phase":"idle"})
        self.log("⚠️ Trigger : processus toujours actif après SIGKILL." if still else "■ Trigger arrêté manuellement.", "error" if still else "warning", "trigger")
        return {"status":"stopped","forced":forced,"still_running":still}
