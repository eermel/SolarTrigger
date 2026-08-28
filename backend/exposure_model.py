from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class LogicalExposureRequest:
    shutter_min: Optional[str]
    shutter_max: Optional[str]
    step_ev: Optional[float]
    speeds: Optional[list[str]]
    iso_target: Optional[str]
    phase: str
    target_time: datetime
    deadline: Optional[datetime]
    origin: Optional[str] = None
    request_id: Optional[str] = None


@dataclass
class MaterializedExposure:
    rig_id: int
    plugin_name: str
    exposures_s: Optional[list[float]]
    iso_applied: Optional[str] = None
    corrections: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    logical_request_id: Optional[str] = None
