"""Autonomous SolarTrigger execution runtime.

This process is intentionally independent from Flask/Gunicorn. It owns the
camera workers, Camera IPC server and TriggerService so a portal restart cannot
interrupt an in-flight eclipse sequence.
"""

from __future__ import annotations

import argparse
from collections import deque
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import signal
import socketserver
import threading
from typing import Any
import uuid

from backend import audio_service
from backend.camera_worker_runtime import CameraWorkerRuntime
from backend.generic_worker import BusyDeviceError
from backend.rig_runtime import load_rig_configuration
from backend.runtime_paths import (
    CONFIGS_DIR,
    GENERATED_DIR,
    STATE_FILE,
    TODAY_ECLIPSE_FILE,
    ensure_var_layout,
)
from backend.runtime_rpc import (
    DEFAULT_RUNTIME_SOCKET,
    _from_wire,
    _to_wire,
)
from backend.state_store import StateStore
from backend.trigger_service import TriggerService, TriggerValidationError
from backend.trigger_run_journal import (
    TriggerRunJournal,
    TriggerRunJournalInvalid,
)


LOG = logging.getLogger("solartrigger-runtime")
_MAX_REQUEST_BYTES = 16 * 1024 * 1024

# Only operator-facing diagnostic calls are proxied through the portal. The
# real-time trigger uses CameraIpcServer directly through an explicit lease.
_ALLOWED_WORKER_METHODS = frozenset({
    "probe_info",
    "read_info",
    "test_photo_fast",
    "sync_datetime",
    "get_battery_level",
})


class RuntimeLogJournal:
    """Bounded, sequenced log journal owned by the autonomous runtime."""

    def __init__(self, size: int = 2000):
        self._entries = deque(maxlen=max(1, int(size)))
        self._lock = threading.RLock()
        self._next_seq = 1

    def append(self, text, level="info", source="runtime", rig_id=None) -> dict:
        with self._lock:
            entry = {
                "seq": self._next_seq,
                "text": str(text),
                "level": str(level),
                "source": str(source),
                "timestamp": datetime.now(timezone.utc).strftime("%H:%M:%S"),
            }
            if rig_id is not None:
                entry["rig_id"] = int(rig_id)
            self._entries.append(entry)
            self._next_seq += 1
            return dict(entry)

    def read(self, after_seq: int = 0, limit: int = 500) -> dict:
        try:
            after_seq = int(after_seq)
            limit = int(limit)
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid runtime log cursor") from exc
        if after_seq < 0:
            raise ValueError("runtime log cursor must be >= 0")
        if not 1 <= limit <= 2000:
            raise ValueError("runtime log limit must be between 1 and 2000")

        with self._lock:
            entries = list(self._entries)
            latest_seq = self._next_seq - 1
            oldest_seq = entries[0]["seq"] if entries else self._next_seq
            gap = bool(entries and after_seq and after_seq < oldest_seq - 1)
            selected = [
                dict(entry)
                for entry in entries
                if entry["seq"] > after_seq
            ][:limit]
        return {
            "entries": selected,
            "latest_seq": latest_seq,
            "oldest_seq": oldest_seq,
            "gap": gap,
        }


