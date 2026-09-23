#!/usr/bin/env python3
"""Apply the remaining Trigger hardening to the current branch.

This is a temporary deterministic transformation used by CI. It is deleted
once the generated product changes have passed the full test suite.
"""

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def write(path, text):
    (ROOT / path).write_text(text, encoding="utf-8")


def replace_once(path, old, new, label):
    text = read(path)
    if old not in text:
        if new in text:
            return
        raise RuntimeError(f"{label}: source fragment not found in {path}")
    write(path, text.replace(old, new, 1))


def patch_trigger_service():
    path = "backend/trigger_service.py"
    text = read(path)
    text = text.replace(
        "import json, os, signal, subprocess, sys, threading\n",
        "import json, os, signal, subprocess, sys, threading\nfrom threading import Thread as _HeartbeatThread\n",
        1,
    )
    text = text.replace(
        "from backend.phase_trigger import build_phase_schedule\n",
        "from backend.phase_trigger import build_phase_schedule\n"
        "from backend.trigger_heartbeat import (\n"
        "    DEFAULT_HEARTBEAT_TIMEOUT_S,\n"
        "    HEARTBEAT_ENV,\n"
        "    HeartbeatSupervisor,\n"
        ")\n",
        1,
    )
    text = text.replace(
        "                 camera_runtime=None, rig_config_loader=None,\n"
        "                 product_configs_dir=None):",
        "                 camera_runtime=None, rig_config_loader=None,\n"
        "                 product_configs_dir=None, run_journal=None,\n"
        "                 heartbeat_timeout_s=DEFAULT_HEARTBEAT_TIMEOUT_S):",
        1,
    )
    marker = """        self._supervisor_threads = {
            rig_id: None
            for rig_id in range(1, 5)
        }

    def _log_rig"""
    replacement = """        self._supervisor_threads = {
            rig_id: None
            for rig_id in range(1, 5)
        }
        self.run_journal = run_journal
        self.heartbeat_timeout_s = max(0.05, float(heartbeat_timeout_s))
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

    def _journal_begin(self, rig_id, mode, selected, *, totality_only=False):
        if self.run_journal is None:
            return None
        try:
            entry = self.run_journal.begin_run(
                rig_id=rig_id,
                mode=mode,
                selected=selected,
                speed=1.0,
                totality_only=totality_only,
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

    def publish_external_failure(self, rig_id, code, detail, *, exit_code=None):
        self.state.update_trigger_rig(
            rig_id,
            {"running": False, "phase": "failed", "mode": None, "speed": None},
        )
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
        return bool(totality_only or self._recovery_window_open(rig_id))

    def recover_persisted_run(self, entry):
        """Resume one same-boot persisted run without replaying past phases."""
        if not isinstance(entry, dict):
            raise TriggerValidationError("Invalid recovery journal entry.", "RECOVERY_INVALID")
        rig_id = int(entry.get("rig_id"))
        run_id = entry.get("run_id")
        if not isinstance(run_id, str) or not run_id:
            raise TriggerValidationError("Recovery run_id is missing.", "RECOVERY_INVALID")
        if entry.get("totality_only") is True or entry.get("mode") == "totality_override":
            return self.start_totality_only(
                rig_id=rig_id,
                _recovery=True,
                _run_id=run_id,
            )
        if entry.get("mode") != "real":
            raise TriggerValidationError(
                "Only real eclipse runs can be recovered automatically.",
                "RECOVERY_MODE_UNSAFE",
            )
        selected = entry.get("selected")
        if not isinstance(selected, dict):
            raise TriggerValidationError("Recovery inputs are missing.", "RECOVERY_INVALID")
        # Resolve once before touching hardware, so an already-ended timeline
        # fails closed instead of briefly starting a camera session.
        paths = self._resolve_trigger_inputs(rig_id, selected)
        self._active_circumstances_paths[rig_id] = paths["circumstances"]
        self._active_photo_paths[rig_id] = paths["photo"]
        self._active_exposure_opt_paths[rig_id] = paths["exposure_opt"]
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
        )

    def _log_rig"""
    if marker not in text:
        raise RuntimeError("TriggerService constructor marker not found")
    text = text.replace(marker, replacement, 1)

    text = text.replace(
        "    def start(self, rig_id=1, simulate=False, speed=60.0, dry_run=False,\n"
        "              selected=None):",
        "    def start(self, rig_id=1, simulate=False, speed=60.0, dry_run=False,\n"
        "              selected=None, _recovery=False, _run_id=None,\n"
        "              _child_recovery_attempt=0):",
        1,
    )
    text = text.replace(
        "                    require_gps=not simulate,\n"
        "                    selected=selected,\n"
        "                    strict_circumstances_date=not (simulate or dry_run),",
        "                    require_gps=not simulate and not _recovery,\n"
        "                    selected=selected,\n"
        "                    strict_circumstances_date=not (simulate or dry_run or _recovery),",
        1,
    )
    mode_block = """            mode = (
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
                )"""
    mode_new = """            mode = (
                "simulation"
                if simulate
                else "dryrun"
                if dry_run
                else "real"
            )
            run_id = _run_id
            if mode == "real" and not _recovery:
                run_id = self._journal_begin(rig_id, mode, selected)
            if run_id is not None:
                self._run_ids_by_rig[rig_id] = run_id
            published_phase = "recovering" if _recovery else "starting"
            try:
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
                    {"rig_id": rig_id, "phase": published_phase, "running": True},
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
                        "recovery_attempt": _child_recovery_attempt,
                    },
                    name=f"eclipse-trigger-process-rig-{rig_id}",
                    daemon=True,
                )"""
    if mode_block not in text:
        raise RuntimeError("normal start mode/thread block not found")
    text = text.replace(mode_block, mode_new, 1)
    # If supervisor thread creation itself fails after a journal entry was
    # created, close the persisted active run rather than leaving a false
    # recovery candidate.
    text = text.replace(
        "            except Exception:\n                self._starting_by_rig[rig_id] = False\n                self._supervisor_threads[rig_id] = None\n",
        "            except Exception as start_exc:\n                self._starting_by_rig[rig_id] = False\n                self._supervisor_threads[rig_id] = None\n"
        "                self._journal_finish(\n"
        "                    rig_id, run_id, \"failed\",\n"
        "                    failure_code=\"START_FAILED\", detail=str(start_exc),\n"
        "                )\n"
        "                self._run_ids_by_rig[rig_id] = None\n",
        1,
    )

    # Extend _run signature/state.
    text = text.replace(
        "        totality_only=False,\n    ):\n        proc=None\n        stdout_stream = None\n        stdout_read_fd = None\n        stdout_write_fd = None\n",
        "        totality_only=False,\n        run_id=None,\n        recovery_attempt=0,\n    ):\n        proc=None\n        stdout_stream = None\n        stdout_read_fd = None\n        stdout_write_fd = None\n        heartbeat_read_fd = None\n        heartbeat_write_fd = None\n        heartbeat = None\n        heartbeat_last_stage = None\n        heartbeat_timed_out = False\n        supervisor_error = None\n        recovery_selection = None\n",
        1,
    )
    text = text.replace(
        "            env=self._subprocess_env(ipc_session)\n            env[\"SET_TRIGGER_RIG_ID\"] = str(rig_id)\n\n            # The real-time scheduler must never wait for the web portal to\n",
        "            env=self._subprocess_env(ipc_session)\n            env[\"SET_TRIGGER_RIG_ID\"] = str(rig_id)\n            recovery_selection = self._active_selection(rig_id)\n            heartbeat_read_fd, heartbeat_write_fd = os.pipe()\n            env[HEARTBEAT_ENV] = str(heartbeat_write_fd)\n\n            # The real-time scheduler must never wait for the web portal to\n",
        1,
    )
    text = text.replace(
        "                    cwd=str(self.project_dir),\n                    env=env,\n                )\n            finally:\n                if stdout_write_fd is not None:\n                    os.close(stdout_write_fd)\n                    stdout_write_fd = None\n\n            # Test doubles historically expose their own .stdout",
        "                    cwd=str(self.project_dir),\n                    env=env,\n                    pass_fds=(heartbeat_write_fd,),\n                )\n            finally:\n                if stdout_write_fd is not None:\n                    os.close(stdout_write_fd)\n                    stdout_write_fd = None\n                if heartbeat_write_fd is not None:\n                    os.close(heartbeat_write_fd)\n                    heartbeat_write_fd = None\n\n            heartbeat = HeartbeatSupervisor(\n                read_fd=heartbeat_read_fd,\n                proc=proc,\n                timeout_s=self.heartbeat_timeout_s,\n                manual_stop_fn=lambda: self._manual_stop_requested_by_rig[rig_id],\n                log_fn=lambda message: self._log_rig(rig_id, message, \"critical\"),\n                thread_factory=_HeartbeatThread,\n            ).start()\n            heartbeat_read_fd = None\n\n            # Test doubles historically expose their own .stdout",
        1,
    )
    text = text.replace(
        "        except Exception as exc:\n            self._log_rig(\n                rig_id,\n                f\"Trigger thread ERROR: {exc}\",\n",
        "        except Exception as exc:\n            supervisor_error = exc\n            self._log_rig(\n                rig_id,\n                f\"Trigger thread ERROR: {exc}\",\n",
        1,
    )

    # Replace only the _run finally body, keeping override_totality and all
    # subsequent lifecycle methods intact.
    pattern = re.compile(
        r"        finally:\n            if stdout_stream is not None:.*?\n    def override_totality\(",
        re.S,
    )
    hardened_finally = '''        finally:
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
            with self._lock:
                manual_stop_requested = self._manual_stop_requested_by_rig[rig_id]
                owns_process = (
                    self._procs[rig_id] is proc
                    if proc is not None
                    else self._starting_by_rig[rig_id]
                )
                if owns_process and not process_still_alive:
                    if proc is not None and self._procs[rig_id] is proc:
                        self._procs[rig_id] = None
                    self._starting_by_rig[rig_id] = False
                    self._clear_active_inputs(rig_id)
                    self._analysis_suppressed_by_rig[rig_id] = False
                    self._manual_stop_requested_by_rig[rig_id] = False
                    self._cancel_start_requested_by_rig[rig_id] = False
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
            can_recover = bool(
                owns_process
                and not process_still_alive
                and failed
                and not manual_stop_requested
                and not simulate
                and not dry_run
                and self._child_recovery_safe(
                    rig_id=rig_id,
                    totality_only=totality_only,
                    recovery_attempt=recovery_attempt,
                    last_stage=heartbeat_last_stage,
                    heartbeat_timed_out=heartbeat_timed_out,
                )
            )
            if can_recover and self.run_journal is not None and run_id is not None:
                try:
                    can_recover = self.run_journal.note_child_recovery(
                        rig_id=rig_id,
                        run_id=run_id,
                    ) is not None
                except Exception as exc:
                    can_recover = False
                    self._log_rig(
                        rig_id,
                        f"Child recovery journal error: {exc}",
                        "error",
                    )

            if can_recover:
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
                    {"rig_id": rig_id, "phase": "recovering", "running": True},
                )
                self._log_rig(
                    rig_id,
                    "Unexpected trigger child exit at a safe boundary — "
                    "starting the single permitted recovery attempt.",
                    "warning",
                )
                try:
                    if totality_only:
                        recovered = self.start_totality_only(
                            rig_id=rig_id,
                            _recovery=True,
                            _run_id=run_id,
                            _child_recovery_attempt=recovery_attempt + 1,
                        )
                        recovered = recovered == "started"
                    else:
                        recovered = self.start(
                            rig_id=rig_id,
                            selected=recovery_selection,
                            _recovery=True,
                            _run_id=run_id,
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
                    {"rig_id": rig_id, "phase": "idle", "running": False},
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
                    {"rig_id": rig_id, "phase": "idle", "running": False},
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
                )

    def override_totality('''
    text, count = pattern.subn(hardened_finally, text, count=1)
    if count != 1:
        raise RuntimeError("_run finally block not found")

    # Emergency Totality startup also receives journal/recovery context.
    text = text.replace(
        "    def start_totality_only(self, rig_id=1):",
        "    def start_totality_only(self, rig_id=1, _recovery=False, _run_id=None,\n"
        "                            _child_recovery_attempt=0):",
        1,
    )
    totality_state = """            self.state.update_trigger_rig(
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
                },"""
    totality_new = """            run_id = _run_id
            if not _recovery:
                run_id = self._journal_begin(
                    rig_id,
                    "totality_override",
                    {},
                    totality_only=True,
                )
            if run_id is not None:
                self._run_ids_by_rig[rig_id] = run_id
            published_phase = "recovering" if _recovery else "totality_override"
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
                {"rig_id": rig_id, "phase": published_phase, "running": True},
            )
            thread = threading.Thread(
                target=self._run,
                kwargs={
                    "ipc_session": ipc_session,
                    "rig_id": rig_id,
                    "totality_only": True,
                    "run_id": run_id,
                    "recovery_attempt": _child_recovery_attempt,
                },"""
    if totality_state not in text:
        raise RuntimeError("totality state/thread block not found")
    text = text.replace(totality_state, totality_new, 1)
    # Totality startup exception gets journal finalization if the run was already recorded.
    text = text.replace(
        "        except Exception:\n            with self._lock:\n                self._starting_by_rig[rig_id] = False\n",
        "        except Exception as start_exc:\n            self._journal_finish(\n"
        "                rig_id, locals().get(\"run_id\"), \"failed\",\n"
        "                failure_code=\"START_FAILED\", detail=str(start_exc),\n"
        "            )\n"
        "            self._run_ids_by_rig[rig_id] = None\n"
        "            with self._lock:\n                self._starting_by_rig[rig_id] = False\n",
        1,
    )

    write(path, text)


