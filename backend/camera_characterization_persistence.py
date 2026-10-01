"""Persistence helpers for camera characterization runtime artifacts.

This module contains only document shaping and atomic publication. It deliberately
has no camera I/O and no characterization orchestration.
"""
from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile

from backend.camera_profiles import validate_profile


def _persistent_profile_document(profile):
    """Return the lean runtime profile written to configs/camera_profiles."""
    contract = profile.get("timing_contract")
    if not (
        isinstance(contract, dict)
        and contract.get("version") == 3
    ):
        return deepcopy(profile)

    result = {}
    for key in (
        "schema_version",
        "config_type",
        "backend",
        "manufacturer",
        "model",
        "characterized_at",
        "strategy",
        "capabilities",
        "commands",
        "warnings",
        "capture_timeout_s",
        "timing_contract",
        "selection",
    ):
        if key in profile:
            result[key] = deepcopy(profile[key])

    result["brackets"] = {}
    for size, spec in profile.get("brackets", {}).items():
        result["brackets"][str(size)] = {
            "step_ev": spec["step_ev"],
            "mode": spec["mode"],
            "trigger": deepcopy(spec["trigger"]),
            "shutter_requires_single_mode": bool(
                spec.get("shutter_requires_single_mode", True)
            ),
        }

    return validate_profile(result)


def _persistent_timing_document(timing):
    """Return the final non-debug timing JSON.

    Raw samples, medians, qualification traces and test pauses remain only in the
    characterization measurement checkpoint.
    """
    contract = timing.get("timing_contract")
    if not (
        isinstance(contract, dict)
        and contract.get("version") == 3
    ):
        return deepcopy(timing)

    return {
        "schema_version": 2,
        "config_type": "camera_timing",
        "backend": timing["backend"],
        "manufacturer": timing["manufacturer"],
        "model": timing["model"],
        "timing_contract": deepcopy(contract),
    }


def publish(profile, timing, root, *, replace_existing=False):
    """Publish lean runtime JSON safely.

    Initial characterization remains create-only.
    Re-characterization leaves the current profile/timing untouched during all
    camera tests, then replaces the pair only after the new documents have been
    completely generated and validated.
    """
    stored_profile = _persistent_profile_document(profile)
    stored_timing = _persistent_timing_document(timing)

    validate_profile(stored_profile)

    for key in ("manufacturer", "model", "backend"):
        if stored_timing.get(key) != stored_profile[key]:
            raise ValueError(
                f"Timing/profile identity mismatch: {key}"
            )

    slug = stored_profile["backend"][8:]
    files = [
        (
            root / "configs/camera_timing" / f"{slug}.json",
            stored_timing,
        ),
        (
            root / "configs/camera_profiles" / f"{slug}.json",
            stored_profile,
        ),
    ]

    existing = [path.exists() for path, _ in files]
    if any(existing) and not replace_existing:
        raise RuntimeError(
            "Characterization files already exist; no overwrite performed"
        )
    if replace_existing and any(existing) and not all(existing):
        raise RuntimeError(
            "Existing characterization is incomplete; refusing replacement"
        )

    import os
    import tempfile

    prepared = []
    backups = {}

    try:
        # Build and validate both replacement files before touching active data.
        for path, document in files:
            path.parent.mkdir(parents=True, exist_ok=True)
            logical_parent = root / "configs" / path.parent.name
            if path.parent.is_symlink():
                shared_parent = root / "var" / "generated" / path.parent.name
                if path.parent.resolve() != shared_parent.resolve():
                    raise ValueError(
                        "Camera configuration symlink must target shared persistent data"
                    )
            elif path.parent.resolve() != logical_parent.resolve():
                raise ValueError("Unsafe camera configuration directory")

            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=path.parent,
                suffix=".tmp",
                delete=False,
            ) as handle:
                temp = Path(handle.name)
                json.dump(
                    document,
                    handle,
                    indent=2,
                    ensure_ascii=False,
                )
                handle.flush()
                os.fsync(handle.fileno())

            if document.get("config_type") == "camera_timing":
                from backend.camera_timing import load_camera_timing_profile
                load_camera_timing_profile(temp)

            prepared.append((path, temp))

        if replace_existing:
            for path, _temp in prepared:
                backups[path] = path.read_bytes() if path.exists() else None

        installed = []
        try:
            for path, temp in prepared:
                if replace_existing:
                    os.replace(temp, path)
                else:
                    os.link(temp, path)
                    temp.unlink(missing_ok=True)
                installed.append(path)
        except Exception:
            if replace_existing:
                for path, _temp in prepared:
                    previous = backups.get(path)
                    if previous is None:
                        path.unlink(missing_ok=True)
                        continue
                    with tempfile.NamedTemporaryFile(
                        mode="wb",
                        dir=path.parent,
                        suffix=".rollback",
                        delete=False,
                    ) as handle:
                        rollback = Path(handle.name)
                        handle.write(previous)
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.replace(rollback, path)
            else:
                for path in installed:
                    path.unlink(missing_ok=True)
            raise

        return [
            str(path.relative_to(root))
            for path, _temp in prepared
        ]

    finally:
        for _path, temp in prepared:
            temp.unlink(missing_ok=True)
