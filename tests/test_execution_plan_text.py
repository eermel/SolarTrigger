import json
from datetime import datetime, timezone

from backend.execution_plan_runtime import (
    load_execution_plan,
)
from backend.execution_plan_text import (
    build_execution_plan_filename,
    parse_execution_plan_text,
    render_execution_plan_text,
)


def _plan():
    return {
        "schema_version": 2,
        "config_type": "execution_plan",
        "sources": {
            "circumstances_file": "egypt.json",
            "photo_setup_file": "photo.json",
            "exposure_opt_file": "expo.json",
        },
        "sequence_start_utc":
            "2027-08-02T08:00:00.000Z",
        "sequence_end_utc":
            "2027-08-02T11:00:00.000Z",
        "sequence_margin_min": 60,
        "initial_state_required": {
            "1": {
                "iso": "100",
                "capturemode": "Single Shot",
            }
        },
        "camera_timing_files": {
            "1": "sony_ilce_7m5.json",
        },
        "commands": [
            {
                "time_utc":
                    "2027-08-02T08:00:00.000Z",
                "rig_id": 1,
                "action": "SET",
                "params": {
                    "parameter": "iso",
                    "value": "100",
                },
            },
            {
                "time_utc":
                    "2027-08-02T09:35:41.800Z",
                "rig_id": 1,
                "action": "PHOTO",
                "params": {
                    "frames": 5,
                    "physical_views": [
                        "1/1000",
                        "1/500",
                        "1/250",
                        "1/125",
                        "1/60",
                    ],
                },
            },
        ],
        "command_phases": [
            "partial",
            "totality",
        ],
    }


def _context():
    return {
        "circumstances": {
            "_date": "2027-08-02",
            "_type": "Totale",
            "_duration": "6m 21s",
            "_magnitude": 1.02,
            "C1": "08:17:34.200",
            "C2": "09:35:41.800",
            "TMAX": "09:38:52.400",
            "C3": "09:42:03.100",
            "C4": "11:04:27.600",
            "_circumstances_location": {
                "latitude": 23.923456,
                "longitude": 35.482123,
                "altitude_m": 18,
                "comment":
                    "Berenice reference site",
            },
            "_timezone_name":
                "Africa/Cairo",
        },
        "photo_setup": {
            "schema_version": 2,
            "config_type": "photo_setup",
            "phases": {
                "partial": {
                    "iso": 100,
                    "interval_s": 30,
                },
                "totality": {
                    "iso": 100,
                    "shutter_min": "4",
                    "shutter_max":
                        "1/4000",
                },
            },
        },
        "exposure_opt": {
            "atmospheric_attenuation_enabled":
                True,
            "rigs": [
                {
                    "rig_id": 1,
                    "photo": {
                        "anti_trailing_enabled":
                            True,
                        "iso_compensation_enabled":
                            True,
                        "iso_max": 6400,
                    },
                }
            ],
        },
        "rig": {
            "rig_id": 1,
            "name": "Main",
            "enabled": True,
            "devices": {
                "camera": {
                    "backend": "sony",
                    "manufacturer":
                        "Sony Corporation",
                    "model": "ILCE-7M5",
                    "serial": "12345",
                },
                "mount": {
                    "model": "AM3",
                    "tracking": "solar",
                },
                "focuser": {
                    "model": "ZWO EAF",
                },
            },
            "optics": {
                "name": "WO Z73",
                "focal_length_mm": 430,
            },
            "photo": {
                "anti_trailing_enabled":
                    True,
                "iso_compensation_enabled":
                    True,
                "iso_max": 6400,
            },
        },
        "effective_rig": {
            "rig_id": 1,
            "photo": {
                "atmos_enabled": True,
                "anti_trailing_enabled":
                    True,
                "iso_compensation_enabled":
                    True,
                "iso_max": 6400,
            },
        },
    }


def test_filename_uses_date_rig_brand_and_name():
    filename = (
        build_execution_plan_filename(
            circumstances=
                _context()["circumstances"],
            rig=_context()["rig"],
            plan_name="Egypt final",
        )
    )

    assert filename == (
        "exec_plan_20270802_"
        "RIG1_SONY_Egypt_final.plan"
    )


def test_text_plan_roundtrip_preserves_contract(
    tmp_path,
):
    filename = (
        "exec_plan_20270802_"
        "RIG1_SONY_Egypt.plan"
    )

    text = render_execution_plan_text(
        _plan(),
        filename=filename,
        context=_context(),
        generated_at=datetime(
            2026,
            9,
            4,
            18,
            0,
            tzinfo=timezone.utc,
        ),
        software={
            "solartrigger_version": "7.1",
            "solartrigger_commit":
                "deadbeef",
        },
    )

    assert "# --- ECLIPSE " in text
    assert (
        '# eclipse.C2="09:35:41.800"'
        in text
    )
    assert "# --- LOCATION " in text
    assert (
        "# location.latitude=23.923456"
        in text
    )
    assert (
        '# rig.devices.camera.manufacturer='
        '"Sony Corporation"'
        in text
    )
    assert (
        "# optimization."
        "atmospheric_attenuation_enabled=true"
        in text
    )
    assert (
        '# source.camera_timing_file='
        '"sony_ilce_7m5.json"'
        in text
    )
    assert (
        '# plan.solartrigger_version="7.1"'
        in text
    )
    assert (
        '# @phase="totality"'
        in text
    )
    assert (
        "2027-08-02T09:35:41.800Z | "
        "RIG1 | PHOTO | "
        in text
    )

    parsed = parse_execution_plan_text(
        text
    )

    assert (
        parsed["commands"]
        == _plan()["commands"]
    )
    assert (
        parsed["command_phases"]
        == _plan()["command_phases"]
    )
    assert (
        parsed["initial_state_required"]
        == _plan()[
            "initial_state_required"
        ]
    )

    path = tmp_path / filename
    path.write_text(
        text,
        encoding="utf-8",
    )

    loaded = load_execution_plan(path)

    assert (
        loaded["commands"]
        == _plan()["commands"]
    )
    assert (
        len(
            loaded["_commands_runtime"]
        )
        == 2
    )
    assert (
        loaded[
            "_commands_runtime"
        ][1]["action"]
        == "PHOTO"
    )


def test_legacy_json_still_loads(
    tmp_path,
):
    path = tmp_path / "legacy.json"

    path.write_text(
        json.dumps(_plan()),
        encoding="utf-8",
    )

    loaded = load_execution_plan(
        path
    )

    assert loaded["schema_version"] == 2
    assert (
        loaded["commands"]
        == _plan()["commands"]
    )
