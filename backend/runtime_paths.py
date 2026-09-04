"""Canonical paths for SolarTrigger mutable runtime data."""

from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]

CONFIGS_DIR = PROJECT_ROOT / "configs"
DATA_DIR = PROJECT_ROOT / "data"

VAR_DIR = PROJECT_ROOT / "var"
STATE_DIR = VAR_DIR / "state"
GENERATED_DIR = VAR_DIR / "generated"
LOGS_DIR = VAR_DIR / "logs"

STATE_FILE = STATE_DIR / "state.json"
TRIGGER_STATE_FILE = STATE_DIR / "trigger_state.json"

TODAY_ECLIPSE_FILE = GENERATED_DIR / "todayeclipse.json"
RIG_CONFIG_DIR = GENERATED_DIR / "rig"
CAMERA_CONFIG_DIR = GENERATED_DIR / "camera_cfg"
CIRCUMSTANCES_DIR = GENERATED_DIR / "circumstances"
PHOTO_CONFIG_DIR = GENERATED_DIR / "photo_cfg"
EXPOSURE_OPT_DIR = GENERATED_DIR / "exposure_opt"
SEQUENCE_DIR = GENERATED_DIR / "sequence"
EXECUTION_PLAN_DIR = GENERATED_DIR / "execution_plan"

LOGS_BUFFER_FILE = LOGS_DIR / "logs_buffer.jsonl"
RIG_TRACES_FILE = LOGS_DIR / "rig_traces.jsonl"


_VAR_DIRECTORIES = (
    STATE_DIR,
    GENERATED_DIR,
    RIG_CONFIG_DIR,
    CAMERA_CONFIG_DIR,
    CIRCUMSTANCES_DIR,
    PHOTO_CONFIG_DIR,
    EXPOSURE_OPT_DIR,
    SEQUENCE_DIR,
    EXECUTION_PLAN_DIR,
    LOGS_DIR,
)


def ensure_var_layout(var_dir: Path = VAR_DIR) -> None:
    """Create the mutable SolarTrigger directory layout if missing."""
    var_dir = Path(var_dir)

    relative_dirs = (
        "state",
        "generated",
        "generated/rig",
        "generated/camera_cfg",
        "generated/circumstances",
        "generated/photo_cfg",
        "generated/exposure_opt",
        "generated/sequence",
        "generated/execution_plan",
        "logs",
    )

    for relative in relative_dirs:
        (var_dir / relative).mkdir(parents=True, exist_ok=True)
