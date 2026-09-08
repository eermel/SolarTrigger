"""End-to-end validation of one characterized camera profile.

The validation deliberately exercises the real execution-plan runtime, local
camera IPC, CameraWorker, CameraService and ProfilePlugin.  It does not use EXIF
metadata and therefore reports software dispatch timing, not physical shutter
opening time.
"""
from __future__ import annotations

from collections import deque
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import re
import statistics
import tempfile
import threading
import time
import uuid
from typing import Any

from backend.camera_profiles import validate_profile
from backend.camera_timing import load_camera_timing_document
from backend.camera_timing_contract import (
    bracket_photo_duration_ms,
    single_photo_duration_ms,
    validate_timing_contract_v3,
)
from backend.camera_worker_runtime import CameraWorkerRuntime, get_camera_worker_runtime
from backend.execution_plan_runtime import ExecutionPlanRuntime, load_execution_plan
from backend.rig_runtime import load_rig_configuration
from backend.trigger_runtime import RuntimeClock
from scripts.camera_ipc_client import CameraIpcClient


ROOT = Path(__file__).resolve().parents[1]
VALIDATION_RELATIVE_DIR = Path("configs/camera_characterization/validation")
PREPARED_TTL_S = 300.0
# CAMERA VALIDATION IVVQ V2
# Diagnostic spacing is not part of the production timing contract. It prevents
# one underestimated operation from making later validation commands expire, so
# every SET/PHOTO can be diagnosed independently in the same run.
VALIDATION_DIAGNOSTIC_GUARD_MS = 2000.0
# Validation-only FILE_ADDED observation grace. Production still uses the exact
# characterized PHOTO budget; a confirmation inside this grace is reported as a
# budget overrun/late confirmation rather than as a missing physical photo.
VALIDATION_CONFIRMATION_GRACE_MS = 1000.0


class CameraValidationError(RuntimeError):
    pass


