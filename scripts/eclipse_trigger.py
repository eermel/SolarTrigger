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
from math import ceil
from pathlib import Path
import signal
import threading
import uuid

from backend import audio_service
from backend.executable_exposure_plan import expand_executable_shutters
from backend.phase_trigger import (
    PHASE_DIAMOND_C2,
    PHASE_DIAMOND_C3,
    PhaseRuntime,
    PhaseSchedule,
    PhaseWindow,
    build_phase_schedule,
)
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
EMERGENCY_PHOTO_CONFIG = (
    ROOT / "configs" / "emergency" / "photo_totality.json"
)


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
    parser.add_argument("--file", help="Eclipse circumstances JSON")
    parser.add_argument("--camera", required=True, help="Photo Setup JSON")
    parser.add_argument("--exposure-opt", help="Exposure Optimization JSON")
    parser.add_argument("--simulate", action="store_true")
    parser.add_argument("--speed", type=float, default=60.0)
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Use today's UTC date with the original circumstances times",
    )
    parser.add_argument(
        "--totality-only",
        action="store_true",
        help="Emergency immediate Totality loop; no eclipse timing required",
    )
    args = parser.parse_args()
    if not args.totality_only and not args.file:
        parser.error("--file is required unless --totality-only is used")
    if not args.totality_only and not args.exposure_opt:
        parser.error("--exposure-opt is required unless --totality-only is used")
    return args


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


def _aware_utc(value: datetime) -> datetime:
    """Attach the UTC contract used by camera IPC to runtime timestamps."""
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _speed_seconds(value: str) -> float:
    text = str(value).strip()
    if "/" in text:
        numerator, denominator = text.split("/", 1)
        return float(numerator) / float(denominator)
    return float(text)


