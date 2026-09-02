"""Compile one complete Sequencer execution plan from configuration files.

Pure backend service:
- no Flask
- no camera access
- no persistent runtime-state mutation
- no command execution
"""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from typing import Any

from backend.camera_timing import load_camera_timing_profile
from backend.preview_context import load_eclipse_context
from backend.rig_runtime import load_rig_configuration
from backend.sequencer_compiler import (
    audit_materialized_capture,
    build_execution_plan_document,
    compile_and_merge_scheduled_rigs,
    compile_capture_targets,
    derive_initial_state_required,
    format_execution_plan_lines,
    materialize_capture_targets,
    sequencer_rig_is_active,
)


class SequencerCompileError(ValueError):
    pass


def _load_json_object(path: Path, description: str) -> dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as handle:
            data = json.load(handle)
    except FileNotFoundError as exc:
        raise SequencerCompileError(
            f"{description} file not found: {path.name}"
        ) from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise SequencerCompileError(
            f"{description} file could not be loaded: {path.name}"
        ) from exc

    if not isinstance(data, dict):
        raise SequencerCompileError(
            f"{description} configuration must be an object"
        )

    return data


def _safe_child(base: Path, filename: str, description: str) -> Path:
    if not isinstance(filename, str) or not filename.strip():
        raise SequencerCompileError(
            f"{description} filename is required"
        )

    requested = filename.strip()

    if Path(requested).name != requested:
        raise SequencerCompileError(
            f"invalid {description} filename"
        )

    path = base / requested

    try:
        if path.resolve().parent != base.resolve():
            raise SequencerCompileError(
                f"invalid {description} filename"
            )
    except OSError as exc:
        raise SequencerCompileError(
            f"invalid {description} path"
        ) from exc

    return path


def _active_rigs(config: dict[str, Any]) -> list[dict[str, Any]]:
    rigs = config.get("rigs")

    if not isinstance(rigs, list):
        raise SequencerCompileError(
            "RIG configuration contains no rigs"
        )

    result = [
        rig
        for rig in rigs
        if isinstance(rig, dict)
        and sequencer_rig_is_active(rig)
    ]

    if not result:
        raise SequencerCompileError(
            "no active RIG"
        )

    return sorted(
        result,
        key=lambda rig: rig.get("rig_id", 0),
    )


def _group_audited_by_rig(materialized):
    result = {}

    for capture in materialized:
        try:
            audited = audit_materialized_capture(capture)
        except ValueError as exc:
            raise SequencerCompileError(str(exc)) from exc

        result.setdefault(audited.rig_id, []).append(audited)

    for captures in result.values():
        captures.sort(
            key=lambda capture: (
                capture.target.target_time,
                capture.target.sequence_index,
            )
        )

    return result


