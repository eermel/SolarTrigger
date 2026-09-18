"""Validated, portable camera command descriptions (never executable code)."""
from __future__ import annotations

from copy import deepcopy
import json
import logging
import math
from pathlib import Path
import re

from backend.camera_timing_contract import validate_timing_contract_v3

PROFILE_DIR = Path(__file__).resolve().parents[1] / "configs" / "camera_profiles"
LOG = logging.getLogger(__name__)


def normalized(value):
    return " ".join(str(value).casefold().split())


def _valid_trigger(spec):
    if not isinstance(spec, dict):
        return False
    if spec.get("method") not in ("trigger_capture", "capture", "widget"):
        return False
    if spec.get("method") == "widget":
        if not spec.get("path"):
            return False
        writer = spec.get("writer")
        if writer not in (None, "single_config"):
            return False
        if writer == "single_config" and (
            not isinstance(spec.get("name"), str)
            or not spec["name"].strip()
        ):
            return False
    return True


def validate_profile(data):
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ValueError("unsupported camera profile schema")
    if data.get("config_type") != "camera_profile":
        raise ValueError("invalid camera profile type")
    for key in ("manufacturer", "model", "backend"):
        if not isinstance(data.get(key), str) or not data[key].strip():
            raise ValueError(f"missing {key}")
    if not re.fullmatch(r"profile-[a-z0-9_-]+", data["backend"]):
        raise ValueError("invalid profile backend")
    if data.get("strategy") not in ("sequential", "bracket"):
        raise ValueError("invalid strategy")

    commands = data.get("commands", {})
    for key in ("manual_mode", "capture_target", "raw", "iso", "shutter"):
        if not isinstance(commands.get(key), dict) or not commands[key].get("path"):
            raise ValueError(f"missing critical command: {key}")
    for key in ("manual_mode", "capture_target", "raw"):
        if "value" not in commands[key]:
            raise ValueError(f"missing value: {key}")
    if "100" not in commands["iso"].get("values", {}):
        raise ValueError("ISO 100 is mandatory")
    if not commands["shutter"].get("values"):
        raise ValueError("shutter choices are mandatory")

    trigger = commands.get("trigger_single", {})
    if not _valid_trigger(trigger):
        raise ValueError("invalid trigger method")

    for command in commands.values():
        if not isinstance(command, dict):
            raise ValueError("invalid command")
        if "path" in command and (
            not isinstance(command["path"], str)
            or not command["path"].strip()
        ):
            raise ValueError("invalid widget path")

    # Characterization may explicitly distinguish readable and writable
    # settings.  Missing flags are accepted for legacy profiles; runtime then
    # uses the live gphoto2 readonly bit as the authority.
    for key, command in commands.items():
        if not isinstance(command, dict):
            continue
        for capability in ("get", "set"):
            if capability in command and type(command[capability]) is not bool:
                raise ValueError(
                    f"invalid {key}.{capability} capability"
                )
        if command.get("set") is False and command.get("get") is False:
            raise ValueError(
                f"camera command {key} is neither readable nor writable"
            )
        writer = command.get("writer")
        if writer not in (None, "single_config"):
            raise ValueError(f"invalid {key}.writer")
        if writer == "single_config" and (
            not isinstance(command.get("name"), str)
            or not command["name"].strip()
        ):
            raise ValueError(f"missing {key}.name for single_config writer")
        invalidates = command.get("invalidates", [])
        if (
            not isinstance(invalidates, list)
            or any(not isinstance(value, str) for value in invalidates)
        ):
            raise ValueError(f"invalid {key}.invalidates")

    contract = data.get("timing_contract")
    contract_version = contract.get("version") if isinstance(contract, dict) else None

    brackets = data.get("brackets", {})
    if not isinstance(brackets, dict):
        raise ValueError("brackets must be an object")
    if data["strategy"] == "bracket" and (
        not brackets or "capture_mode" not in commands
    ):
        raise ValueError("bracket configuration missing")

    for size, spec in brackets.items():
        if not str(size).isdigit() or int(size) < 3 or int(size) % 2 == 0:
            raise ValueError("invalid bracket size")
        if not isinstance(spec, dict):
            raise ValueError("invalid bracket specification")
        if spec.get("step_ev") != 1 or "mode" not in spec:
            raise ValueError("only validated 1 EV brackets are supported")
        if not _valid_trigger(spec.get("trigger", {})):
            raise ValueError("invalid bracket trigger")
        if (
            "shutter_requires_single_mode" in spec
            and type(spec["shutter_requires_single_mode"]) is not bool
        ):
            raise ValueError("invalid bracket shutter preparation policy")

        # Contract v3 no longer stores per-size timing histories. Older profiles
        # still require their legacy atomic/total fields for compatibility.
        if contract_version != 3:
            for field in ("total_ms", "atomic_ms"):
                value = spec.get(field)
                if (
                    type(value) not in (int, float)
                    or not math.isfinite(value)
                    or value <= 0
                ):
                    raise ValueError(f"invalid bracket timing: {field}")

    if contract is not None:
        if not isinstance(contract, dict):
            raise ValueError("invalid timing contract")

        if contract_version == 2:
            def positive(value):
                return (
                    type(value) in (int, float)
                    and math.isfinite(value)
                    and value > 0
                )

            if not positive(contract.get("iso_ms")):
                raise ValueError("invalid ISO reservation")
            blocks = contract.get("brackets")
            if not isinstance(blocks, dict) or set(blocks) != set(brackets):
                raise ValueError("bracket budget mismatch")
            for block in [contract.get("single"), *blocks.values()]:
                if not isinstance(block, dict) or any(
                    not positive(block.get(key))
                    for key in ("setup_ms", "duration_ms", "reference_exposure_s")
                ):
                    raise ValueError("invalid operation budget")
            if contract.get("sustained", {}).get("status") != "validated":
                raise ValueError("sustained qualification required")

        elif contract_version == 3:
            validate_timing_contract_v3(
                contract,
                bracket_frames=[int(value) for value in brackets],
            )

        else:
            raise ValueError("unsupported timing contract")

    return deepcopy(data)