def patch_runtime_daemon():
    path = "backend/runtime_daemon.py"
    text = read(path)
    text = text.replace(
        "from backend.trigger_service import TriggerService, TriggerValidationError\n",
        "from backend.trigger_service import TriggerService, TriggerValidationError\n"
        "from backend.trigger_run_journal import TriggerRunJournal\n",
        1,
    )
    text = text.replace(
        "        self.state.reset_boot_sensitive()\n        self.camera_runtime = CameraWorkerRuntime(log_fn=self._runtime_log)\n",
        "        self.state.reset_boot_sensitive()\n"
        "        self.run_journal = TriggerRunJournal(\n"
        "            self.project_root / \"var\" / \"state\" / \"trigger_state.json\"\n"
        "        )\n"
        "        self.camera_runtime = CameraWorkerRuntime(log_fn=self._runtime_log)\n",
        1,
    )
    text = text.replace(
        "            rig_config_loader=load_rig_configuration,\n        )\n        self._shutdown_lock",
        "            rig_config_loader=load_rig_configuration,\n"
        "            run_journal=self.run_journal,\n"
        "        )\n        self._shutdown_lock",
        1,
    )
    marker = """        self._portal_camera_sessions: set[str] = set()
        self._portal_camera_sessions_lock = threading.RLock()

    def _runtime_log"""
    replacement = """        self._portal_camera_sessions: set[str] = set()
        self._portal_camera_sessions_lock = threading.RLock()
        self._recover_active_trigger_runs()

    def _recover_active_trigger_runs(self):
        """Recover at most once, and only inside the same Linux boot."""
        current_boot = self.run_journal.boot_id
        for entry in self.run_journal.active_entries():
            rig_id = int(entry.get("rig_id", 0) or 0)
            run_id = entry.get("run_id")
            if not 1 <= rig_id <= 4 or not isinstance(run_id, str):
                continue
            if not current_boot or entry.get("boot_id") != current_boot:
                detail = (
                    "active trigger journal belongs to another/unknown boot; "
                    "automatic recovery is forbidden"
                )
                self.run_journal.finish(
                    rig_id=rig_id,
                    run_id=run_id,
                    status="failed",
                    failure_code="RUNTIME_REBOOT_DURING_RUN",
                    detail=detail,
                )
                self.trigger.publish_external_failure(
                    rig_id, "RUNTIME_REBOOT_DURING_RUN", detail
                )
                continue
            if entry.get("mode") not in {"real", "totality_override"}:
                detail = "non-real trigger modes are never automatically recovered"
                self.run_journal.finish(
                    rig_id=rig_id,
                    run_id=run_id,
                    status="failed",
                    failure_code="RECOVERY_MODE_UNSAFE",
                    detail=detail,
                )
                self.trigger.publish_external_failure(
                    rig_id, "RECOVERY_MODE_UNSAFE", detail
                )
                continue
            claimed = self.run_journal.claim_runtime_recovery(
                rig_id=rig_id,
                run_id=run_id,
            )
            if claimed is None:
                detail = "automatic runtime recovery attempt was already consumed"
                self.run_journal.finish(
                    rig_id=rig_id,
                    run_id=run_id,
                    status="failed",
                    failure_code="RECOVERY_LIMIT_REACHED",
                    detail=detail,
                )
                self.trigger.publish_external_failure(
                    rig_id, "RECOVERY_LIMIT_REACHED", detail
                )
                continue
            self._runtime_log(
                f"RIG {rig_id}: same-boot runtime recovery claimed.",
                "warning",
                "trigger",
                rig_id=rig_id,
            )
            try:
                result = self.trigger.recover_persisted_run(claimed)
                if result is False:
                    raise RuntimeError("trigger recovery refused start")
            except Exception as exc:
                detail = f"runtime recovery failed: {type(exc).__name__}: {exc}"
                self.run_journal.finish(
                    rig_id=rig_id,
                    run_id=run_id,
                    status="failed",
                    failure_code="RUNTIME_RECOVERY_FAILED",
                    detail=detail,
                )
                self.trigger.publish_external_failure(
                    rig_id, "RUNTIME_RECOVERY_FAILED", detail
                )

    def _runtime_log"""
    if marker not in text:
        raise RuntimeError("runtime controller recovery insertion marker not found")
    text = text.replace(marker, replacement, 1)
    write(path, text)