def _load_timing_profiles(
    *,
    active_rigs: list[dict[str, Any]],
    timing_dir: Path,
    camera_timing_files: dict[int, str],
):
    profiles = {}

    for rig in active_rigs:
        rig_id = rig.get("rig_id")

        filename = camera_timing_files.get(rig_id)

        if not filename:
            raise SequencerCompileError(
                f"missing calibrated camera timing profile for RIG {rig_id}"
            )

        path = _safe_child(
            timing_dir,
            filename,
            f"RIG {rig_id} camera timing",
        )

        try:
            profile = load_camera_timing_profile(path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise SequencerCompileError(
                f"invalid camera timing profile for RIG {rig_id}: {exc}"
            ) from exc

        devices = rig.get("devices")
        camera = (
            devices.get("camera")
            if isinstance(devices, dict)
            else None
        )

        rig_backend = (
            str(camera.get("backend") or "").strip().lower()
            if isinstance(camera, dict)
            else ""
        )

        if not rig_backend or rig_backend == "none":
            raise SequencerCompileError(
                f"RIG {rig_id} has no camera backend"
            )

        if profile.backend != rig_backend:
            raise SequencerCompileError(
                f"RIG {rig_id} timing profile backend "
                f"{profile.backend!r} does not match "
                f"camera backend {rig_backend!r}"
            )

        profiles[rig_id] = profile

    return profiles


def compile_execution_plan_from_files(
    *,
    configs_dir: str | Path,
    sequence_file: str,
    camera_timing_files: dict[int, str],
    rig_config: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Compile one complete execution plan without touching hardware."""

    configs_dir = Path(configs_dir)

    sequence_path = _safe_child(
        configs_dir / "sequence",
        sequence_file,
        "Sequence",
    )

    sequence_config = _load_json_object(
        sequence_path,
        "Sequence",
    )

    if sequence_config.get("config_type") != "sequence":
        raise SequencerCompileError(
            "invalid Sequence configuration"
        )

    circumstances_file = sequence_config.get(
        "circumstances_file"
    )
    photo_setup_file = sequence_config.get(
        "photo_setup_file"
    )
    exposure_opt_file = sequence_config.get(
        "exposure_opt_file"
    )
    sequence_margin_min = sequence_config.get(
        "sequence_margin_min",
        60,
    )

    required = {
        "circumstances_file": circumstances_file,
        "photo_setup_file": photo_setup_file,
        "exposure_opt_file": exposure_opt_file,
    }

    for field, value in required.items():
        if not isinstance(value, str) or not value.strip():
            raise SequencerCompileError(
                f"missing Sequencer input: {field}"
            )

    if (
        isinstance(sequence_margin_min, bool)
        or not isinstance(sequence_margin_min, (int, float))
        or sequence_margin_min < 0
    ):
        raise SequencerCompileError(
            "invalid sequence_margin_min"
        )

    circumstances_file = circumstances_file.strip()
    photo_setup_file = photo_setup_file.strip()
    exposure_opt_file = exposure_opt_file.strip()

    circumstances_path = _safe_child(
        configs_dir / "circumstances",
        circumstances_file,
        "circumstances",
    )

    photo_path = _safe_child(
        configs_dir / "photo_cfg",
        photo_setup_file,
        "Photo Setup",
    )

    exposure_path = _safe_child(
        configs_dir / "exposure_opt",
        exposure_opt_file,
        "Exposure Optimization",
    )

    circumstances = _load_json_object(
        circumstances_path,
        "circumstances",
    )
    photo_config = _load_json_object(
        photo_path,
        "Photo Setup",
    )
    exposure_opt_config = _load_json_object(
        exposure_path,
        "Exposure Optimization",
    )

    try:
        eclipse_context = load_eclipse_context(
            circumstances_path
        )
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        raise SequencerCompileError(
            f"circumstances context is invalid: {exc}"
        ) from exc

    timeline = eclipse_context.get("timeline")

    if not isinstance(timeline, dict):
        raise SequencerCompileError(
            "circumstances context contains no timeline"
        )

    config = deepcopy(
        load_rig_configuration()
        if rig_config is None
        else rig_config
    )

    active_rigs = _active_rigs(config)

    try:
        targets = compile_capture_targets(
            timeline,
            photo_config,
            sequence_margin_min=sequence_margin_min,
        )

        materialized = materialize_capture_targets(
            targets,
            active_rigs,
            photo_config,
            exposure_opt_config,
            eclipse_context,
            eclipse_config=config.get("eclipse"),
        )
    except (TypeError, ValueError, KeyError) as exc:
        raise SequencerCompileError(
            f"capture materialization failed: {exc}"
        ) from exc

    audited_by_rig = _group_audited_by_rig(
        materialized
    )

    initial_states = derive_initial_state_required(
        audited_by_rig
    )

    timing_profiles = _load_timing_profiles(
        active_rigs=active_rigs,
        timing_dir=configs_dir / "camera_timing",
        camera_timing_files=camera_timing_files,
    )

    try:
        merged, final_states = (
            compile_and_merge_scheduled_rigs(
                audited_by_rig,
                initial_states=initial_states,
                timing_profiles=timing_profiles,
            )
        )
    except ValueError as exc:
        raise SequencerCompileError(
            f"command scheduling failed: {exc}"
        ) from exc

    sequence_start = (
        timeline["C1"]
        - timedelta(minutes=sequence_margin_min)
    )

    sequence_end = (
        timeline["C4"]
        + timedelta(minutes=sequence_margin_min)
    )

    plan = build_execution_plan_document(
        merged,
        initial_states=initial_states,
        final_states=final_states,
        circumstances_file=Path(
            circumstances_file
        ).name,
        photo_setup_file=Path(
            photo_setup_file
        ).name,
        exposure_opt_file=Path(
            exposure_opt_file
        ).name,
        sequence_start=sequence_start,
        sequence_end=sequence_end,
    )

    if sequence_file is not None:
        plan["sources"]["sequence_file"] = Path(
            sequence_file
        ).name

    plan["camera_timing_files"] = {
        str(rig_id): Path(filename).name
        for rig_id, filename in sorted(
            camera_timing_files.items()
        )
    }

    plan["sequence_margin_min"] = (
        sequence_margin_min
    )

    return plan, format_execution_plan_lines(plan)


__all__ = [
    "SequencerCompileError",
    "compile_execution_plan_from_files",
]