def _phase_label(window: PhaseWindow) -> str:
    return {
        "partial": "Partial",
        "diamond_ring": "Diamond ring",
        "totality": "Totality",
    }[window.photo_phase]


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
        (120, "2minutes.wav"),
        (60, "60seconds.wav"), (30, "30seconds.wav"),
        (10, "10seconds.wav"), (5, "5.wav"), (4, "4.wav"),
        (3, "3.wav"), (2, "2.wav"), (1, "1.wav"),
        (0, "contact.wav"),
    )
    alerts = []
    previous_contact = schedule.tstart
    for contact in (timeline.get(name) for name in ("C1", "C2", "C3", "C4")):
        if contact is None:
            continue
        for seconds, filename in offsets:
            instant = contact - timedelta(seconds=seconds)
            # Do not let a long countdown for the next contact leak into the
            # preceding eclipse phase (notably C3 announcements before C2).
            if previous_contact <= instant < schedule.tend:
                alerts.append((instant, filename))
        previous_contact = contact

    # Filter handling follows the phase boundaries computed from the selected
    # circumstances and Photo Setup files, including generated dry-run files.
    alerts.append((schedule.tstart - timedelta(seconds=30), "filters_on.wav"))
    windows = {window.name: window for window in schedule.windows}
    diamond_c2 = windows.get(PHASE_DIAMOND_C2)
    diamond_c3 = windows.get(PHASE_DIAMOND_C3)
    if diamond_c2 is not None and diamond_c3 is not None:
        alerts.extend((
            (diamond_c2.start - timedelta(seconds=2), "filters_off.wav"),
            (diamond_c3.end + timedelta(seconds=2), "filters_on.wav"),
        ))
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
    if args.totality_only and (args.simulate or args.dry_run):
        raise ValueError("totality-only cannot be combined with simulation or dry-run")

    clock = RuntimeClock()
    clock.configure(args.simulate, args.speed)
    stopped = threading.Event()
    override = threading.Event()

    signal.signal(signal.SIGTERM, lambda _sig, _frame: stopped.set())
    signal.signal(signal.SIGINT, lambda _sig, _frame: stopped.set())
    if hasattr(signal, "SIGUSR1"):
        signal.signal(signal.SIGUSR1, lambda _sig, _frame: override.set())

    photo_setup = load_json(args.camera, "Photo Setup")
    emergency_photo_setup = load_json(
        str(EMERGENCY_PHOTO_CONFIG),
        "Emergency Totality Photo Setup",
    )
    exposure_opt = (
        {}
        if args.totality_only
        else load_json(args.exposure_opt, "Exposure Optimization")
    )
    rig_id = int(os.environ.get("SET_TRIGGER_RIG_ID", "1"))
    rig_exposure = (
        {}
        if args.totality_only
        else exposure_rig(exposure_opt, rig_id)
    )

    circumstances = {}
    if args.totality_only:
        now = _aware_utc(clock.now())
        distant = datetime.max.replace(tzinfo=timezone.utc)
        emergency_window = PhaseWindow(
            "totality_override", "totality", now, distant, 0.0,
        )
        schedule = PhaseSchedule(
            tstart=now,
            tend=distant,
            tmax=now,
            windows=(emergency_window,),
        )
        timeline = {}
        override.set()
    else:
        circumstances = load_json(args.file, "circumstances")
        if args.dry_run:
            circumstances = today_circumstances(
                circumstances,
                datetime.now(timezone.utc),
            )
        timeline = build_timeline(
            circumstances,
            fallback_date=clock.now().date(),
        )
        schedule = build_phase_schedule(
            timeline,
            photo_setup,
            honor_timeline_bounds=circumstances.get("_debug_scenario") is True,
        )
        timeline = dict(timeline)
        timeline.update(
            TSTART=schedule.tstart,
            TMAX=schedule.tmax,
            TEND=schedule.tend,
        )
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
    audio_thread = None
    if not args.totality_only:
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
            if window.name == "totality_override":
                return emergency_photo_setup["phases"]["totality"]
            return photo_setup["phases"][window.photo_phase]

        camera_initialized = False

        def photo_log_level(window: PhaseWindow, instant: datetime) -> str:
            if window.photo_phase == "diamond_ring":
                return "purple"
            if window.photo_phase == "totality":
                return "totality"
            c1 = timeline.get("C1")
            c4 = timeline.get("C4")
            if window.name == "partial_before":
                return "warning" if c1 is not None and instant < c1 else "orange"
            if window.name == "partial_after":
                return "warning" if c4 is not None and instant >= c4 else "orange"
            return "orange"

        def initialize_phase(window: PhaseWindow) -> None:
            nonlocal camera_initialized
            config = phase_config(window)
            aperture = config.get("aperture", "f/8")
            iso = str(config.get("iso", "100"))
            log("TRIGGER_PHASE_BORDER")
            log(f"TRIGGER_PHASE {window.name}")
            log("TRIGGER_PHASE_BORDER")
            log(
                "TRIGGER_CONFIG "
                f"Camera phase initialization: aperture={aperture} ISO={iso}"
            )
            if not camera_initialized:
                log(
                    "TRIGGER_CONFIG "
                    f"SET camera initialize aperture={aperture} ISO={iso}"
                )
                camera.initialize(aperture=aperture, iso=iso)
                camera_initialized = True

        def reconcile_phase(window: PhaseWindow) -> None:
            nonlocal camera_initialized
            config = phase_config(window)
            aperture = config.get("aperture", "f/8")
            iso = str(config.get("iso", "100"))
            if not camera_initialized:
                log(
                    "TRIGGER_CONFIG "
                    f"SET camera initialize aperture={aperture} ISO={iso}"
                )
                camera.initialize(aperture=aperture, iso=iso)
                camera_initialized = True
                return
            log("TRIGGER_CONFIG " f"SET aperture={aperture} ISO={iso}")
            camera.apply_phase_settings(aperture=aperture, iso=iso)

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
                    log(
                        f'INFO phase="{_phase_label(window)}" '
                        f"atmos_exposure={atmos_speed}"
                    )
            _regular, fastest, slowest, step_ev, speeds = plan
            planned_speeds = expand_executable_shutters(rig_snapshot, plan)
            # Every phase boundary is authoritative.  The camera may finish
            # an admitted atomic PHOTO group, but must never start a group
            # whose characterized budget crosses into the next phase.
            deadline = _aware_utc(window.end)
            intent = CaptureIntent(
                shutter_min=None if speeds is not None else slowest,
                shutter_max=None if speeds is not None else fastest,
                step_ev=None if speeds is not None else step_ev,
                speeds=speeds,
                phase=window.photo_phase,
                target_time=_aware_utc(started),
                deadline=deadline,
                overflow_policy="truncate",
                origin="atmos" if atmos_added else window.photo_phase,
                request_id=uuid.uuid4().hex,
            )
            prepared = camera.prepare_capture(intent)
            result = camera.trigger_prepared(prepared, deadline=deadline)
            frames = getattr(result, "frames", 0)
            planned = getattr(result, "planned", None)
            truncated = getattr(result, "detail", "") == "deadline"
            if planned is not None and frames != planned:
                if truncated:
                    log(
                        f'INFO phase="{_phase_label(window)}" stage=photo '
                        f"captured={frames}/{planned} truncated="
                        + (
                            "C3_safety"
                            if window.photo_phase == "totality"
                            else "phase_boundary"
                        )
                    )
                else:
                    log(
                        f'ERROR phase="{_phase_label(window)}" stage=photo '
                        f"captured={frames}/{planned}"
                    )
            exposure_text = "".join(
                f"[{value}]" for value in planned_speeds[:max(0, int(frames))]
            )
            photo_text = (
                f'phase="{_phase_label(window)}" PHOTO frames={frames}'
                + (f" {exposure_text}" if exposure_text else "")
            )
            log(
                f"TRIGGER_PHOTO {photo_log_level(window, started)} "
                f"{photo_text}"
            )
            return not truncated

        def log_next_capture(window: PhaseWindow, target: datetime) -> None:
            now = clock.now()
            level = photo_log_level(window, now)
            remaining_s = max(0, ceil((target - now).total_seconds()))
            log(
                f"TRIGGER_WAIT {level} "
                f"Next photo in {remaining_s} s"
            )

        def wait_until(target: datetime) -> None:
            remaining = (target - clock.now()).total_seconds()
            if remaining > 0:
                stopped.wait(min(0.25, remaining / clock.speed))

        override_window = PhaseWindow(
            "totality_override",
            "totality",
            datetime.min.replace(tzinfo=timezone.utc),
            datetime.max.replace(tzinfo=timezone.utc),
            0.0,
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
            next_capture_log=log_next_capture,
        ).run()
        log("INFO trigger sequence complete")
        return 0
    finally:
        stopped.set()
        if audio_thread is not None:
            audio_service.shutdown()
            audio_thread.join(timeout=2.0)
        if camera is not None:
            camera.close()


if __name__ == "__main__":
    raise SystemExit(main())