class CameraValidationCancelled(CameraValidationError):
    pass


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_text(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    value = value.astimezone(timezone.utc)
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _parse_utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _normalize(value: Any) -> str:
    return " ".join(str(value or "").casefold().split())


def _finite_nonnegative(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CameraValidationError(f"{field} must be numeric")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise CameraValidationError(f"{field} must be finite and nonnegative")
    return result


def _speed_seconds(value: Any) -> float:
    text = str(value).strip()
    if "/" in text:
        numerator, denominator = text.split("/", 1)
        result = float(numerator) / float(denominator)
    else:
        result = float(text)
    if not math.isfinite(result) or result <= 0:
        raise ValueError(f"invalid shutter speed: {value!r}")
    return result


def _aperture_number(value: Any) -> float:
    text = str(value).strip().casefold().replace("f/", "").replace("f", "")
    return float(text)


def _atomic_json(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as handle:
            temporary = Path(handle.name)
            json.dump(document, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _safe_relative(path: Path, root: Path) -> str:
    return str(path.resolve().relative_to(root.resolve()))


def _camera_files_for_entry(entry: dict[str, Any], root: Path = ROOT) -> dict[str, Any]:
    root = Path(root)
    backend = str(entry.get("backend") or "").strip()
    if not backend.startswith("profile-"):
        raise CameraValidationError("camera has no characterized profile backend")

    slug = backend[len("profile-") :]
    if not re.fullmatch(r"[a-z0-9_-]+", slug):
        raise CameraValidationError("invalid camera profile backend")

    profile_path = root / "configs/camera_profiles" / f"{slug}.json"
    timing_path = root / "configs/camera_timing" / f"{slug}.json"

    try:
        profile = validate_profile(json.loads(profile_path.read_text(encoding="utf-8")))
    except FileNotFoundError as exc:
        raise CameraValidationError(f"camera profile missing: {profile_path.name}") from exc
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise CameraValidationError(f"invalid camera profile: {exc}") from exc

    try:
        timing = load_camera_timing_document(timing_path)
    except FileNotFoundError as exc:
        raise CameraValidationError(f"camera timing missing: {timing_path.name}") from exc
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise CameraValidationError(f"invalid camera timing: {exc}") from exc

    for key in ("backend", "manufacturer", "model"):
        if _normalize(profile.get(key)) != _normalize(timing.get(key)):
            raise CameraValidationError(f"profile/timing identity mismatch: {key}")

    if _normalize(profile.get("backend")) != _normalize(backend):
        raise CameraValidationError("inventory/profile backend mismatch")
    for key in ("manufacturer", "model"):
        if entry.get(key) and _normalize(profile.get(key)) != _normalize(entry.get(key)):
            raise CameraValidationError(f"inventory/profile identity mismatch: {key}")

    profile_contract = profile.get("timing_contract")
    timing_contract = timing.get("timing_contract")
    if not isinstance(profile_contract, dict) or profile_contract.get("version") != 3:
        raise CameraValidationError("camera profile is not timing contract v3")
    if not isinstance(timing_contract, dict) or timing_contract.get("version") != 3:
        raise CameraValidationError("camera timing is not timing contract v3")

    validate_timing_contract_v3(
        profile_contract,
        bracket_frames=[int(value) for value in profile.get("brackets", {})],
    )
    validate_timing_contract_v3(timing_contract)
    if profile_contract != timing_contract:
        raise CameraValidationError("profile/timing contract content differs")

    return {
        "profile": profile,
        "timing": timing,
        "profile_path": profile_path,
        "timing_path": timing_path,
        "slug": slug,
    }


def validation_candidates(
    inventory: dict[str, Any], root: Path = ROOT
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    ready: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for entry in inventory.get("camera", []) if isinstance(inventory, dict) else []:
        if not isinstance(entry, dict) or not entry.get("present") or not entry.get("pilotable"):
            continue
        try:
            bundle = _camera_files_for_entry(entry, root)
        except CameraValidationError as exc:
            rejected.append(
                {
                    "transport_locator": entry.get("transport_locator"),
                    "manufacturer": entry.get("manufacturer"),
                    "model": entry.get("model"),
                    "backend": entry.get("backend"),
                    "reason": str(exc),
                }
            )
            continue
        ready.append(
            {
                "transport_locator": entry.get("transport_locator"),
                "manufacturer": entry.get("manufacturer"),
                "model": entry.get("model"),
                "serial": entry.get("serial"),
                "fallback_physical_path": entry.get("fallback_physical_path"),
                "backend": entry.get("backend"),
                "display_label": entry.get("display_label") or entry.get("model"),
                "profile_file": bundle["profile_path"].name,
                "timing_file": bundle["timing_path"].name,
            }
        )
    ready.sort(key=lambda item: (str(item.get("display_label") or ""), str(item.get("serial") or "")))
    rejected.sort(key=lambda item: (str(item.get("model") or ""), str(item.get("backend") or "")))
    return ready, rejected


def _deduplicated_shutters(profile: dict[str, Any]) -> list[tuple[str, float]]:
    raw = profile["commands"]["shutter"].get("values", {})
    if not isinstance(raw, dict) or not raw:
        raise CameraValidationError("camera profile contains no shutter values")

    by_duration: dict[float, str] = {}
    for logical in sorted(raw, key=lambda value: (_speed_seconds(value), str(value))):
        seconds = _speed_seconds(logical)
        # Round only for duplicate grouping; the original logical value is retained.
        key = round(seconds, 12)
        by_duration.setdefault(key, str(logical))
    return sorted(((logical, duration) for duration, logical in by_duration.items()), key=lambda item: item[1])


def _closest_shutter(shutters: list[tuple[str, float]], target_s: float) -> str:
    return min(
        shutters,
        key=lambda item: (abs(math.log2(item[1] / target_s)), item[1], item[0]),
    )[0]


def _bracket_views(shutters: list[tuple[str, float]], frames: int) -> list[str]:
    if frames <= 1 or frames % 2 == 0:
        raise CameraValidationError("bracket size must be odd and > 1")

    candidates: list[tuple[float, list[str]]] = []
    durations = [(logical, seconds) for logical, seconds in shutters]
    for logical, start_s in durations:
        views = [logical]
        valid = True
        for step in range(1, frames):
            target_s = start_s * (2.0 ** step)
            best = min(
                durations,
                key=lambda item: abs(math.log2(item[1] / target_s)),
            )
            error_ev = abs(math.log2(best[1] / target_s))
            if error_ev > 0.12 or best[0] in views:
                valid = False
                break
            views.append(best[0])
        if not valid:
            continue
        centre_s = _speed_seconds(views[frames // 2])
        score = abs(math.log2(centre_s / (1.0 / 500.0)))
        candidates.append((score, views))

    if not candidates:
        raise CameraValidationError(
            f"cannot build a characterized 1 EV bracket of {frames} frames"
        )
    candidates.sort(key=lambda item: (item[0], [_speed_seconds(v) for v in item[1]], item[1]))
    return candidates[0][1]


def _choose_iso_pair(profile: dict[str, Any]) -> tuple[str, str]:
    values = profile["commands"]["iso"].get("values", {})
    if not isinstance(values, dict) or "100" not in values:
        raise CameraValidationError("ISO 100 is unavailable")
    numeric = sorted(
        (int(key), str(key))
        for key in values
        if str(key).isdigit() and int(key) > 0
    )
    alternate = next((text for number, text in numeric if number > 100), None)
    if alternate is None:
        alternate = next((text for number, text in numeric if text != "100"), None)
    if alternate is None:
        raise CameraValidationError("cannot build a real ISO transition")
    return "100", alternate


def _choose_aperture_pair(profile: dict[str, Any]) -> tuple[str, str] | None:
    spec = profile.get("commands", {}).get("aperture")
    if not isinstance(spec, dict) or spec.get("set") is False:
        return None
    values = spec.get("values")
    if not isinstance(values, dict) or len(values) < 2:
        return None
    ordered = sorted((str(key) for key in values), key=lambda item: (_aperture_number(item), item))
    baseline = min(ordered, key=lambda item: (abs(math.log2(_aperture_number(item) / 8.0)), _aperture_number(item)))
    baseline_n = _aperture_number(baseline)
    alternate = max(
        (item for item in ordered if item != baseline),
        key=lambda item: (abs(math.log2(_aperture_number(item) / baseline_n)), _aperture_number(item)),
    )
    return baseline, alternate


def _resolved_readback(profile: dict[str, Any], semantic: str, requested: Any) -> str:
    spec = profile["commands"][semantic]
    values = spec.get("values")
    if isinstance(values, dict) and str(requested) in values:
        return str(values[str(requested)])
    return str(requested)


def build_validation_recipe(profile: dict[str, Any]) -> dict[str, Any]:
    """Build a short deterministic relative execution plan recipe.

    One run validates four singles, every characterized native bracket size,
    real ISO/shutter/capture-mode transitions and aperture transitions where
    aperture SET was characterized.
    """
    profile = validate_profile(profile)
    contract = profile.get("timing_contract")
    if not isinstance(contract, dict) or contract.get("version") != 3:
        raise CameraValidationError("validation requires timing contract v3")

    set_ms = _finite_nonnegative(contract["set_overhead_ms"], "set_overhead_ms")
    single_overhead_ms = _finite_nonnegative(contract["single_overhead_ms"], "single_overhead_ms")
    bracket_overhead_ms = _finite_nonnegative(contract.get("bracket_overhead_ms", 0), "bracket_overhead_ms")
    bracket_inter_ms = _finite_nonnegative(contract.get("bracket_inter_image_ms", 0), "bracket_inter_image_ms")

    shutters = _deduplicated_shutters(profile)
    iso_base, iso_alt = _choose_iso_pair(profile)
    aperture_pair = _choose_aperture_pair(profile)
    aperture_base = aperture_pair[0] if aperture_pair else None
    aperture_alt = aperture_pair[1] if aperture_pair else None

    single_speeds: list[str] = []
    for target in (1 / 1000, 1 / 500, 1 / 250, 1 / 60):
        selected = _closest_shutter(shutters, target)
        if selected not in single_speeds:
            single_speeds.append(selected)
    while len(single_speeds) < 4:
        for logical, _seconds in shutters:
            if logical not in single_speeds:
                single_speeds.append(logical)
            if len(single_speeds) == 4:
                break
    if len(single_speeds) < 4:
        raise CameraValidationError("camera does not expose enough shutter values")

    has_capture_mode = (
        isinstance(profile.get("commands", {}).get("capture_mode"), dict)
        and profile["commands"]["capture_mode"].get("set") is not False
    )
    single_mode = (
        str(profile["commands"]["capture_mode"]["value"])
        if has_capture_mode
        else None
    )

    commands: list[dict[str, Any]] = []
    photo_id = 0

    def add_set(parameter: str, value: Any, semantic: str | None = None) -> None:
        commands.append(
            {
                "action": "SET",
                "duration_ms": set_ms,
                "params": {
                    "parameter": parameter,
                    "value": value,
                    "duration_ms": set_ms,
                    "timing_contract_version": 2,
                    "camera_timing_model_version": 3,
                    "validation_command_id": f"set-{len(commands):03d}",
                },
                "semantic": semantic,
            }
        )

    def add_photo(views: list[str], *, label: str) -> None:
        nonlocal photo_id
        photo_id += 1
        frames = len(views)
        exposure_s = [_speed_seconds(value) for value in views]
        if frames == 1:
            duration_ms = single_photo_duration_ms(single_overhead_ms, exposure_s[0])
        else:
            duration_ms = bracket_photo_duration_ms(
                bracket_overhead_ms,
                bracket_inter_ms,
                exposure_s,
            )
        centre = views[frames // 2]
        commands.append(
            {
                "action": "PHOTO",
                "duration_ms": float(duration_ms),
                "frames": frames,
                "params": {
                    "shutter": centre,
                    "centre": centre,
                    "frames": frames,
                    "expected_frames": frames,
                    "physical_views": list(views),
                    "duration_ms": float(duration_ms),
                    "timing_contract_version": 2,
                    "camera_timing_model_version": 3,
                    "validation_photo_id": f"photo-{photo_id:02d}-{label}",
                    "validation_confirmation_grace_ms": VALIDATION_CONFIRMATION_GRACE_MS,
                },
            }
        )

    if aperture_base is not None:
        add_set("f-number", aperture_base, "aperture")

    # Four deterministic singles, alternating ISO and aperture where possible.
    for index, shutter in enumerate(single_speeds):
        iso = iso_base if index % 2 == 0 else iso_alt
        add_set("iso", iso, "iso")
        if has_capture_mode:
            add_set("capturemode", single_mode, "capture_mode")
        add_set("shutterspeed", shutter, "shutter")
        if aperture_alt is not None and index == 1:
            add_set("f-number", aperture_alt, "aperture")
        if aperture_base is not None and index == 3:
            add_set("f-number", aperture_base, "aperture")
        add_photo([shutter], label=f"single-{index + 1}")

    supported_brackets = (
        [
            int(value)
            for value in contract.get("supported_bracket_frames", [])
        ]
        if profile["strategy"] == "bracket"
        else []
    )
    supported_brackets = sorted(set(supported_brackets))
    for index, frames in enumerate(supported_brackets):
        spec = profile.get("brackets", {}).get(str(frames))
        if not isinstance(spec, dict):
            raise CameraValidationError(f"profile missing bracket {frames}")
        views = _bracket_views(shutters, frames)
        iso = iso_alt if index % 2 == 0 else iso_base
        add_set("iso", iso, "iso")
        if has_capture_mode:
            add_set("capturemode", single_mode, "capture_mode")
        add_set("shutterspeed", views[frames // 2], "shutter")
        if has_capture_mode:
            add_set("capturemode", str(spec["mode"]), "capture_mode")
        add_photo(views, label=f"bracket-{frames}")

    # Deterministic final state, itself exercised through scheduled SETs.
    final_shutter = single_speeds[1]
    if has_capture_mode:
        add_set("capturemode", single_mode, "capture_mode")
    add_set("iso", iso_base, "iso")
    add_set("shutterspeed", final_shutter, "shutter")
    if aperture_base is not None:
        add_set("f-number", aperture_base, "aperture")

    offset_ms = 0.0
    for index, command in enumerate(commands):
        command["index"] = index
        command["offset_ms"] = offset_ms
        command["diagnostic_guard_ms"] = (
            VALIDATION_DIAGNOSTIC_GUARD_MS
            if index < len(commands) - 1
            else 0.0
        )
        offset_ms += float(command["duration_ms"])
        offset_ms += float(command["diagnostic_guard_ms"])

    expected_photos = sum(
        int(command.get("frames", 0))
        for command in commands
        if command["action"] == "PHOTO"
    )
    photo_commands = sum(1 for command in commands if command["action"] == "PHOTO")
    set_commands = sum(1 for command in commands if command["action"] == "SET")

    invariant_count = sum(
        1
        for key in ("manual_mode", "capture_target", "raw", "capture_mode", "self_timer", "time_lapse")
        if isinstance(profile.get("commands", {}).get(key), dict)
    )
    preflight_reserve_s = max(5.0, invariant_count * set_ms / 1000.0 + 2.0)

    final_state: dict[str, dict[str, str]] = {
        "iso": {
            "requested": iso_base,
            "expected_readback": _resolved_readback(profile, "iso", iso_base),
        },
        "shutterspeed": {
            "requested": final_shutter,
            "expected_readback": _resolved_readback(profile, "shutter", final_shutter),
        },
    }
    if has_capture_mode:
        final_state["capturemode"] = {
            "requested": single_mode,
            "expected_readback": str(single_mode),
        }
    if aperture_base is not None:
        final_state["f-number"] = {
            "requested": aperture_base,
            "expected_readback": _resolved_readback(profile, "aperture", aperture_base),
        }

    invariants: dict[str, dict[str, str]] = {}
    for semantic, parameter in (
        ("manual_mode", "manual_mode"),
        ("capture_target", "capture_target"),
        ("raw", "raw"),
        ("capture_mode", "capturemode"),
    ):
        if parameter in final_state:
            continue
        spec = profile.get("commands", {}).get(semantic)
        if isinstance(spec, dict) and "value" in spec:
            invariants[parameter] = {
                "requested": str(spec["value"]),
                "expected_readback": str(spec["value"]),
            }

    return {
        "schema_version": 1,
        "config_type": "camera_validation_recipe",
        "backend": profile["backend"],
        "manufacturer": profile["manufacturer"],
        "model": profile["model"],
        "strategy": profile["strategy"],
        "timing_contract_version": 3,
        "commands": commands,
        "expected_photos": expected_photos,
        "photo_command_count": photo_commands,
        "set_command_count": set_commands,
        "supported_bracket_frames": supported_brackets,
        "sequence_duration_s": offset_ms / 1000.0,
        "preflight_reserve_s": preflight_reserve_s,
        "estimated_duration_s": preflight_reserve_s + offset_ms / 1000.0,
        "set_overhead_ms": set_ms,
        "validation_diagnostic_guard_ms": VALIDATION_DIAGNOSTIC_GUARD_MS,
        "validation_confirmation_grace_ms": VALIDATION_CONFIRMATION_GRACE_MS,
        "final_state": final_state,
        "invariants": invariants,
    }


def materialize_validation_plan(
    recipe: dict[str, Any],
    *,
    rig_id: int,
    first_command_utc: datetime,
    profile_filename: str,
    timing_filename: str,
) -> tuple[dict[str, Any], str]:
    if not isinstance(rig_id, int) or isinstance(rig_id, bool) or not 1 <= rig_id <= 4:
        raise CameraValidationError("rig_id must be in 1..4")
    if first_command_utc.tzinfo is None:
        first_command_utc = first_command_utc.replace(tzinfo=timezone.utc)
    first_command_utc = first_command_utc.astimezone(timezone.utc)

    commands = []
    phases = []
    for item in recipe["commands"]:
        target = first_command_utc + timedelta(milliseconds=float(item["offset_ms"]))
        params = deepcopy(item["params"])
        if item["action"] == "PHOTO":
            params["validation_target_utc"] = _utc_text(target)
        commands.append(
            {
                "time_utc": _utc_text(target),
                "rig_id": rig_id,
                "action": item["action"],
                "params": params,
            }
        )
        phases.append("camera_validation")

    sequence_start = first_command_utc
    sequence_end = first_command_utc + timedelta(seconds=float(recipe["sequence_duration_s"]))
    plan = {
        "schema_version": 2,
        "config_type": "execution_plan",
        "sources": {
            "camera_profile": profile_filename,
            "camera_timing_file": timing_filename,
            "validation_recipe": "camera_validation_v1",
        },
        "sequence_start_utc": _utc_text(sequence_start),
        "sequence_end_utc": _utc_text(sequence_end),
        "initial_state_required": {str(rig_id): {}},
        "commands": commands,
        "command_phases": phases,
    }

    lines = [
        "# =============================================================================",
        "# SolarTrigger Camera Validation Plan",
        "# =============================================================================",
        "# plan.format_version=1",
        f"# plan.generated_at_utc={json.dumps(_utc_text(_utc_now()))}",
        '# plan.time_reference="UTC"',
        f"# plan.rig_id={rig_id}",
        f"# plan.sequence_start_utc={json.dumps(plan['sequence_start_utc'])}",
        f"# plan.sequence_end_utc={json.dumps(plan['sequence_end_utc'])}",
        f"# source.camera_profile={json.dumps(profile_filename)}",
        f"# source.camera_timing_file={json.dumps(timing_filename)}",
        '# source.validation_recipe="camera_validation_v1"',
        "# initial_state={}",
        '# @phase="camera_validation"',
    ]
    for command in commands:
        lines.append(
            f"{command['time_utc']} | RIG{rig_id} | {command['action']} | "
            + json.dumps(command["params"], ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        )
    lines.append("")
    return plan, "\n".join(lines)


def _same_physical_camera(left: dict[str, Any], right: dict[str, Any]) -> bool:
    for key in ("serial", "fallback_physical_path"):
        a = str(left.get(key) or "").strip()
        b = str(right.get(key) or "").strip()
        if a and b:
            return a == b
    return False


def _bound_rig_for_camera(entry: dict[str, Any], config: dict[str, Any]) -> int | None:
    matches = []
    for rig in config.get("rigs", []) if isinstance(config, dict) else []:
        if not isinstance(rig, dict):
            continue
        devices = rig.get("devices")
        camera = devices.get("camera") if isinstance(devices, dict) else None
        if not isinstance(camera, dict):
            continue
        if _same_physical_camera(entry, camera):
            rig_id = rig.get("rig_id")
            if isinstance(rig_id, int) and not isinstance(rig_id, bool) and 1 <= rig_id <= 4:
                matches.append(rig_id)
    matches = sorted(set(matches))
    if len(matches) > 1:
        raise CameraValidationError(
            "same physical camera is configured on multiple RIGs: "
            + ", ".join(map(str, matches))
        )
    return matches[0] if matches else None


class RecordingCameraClient:
    def __init__(self, delegate: CameraIpcClient, *, set_budget_ms: float):
        self.delegate = delegate
        self.set_budget_ms = float(set_budget_ms)
        self.lock = threading.Lock()
        self.set_events: list[dict[str, Any]] = []
        self.photo_events: list[dict[str, Any]] = []
        self.preflight_event: dict[str, Any] | None = None
        self.get_events: list[dict[str, Any]] = []

    @staticmethod
    def _error(exc: BaseException) -> dict[str, str]:
        return {
            "code": str(getattr(exc, "code", type(exc).__name__)),
            "message": str(getattr(exc, "message", str(exc))),
        }

    def preflight(self, rig_id: int, required_state=None, **kwargs):
        started = _utc_now()
        mono = time.monotonic()
        try:
            result = self.delegate.preflight(rig_id, required_state, **kwargs)
        except Exception as exc:
            event = {
                "status": "error",
                "started_utc": _utc_text(started),
                "ended_utc": _utc_text(_utc_now()),
                "duration_ms": (time.monotonic() - mono) * 1000.0,
                "error": self._error(exc),
            }
            with self.lock:
                self.preflight_event = event
            raise
        event = {
            "status": "success",
            "started_utc": _utc_text(started),
            "ended_utc": _utc_text(_utc_now()),
            "duration_ms": (time.monotonic() - mono) * 1000.0,
            "result": deepcopy(result),
        }
        with self.lock:
            self.preflight_event = event
        return result

    def set_parameter(self, rig_id, parameter, value, *, fallback_parameter=None, **kwargs):
        started = _utc_now()
        mono = time.monotonic()
        event = {
            "parameter": parameter,
            "value": deepcopy(value),
            "fallback_parameter": fallback_parameter,
            "started_utc": _utc_text(started),
            "budget_ms": self.set_budget_ms,
        }
        try:
            result = self.delegate.set_parameter(
                rig_id,
                parameter,
                value,
                fallback_parameter=fallback_parameter,
                **kwargs,
            )
        except Exception as exc:
            event.update(
                status="error",
                ended_utc=_utc_text(_utc_now()),
                duration_ms=(time.monotonic() - mono) * 1000.0,
                error=self._error(exc),
            )
            with self.lock:
                self.set_events.append(event)
            raise
        event.update(
            status="success",
            ended_utc=_utc_text(_utc_now()),
            duration_ms=(time.monotonic() - mono) * 1000.0,
            result=deepcopy(result),
        )
        with self.lock:
            self.set_events.append(event)
        return result

    def execute_photo(self, rig_id, params, **kwargs):
        target_text = params.get("validation_target_utc")
        started = _utc_now()
        mono = time.monotonic()
        target = _parse_utc(target_text) if target_text else None
        event = {
            "validation_photo_id": params.get("validation_photo_id"),
            "expected_frames": int(params.get("expected_frames", params.get("frames", 1)) or 0),
            "target_utc": target_text,
            "dispatch_utc": _utc_text(started),
            "budget_ms": float(params.get("duration_ms", 0) or 0),
            "physical_views": deepcopy(params.get("physical_views") or []),
        }
        if target is not None:
            dispatch_error_ms = (started - target).total_seconds() * 1000.0
            event["dispatch_error_ms"] = dispatch_error_ms
            event["timing_direction"] = (
                "EARLY_TRIGGER"
                if dispatch_error_ms < 0
                else "LATE_TRIGGER"
                if dispatch_error_ms > 0
                else "ON_TIME"
            )
        try:
            result = self.delegate.execute_photo(rig_id, params, **kwargs)
        except Exception as exc:
            error = self._error(exc)
            event.update(
                status="error",
                completion_utc=_utc_text(_utc_now()),
                duration_ms=(time.monotonic() - mono) * 1000.0,
                error=error,
            )
            match = re.search(r"(\d+)\s*/\s*(\d+)", error["message"])
            if error["code"] == "CAPTURE_COUNT_ERROR" and match:
                event["confirmed_frames"] = int(match.group(1))
                event["reported_expected_frames"] = int(match.group(2))
            else:
                event["confirmed_frames"] = None
            with self.lock:
                self.photo_events.append(event)
            raise
        frames = None
        if isinstance(result, dict):
            raw_frames = result.get("frames")
            if isinstance(raw_frames, int) and not isinstance(raw_frames, bool):
                frames = raw_frames
        event.update(
            status="success",
            completion_utc=_utc_text(_utc_now()),
            duration_ms=(time.monotonic() - mono) * 1000.0,
            confirmed_frames=frames,
            result=deepcopy(result),
        )
        with self.lock:
            self.photo_events.append(event)
        return result

    def get_parameter(self, rig_id, parameter, **kwargs):
        started = _utc_now()
        mono = time.monotonic()
        try:
            result = self.delegate.get_parameter(rig_id, parameter, **kwargs)
        except Exception as exc:
            with self.lock:
                self.get_events.append(
                    {
                        "parameter": parameter,
                        "status": "error",
                        "started_utc": _utc_text(started),
                        "duration_ms": (time.monotonic() - mono) * 1000.0,
                        "error": self._error(exc),
                    }
                )
            raise
        with self.lock:
            self.get_events.append(
                {
                    "parameter": parameter,
                    "status": "success",
                    "started_utc": _utc_text(started),
                    "duration_ms": (time.monotonic() - mono) * 1000.0,
                    "value": deepcopy(result),
                }
            )
        return result

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "preflight": deepcopy(self.preflight_event),
                "sets": deepcopy(self.set_events),
                "photos": deepcopy(self.photo_events),
                "gets": deepcopy(self.get_events),
            }


def _classify_transport_error(error: dict[str, Any], *, operation: str) -> str:
    code = str((error or {}).get("code") or "")
    if code in {"TIMEOUT", "EXPIRED"}:
        return "CAPTURE_TIMEOUT" if operation == "PHOTO" else "SET_ERROR"
    if code in {"BUSY"}:
        return "CAMERA_BUSY"
    if code in {"IPC_UNAVAILABLE"}:
        return "CAMERA_DISCONNECTED"
    if code == "CAPTURE_COUNT_ERROR":
        return "BRACKET_COUNT_ERROR"
    if code in {"PREFLIGHT_FAILED"}:
        return "READBACK_ERROR"
    if code == "INTERNAL_ERROR":
        return "INTERNAL_ERROR"
    return "USB_ERROR" if operation in {"PHOTO", "SET"} else "INTERNAL_ERROR"


def _timing_statistics(photo_events: list[dict[str, Any]]) -> dict[str, Any]:
    samples = [
        float(event["dispatch_error_ms"])
        for event in photo_events
        if isinstance(event.get("dispatch_error_ms"), (int, float))
        and not isinstance(event.get("dispatch_error_ms"), bool)
        and math.isfinite(float(event["dispatch_error_ms"]))
    ]
    if not samples:
        return {
            "measurement": "software_dispatch_start_vs_plan_target",
            "physical_shutter_time_measured": False,
            "count": 0,
            "samples_ms": [],
        }
    abs_samples = [abs(value) for value in samples]
    max_abs_index = max(range(len(samples)), key=lambda index: abs_samples[index])
    return {
        "measurement": "software_dispatch_start_vs_plan_target",
        "physical_shutter_time_measured": False,
        "threshold_ms": None,
        "count": len(samples),
        "samples_ms": samples,
        "mean_ms": statistics.fmean(samples),
        "stddev_ms": statistics.pstdev(samples),
        "min_ms": min(samples),
        "max_ms": max(samples),
        "max_abs_ms": abs_samples[max_abs_index],
        "max_abs_signed_ms": samples[max_abs_index],
        "early_count": sum(1 for value in samples if value < 0),
        "late_count": sum(1 for value in samples if value > 0),
        "exact_count": sum(1 for value in samples if value == 0),
    }


def analyse_validation(
    *,
    recipe: dict[str, Any],
    recording: dict[str, Any],
    runtime_logs: list[str],
    readbacks: list[dict[str, Any]],
    operator_outcome: str | None,
    fatal_error: dict[str, Any] | None = None,
    cancelled: bool = False,
) -> dict[str, Any]:
    errors: list[dict[str, Any]] = []
    expected = int(recipe["expected_photos"])
    photos = list(recording.get("photos") or [])
    sets = list(recording.get("sets") or [])

    confirmed_known = 0
    actual_count_complete = True
    for event in photos:
        confirmed = event.get("confirmed_frames")
        if isinstance(confirmed, int) and not isinstance(confirmed, bool):
            confirmed_known += confirmed
        else:
            actual_count_complete = False

        if event.get("status") == "error":
            kind = _classify_transport_error(event.get("error") or {}, operation="PHOTO")
            errors.append(
                {
                    "type": kind,
                    "severity": "FAIL",
                    "photo_id": event.get("validation_photo_id"),
                    "raw": deepcopy(event.get("error")),
                }
            )
            expected_frames = int(event.get("expected_frames") or 0)
            if kind == "BRACKET_COUNT_ERROR" and expected_frames <= 1:
                errors[-1]["type"] = "MISSING_PHOTO"

    if actual_count_complete and confirmed_known < expected:
        errors.append(
            {
                "type": "MISSING_PHOTO",
                "severity": "FAIL",
                "expected": expected,
                "confirmed": confirmed_known,
                "missing": expected - confirmed_known,
            }
        )
    elif not actual_count_complete:
        errors.append(
            {
                "type": "MISSING_PHOTO",
                "severity": "FAIL",
                "expected": expected,
                "confirmed_lower_bound": confirmed_known,
                "actual_count_complete": False,
                "message": "physical frame count is unknowable after one or more transport failures",
            }
        )

    planned_photo_commands = int(recipe["photo_command_count"])
    if len(photos) < planned_photo_commands:
        errors.append(
            {
                "type": "UNEXPECTED_EVENT",
                "severity": "FAIL",
                "planned_photo_commands": planned_photo_commands,
                "attempted_photo_commands": len(photos),
                "message": "one or more planned PHOTO commands were never dispatched",
            }
        )

    planned_set_commands = int(recipe["set_command_count"])
    if len(sets) < planned_set_commands:
        errors.append(
            {
                "type": "SET_ERROR",
                "severity": "FAIL",
                "planned_set_commands": planned_set_commands,
                "set_attempts": len(sets),
                "message": "one or more planned SET commands were never attempted",
            }
        )

    for event in sets:
        if event.get("status") == "error":
            errors.append(
                {
                    "type": _classify_transport_error(event.get("error") or {}, operation="SET"),
                    "severity": "FAIL",
                    "parameter": event.get("parameter"),
                    "value": event.get("value"),
                    "raw": deepcopy(event.get("error")),
                }
            )

    for item in readbacks:
        if item.get("status") != "success" or not item.get("matches"):
            errors.append(
                {
                    "type": "READBACK_ERROR",
                    "severity": "FAIL",
                    "parameter": item.get("parameter"),
                    "expected": item.get("expected"),
                    "actual": item.get("actual"),
                    "raw": deepcopy(item.get("error")),
                }
            )

    budget_overruns = []
    for operation, events in (("SET", sets), ("PHOTO", photos)):
        for event in events:
            duration = event.get("duration_ms")
            budget = event.get("budget_ms")
            if (
                isinstance(duration, (int, float))
                and not isinstance(duration, bool)
                and isinstance(budget, (int, float))
                and not isinstance(budget, bool)
                and float(duration) > float(budget)
            ):
                budget_overruns.append(
                    {
                        "operation": operation,
                        "duration_ms": float(duration),
                        "budget_ms": float(budget),
                        "overrun_ms": float(duration) - float(budget),
                        "parameter": event.get("parameter"),
                        "photo_id": event.get("validation_photo_id"),
                    }
                )
    if budget_overruns:
        errors.append(
            {
                "type": "BUDGET_OVERRUN",
                "severity": "WARNING",
                "count": len(budget_overruns),
                "items": budget_overruns,
            }
        )

    late_confirmations = [
        {
            "photo_id": event.get("validation_photo_id"),
            "duration_ms": event.get("duration_ms"),
            "budget_ms": event.get("budget_ms"),
            "confirmed_frames": event.get("confirmed_frames"),
        }
        for event in photos
        if isinstance(event.get("result"), dict)
        and "validation confirmation after budget"
        in str(event["result"].get("detail") or "")
    ]
    if late_confirmations:
        errors.append(
            {
                "type": "LATE_FILE_CONFIRMATION",
                "severity": "WARNING",
                "count": len(late_confirmations),
                "items": late_confirmations,
                "message": (
                    "camera file event arrived after the characterized PHOTO "
                    "budget but inside the validation-only observation grace"
                ),
            }
        )

    skip_lines = [
        line for line in runtime_logs
        if "skip_past index=" in line or "skip_elapsed_after_recovery index=" in line
    ]
    if skip_lines:
        errors.append(
            {
                "type": "UNEXPECTED_EVENT",
                "severity": "FAIL",
                "message": "execution-plan command skipped because its absolute slot elapsed",
                "raw_lines": skip_lines,
            }
        )

    if fatal_error is not None:
        errors.append(
            {
                "type": _classify_transport_error(fatal_error, operation="RUNTIME"),
                "severity": "FAIL",
                "raw": deepcopy(fatal_error),
            }
        )
    if cancelled:
        errors.append({"type": "CANCELLED", "severity": "FAIL"})

    if operator_outcome == "extra":
        errors.append(
            {
                "type": "EXTRA_PHOTO",
                "severity": "WARNING",
                "message": "operator reports one or more extra photos on the card",
            }
        )
    elif operator_outcome == "incorrect":
        errors.append(
            {
                "type": "OPERATOR_REJECTED",
                "severity": "FAIL",
                "message": "operator reports missing or incorrect photos",
            }
        )
    elif operator_outcome not in {"ok", None}:
        errors.append(
            {
                "type": "OPERATOR_CONFIRMATION_INVALID",
                "severity": "FAIL",
                "value": operator_outcome,
            }
        )

    verdict = "PASS"
    if any(item.get("severity") == "FAIL" for item in errors):
        verdict = "FAIL"
    elif any(item.get("severity") == "WARNING" for item in errors):
        verdict = "WARNING"

    return {
        "verdict": verdict,
        "expected_photos": expected,
        "confirmed_photos": confirmed_known,
        "actual_count_complete": actual_count_complete,
        "planned_photo_commands": planned_photo_commands,
        "attempted_photo_commands": len(photos),
        "planned_set_commands": planned_set_commands,
        "set_attempts": len(sets),
        "set_successes": sum(1 for item in sets if item.get("status") == "success"),
        "set_failures": sum(1 for item in sets if item.get("status") == "error"),
        "operator_outcome": operator_outcome,
        "timing": _timing_statistics(photos),
        "errors": errors,
        "readbacks": readbacks,
        "recording": recording,
    }


class CameraValidationJob:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.condition = threading.Condition(self.lock)
        self.logs = deque(maxlen=4000)
        self.running = False
        self.cancel_event = threading.Event()
        self.job_id: str | None = None
        self.phase = "idle"
        self.question: dict[str, Any] | None = None
        self.answer: str | None = None
        self.result: dict[str, Any] | None = None
        self.prepared: dict[str, Any] | None = None
        self.log_path: Path | None = None
        self.report_path: Path | None = None

    def log(self, message: Any) -> None:
        line = f"{_utc_now().isoformat(timespec='milliseconds')} {message}"
        with self.lock:
            self.logs.append(line)
            path = self.log_path
        if path is not None:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
                    handle.flush()
            except OSError:
                pass

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            prepared_public = None
            if self.prepared is not None:
                prepared_public = {
                    key: deepcopy(self.prepared[key])
                    for key in (
                        "token",
                        "prepared_at_utc",
                        "expected_photos",
                        "photo_command_count",
                        "set_command_count",
                        "supported_bracket_frames",
                        "sequence_duration_s",
                        "preflight_reserve_s",
                        "estimated_duration_s",
                        "camera",
                    )
                    if key in self.prepared
                }
            return {
                "running": self.running,
                "job_id": self.job_id,
                "phase": self.phase,
                "logs": list(self.logs),
                "question": deepcopy(self.question),
                "result": deepcopy(self.result),
                "prepared": prepared_public,
            }

    def prepare(self, entry: dict[str, Any], root: Path = ROOT) -> dict[str, Any]:
        root = Path(root)
        bundle = _camera_files_for_entry(entry, root)
        recipe = build_validation_recipe(bundle["profile"])
        token = uuid.uuid4().hex
        prepared_at = _utc_now()
        public = {
            "token": token,
            "prepared_at_utc": _utc_text(prepared_at),
            "expected_photos": recipe["expected_photos"],
            "photo_command_count": recipe["photo_command_count"],
            "set_command_count": recipe["set_command_count"],
            "supported_bracket_frames": recipe["supported_bracket_frames"],
            "sequence_duration_s": recipe["sequence_duration_s"],
            "preflight_reserve_s": recipe["preflight_reserve_s"],
            "estimated_duration_s": recipe["estimated_duration_s"],
            "camera": {
                "manufacturer": entry.get("manufacturer"),
                "model": entry.get("model"),
                "serial": entry.get("serial"),
                "backend": entry.get("backend"),
            },
        }
        with self.lock:
            if self.running:
                raise CameraValidationError("camera validation already running")
            self.prepared = {
                **public,
                "prepared_monotonic": time.monotonic(),
                "entry": deepcopy(entry),
                "recipe": recipe,
                "profile_path": bundle["profile_path"],
                "timing_path": bundle["timing_path"],
                "slug": bundle["slug"],
            }
            self.phase = "prepared"
            self.result = None
            self.question = None
            self.logs.clear()
        return public

    def start(self, token: str, root: Path = ROOT) -> None:
        with self.lock:
            if self.running:
                raise CameraValidationError("camera validation already running")
            if not isinstance(token, str) or not token or self.prepared is None:
                raise CameraValidationError("prepare camera validation first")
            if token != self.prepared.get("token"):
                raise CameraValidationError("stale validation authorization")
            if time.monotonic() - float(self.prepared["prepared_monotonic"]) > PREPARED_TTL_S:
                self.prepared = None
                self.phase = "idle"
                raise CameraValidationError("validation authorization expired; prepare again")

            prepared = self.prepared
            self.prepared = None
            self.running = True
            self.cancel_event.clear()
            self.job_id = uuid.uuid4().hex
            self.phase = "starting"
            self.question = None
            self.answer = None
            self.result = None
            self.logs.clear()
            thread = threading.Thread(
                target=self._run,
                args=(prepared, Path(root)),
                daemon=True,
                name=f"camera-validation-{self.job_id[:8]}",
            )
            thread.start()

    def cancel(self) -> None:
        self.cancel_event.set()
        with self.condition:
            self.condition.notify_all()

    def respond(self, question_id: str, outcome: str) -> None:
        if outcome not in {"ok", "extra", "incorrect"}:
            raise ValueError("invalid operator outcome")
        with self.condition:
            if not self.question or question_id != self.question.get("id"):
                raise ValueError("stale or invalid operator confirmation")
            self.answer = outcome
            self.condition.notify_all()

    def _ask_operator(self, automatic: dict[str, Any]) -> str:
        with self.condition:
            self.phase = "operator_confirmation"
            self.question = {
                "id": uuid.uuid4().hex,
                "kind": "final",
                "message": (
                    f"Automatic validation completed: "
                    f"{automatic['confirmed_photos']}/{automatic['expected_photos']} "
                    "photos confirmed by software. Check the card: "
                    "are all expected photos present and correct?"
                ),
            }
            self.answer = None
            self.log(
                "AUTOMATIC VALIDATION COMPLETED - WAITING FOR OPERATOR CONFIRMATION "
                f"confirmed={automatic['confirmed_photos']}/{automatic['expected_photos']}"
            )
            deadline = time.monotonic() + 600.0
            while self.answer is None:
                if self.cancel_event.is_set():
                    raise CameraValidationCancelled("validation cancelled during operator confirmation")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise CameraValidationError("operator confirmation timed out")
                self.condition.wait(min(1.0, remaining))
            outcome = self.answer
            self.question = None
            self.answer = None
            return outcome

    @staticmethod
    def _exception_payload(exc: BaseException) -> dict[str, str]:
        return {
            "code": str(getattr(exc, "code", type(exc).__name__)),
            "message": str(getattr(exc, "message", str(exc))),
        }

    def _read_final_state(
        self,
        client: CameraIpcClient,
        rig_id: int,
        recipe: dict[str, Any],
    ) -> list[dict[str, Any]]:
        checks = {**recipe.get("invariants", {}), **recipe.get("final_state", {})}
        result = []
        for parameter, spec in checks.items():
            try:
                actual = client.get_parameter(rig_id, parameter, timeout_s=10.0)
            except Exception as exc:
                result.append(
                    {
                        "parameter": parameter,
                        "status": "error",
                        "expected": spec.get("expected_readback"),
                        "actual": None,
                        "matches": False,
                        "error": self._exception_payload(exc),
                    }
                )
                continue
            expected = str(spec.get("expected_readback"))
            result.append(
                {
                    "parameter": parameter,
                    "status": "success",
                    "requested": spec.get("requested"),
                    "expected": expected,
                    "actual": str(actual),
                    "matches": str(actual) == expected,
                }
            )
        return result

    def _open_runtime(self, entry: dict[str, Any]):
        config = load_rig_configuration()
        bound_rig = _bound_rig_for_camera(entry, config)
        if bound_rig is not None:
            runtime = get_camera_worker_runtime(log_fn=self.log)
            runtime.reconcile(config)
            session = runtime.open_ipc_session([bound_rig])
            return runtime, session, bound_rig, False, "configured_rig"

        if not str(entry.get("serial") or "").strip():
            raise CameraValidationError(
                "unbound camera validation requires a stable USB serial; "
                "bind this camera to a RIG first"
            )

        temp_runtime = CameraWorkerRuntime(log_fn=self.log)
        temp_config = {
            "rigs": [
                {
                    "rig_id": 1,
                    "devices": {"camera": deepcopy(entry)},
                }
            ]
        }
        temp_runtime.reconcile(temp_config)
        session = temp_runtime.open_ipc_session([1])
        return temp_runtime, session, 1, True, "temporary_rig"

    def _run(self, prepared: dict[str, Any], root: Path) -> None:
        runtime_owner = None
        session = None
        owns_runtime = False
        recorder: RecordingCameraClient | None = None
        readbacks: list[dict[str, Any]] = []
        fatal_error: dict[str, Any] | None = None
        operator_outcome: str | None = None
        plan_path: Path | None = None
        automatic: dict[str, Any] | None = None

        entry = deepcopy(prepared["entry"])
        recipe = deepcopy(prepared["recipe"])
        run_id = self.job_id or uuid.uuid4().hex
        stamp = _utc_now().strftime("%Y%m%dT%H%M%SZ")
        run_dir = root / VALIDATION_RELATIVE_DIR / f"{stamp}_{prepared['slug']}_{run_id[:8]}"
        run_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = run_dir / "run.log"
        self.report_path = run_dir / "report.json"

        try:
            self.phase = "preflight"
            self.log(
                f"VALIDATION START camera={entry.get('manufacturer')} {entry.get('model')} "
                f"expected_photos={recipe['expected_photos']}"
            )
            if self.cancel_event.is_set():
                raise CameraValidationCancelled("validation cancelled before worker startup")

            runtime_owner, session, rig_id, owns_runtime, ownership = self._open_runtime(entry)
            self.log(f"CAMERA WORKER ownership={ownership} rig={rig_id}")
            delegate = CameraIpcClient(session.socket_path, session.session_id, log_fn=self.log)
            delegate.ping()
            recorder = RecordingCameraClient(
                delegate,
                set_budget_ms=float(recipe["set_overhead_ms"]),
            )

            first_command = _utc_now() + timedelta(seconds=float(recipe["preflight_reserve_s"]))
            _plan_document, plan_text = materialize_validation_plan(
                recipe,
                rig_id=rig_id,
                first_command_utc=first_command,
                profile_filename=prepared["profile_path"].name,
                timing_filename=prepared["timing_path"].name,
            )
            plan_path = run_dir / "validation.plan"
            plan_path.write_text(plan_text, encoding="utf-8")

            # Re-parse the exact text file that will be executed.  This is part
            # of the validation: no special in-memory plan bypass exists.
            plan = load_execution_plan(plan_path)
            execution = ExecutionPlanRuntime(
                clock=RuntimeClock(),
                camera_client=recorder,
                log_fn=self.log,
                stop_event=self.cancel_event,
            )
            execution.prepare_for_execution(plan)
            if self.cancel_event.is_set():
                raise CameraValidationCancelled("validation cancelled after preflight")

            self.phase = "running"
            execution.run(plan)
            if self.cancel_event.is_set():
                raise CameraValidationCancelled("validation cancelled")

            self.phase = "readback"
            readbacks = self._read_final_state(delegate, rig_id, recipe)

        except CameraValidationCancelled as exc:
            fatal_error = self._exception_payload(exc)
            self.log(f"CANCELLED: {exc}")
        except Exception as exc:
            fatal_error = self._exception_payload(exc)
            self.log(f"FAILED: {type(exc).__name__}: {exc}")
        finally:
            if runtime_owner is not None and session is not None:
                try:
                    runtime_owner.close_ipc_session(session.session_id)
                except Exception as exc:
                    self.log(f"IPC close warning: {exc}")
            if owns_runtime and runtime_owner is not None:
                try:
                    runtime_owner.shutdown()
                except Exception as exc:
                    self.log(f"worker shutdown warning: {exc}")

        recording = recorder.snapshot() if recorder is not None else {
            "preflight": None,
            "sets": [],
            "photos": [],
            "gets": [],
        }
        runtime_logs = list(self.logs)
        automatic = analyse_validation(
            recipe=recipe,
            recording=recording,
            runtime_logs=runtime_logs,
            readbacks=readbacks,
            operator_outcome=None,
            fatal_error=fatal_error,
            cancelled=self.cancel_event.is_set(),
        )

        # Ask the operator only after a real capture run reached at least one
        # PHOTO dispatch.  Preflight/startup failures are already conclusive.
        if recording.get("photos") and not self.cancel_event.is_set():
            try:
                operator_outcome = self._ask_operator(automatic)
            except CameraValidationCancelled as exc:
                fatal_error = self._exception_payload(exc)
                self.log(f"CANCELLED: {exc}")
            except Exception as exc:
                fatal_error = self._exception_payload(exc)
                self.log(f"OPERATOR CONFIRMATION FAILED: {exc}")

        analysis = analyse_validation(
            recipe=recipe,
            recording=recording,
            runtime_logs=list(self.logs),
            readbacks=readbacks,
            operator_outcome=operator_outcome,
            fatal_error=fatal_error,
            cancelled=self.cancel_event.is_set(),
        )

        report = {
            "schema_version": 1,
            "config_type": "camera_validation_report",
            "validation_id": run_id,
            "started_from_prepared_at_utc": prepared["prepared_at_utc"],
            "completed_at_utc": _utc_text(_utc_now()),
            "camera": {
                "manufacturer": entry.get("manufacturer"),
                "model": entry.get("model"),
                "serial": entry.get("serial"),
                "backend": entry.get("backend"),
            },
            "recipe": {
                key: deepcopy(recipe[key])
                for key in (
                    "strategy",
                    "timing_contract_version",
                    "expected_photos",
                    "photo_command_count",
                    "set_command_count",
                    "supported_bracket_frames",
                    "sequence_duration_s",
                    "preflight_reserve_s",
                    "estimated_duration_s",
                    "set_overhead_ms",
                    "validation_diagnostic_guard_ms",
                    "validation_confirmation_grace_ms",
                )
            },
            "analysis": analysis,
            "artifacts": {
                "camera_profile_path": _safe_relative(prepared["profile_path"], root),
                "camera_timing_path": _safe_relative(prepared["timing_path"], root),
                "plan_path": _safe_relative(plan_path, root) if plan_path else None,
                "run_log_path": _safe_relative(self.log_path, root) if self.log_path else None,
                "report_path": _safe_relative(self.report_path, root) if self.report_path else None,
            },
        }

        if self.report_path is not None:
            _atomic_json(self.report_path, report)
        self.log(
            f"VALIDATION RESULT verdict={analysis['verdict']} "
            f"confirmed={analysis['confirmed_photos']}/{analysis['expected_photos']}"
        )

        with self.condition:
            self.result = report
            self.running = False
            self.phase = "completed"
            self.question = None
            self.answer = None
            self.condition.notify_all()

    def delete_generated_files(self, root: Path = ROOT) -> dict[str, Any]:
        root = Path(root)
        with self.lock:
            if self.running:
                raise CameraValidationError("cannot delete camera files while validation is running")
            report = deepcopy(self.result)
            report_path = self.report_path
        if not isinstance(report, dict):
            raise CameraValidationError("no completed validation report")
        analysis = report.get("analysis")
        if not isinstance(analysis, dict) or analysis.get("verdict") != "FAIL":
            raise CameraValidationError("camera files may only be deleted after FAIL")

        artifacts = report.get("artifacts") or {}
        targets = []
        allowed = {
            "camera_profile_path": (root / "configs/camera_profiles").resolve(),
            "camera_timing_path": (root / "configs/camera_timing").resolve(),
        }
        for key, parent in allowed.items():
            raw = artifacts.get(key)
            if not isinstance(raw, str) or not raw:
                raise CameraValidationError(f"validation report has no {key}")
            path = (root / raw).resolve()
            if path.parent != parent or path.suffix != ".json":
                raise CameraValidationError(f"unsafe generated camera path: {raw}")
            targets.append(path)

        deleted = []
        missing = []
        for path in targets:
            try:
                path.unlink()
                deleted.append(_safe_relative(path, root))
            except FileNotFoundError:
                missing.append(_safe_relative(path, root))

        audit = {
            "deleted_at_utc": _utc_text(_utc_now()),
            "deleted": deleted,
            "already_missing": missing,
        }
        report["generated_camera_files_deletion"] = audit
        if report_path is not None:
            _atomic_json(report_path, report)
        with self.lock:
            self.result = report
        return audit


JOB = CameraValidationJob()


__all__ = [
    "CameraValidationError",
    "CameraValidationJob",
    "JOB",
    "analyse_validation",
    "build_validation_recipe",
    "materialize_validation_plan",
    "validation_candidates",
]
