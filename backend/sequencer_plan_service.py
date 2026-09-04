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
    apply_exposure_optimization,
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


def _resolve_photo_config_path(
    generated_configs_dir: Path,
    product_configs_dir: Path | None,
    filename: str,
) -> Path:
    """Resolve user Photo Setup first, then bundled photo_default.json only."""
    generated = _safe_child(
        generated_configs_dir / "photo_cfg",
        filename,
        "Photo Setup",
    )

    if generated.is_file():
        return generated

    if (
        product_configs_dir is not None
        and Path(filename).name == "photo_default.json"
    ):
        return _safe_child(
            product_configs_dir / "photo_cfg",
            filename,
            "Photo Setup",
        )

    return generated


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


def _validate_active_rigs_for_sequencer(
    active_rigs: list[dict[str, Any]],
) -> None:
    """Reject incomplete RIG configuration before capture materialization."""

    for rig in active_rigs:
        rig_id = rig.get("rig_id")

        devices = rig.get("devices")
        camera = (
            devices.get("camera")
            if isinstance(devices, dict)
            else None
        )

        if not isinstance(camera, dict):
            raise SequencerCompileError(
                f"RIG {rig_id} is not configured: camera required"
            )

        backend = _identity_text(
            camera.get("backend")
        )

        if not backend or backend == "none":
            raise SequencerCompileError(
                f"RIG {rig_id} is not configured: camera required"
            )

        optics = rig.get("optics")
        focal_length = (
            optics.get("focal_length_mm")
            if isinstance(optics, dict)
            else None
        )

        if (
            isinstance(focal_length, bool)
            or not isinstance(focal_length, (int, float))
            or focal_length <= 0
        ):
            raise SequencerCompileError(
                f"RIG {rig_id} is not configured: focal length required"
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


def _identity_text(value: Any) -> str:
    return (
        str(value).strip().casefold()
        if value is not None
        else ""
    )


def _load_timing_profiles(
    *,
    active_rigs: list[dict[str, Any]],
    timing_dir: Path,
):
    """Resolve calibrated timing profiles from each RIG camera identity."""
    profiles = {}
    resolved_files = {}

    documents = []

    if timing_dir.exists():
        for path in sorted(timing_dir.glob("*.json")):
            try:
                data = _load_json_object(
                    path,
                    f"camera timing {path.name}",
                )
            except (
                OSError,
                ValueError,
                json.JSONDecodeError,
                SequencerCompileError,
            ):
                continue

            if data.get("config_type") != "camera_timing":
                continue

            documents.append((
                path,
                data,
            ))

    for rig in active_rigs:
        rig_id = rig.get("rig_id")

        devices = rig.get("devices")
        camera = (
            devices.get("camera")
            if isinstance(devices, dict)
            else None
        )

        if not isinstance(camera, dict):
            raise SequencerCompileError(
                f"RIG {rig_id} has no configured camera"
            )

        rig_backend = _identity_text(
            camera.get("backend")
        )
        rig_manufacturer = _identity_text(
            camera.get("manufacturer")
        )
        rig_model = _identity_text(
            camera.get("model")
        )

        if not rig_backend or rig_backend == "none":
            raise SequencerCompileError(
                f"RIG {rig_id} has no camera backend"
            )

        if not rig_manufacturer or not rig_model:
            raise SequencerCompileError(
                f"RIG {rig_id} camera identity is incomplete "
                f"(manufacturer/model required)"
            )

        matches = []

        for path, data in documents:
            if (
                _identity_text(data.get("backend"))
                == rig_backend
                and _identity_text(data.get("manufacturer"))
                == rig_manufacturer
                and _identity_text(data.get("model"))
                == rig_model
            ):
                matches.append(path)

        camera_label = " ".join(
            value
            for value in (
                str(camera.get("manufacturer") or "").strip(),
                str(camera.get("model") or "").strip(),
            )
            if value
        )

        if not matches:
            raise SequencerCompileError(
                f"no calibrated camera timing profile matches "
                f"RIG {rig_id} camera {camera_label!r} "
                f"(backend {rig_backend!r})"
            )

        if len(matches) > 1:
            names = ", ".join(
                path.name
                for path in matches
            )
            raise SequencerCompileError(
                f"multiple calibrated camera timing profiles match "
                f"RIG {rig_id} camera {camera_label!r}: {names}"
            )

        path = matches[0]

        try:
            profile = load_camera_timing_profile(path)
        except (
            OSError,
            ValueError,
            json.JSONDecodeError,
        ) as exc:
            raise SequencerCompileError(
                f"invalid camera timing profile for "
                f"RIG {rig_id}: {exc}"
            ) from exc

        profiles[rig_id] = profile
        resolved_files[rig_id] = path.name

    return profiles, resolved_files


def compile_execution_plan_from_files(
    *,
    configs_dir: str | Path,
    circumstances_file: str,
    photo_setup_file: str,
    exposure_opt_file: str,
    sequence_margin_min: int | float,
    rig_config: dict[str, Any] | None = None,
    sequence_file: str | None = None,
    camera_timing_dir: str | Path | None = None,
    product_configs_dir: str | Path | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Compile one complete execution plan without touching hardware."""

    configs_dir = Path(configs_dir)
    camera_timing_dir = (
        Path(camera_timing_dir)
        if camera_timing_dir is not None
        else configs_dir / "camera_timing"
    )
    product_configs_dir = (
        Path(product_configs_dir)
        if product_configs_dir is not None
        else None
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

    photo_path = _resolve_photo_config_path(
        configs_dir,
        product_configs_dir,
        photo_setup_file,
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
    _validate_active_rigs_for_sequencer(active_rigs)

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

    timing_profiles, resolved_timing_files = _load_timing_profiles(
        active_rigs=active_rigs,
        timing_dir=camera_timing_dir,
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
        str(rig_id): filename
        for rig_id, filename in sorted(
            resolved_timing_files.items()
        )
    }

    plan["sequence_margin_min"] = (
        sequence_margin_min
    )

    return plan, format_execution_plan_lines(plan)


def compile_rig_execution_plan_from_files(
    *,
    configs_dir: str | Path,
    rig_id: int,
    circumstances_file: str,
    photo_setup_file: str,
    exposure_opt_file: str,
    sequence_margin_min: int | float,
    rig_config: dict[str, Any] | None = None,
    sequence_file: str | None = None,
    camera_timing_dir: str | Path | None = None,
    product_configs_dir: str | Path | None = None,
) -> tuple[
    dict[str, Any],
    list[str],
    dict[str, Any],
]:
    """Compile one independent RIG and build its audit context."""

    if (
        not isinstance(rig_id, int)
        or isinstance(rig_id, bool)
        or not 1 <= rig_id <= 4
    ):
        raise SequencerCompileError(
            f"invalid RIG id: {rig_id!r}"
        )

    configs_dir = Path(configs_dir)

    config = deepcopy(
        load_rig_configuration()
        if rig_config is None
        else rig_config
    )

    rigs = config.get("rigs")

    if not isinstance(rigs, list):
        raise SequencerCompileError(
            "RIG configuration contains no rigs"
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
        raise SequencerCompileError(
            f"RIG {rig_id} not found"
        )

    if not sequencer_rig_is_active(rig):
        raise SequencerCompileError(
            f"RIG {rig_id} is not active"
        )

    single_config = deepcopy(config)
    single_config["rigs"] = [
        deepcopy(rig)
    ]

    plan, lines = (
        compile_execution_plan_from_files(
            configs_dir=configs_dir,
            circumstances_file=
                circumstances_file,
            photo_setup_file=
                photo_setup_file,
            exposure_opt_file=
                exposure_opt_file,
            sequence_margin_min=
                sequence_margin_min,
            rig_config=single_config,
            sequence_file=sequence_file,
            camera_timing_dir=camera_timing_dir,
            product_configs_dir=product_configs_dir,
        )
    )

    circumstances_path = _safe_child(
        configs_dir / "circumstances",
        circumstances_file.strip(),
        "circumstances",
    )

    photo_path = _resolve_photo_config_path(
        configs_dir,
        (
            Path(product_configs_dir)
            if product_configs_dir is not None
            else None
        ),
        photo_setup_file.strip(),
    )

    exposure_path = _safe_child(
        configs_dir / "exposure_opt",
        exposure_opt_file.strip(),
        "Exposure Optimization",
    )

    circumstances = _load_json_object(
        circumstances_path,
        "circumstances",
    )

    photo_setup = _load_json_object(
        photo_path,
        "Photo Setup",
    )

    exposure_opt = _load_json_object(
        exposure_path,
        "Exposure Optimization",
    )

    try:
        effective_rig = (
            apply_exposure_optimization(
                rig,
                exposure_opt,
            )
        )
    except (
        TypeError,
        ValueError,
        KeyError,
    ) as exc:
        raise SequencerCompileError(
            "Exposure Optimization failed "
            f"for RIG {rig_id}: {exc}"
        ) from exc

    context = {
        "circumstances": circumstances,
        "photo_setup": photo_setup,
        "exposure_opt": exposure_opt,
        "rig": deepcopy(rig),
        "effective_rig": effective_rig,
    }

    return plan, lines, context


__all__ = [
    "SequencerCompileError",
    "compile_execution_plan_from_files",
    "compile_rig_execution_plan_from_files",
]
