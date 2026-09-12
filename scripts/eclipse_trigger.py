#!/usr/bin/env python3
"""Real-time, phase-driven eclipse trigger.

The trigger never consumes an Execution Plan and never schedules individual
camera SET commands. One camera plugin owns each complete capture operation.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import fcntl
import json
import os
from pathlib import Path
import signal
import threading
import uuid

from backend import audio_service
from backend.executable_exposure_plan import expand_executable_shutters
from backend.phase_trigger import PhaseRuntime, PhaseWindow, build_phase_schedule
from backend.preview_materializer import apply_atmos_if_enabled, normalize_intent_plan
from backend.rig_runtime import load_rig_configuration
from backend.timeline import build_timeline
from backend.trigger_runtime import RuntimeClock
from plugins.camera.base import CaptureResult
from scripts.camera_ipc_client import CameraIpcClient
from scripts.fanout_camera_adapter import FanoutCameraAdapter
from services.camera_service import CaptureIntent, PreparedCapture


ROOT = Path(__file__).resolve().parent.parent
SOUNDS_DIR = ROOT / "Sounds"
AUDIO_LOCK = "/tmp/solartrigger-global-audio.lock"


def log(message: str) -> None:
    print(message, flush=True)


def load_json(path: str, label: str) -> dict:
    with open(path, encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"{label} must contain a JSON object")
    return value


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SolarTrigger phase runtime")
    parser.add_argument("--file", required=True, help="Eclipse circumstances JSON")
    parser.add_argument("--camera", required=True, help="Photo Setup JSON")
    parser.add_argument("--exposure-opt", required=True, help="Exposure Optimization JSON")
    parser.add_argument("--simulate", action="store_true")
    parser.add_argument("--speed", type=float, default=60.0)
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Use today's UTC date with the original circumstances times",
    )
    return parser.parse_args()


def exposure_rig(exposure_opt: dict, rig_id: int) -> dict:
    if exposure_opt.get("config_type") != "exposure_optimization":
        raise ValueError("invalid Exposure Optimization configuration")
    item = next((candidate for candidate in exposure_opt.get("rigs", ())
                 if isinstance(candidate, dict) and candidate.get("rig_id") == rig_id), None)
    if item is None:
        raise ValueError(f"Exposure Optimization has no RIG {rig_id}")
    return item


def today_circumstances(source: dict, today: datetime) -> dict:
    result = deepcopy(source)
    date = today.date().isoformat()
    result["_date"] = date
    result["_date_utc"] = date
    return result


def _speed_seconds(value: str) -> float:
    text = str(value).strip()
    if "/" in text:
        numerator, denominator = text.split("/", 1)
        return float(numerator) / float(denominator)
    return float(text)


class SimulationCamera:
    def __init__(self, clock: RuntimeClock, rig_snapshot: dict) -> None:
        self.clock = clock
        self.rig_snapshot = rig_snapshot

    def initialize(self, **settings) -> None:
        log(f"INFO camera=simulation initialize={settings}")

    def apply_phase_settings(self, **settings) -> None:
        log(f"INFO camera=simulation reconcile={settings}")

    def prepare_capture(self, intent: CaptureIntent) -> PreparedCapture:
        plan = normalize_intent_plan(intent)
        speeds = expand_executable_shutters(self.rig_snapshot, plan)
        exposures = [_speed_seconds(value) for value in speeds]
        return PreparedCapture(
            token=(intent, speeds), estimated_total_s=sum(exposures),
            exposures_s=exposures, planned_count=len(speeds),
            plugin_name="simulation",
        )

    def trigger_prepared(self, prepared, deadline=None) -> CaptureResult:
        del deadline
        self.clock.sleep(float(prepared.estimated_total_s or 0.0))
        return CaptureResult(frames=prepared.planned_count, planned=prepared.planned_count)

    def close(self) -> None:
        return None


def _phase_alerts(schedule, timeline: dict) -> list[tuple[datetime, str]]:
    offsets = (
        (600, "10minutes.wav"), (300, "5minutes.wav"),
        (60, "60seconds.wav"), (30, "30seconds.wav"),
        (10, "10seconds.wav"), (5, "5.wav"), (4, "4.wav"),
        (3, "3.wav"), (2, "2.wav"), (1, "1.wav"),
        (0, "contact.wav"),
    )
    alerts = []
    for contact in (timeline.get(name) for name in ("C1", "C2", "C3", "C4")):
        if contact is None:
            continue
        for seconds, filename in offsets:
            instant = contact - timedelta(seconds=seconds)
            if schedule.tstart <= instant < schedule.tend:
                alerts.append((instant, filename))
    return sorted(set(alerts), key=lambda item: item[0])


def _audio_scheduler(alerts, clock, stopped) -> None:
    """One global timing announcer across all per-RIG processes."""
    with open(AUDIO_LOCK, "a+", encoding="utf-8") as lock:
        while not stopped.is_set():
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                stopped.wait(0.25)
        else:
            return
        audio_service.init(log_fn=log, driver="alsa")
        audio_service.set_sounds_dir(SOUNDS_DIR)
        pending = [item for item in alerts if item[0] > clock.now()]
        log(f"INFO global_audio announcements={len(pending)}")
        while pending and not stopped.is_set():
            for item in [candidate for candidate in pending if candidate[0] <= clock.now()]:
                pending.remove(item)
                thread = threading.Thread(
                    target=audio_service.play, args=(item[1],), daemon=True,
                    name=f"audio-{item[1]}",
                )
                audio_service.register_thread(thread)
                thread.start()
            stopped.wait(0.1)


def main() -> int:
    args = parse_args()
    if args.simulate and args.dry_run:
        raise ValueError("simulation and dry-run are mutually exclusive")

    clock = RuntimeClock()
    clock.configure(args.simulate, args.speed)
    stopped = threading.Event()
    override = threading.Event()

    signal.signal(signal.SIGTERM, lambda _sig, _frame: stopped.set())
    signal.signal(signal.SIGINT, lambda _sig, _frame: stopped.set())
    if hasattr(signal, "SIGUSR1"):
        signal.signal(signal.SIGUSR1, lambda _sig, _frame: override.set())

    circumstances = load_json(args.file, "circumstances")
    if args.dry_run:
        circumstances = today_circumstances(circumstances, datetime.now(timezone.utc))
    photo_setup = load_json(args.camera, "Photo Setup")
    exposure_opt = load_json(args.exposure_opt, "Exposure Optimization")
    rig_id = int(os.environ.get("SET_TRIGGER_RIG_ID", "1"))
    rig_exposure = exposure_rig(exposure_opt, rig_id)

    timeline = build_timeline(circumstances, fallback_date=clock.now().date())
    schedule = build_phase_schedule(timeline, photo_setup)
    timeline = dict(timeline)
    timeline.update(TSTART=schedule.tstart, TMAX=schedule.tmax, TEND=schedule.tend)
    if args.simulate:
        clock.start_simulation(schedule.tstart - timedelta(seconds=30))

    try:
        rig_config = load_rig_configuration()
    except Exception as exc:
        log(f"WARNING rig configuration unavailable: {exc}")
        rig_config = {"rigs": []}
    rig_snapshot = next((deepcopy(item) for item in rig_config.get("rigs", ())
                         if isinstance(item, dict) and item.get("rig_id") == rig_id),
                        {"rig_id": rig_id, "photo": {}})
    rig_snapshot.setdefault("photo", {}).update(rig_exposure.get("photo") or {})
    rig_snapshot["photo"]["atmos_enabled"] = bool(
        exposure_opt.get("atmospheric_attenuation_enabled", False)
    )
    eclipse_context = {
        "timeline": {name: timeline[name] for name in ("C1", "C2", "TMAX", "C3", "C4")
                     if timeline.get(name) is not None},
        "altitudes": {name: circumstances.get(name) for name in (
            "C1_alt_deg", "C2_alt_deg", "TMAX_alt_deg", "C3_alt_deg", "C4_alt_deg")},
        "location": circumstances.get("_circumstances_location"),
    }

    camera = None
    audio_thread = threading.Thread(
        target=_audio_scheduler,
        args=(_phase_alerts(schedule, timeline), clock, stopped),
        daemon=True, name="global-timing-announcer",
    )
    audio_thread.start()

    try:
        if args.simulate:
            camera = SimulationCamera(clock, rig_snapshot)
        else:
            socket_path = os.environ.get("SET_CAMERA_IPC_SOCKET")
            session = os.environ.get("SET_CAMERA_IPC_SESSION")
            if not socket_path or not session:
                raise RuntimeError("camera IPC session is required")
            client = CameraIpcClient(socket_path, session, log_fn=log)
            client.ping()
            camera = FanoutCameraAdapter(client, log_fn=log)

        def phase_config(window: PhaseWindow) -> dict:
            return photo_setup["phases"][window.photo_phase]

        def initialize_phase(window: PhaseWindow) -> None:
            config = phase_config(window)
            log(f"TRIGGER_PHASE {window.name}")
            camera.initialize(
                aperture=config.get("aperture", "f/8"),
                iso=str(config.get("iso", "100")),
            )

        def reconcile_phase(window: PhaseWindow) -> None:
            config = phase_config(window)
            camera.apply_phase_settings(
                aperture=config.get("aperture", "f/8"),
                iso=str(config.get("iso", "100")),
            )

        def capture_cycle(window: PhaseWindow, started: datetime) -> bool:
            config = phase_config(window)
            plan = normalize_intent_plan({
                "speeds": config.get("speeds"),
                "shutter_min": config.get("shutter_min"),
                "shutter_max": config.get("shutter_max"),
                "step_ev": config.get("step_ev", 1.0),
            })
            atmos_added = False
            if window.photo_phase == "partial":
                plan, atmos_added, atmos_speed = apply_atmos_if_enabled(
                    rig_snapshot, plan, started, eclipse_context,
                )
                if atmos_added:
                    log(f"INFO phase={window.name} atmos_exposure={atmos_speed}")
            _regular, fastest, slowest, step_ev, speeds = plan
            deadline = window.end if window.photo_phase == "totality" else None
            intent = CaptureIntent(
                shutter_min=None if speeds is not None else slowest,
                shutter_max=None if speeds is not None else fastest,
                step_ev=None if speeds is not None else step_ev,
                speeds=speeds,
                phase=window.photo_phase,
                target_time=started,
                deadline=deadline,
                overflow_policy="truncate",
                origin="atmos" if atmos_added else window.photo_phase,
                request_id=uuid.uuid4().hex,
            )
            prepared = camera.prepare_capture(intent)
            estimate = getattr(prepared, "estimated_total_s", None)
            if (window.photo_phase == "totality" and estimate is not None
                    and clock.now() + timedelta(seconds=float(estimate)) > window.end):
                log("INFO totality capture not started: Diamond Ring C3 has priority")
                return False
            result = camera.trigger_prepared(prepared, deadline=deadline)
            frames = getattr(result, "frames", 0)
            planned = getattr(result, "planned", None)
            if planned is not None and frames != planned:
                log(
                    f"ERROR phase={window.name} stage=photo "
                    f"captured={frames}/{planned}"
                )
            log(f"INFO phase={window.name} PHOTO frames={frames}")
            return True

        def wait_until(target: datetime) -> None:
            remaining = (target - clock.now()).total_seconds()
            if remaining > 0:
                stopped.wait(min(0.25, remaining / clock.speed))

        override_window = PhaseWindow(
            "totality_override", "totality", datetime.min, datetime.max, 0.0,
        )
        PhaseRuntime(
            schedule,
            now=clock.now,
            wait_until=wait_until,
            enter_phase=initialize_phase,
            reconcile_phase=reconcile_phase,
            capture=capture_cycle,
            log_error=lambda message: log(f"ERROR {message}"),
            stopped=stopped.is_set,
            override_phase=lambda _current: override_window if override.is_set() else None,
        ).run()
        log("INFO trigger sequence complete")
        return 0
    finally:
        stopped.set()
        audio_service.shutdown()
        audio_thread.join(timeout=2.0)
        if camera is not None:
            camera.close()


if __name__ == "__main__":
    raise SystemExit(main())
