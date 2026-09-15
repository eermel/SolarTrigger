import json
from datetime import date
from pathlib import Path

import pytest

import backend.trigger_service as trigger_service_module
from backend.trigger_service import TriggerService, TriggerValidationError
from plugins.camera.profile import ProfilePlugin


class _State:
    def snapshot(self, key):
        assert key == "gps"
        return {}


def _write_json(path: Path, payload):
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _service(tmp_path, circumstances):
    circ = _write_json(tmp_path / "circ.json", circumstances)
    photo = _write_json(tmp_path / "photo.json", {
        "config_type": "photo_setup",
        "phases": {
            "partial": {
                "enabled": True, "interval_s": 60, "iso": 100,
                "aperture": "f/8", "shutter_min": "1/500",
                "shutter_max": "1/500", "step_ev": 1.0,
            },
            "diamond_ring": {
                "enabled": True, "duration_s": 40, "interval_s": 4,
                "totality_overlap_s": 5, "iso": 100, "aperture": "f/8",
                "shutter_min": "1/500", "shutter_max": "1/500",
                "step_ev": 1.0,
            },
        },
    })
    exposure = _write_json(tmp_path / "exposure.json", {
        "config_type": "exposure_optimization",
        "rigs": [{"rig_id": 1, "photo": {}}],
    })
    service = object.__new__(TriggerService)
    service.state = _State()
    service._active_circumstances_paths = {}
    service._active_photo_paths = {}
    service._active_exposure_opt_paths = {}
    service._resolve_trigger_inputs = lambda *_args, **_kwargs: {
        "circumstances": circ,
        "photo": photo,
        "exposure_opt": exposure,
    }
    return service


def _circ(day):
    return {
        "_date": day,
        "TSTART": "08:00:00",
        "C1": "08:10:00",
        "C2": "09:00:00",
        "TMAX": "09:03:00",
        "C3": "09:06:00",
        "C4": "10:00:00",
        "TEND": "11:00:00",
    }


def test_real_start_rejects_stale_circumstances_date(monkeypatch, tmp_path):
    monkeypatch.setattr(trigger_service_module, "_utc_today", lambda: date(2027, 8, 2))
    service = _service(tmp_path, _circ("2027-08-01"))
    with pytest.raises(TriggerValidationError) as exc_info:
        service.validate_start(
            rig_id=1, require_gps=False, selected={},
            strict_circumstances_date=True,
        )
    assert exc_info.value.code == "CIRCUMSTANCES_DATE_MISMATCH"


def test_dryrun_keeps_non_current_date(monkeypatch, tmp_path):
    monkeypatch.setattr(trigger_service_module, "_utc_today", lambda: date(2030, 1, 1))
    service = _service(tmp_path, _circ("2027-08-02"))
    result = service.validate_start(
        rig_id=1, require_gps=False, selected={},
        strict_circumstances_date=False,
    )
    assert result["_date"] == "2027-08-02"


def test_profile_init_applies_characterized_image_format_and_white_balance():
    plugin = object.__new__(ProfilePlugin)
    plugin.commands = {
        "iso": {"value": "100"},
        "capture_mode": {"value": "Single Shot"},
        "raw": {"value": "RAW"},
        "white_balance": {"value": "Daylight"},
    }
    plugin.preflight = lambda required: required
    result = plugin.init_settings(
        iso=200,
        image_format="RAW",
        white_balance="Daylight",
    )
    assert result["iso"] == 200
    assert result["capturemode"] == "Single Shot"
    assert result["image_format"] == "RAW"
    assert result["white_balance"] == "Daylight"


def test_profile_init_skips_uncharacterized_white_balance():
    plugin = object.__new__(ProfilePlugin)
    plugin.commands = {
        "iso": {"value": "100"},
        "raw": {"value": "RAW"},
    }
    plugin.preflight = lambda required: required
    result = plugin.init_settings(
        image_format="RAW",
        white_balance="Daylight",
    )
    assert result["image_format"] == "RAW"
    assert "white_balance" not in result
