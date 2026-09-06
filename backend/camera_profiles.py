"""Validated, portable camera command descriptions (never executable code)."""
from __future__ import annotations

from copy import deepcopy
import json
import logging
import math
from pathlib import Path
import re

PROFILE_DIR = Path(__file__).resolve().parents[1] / "configs" / "camera_profiles"
LOG = logging.getLogger(__name__)


def normalized(value):
    return " ".join(str(value).casefold().split())


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
    if trigger.get("method") not in ("trigger_capture", "capture", "widget"):
        raise ValueError("invalid trigger method")
    if trigger.get("method") == "widget" and not trigger.get("path"):
        raise ValueError("missing trigger widget")
    for command in commands.values():
        if not isinstance(command, dict):
            raise ValueError("invalid command")
        if "path" in command and (not isinstance(command["path"], str)
                                  or not command["path"].strip()):
            raise ValueError("invalid widget path")
    if data["strategy"] == "bracket":
        brackets = data.get("brackets", {})
        if not brackets or "capture_mode" not in commands:
            raise ValueError("bracket configuration missing")
        for size, spec in brackets.items():
            if not str(size).isdigit() or int(size) < 3 or int(size) % 2 == 0:
                raise ValueError("invalid bracket size")
            if spec.get("step_ev") != 1 or "mode" not in spec:
                raise ValueError("only validated 1 EV brackets are supported")
            for field in ("total_ms", "atomic_ms"):
                value = spec.get(field)
                if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                    raise ValueError(f"invalid bracket timing: {field}")
            if spec.get("trigger", {}).get("method") not in ("trigger_capture", "capture", "widget"):
                raise ValueError("invalid bracket trigger")
    contract = data.get("timing_contract")
    if contract is not None:
        if not isinstance(contract, dict) or contract.get("version") != 2:
            raise ValueError("unsupported timing contract")
        def positive(value):
            return type(value) in (int, float) and math.isfinite(value) and value > 0
        if not positive(contract.get("iso_ms")):
            raise ValueError("invalid ISO reservation")
        blocks = contract.get("brackets")
        if not isinstance(blocks, dict) or set(blocks) != set(data.get("brackets", {})):
            raise ValueError("bracket budget mismatch")
        for block in [contract.get("single"), *blocks.values()]:
            if not isinstance(block, dict) or any(not positive(block.get(k)) for k in
                                                   ("setup_ms", "duration_ms", "reference_exposure_s")):
                raise ValueError("invalid operation budget")
        if contract.get("sustained", {}).get("status") != "validated":
            raise ValueError("sustained qualification required")
    return deepcopy(data)


def discover_profiles(directory=None):
    """Fail closed on invalid files and ambiguous model/backend identities."""
    found = []
    for path in sorted(Path(directory or PROFILE_DIR).glob("*.json")):
        try:
            found.append(validate_profile(json.loads(path.read_text(encoding="utf-8"))))
        except (OSError, ValueError, TypeError) as exc:
            LOG.warning("Ignoring camera profile %s: %s", path, exc)
    result = {}
    for item in found:
        identity = (normalized(item["manufacturer"]), normalized(item["model"]))
        collisions = [p for p in found if p["backend"] == item["backend"] or
                      (normalized(p["manufacturer"]), normalized(p["model"])) == identity]
        if len(collisions) == 1:
            result[item["backend"]] = item
        else:
            LOG.error("Ambiguous camera profile: %s", item["backend"])
    return result


def profile_for_model(model, directory=None):
    matches = [p for p in discover_profiles(directory).values()
               if normalized(p["model"]) == normalized(model)]
    return matches[0] if len(matches) == 1 else None


def is_characterized_model(manufacturer, model):
    profile = profile_for_model(model)
    if profile:
        return normalized(profile["manufacturer"]) == normalized(manufacturer)
    for path in sorted((PROFILE_DIR.parent / "camera_timing").glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if (not str(data.get("backend", "")).startswith("profile-")
                    and data.get("config_type") == "camera_timing"
                    and normalized(data.get("manufacturer")) == normalized(manufacturer)
                    and normalized(data.get("model")) == normalized(model)):
                return True
        except (OSError, ValueError, TypeError):
            continue
    return False