def patch_eclipse_trigger():
    path = "scripts/eclipse_trigger.py"
    text = read(path)
    text = text.replace(
        "from backend.trigger_runtime import RuntimeClock\n",
        "from backend.trigger_runtime import RuntimeClock\n"
        "from backend.trigger_heartbeat import HeartbeatEmitter\n",
        1,
    )
    text = text.replace(
        "    log_fn=log,\n) -> dict[str, int]:",
        "    log_fn=log,\n    heartbeat_fn=lambda _stage: None,\n) -> dict[str, int]:",
        1,
    )
    text = text.replace(
        "    camera_configured = False\n    try:\n",
        "    camera_configured = False\n    heartbeat_fn(\"phase.setup.begin\")\n    try:\n",
        1,
    )
    text = text.replace(
        "        camera_configured = True\n    except Exception as exc:\n",
        "        camera_configured = True\n        heartbeat_fn(\"phase.ready\")\n    except Exception as exc:\n"
        "        heartbeat_fn(\"phase.setup.error\")\n",
        1,
    )
    text = text.replace(
        "            if wait_s > 0:\n                stopped.wait(wait_s)\n",
        "            if wait_s > 0:\n                heartbeat_fn(\"wait\")\n                stopped.wait(min(wait_s, 0.25))\n",
        1,
    )
    text = text.replace(
        "        try:\n            prepared = camera.prepare_capture(intent)\n            result = camera.trigger_prepared(prepared, deadline=None)\n",
        "        try:\n            heartbeat_fn(\"capture.begin\")\n            prepared = camera.prepare_capture(intent)\n            result = camera.trigger_prepared(prepared, deadline=None)\n            heartbeat_fn(\"capture.end\")\n",
        1,
    )
    text = text.replace(
        "        except Exception as exc:\n            stats[\"errors\"] += 1\n            camera_configured = False\n",
        "        except Exception as exc:\n            heartbeat_fn(\"capture.error\")\n            stats[\"errors\"] += 1\n            camera_configured = False\n",
        1,
    )
    text = text.replace(
        "def main() -> int:\n    args = parse_args()\n",
        "def main() -> int:\n    heartbeat = HeartbeatEmitter.from_environment()\n"
        "    heartbeat.pulse(\"startup\")\n"
        "    args = parse_args()\n",
        1,
    )
    text = text.replace(
        "                    client.ping()\n                    break\n",
        "                    client.ping()\n                    heartbeat.pulse(\"ipc.ready\")\n                    break\n",
        1,
    )
    # Phase setup and capture boundaries.
    text = text.replace(
        "        def initialize_phase(window: PhaseWindow) -> None:\n            nonlocal camera_initialized\n",
        "        def initialize_phase(window: PhaseWindow) -> None:\n            nonlocal camera_initialized\n            heartbeat.pulse(\"phase.setup.begin\")\n",
        1,
    )
    text = text.replace(
        "                camera.initialize(aperture=aperture, iso=iso)\n                camera_initialized = True\n\n        def reconcile_phase",
        "                camera.initialize(aperture=aperture, iso=iso)\n                camera_initialized = True\n            heartbeat.pulse(\"phase.ready\")\n\n        def reconcile_phase",
        1,
    )
    text = text.replace(
        "        def reconcile_phase(window: PhaseWindow) -> None:\n            nonlocal camera_initialized\n",
        "        def reconcile_phase(window: PhaseWindow) -> None:\n            nonlocal camera_initialized\n            heartbeat.pulse(\"phase.setup.begin\")\n",
        1,
    )
    text = text.replace(
        "                camera_initialized = True\n                return\n            changed = camera.apply_phase_settings(\n",
        "                camera_initialized = True\n                heartbeat.pulse(\"phase.ready\")\n                return\n            changed = camera.apply_phase_settings(\n",
        1,
    )
    text = text.replace(
        "            if changed:\n                log(\n                    \"TRIGGER_CONFIG \"\n                    f\"SET aperture={aperture} ISO={iso}\"\n                )\n\n        def capture_cycle",
        "            if changed:\n                log(\n                    \"TRIGGER_CONFIG \"\n                    f\"SET aperture={aperture} ISO={iso}\"\n                )\n            heartbeat.pulse(\"phase.ready\")\n\n        def capture_cycle",
        1,
    )
    text = text.replace(
        "            prepared = camera.prepare_capture(intent)\n            result = camera.trigger_prepared(prepared, deadline=deadline)\n",
        "            heartbeat.pulse(\"capture.begin\")\n            try:\n"
        "                prepared = camera.prepare_capture(intent)\n"
        "                result = camera.trigger_prepared(prepared, deadline=deadline)\n"
        "            except Exception:\n"
        "                heartbeat.pulse(\"capture.error\")\n"
        "                raise\n"
        "            heartbeat.pulse(\"capture.end\")\n",
        1,
    )
    text = text.replace(
        "        def wait_until(target: datetime) -> None:\n            remaining = (target - clock.now()).total_seconds()\n",
        "        def wait_until(target: datetime) -> None:\n            heartbeat.pulse(\"wait\")\n            remaining = (target - clock.now()).total_seconds()\n",
        1,
    )
    # Emergency calls receive heartbeat and the normal runtime advertises begin/end.
    text = text.replace(
        "                log_fn=log,\n            )\n        else:\n            try:\n                PhaseRuntime(",
        "                log_fn=log,\n                heartbeat_fn=heartbeat.pulse,\n            )\n        else:\n            try:\n                heartbeat.pulse(\"runtime.begin\")\n                PhaseRuntime(",
        1,
    )
    text = text.replace(
        "                ).run()\n            except EmergencyTotalityRequested:\n",
        "                ).run()\n                heartbeat.pulse(\"runtime.end\")\n            except EmergencyTotalityRequested:\n",
        1,
    )
    text = text.replace(
        "                    log_fn=log,\n                )\n            except Exception as exc:\n",
        "                    log_fn=log,\n                    heartbeat_fn=heartbeat.pulse,\n                )\n            except Exception as exc:\n"
        "                heartbeat.pulse(\"runtime.error\")\n",
        1,
    )
    text = text.replace(
        "    finally:\n        stopped.set()\n",
        "    finally:\n        heartbeat.pulse(\"shutdown\")\n        stopped.set()\n",
        1,
    )
    text = text.replace(
        "        if camera is not None:\n            camera.close()\n",
        "        if camera is not None:\n            camera.close()\n        heartbeat.close()\n",
        1,
    )
    write(path, text)


