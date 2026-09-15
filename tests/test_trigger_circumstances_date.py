import json
from datetime import date
from pathlib import Path

import pytest

import backend.trigger_service as trigger_service_module
from backend.trigger_service import TriggerService, TriggerValidationError


class _State:
    def snapshot(self, key):
        assert key == "gps"
        return {}


def _write_json(path: Path, payload):
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _service(tmp_path, circumstances):
    circ = _write_json(tmp_path / "circ.json", circumstances)
    photo = _write_json(
        tmp_path / "photo.json",
        {
            "config_type": "photo_setup",
            "phases": {
                "partial": {
                    "enabled": True,
                    "interval_s": 60,
                    "iso": 100,
                    "aperture": "f/8",
                    "shutter_min": "1/500",
                    "shutter_max": "1/500",
                    "step_ev": 1.0,
                },
                "diamond_ring": {
                    "enabled": True,
                    "duration_s": 40,
                    "interval_s": 4,
                    "totality_overlap_s": 5,
                    "iso": 100,
                    "aperture": "f/8",
                    "shutter_min": "1/500",
                    "shutter_max": "1/500",
                    "step_ev": 1.0,
                },
            },
        },
    )
    exposure = _write_json(
        tmp_path / "exposure.json",
        {
            "config_type": "exposure_optimization",
            "rigs": [{"rig_id": 1, "photo": {}}],
        },
    )

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


def _base_circumstances():
    return {
        "TSTART": "08:00:00",
        "C1": "08:10:00",
        "C2": "09:00:00",
        "TMAX": "09:03:00",
        "C3": "09:06:00",
        "C4": "10:00:00",
        "TEND": "11:00:00",
    }


def test_real_start_rejects_missing_explicit_date(tmp_path):
    service = _service(tmp_path, _base_circumstances())

    with pytest.raises(TriggerValidationError) as exc_info:
        service.validate_start(
            rig_id=1,
            require_gps=False,
            selected={},
            strict_circumstances_date=True,
        )

    assert exc_info.value.code == "CIRCUMSTANCES_DATE_MISSING"


@pytest.mark.parametrize(
    "bad_date",
    [
        "2027/08/02",
        "02-08-2027",
        "2027-02-30",
        "2027-08-02T00:00:00Z",
        " 2027-08-02 extra ",
    ],
)
def test_real_start_rejects_invalid_explicit_date(tmp_path, bad_date):
    cfg = _base_circumstances()
    cfg["_date"] = bad_date
    service = _service(tmp_path, cfg)

    with pytest.raises(TriggerValidationError) as exc_info:
        service.validate_start(
            rig_id=1,
            require_gps=False,
            selected={},
            strict_circumstances_date=True,
        )

    assert exc_info.value.code == "CIRCUMSTANCES_DATE_INVALID"


def test_real_start_accepts_valid_explicit_date(monkeypatch, tmp_path):
    cfg = _base_circumstances()
    cfg["_date"] = "2027-08-02"
    service = _service(tmp_path, cfg)
    monkeypatch.setattr(
        trigger_service_module,
        "_utc_today",
        lambda: date(2027, 8, 2),
    )

    result = service.validate_start(
        rig_id=1,
        require_gps=False,
        selected={},
        strict_circumstances_date=True,
    )

    assert result["_date"] == "2027-08-02"


def test_dryrun_compatibility_mode_keeps_legacy_date_fallback(tmp_path):
    cfg = _base_circumstances()
    service = _service(tmp_path, cfg)

    result = service.validate_start(
        rig_id=1,
        require_gps=False,
        selected={},
        strict_circumstances_date=False,
    )

    assert result["C2"] == "09:00:00"


def test_start_wires_strict_date_only_for_real_execution():
    source = Path("backend/trigger_service.py").read_text(encoding="utf-8")

    assert "strict_circumstances_date=not (simulate or dry_run)" in source