def discover_profiles(directory=None):
    """Fail closed on invalid files and ambiguous model/backend identities."""
    found = []
    for path in sorted(Path(directory or PROFILE_DIR).glob("*.json")):
        try:
            found.append(
                validate_profile(json.loads(path.read_text(encoding="utf-8")))
            )
        except (OSError, ValueError, TypeError) as exc:
            LOG.warning("Ignoring camera profile %s: %s", path, exc)

    result = {}
    for item in found:
        identity = (
            normalized(item["manufacturer"]),
            normalized(item["model"]),
        )
        collisions = [
            profile
            for profile in found
            if profile["backend"] == item["backend"]
            or (
                normalized(profile["manufacturer"]),
                normalized(profile["model"]),
            ) == identity
        ]
        if len(collisions) == 1:
            result[item["backend"]] = item
        else:
            LOG.error("Ambiguous camera profile: %s", item["backend"])
    return result


def one_ev_iso_values(profile, *, max_iso=25600):
    """Return real camera ISO choices restricted to full 1 EV steps from ISO 100."""
    commands = profile.get("commands", {}) if isinstance(profile, dict) else {}
    iso_spec = commands.get("iso", {}) if isinstance(commands, dict) else {}
    raw_values = iso_spec.get("values", {}) if isinstance(iso_spec, dict) else {}

    available = set()
    if isinstance(raw_values, dict):
        for raw in raw_values:
            try:
                value = int(str(raw).strip())
            except (TypeError, ValueError):
                continue
            if value > 0:
                available.add(value)

    result = []
    value = 100
    while value <= int(max_iso):
        if value in available:
            result.append(value)
        value *= 2
    return result



def exposure_planning_capabilities(backend, directory=None):
    # Return full characterized exposure grids without touching hardware.
    # These values are the planning authority for profile-backed cameras.
    # iso_values contains every characterized positive integer ISO, while
    # shutter_values contains every characterized parseable shutter value.

    from backend.exposure_selection import parse_speed

    key = str(backend or "").strip()
    profiles = (
        discover_profiles()
        if directory is None
        else discover_profiles(directory)
    )
    profile = profiles.get(key)
    if profile is None:
        return {
            "strategy": None,
            "iso_values": [],
            "shutter_values": [],
        }

    commands = profile.get("commands", {})
    if not isinstance(commands, dict):
        commands = {}

    iso_spec = commands.get("iso", {})
    raw_isos = iso_spec.get("values", {}) if isinstance(iso_spec, dict) else {}
    iso_values = []
    if isinstance(raw_isos, dict):
        for raw in raw_isos:
            try:
                value = int(str(raw).strip())
            except (TypeError, ValueError):
                continue
            if value > 0:
                iso_values.append(value)
    iso_values = sorted(set(iso_values))

    shutter_spec = commands.get("shutter", {})
    raw_shutters = (
        shutter_spec.get("values", {})
        if isinstance(shutter_spec, dict)
        else {}
    )
    shutter_values = []
    if isinstance(raw_shutters, dict):
        for raw in raw_shutters:
            value = str(raw).strip()
            try:
                parse_speed(value)
            except (ArithmeticError, TypeError, ValueError):
                continue
            if value not in shutter_values:
                shutter_values.append(value)

    return {
        "strategy": profile.get("strategy"),
        "iso_values": iso_values,
        "shutter_values": shutter_values,
    }


def exposure_ui_capabilities(backend, directory=None):
    """Return planning-relevant camera capabilities without touching hardware."""
    key = str(backend or "").strip()
    profile = discover_profiles(directory).get(key)
    if profile is None:
        return {"strategy": None, "iso_values": []}
    return {
        "strategy": profile.get("strategy"),
        "iso_values": one_ev_iso_values(profile),
    }


def profile_for_model(model, directory=None):
    matches = [
        profile
        for profile in discover_profiles(directory).values()
        if normalized(profile["model"]) == normalized(model)
    ]
    return matches[0] if len(matches) == 1 else None


def is_characterized_model(manufacturer, model):
    """Return True only for a valid published characterization profile.

    Timing-only legacy files are evidence, not executable qualification.
    Runtime characterization status follows the camera_profile JSON because it
    contains the discovered commands, the sequential/bracket strategy and the
    validated timing contract consumed by ProfilePlugin.
    """
    profile = profile_for_model(model)
    if profile is None:
        return False
    return normalized(profile["manufacturer"]) == normalized(manufacturer)