def patch_frontend():
    path = "flask_app/static/js/solartrigger.js"
    text = read(path)
    # Add names without depending on exact surrounding ordering.
    text, count = re.subn(
        r"(const PHASE_NAMES\s*=\s*\{)",
        r"\1\n  recovering: '↻ RECOVERING',\n  failed: '⚠ TRIGGER FAILED',",
        text,
        count=1,
    )
    if count != 1:
        raise RuntimeError("PHASE_NAMES not found")
    # Respect an explicit backend running flag; failed normally means stopped,
    # but failed+running is used when an unkillable child is retained for STOP.
    old = "nextState.running = nextPhase !== \"idle\";"
    if old not in text:
        old = "nextState.running = nextPhase !== 'idle';"
    if old not in text:
        raise RuntimeError("trigger_phase running assignment not found")
    quote = '"' if '"idle"' in old else "'"
    new = (
        "nextState.running = typeof s.running === 'boolean'\n"
        "      ? s.running\n"
        f"      : ![{quote}idle{quote}, {quote}failed{quote}].includes(nextPhase);"
    )
    text = text.replace(old, new, 1)
    # Add an explicit high-visibility browser alert beside the existing phase event.
    socket_marker = "socket.on(\"trigger_phase\""
    if socket_marker not in text:
        socket_marker = "socket.on('trigger_phase'"
    pos = text.find(socket_marker)
    if pos < 0:
        raise RuntimeError("trigger_phase socket handler not found")
    # Insert before the next socket.on after this handler. A small parser counts braces.
    brace = text.find("{", pos)
    depth = 0
    end = None
    for idx in range(brace, len(text)):
        ch = text[idx]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                semi = text.find(";", idx)
                end = semi + 1 if semi >= 0 else idx + 1
                break
    if end is None:
        raise RuntimeError("could not locate trigger_phase handler end")
    handler = """

  socket.on("trigger_failure", (payload) => {
    const rigId = normalizeRigId(payload && payload.rig_id, selectedTriggerRigId);
    const code = payload && payload.code ? ` [${payload.code}]` : '';
    const message = payload && payload.message ? payload.message : 'Trigger process failed.';
    flash(`RIG ${rigId} — TRIGGER FAILED${code}: ${message}`, 'red');
  });"""
    text = text[:end] + handler + text[end:]
    write(path, text)


def patch_systemd():
    path = "install/install_standalone_runtime_service.sh"
    text = read(path)
    needle = "TimeoutStopSec=45\n"
    if needle not in text:
        raise RuntimeError("runtime systemd TimeoutStopSec marker not found")
    text = text.replace(needle, needle + "KillMode=control-group\n", 1)
    write(path, text)


def main():
    patch_trigger_service()
    patch_runtime_daemon()
    patch_eclipse_trigger()
    patch_frontend()
    patch_systemd()
    print("remaining trigger hardening applied")


if __name__ == "__main__":
    main()
