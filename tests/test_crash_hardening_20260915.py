import argparse
from pathlib import Path

import pytest

from backend.trigger_service import TriggerService


@pytest.mark.parametrize(
    "line, expected",
    [
        ("TRIGGER_PHASE ", "Malformed trigger event: TRIGGER_PHASE"),
        ("TRIGGER_CONFIG ", "Malformed trigger event: TRIGGER_CONFIG"),
        ("TRIGGER_AUDIO ", "Malformed trigger event: TRIGGER_AUDIO"),
    ],
)
def test_runtime_log_parser_never_crashes_on_empty_marker_payload(line, expected):
    message, level, public_phase = TriggerService._runtime_log_event(line)
    assert message == expected
    assert level == "error"
    assert public_phase is None


def test_crash_hardening_source_contracts():
    trigger_service = Path("backend/trigger_service.py").read_text(encoding="utf-8")
    trigger = Path("scripts/eclipse_trigger.py").read_text(encoding="utf-8")
    characterization = Path("backend/camera_characterization.py").read_text(encoding="utf-8")
    worker = Path("backend/generic_worker.py").read_text(encoding="utf-8")

    assert "Trigger output processing ERROR:" in trigger_service
    assert "Never forget a still-running child" in trigger_service
    assert "FATAL scheduler failure:" in trigger
    assert 'summary["status"] = "FAILED"' in characterization
    assert "worker thread crashed:" in worker