class RuntimeEventJournal:
    """Bounded, sequenced Socket.IO event journal owned by the runtime."""

    def __init__(self, size: int = 2000):
        self._entries = deque(maxlen=max(1, int(size)))
        self._lock = threading.RLock()
        self._next_seq = 1

    def append(self, event: str, payload: dict) -> dict:
        if not isinstance(event, str) or not event:
            raise ValueError("runtime event name is required")
        if not isinstance(payload, dict):
            raise ValueError("runtime event payload must be an object")
        with self._lock:
            entry = {
                "seq": self._next_seq,
                "event": event,
                "payload": dict(payload),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            self._entries.append(entry)
            self._next_seq += 1
            return dict(entry)

    def read(self, after_seq: int = 0, limit: int = 500) -> dict:
        try:
            after_seq = int(after_seq)
            limit = int(limit)
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid runtime event cursor") from exc
        if after_seq < 0:
            raise ValueError("runtime event cursor must be >= 0")
        if not 1 <= limit <= 2000:
            raise ValueError("runtime event limit must be between 1 and 2000")

        with self._lock:
            entries = list(self._entries)
            latest_seq = self._next_seq - 1
            oldest_seq = entries[0]["seq"] if entries else self._next_seq
            gap = bool(entries and after_seq and after_seq < oldest_seq - 1)
            selected = [
                dict(entry)
                for entry in entries
                if entry["seq"] > after_seq
            ][:limit]
        return {
            "entries": selected,
            "latest_seq": latest_seq,
            "oldest_seq": oldest_seq,
            "gap": gap,
        }


class RuntimeController:
    """Own all hardware/runtime objects and dispatch a narrow RPC surface."""

    def __init__(self, project_root: Path | None = None):
        self.project_root = (
            Path(project_root).resolve()
            if project_root is not None
            else Path(__file__).resolve().parents[1]
        )
        ensure_var_layout(self.project_root / "var")

        self.state_file = self.project_root / "var" / "state" / "state.json"
        self.started_utc = datetime.now(timezone.utc)
        self.runtime_id = uuid.uuid4().hex
        self.log_journal = RuntimeLogJournal()
        self.event_journal = RuntimeEventJournal()
        self.state = StateStore(self.state_file)
        # A real runtime-service start corresponds to a new execution owner
        # (including machine boot). Never inherit a persisted "GPS synced"
        # assertion from a previous runtime instance. A later portal GPS sync is
        # accepted from state.json only when its timestamp belongs to this
        # runtime lifetime.
        self.state.reset_boot_sensitive()
        self.run_journal = TriggerRunJournal(
            self.project_root / "var" / "state" / "trigger_state.json"
        )
        self.camera_runtime = CameraWorkerRuntime(log_fn=self._runtime_log)
        self._failure_audio_lock = threading.Lock()
        self.trigger = TriggerService(
            self.state,
            self.project_root / "scripts" / "eclipse_trigger.py",
            self.project_root / "var" / "generated" / "todayeclipse.json",
            self.project_root / "var" / "generated",
            log_fn=self._runtime_log,
            emit_fn=self._runtime_emit,
            product_configs_dir=self.project_root / "configs",
            camera_runtime=self.camera_runtime,
            rig_config_loader=load_rig_configuration,
            run_journal=self.run_journal,
            failure_alert_fn=self._trigger_failure_alert,
        )
        self._shutdown_lock = threading.Lock()
        self._shutdown = False
        # Sessions created through portal RPC are diagnostic/validation leases.
        # TriggerService opens its own local leases and is never included here.
        self._portal_camera_sessions: set[str] = set()
        self._portal_camera_sessions_lock = threading.RLock()
        self._restore_trigger_journal_state()

    def _trigger_failure_alert(self, rig_id, code, detail):
        """Play a local alarm for a live trigger failure without blocking supervision."""
        def _play():
            with self._failure_audio_lock:
                audio_service.init(
                    lambda message: self._runtime_log(
                        message,
                        "warning",
                        "audio",
                        rig_id=rig_id,
                    ),
                    driver="alsa",
                )
                audio_service.set_sounds_dir(self.project_root / "Sounds")
                # Reuse the bundled neutral alert tone so the hardening does
                # not depend on an extra generated media asset.
                for _ in range(3):
                    audio_service.play("contact.wav")

        threading.Thread(
            target=_play,
            name=f"trigger-failure-audio-rig-{rig_id}",
            daemon=True,
        ).start()

    def _publish_recovery_journal_invalid(self, exc):
        detail = (
            "trigger recovery journal is invalid or unreadable; "
            f"automatic recovery is disabled: {exc}"
        )
        self._runtime_log(
            detail,
            "critical",
            "trigger",
        )
        for rig_id in range(1, 5):
            self.trigger.publish_external_failure(
                rig_id,
                "RECOVERY_JOURNAL_INVALID",
                detail,
            )

    def _restore_trigger_journal_state(self):
        try:
            failed_entries = self.run_journal.failed_entries()
        except TriggerRunJournalInvalid as exc:
            self._publish_recovery_journal_invalid(exc)
            return

        for entry in failed_entries:
            try:
                rig_id = int(entry.get("rig_id", 0) or 0)
            except (TypeError, ValueError):
                continue
            if not 1 <= rig_id <= 4:
                continue
            code = entry.get("failure_code") or "TRIGGER_FAILED"
            detail = entry.get("detail") or "previous trigger run failed"
            self.trigger.publish_external_failure(
                rig_id,
                code,
                detail,
                exit_code=entry.get("exit_code"),
            )

        self._recover_active_trigger_runs()

    def _recover_active_trigger_runs(self):
        # Recover at most once, and only inside the same Linux boot.
        current_boot = self.run_journal.boot_id
        try:
            active_entries = self.run_journal.active_entries()
        except TriggerRunJournalInvalid as exc:
            self._publish_recovery_journal_invalid(exc)
            return

        for entry in active_entries:
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

    def _runtime_log(self, text, level="info", source="runtime", rig_id=None):
        self.log_journal.append(text, level, source, rig_id)
        numeric = {
            "debug": logging.DEBUG,
            "info": logging.INFO,
            "success": logging.INFO,
            "warning": logging.WARNING,
            "error": logging.ERROR,
            "critical": logging.CRITICAL,
        }.get(str(level).lower(), logging.INFO)
        prefix = f"[{source}]"
        if rig_id is not None:
            prefix += f"[RIG {rig_id}]"
        LOG.log(numeric, "%s %s", prefix, text)

    def _runtime_emit(self, event, payload):
        """Record UI events without making capture depend on the portal."""
        try:
            self.event_journal.append(str(event), dict(payload or {}))
        except Exception:
            LOG.exception("Unable to journal runtime event %r", event)

    def _refresh_persisted_state(self) -> None:
        """Refresh portal-owned persisted inputs without touching live Trigger state.

        GPS synchronization is boot-sensitive. StateStore persists the last GPS
        record for diagnostics, but a new autonomous runtime must not trust a
        "synced" flag written by an earlier runtime/boot merely because it is
        less than TriggerService's two-hour freshness threshold.
        """
        fresh = StateStore(self.state_file)
        for key in StateStore.PERSISTED_KEYS:
            if key == "trigger":
                continue
            value = fresh.get(key)
            if value is None:
                continue

            if key == "gps" and isinstance(value, dict):
                raw_sync_time = value.get("sync_time")
                current_runtime_sync = False
                if value.get("synced") and isinstance(raw_sync_time, str):
                    try:
                        sync_dt = datetime.fromisoformat(
                            raw_sync_time.strip().replace("Z", "+00:00")
                        )
                        if sync_dt.tzinfo is None:
                            sync_dt = sync_dt.replace(tzinfo=timezone.utc)
                        current_runtime_sync = (
                            sync_dt.astimezone(timezone.utc)
                            >= self.started_utc
                        )
                    except (TypeError, ValueError):
                        current_runtime_sync = False

                if not current_runtime_sync:
                    previous = self.state.snapshot("gps") or {}
                    value["synced"] = bool(previous.get("synced"))
                    value["sync_time"] = previous.get("sync_time")
                    value["gps_sync_running"] = False

            self.state.set(key, value, persist=False)

    def _trigger_snapshot(self) -> dict:
        trigger = self.state.snapshot("trigger") or {}
        rigs = trigger.setdefault("rigs", {})
        any_active = False

        active_inputs_fn = getattr(self.trigger, "active_inputs_snapshot", None)
        active_inputs = active_inputs_fn() if callable(active_inputs_fn) else {}

        for rig_id in range(1, 5):
            key = str(rig_id)
            rig_state = rigs.setdefault(
                key,
                {
                    "running": False,
                    "phase": "idle",
                    "mode": None,
                    "speed": None,
                },
            )
            rig_state["inputs"] = active_inputs.get(key, {})
            active = self.trigger.is_active_or_starting(rig_id)
            if active:
                any_active = True
                # The private starting window precedes state publication.
                if not rig_state.get("running"):
                    rig_state["running"] = True
                    if rig_state.get("phase") in (None, "idle"):
                        rig_state["phase"] = "starting"

        trigger["running"] = any_active
        if not any_active and trigger.get("phase") not in (None, "idle"):
            trigger["phase"] = "idle"
        return trigger

    def _worker(self, rig_id: Any):
        if (
            not isinstance(rig_id, int)
            or isinstance(rig_id, bool)
            or not 1 <= rig_id <= 4
        ):
            raise ValueError("rig_id must be an integer from 1 to 4")
        worker = self.camera_runtime.get_for_rig(rig_id)
        if worker is None:
            raise RuntimeError(f"camera worker unavailable for RIG {rig_id}")
        return worker

    def dispatch(self, operation: str, payload: dict):
        if not isinstance(payload, dict):
            raise ValueError("payload must be an object")

        if operation == "ping":
            return {
                "status": "ok",
                "pid": os.getpid(),
                "trigger": self._trigger_snapshot(),
            }

        if operation == "trigger.status":
            # A portal may restart independently from this runtime. Refresh
            # persisted operator-owned state here so a GPS sync performed
            # during this runtime lifetime is restored after Gunicorn restart.
            # _refresh_persisted_state() preserves live Trigger state and
            # rejects GPS sync timestamps from an older runtime/boot.
            self._refresh_persisted_state()
            return {
                "pid": os.getpid(),
                "trigger": self._trigger_snapshot(),
                "gps": self.state.snapshot("gps"),
            }

        if operation == "logs.read":
            result = self.log_journal.read(
                payload.get("after_seq", 0),
                payload.get("limit", 500),
            )
            result["runtime_id"] = self.runtime_id
            return result

        if operation == "events.read":
            result = self.event_journal.read(
                payload.get("after_seq", 0),
                payload.get("limit", 500),
            )
            result["runtime_id"] = self.runtime_id
            return result

        if operation == "relay.read":
            limit = payload.get("limit", 500)
            return {
                "runtime_id": self.runtime_id,
                "logs": self.log_journal.read(
                    payload.get("log_after_seq", 0),
                    limit,
                ),
                "events": self.event_journal.read(
                    payload.get("event_after_seq", 0),
                    limit,
                ),
            }

        if operation == "trigger.is_active_or_starting":
            return self.trigger.is_active_or_starting(payload.get("rig_id"))

        if operation == "trigger.any_active_or_starting":
            return self.trigger.any_active_or_starting()

        if operation == "trigger.start":
            self._refresh_persisted_state()
            return self.trigger.start(
                rig_id=payload.get("rig_id", 1),
                simulate=payload.get("simulate", False),
                speed=payload.get("speed", 60.0),
                dry_run=payload.get("dry_run", False),
                selected=payload.get("selected"),
            )

        if operation == "trigger.totality_only":
            self._refresh_persisted_state()
            return self.trigger.start_totality_only(
                rig_id=payload.get("rig_id", 1)
            )

        if operation == "trigger.override_totality":
            return self.trigger.override_totality(
                rig_id=payload.get("rig_id", 1)
            )

        if operation == "trigger.stop":
            force = payload.get("force", False)
            if not isinstance(force, bool):
                raise ValueError("force must be boolean")
            return self.trigger.stop(
                rig_id=payload.get("rig_id", 1),
                force=force,
            )

        if operation == "camera.reconcile":
            config = payload.get("config")
            if not isinstance(config, dict):
                raise ValueError("camera.reconcile requires a configuration object")
            self.camera_runtime.reconcile(config)
            return None

        if operation == "camera.release_idle_workers":
            self.camera_runtime.release_idle_workers()
            return None

        if operation == "camera.worker_exists":
            rig_id = payload.get("rig_id")
            if (
                not isinstance(rig_id, int)
                or isinstance(rig_id, bool)
                or not 1 <= rig_id <= 4
            ):
                return False
            return self.camera_runtime.get_for_rig(rig_id) is not None

        if operation == "camera.worker_call":
            method = payload.get("method")
            if method not in _ALLOWED_WORKER_METHODS:
                raise ValueError(f"camera worker method is not exposed: {method}")
            worker = self._worker(payload.get("rig_id"))
            args = payload.get("args") or []
            kwargs = payload.get("kwargs") or {}
            if not isinstance(args, list) or not isinstance(kwargs, dict):
                raise ValueError("invalid camera worker arguments")
            return getattr(worker, method)(*args, **kwargs)

        if operation == "camera.policy_config":
            return self.camera_runtime.get_policy_config_for_rig(
                payload.get("rig_id")
            )

        if operation == "camera.active_rig_ids":
            return list(self.camera_runtime.active_camera_rig_ids())

        if operation == "camera.open_ipc_session":
            rig_ids = payload.get("rig_ids")
            session = self.camera_runtime.open_ipc_session(rig_ids)
            with self._portal_camera_sessions_lock:
                self._portal_camera_sessions.add(session.session_id)
            return {
                "socket_path": session.socket_path,
                "session_id": session.session_id,
            }

        if operation == "camera.close_ipc_session":
            session_id = str(payload.get("session_id") or "")
            self.camera_runtime.close_ipc_session(session_id)
            with self._portal_camera_sessions_lock:
                self._portal_camera_sessions.discard(session_id)
            return None

        if operation == "camera.revoke_portal_sessions":
            with self._portal_camera_sessions_lock:
                session_ids = tuple(self._portal_camera_sessions)
            errors = []
            for session_id in session_ids:
                try:
                    self.camera_runtime.close_ipc_session(session_id)
                except Exception as exc:
                    errors.append(f"{session_id}: {exc}")
                finally:
                    with self._portal_camera_sessions_lock:
                        self._portal_camera_sessions.discard(session_id)
            if errors:
                raise RuntimeError(
                    "unable to revoke all portal camera sessions: "
                    + " | ".join(errors)
                )
            return len(session_ids)

        raise ValueError(f"unknown runtime operation: {operation}")

    def shutdown(self) -> None:
        """Gracefully stop trigger children before releasing camera ownership."""
        with self._shutdown_lock:
            if self._shutdown:
                return
            self._shutdown = True

        threads = []
        for rig_id in range(1, 5):
            if not self.trigger.is_active_or_starting(rig_id):
                continue
            thread = threading.Thread(
                target=self.trigger.stop,
                kwargs={"rig_id": rig_id, "force": True},
                name=f"runtime-stop-rig-{rig_id}",
                daemon=True,
            )
            thread.start()
            threads.append(thread)

        for thread in threads:
            thread.join(timeout=5.0)

        self.camera_runtime.shutdown()


class _RuntimeRequestHandler(socketserver.StreamRequestHandler):
    def handle(self):
        line = self.rfile.readline(_MAX_REQUEST_BYTES + 1)
        if not line:
            return
        if len(line) > _MAX_REQUEST_BYTES:
            self._write_error(ValueError("runtime request exceeds size limit"))
            return

        try:
            request = json.loads(line.decode("utf-8"))
            if not isinstance(request, dict):
                raise ValueError("runtime request must be an object")
            operation = request.get("operation")
            if not isinstance(operation, str) or not operation:
                raise ValueError("runtime operation is required")
            payload = _from_wire(request.get("payload") or {})
            result = self.server.controller.dispatch(operation, payload)
            response = {
                "ok": True,
                "result": _to_wire(result),
            }
        except BaseException as exc:
            LOG.exception("RPC operation failed")
            response = self._error_response(exc)

        encoded = (
            json.dumps(response, separators=(",", ":"), ensure_ascii=False)
            + "\n"
        ).encode("utf-8")
        self.wfile.write(encoded)

    @staticmethod
    def _error_response(exc: BaseException) -> dict:
        code = getattr(exc, "code", None)
        return {
            "ok": False,
            "error": {
                "type": type(exc).__name__,
                "message": str(exc),
                "code": code,
            },
        }

    def _write_error(self, exc: BaseException):
        encoded = (
            json.dumps(
                self._error_response(exc),
                separators=(",", ":"),
                ensure_ascii=False,
            )
            + "\n"
        ).encode("utf-8")
        self.wfile.write(encoded)


class RuntimeUnixServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, socket_path: str, controller: RuntimeController):
        self.controller = controller
        path = Path(socket_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        super().__init__(str(path), _RuntimeRequestHandler)
        os.chmod(path, 0o660)
        socket_group = os.environ.get("SOLARTRIGGER_RUNTIME_SOCKET_GROUP")
        if socket_group:
            import grp
            try:
                gid = grp.getgrnam(socket_group).gr_gid
            except KeyError as exc:
                raise RuntimeError(
                    f"runtime socket group does not exist: {socket_group}"
                ) from exc
            os.chown(path, -1, gid)
        self.socket_path = path

    def server_close(self):
        try:
            super().server_close()
        finally:
            try:
                self.socket_path.unlink()
            except FileNotFoundError:
                pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="SolarTrigger standalone runtime")
    parser.add_argument(
        "--socket",
        default=os.environ.get(
            "SOLARTRIGGER_RUNTIME_SOCKET",
            DEFAULT_RUNTIME_SOCKET,
        ),
    )
    parser.add_argument(
        "--root",
        default=os.environ.get("SOLARTRIGGER_ROOT"),
    )
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    root = Path(args.root).resolve() if args.root else None
    controller = RuntimeController(root)
    server = RuntimeUnixServer(args.socket, controller)

    def request_shutdown(signum, frame):
        LOG.warning("Runtime shutdown requested by signal %s", signum)
        threading.Thread(
            target=server.shutdown,
            name="runtime-shutdown",
            daemon=True,
        ).start()

    signal.signal(signal.SIGTERM, request_shutdown)
    signal.signal(signal.SIGINT, request_shutdown)

    LOG.info(
        "Standalone runtime ready pid=%s socket=%s",
        os.getpid(),
        args.socket,
    )
    try:
        server.serve_forever(poll_interval=0.2)
    finally:
        server.server_close()
        controller.shutdown()
        LOG.info("Standalone runtime stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
