import json
from datetime import datetime

import pytest

import backend.sequencer_plan_service as service
from backend.sequencer_plan_service import (
    SequencerCompileError,
    compile_execution_plan_from_files,
)


def _write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2),
        encoding="utf-8",
    )


def _build_configs(tmp_path):
    configs = tmp_path / "configs"

    _write_json(
        configs / "circumstances" / "test.json",
        {
            "C1": "10:00:00.000",
            "C2": "10:02:00.000",
            "TMAX": "10:02:30.000",
            "C3": "10:03:00.000",
            "C4": "10:05:00.000",
            "TSTART": "09:59:00.000",
            "TEND": "10:06:00.000",
        },
    )

    _write_json(
        configs / "photo_cfg" / "photo.json",
        {
            "schema_version": 2,
            "config_type": "photo_setup",
            "phases": {
                "partial": {
                    "enabled": True,
                    "interval_s": 30,
                    "duration_s": None,
                    "iso": 100,
                    "aperture": "f/8",
                    "shutter_min": "1/250",
                    "shutter_max": "1/1000",
                    "step_ev": 1.0,
                },
                "diamond_ring": {
                    "enabled": True,
                    "interval_s": 10,
                    "duration_s": 30,
                    "iso": 100,
                    "aperture": "f/8",
                    "shutter_min": "1/60",
                    "shutter_max": "1/1000",
                    "step_ev": 1.0,
                },
                "totality": {
                    "enabled": True,
                    "interval_s": 15,
                    "duration_s": None,
                    "iso": 100,
                    "aperture": "f/8",
                    "shutter_min": "1/60",
                    "shutter_max": "1/1000",
                    "step_ev": 1.0,
                },
            },
        },
    )

    _write_json(
        configs / "exposure_opt" / "expo.json",
        {
            "schema_version": 1,
            "config_type": "exposure_optimization",
            "atmospheric_attenuation_enabled": False,
            "rigs": [
                {
                    "rig_id": 1,
                    "photo": {
                        "anti_trailing_enabled": False,
                        "iso_compensation_enabled": True,
                        "iso_max": 6400,
                    },
                },
                {
                    "rig_id": 2,
                    "photo": {
                        "anti_trailing_enabled": False,
                        "iso_compensation_enabled": True,
                        "iso_max": 6400,
                    },
                },
            ],
        },
    )

    _write_json(
        configs / "sequence" / "test_sequence.json",
        {
            "schema_version": 1,
            "config_type": "sequence",
            "circumstances_file": "test.json",
            "photo_setup_file": "photo.json",
            "exposure_opt_file": "expo.json",
            "sequence_margin_min": 1,
        },
    )


    return configs


def _rig(
    rig_id,
    *,
    enabled=True,
    backend="sony",
    manufacturer="Sony",
    model="ILCE-7M5",
):
    return {
        "rig_id": rig_id,
        "name": f"RIG {rig_id}",
        "enabled": enabled,
        "devices": {
            "camera": {
                "backend": backend,
                "manufacturer": manufacturer,
                "model": model,
            },
        },
        "optics": {
            "focal_length_mm": 430,
        },
        "photo": {
            "atmos_enabled": False,
            "anti_trailing_enabled": False,
            "mechanical_vibration_enabled": False,
            "motion_tolerance_px": 1.0,
            "iso_compensation_enabled": True,
            "iso_max": 6400,
        },
    }


def _context():
    return {
        "timeline": {
            "C1": datetime(2027, 8, 2, 10, 0, 0),
            "C2": datetime(2027, 8, 2, 10, 2, 0),
            "TMAX": datetime(2027, 8, 2, 10, 2, 30),
            "C3": datetime(2027, 8, 2, 10, 3, 0),
            "C4": datetime(2027, 8, 2, 10, 5, 0),
        },
    }


def _timing(
    configs,
    filename,
    latency,
    *,
    backend="sony",
    manufacturer="Sony",
    model="ILCE-7M5",
    set_iso_ms=100,
    set_capturemode_ms=120,
    set_shutter_ms=150,
    trigger_single_latency_ms=250,
    trigger_single_duration_ms=0,
    bracket_release_ms=0,
    settle_idle_ms=0,
    bracket_atomic_ms_by_frames=None,
):
    if bracket_atomic_ms_by_frames is None:
        bracket_atomic_ms_by_frames = (
            {
                "3": 3000,
                "5": 3200,
                "7": 3600,
                "9": 4000,
            }
            if backend == "sony"
            else {}
        )

    _write_json(
        configs / "camera_timing" / filename,
        {
            "schema_version": 1,
            "config_type": "camera_timing",
            "backend": backend,
            "manufacturer": manufacturer,
            "model": model,
            "timing": {
                "set_iso_ms": set_iso_ms,
                "set_capturemode_ms": set_capturemode_ms,
                "set_shutter_ms": set_shutter_ms,
                "trigger_single_latency_ms": trigger_single_latency_ms,
                "trigger_single_duration_ms": trigger_single_duration_ms,
                "bracket_press_latency_ms": latency,
                "bracket_release_ms": bracket_release_ms,
                "settle_idle_ms": settle_idle_ms,
                "bracket_atomic_ms_by_frames": (
                    bracket_atomic_ms_by_frames
                ),
            },
        },
    )


def test_service_compiles_complete_plan_without_hardware(
    tmp_path,
    monkeypatch,
):
    configs = _build_configs(tmp_path)
    _timing(configs, "rig1.json", 280)

    monkeypatch.setattr(
        service,
        "load_eclipse_context",
        lambda _path: _context(),
    )

    rig_config = {
        "rigs": [_rig(1, enabled=False)],
        "sequence": {"common": {}},
        "eclipse": None,
    }

    plan, lines = compile_execution_plan_from_files(
        configs_dir=configs,
        circumstances_file="test.json",
        photo_setup_file="photo.json",
        exposure_opt_file="expo.json",
        sequence_margin_min=1,
        sequence_file="test_sequence.json",
        rig_config=rig_config,
    )

    assert plan["schema_version"] == 2
    assert plan["config_type"] == "execution_plan"

    assert plan["sources"] == {
        "circumstances_file": "test.json",
        "photo_setup_file": "photo.json",
        "exposure_opt_file": "expo.json",
        "sequence_file": "test_sequence.json",
    }

    assert plan["camera_timing_files"] == {
        "1": "rig1.json",
    }

    # RIG1 remains active even with enabled=false.
    assert set(plan["initial_state_required"]) == {"1"}

    assert plan["commands"]
    assert lines

    assert {
        command["action"]
        for command in plan["commands"]
    } <= {"SET", "PHOTO"}

    assert any(
        command["action"] == "PHOTO"
        for command in plan["commands"]
    )


def test_service_requires_matching_timing_for_every_active_rig(
    tmp_path,
    monkeypatch,
):
    configs = _build_configs(tmp_path)
    _timing(configs, "rig1.json", 280)

    monkeypatch.setattr(
        service,
        "load_eclipse_context",
        lambda _path: _context(),
    )

    rig_config = {
        "rigs": [
            _rig(1, enabled=False),
            _rig(
                2,
                enabled=True,
                backend="sony",
                manufacturer="Sony",
                model="UNSUPPORTED-MODEL",
            ),
        ],
        "sequence": {"common": {}},
        "eclipse": None,
    }

    with pytest.raises(
        SequencerCompileError,
        match="no calibrated camera timing profile matches RIG 2",
    ):
        compile_execution_plan_from_files(
            configs_dir=configs,
            circumstances_file="test.json",
            photo_setup_file="photo.json",
            exposure_opt_file="expo.json",
            sequence_margin_min=1,
            sequence_file="test_sequence.json",
            rig_config=rig_config,
        )


def test_service_reuses_timing_profile_for_same_camera_identity(
    tmp_path,
    monkeypatch,
):
    configs = _build_configs(tmp_path)

    _timing(configs, "sony_profile.json", 275)

    monkeypatch.setattr(
        service,
        "load_eclipse_context",
        lambda _path: _context(),
    )

    rig_config = {
        "rigs": [
            _rig(1, enabled=False),
            _rig(2, enabled=True),
        ],
        "sequence": {"common": {}},
        "eclipse": None,
    }

    plan, _lines = compile_execution_plan_from_files(
        configs_dir=configs,
        circumstances_file="test.json",
        photo_setup_file="photo.json",
        exposure_opt_file="expo.json",
        sequence_margin_min=1,
        sequence_file="test_sequence.json",
        rig_config=rig_config,
    )

    assert plan["camera_timing_files"] == {
        "1": "sony_profile.json",
        "2": "sony_profile.json",
    }

    photos_by_rig = {
        rig_id: [
            command
            for command in plan["commands"]
            if command["rig_id"] == rig_id
            and command["action"] == "PHOTO"
        ]
        for rig_id in (1, 2)
    }

    assert photos_by_rig[1]
    assert photos_by_rig[2]

    # Same camera identity + same timing profile + same logical target:
    # physical trigger times remain identical.
    assert (
        photos_by_rig[1][0]["time_utc"]
        == photos_by_rig[2][0]["time_utc"]
    )


def test_service_rejects_path_traversal(
    tmp_path,
):
    configs = _build_configs(tmp_path)

    with pytest.raises(
        SequencerCompileError,
        match="invalid circumstances filename",
    ):
        compile_execution_plan_from_files(
            configs_dir=configs,
            circumstances_file="../test.json",
            photo_setup_file="photo.json",
            exposure_opt_file="expo.json",
            sequence_margin_min=1,
            rig_config={
                "rigs": [_rig(1)],
            },
        )


def test_service_camera_backends_are_independent_of_rig_numbers(
    tmp_path,
    monkeypatch,
):
    configs = _build_configs(tmp_path)

    expo_path = configs / "exposure_opt" / "expo.json"
    expo = json.loads(expo_path.read_text(encoding="utf-8"))
    expo["rigs"].append({
        "rig_id": 4,
        "photo": {
            "anti_trailing_enabled": False,
            "iso_compensation_enabled": True,
            "iso_max": 6400,
        },
    })
    _write_json(expo_path, expo)

    _timing(
        configs,
        "d850_rig1.json",
        0,
        backend="nikon-dslr",
        manufacturer="Nikon",
        model="D850",
        set_iso_ms=550,
        set_capturemode_ms=0,
        set_shutter_ms=543,
        trigger_single_latency_ms=285,
        trigger_single_duration_ms=285,
        bracket_release_ms=0,
        settle_idle_ms=0,
    )

    _timing(
        configs,
        "sony_rig4.json",
        840,
        backend="sony",
        manufacturer="Sony",
        model="ILCE-7M5",
        set_iso_ms=830,
        set_capturemode_ms=838,
        set_shutter_ms=827,
        trigger_single_latency_ms=26,
        bracket_release_ms=854,
        settle_idle_ms=666,
    )

    monkeypatch.setattr(
        service,
        "load_eclipse_context",
        lambda _path: _context(),
    )

    rig_config = {
        "rigs": [
            _rig(
                1,
                enabled=False,
                backend="nikon-dslr",
                manufacturer="Nikon",
                model="D850",
            ),
            _rig(
                4,
                enabled=True,
                backend="sony",
                manufacturer="Sony",
                model="ILCE-7M5",
            ),
        ],
        "sequence": {"common": {}},
        "eclipse": None,
    }

    plan, lines = compile_execution_plan_from_files(
        configs_dir=configs,
        circumstances_file="test.json",
        photo_setup_file="photo.json",
        exposure_opt_file="expo.json",
        sequence_margin_min=1,
        sequence_file="test_sequence.json",
        rig_config=rig_config,
    )

    assert set(plan["initial_state_required"]) == {"1", "4"}

    assert plan["camera_timing_files"] == {
        "1": "d850_rig1.json",
        "4": "sony_rig4.json",
    }

    rig1 = [
        command
        for command in plan["commands"]
        if command["rig_id"] == 1
    ]

    rig4 = [
        command
        for command in plan["commands"]
        if command["rig_id"] == 4
    ]

    assert rig1
    assert rig4

    assert any(
        command["action"] == "PHOTO"
        for command in rig1
    )

    assert any(
        command["action"] == "PHOTO"
        for command in rig4
    )

    # Nikon uses its own shutter parameter and never Sony capturemode.
    assert any(
        command["action"] == "SET"
        and command["params"].get("parameter") == "shutterspeed2"
        for command in rig1
    )

    assert not any(
        command["action"] == "SET"
        and command["params"].get("parameter") == "capturemode"
        for command in rig1
    )

    # Sony remains independent of the logical RIG number.
    assert any(
        command["action"] == "SET"
        and command["params"].get("parameter") == "capturemode"
        for command in rig4
    )

    assert lines


def test_service_sequence_bounds_are_logical_window_not_photo_targets(
    tmp_path,
    monkeypatch,
):
    configs = _build_configs(tmp_path)
    _timing(configs, "rig1.json", 280)

    monkeypatch.setattr(
        service,
        "load_eclipse_context",
        lambda _path: _context(),
    )

    rig_config = {
        "rigs": [_rig(1, enabled=False)],
        "sequence": {"common": {}},
        "eclipse": None,
    }

    plan, _lines = compile_execution_plan_from_files(
        configs_dir=configs,
        circumstances_file="test.json",
        photo_setup_file="photo.json",
        exposure_opt_file="expo.json",
        sequence_margin_min=1,
        sequence_file="test_sequence.json",
        rig_config=rig_config,
    )

    assert plan["sequence_start_utc"] == (
        "2027-08-02T09:59:00.000Z"
    )

    assert plan["sequence_end_utc"] == (
        "2027-08-02T10:06:00.000Z"
    )

    assert all(
        command["time_utc"].endswith("Z")
        for command in plan["commands"]
    )

    assert plan["commands"] == sorted(
        plan["commands"],
        key=lambda item: (
            item["time_utc"],
            item["rig_id"],
        ),
    )


def test_service_current_run_values_override_sequence_preset(
    tmp_path,
    monkeypatch,
):
    configs = _build_configs(tmp_path)
    _timing(configs, "rig1.json", 280)

    _write_json(
        configs / "sequence" / "canonical.json",
        {
            "schema_version": 1,
            "config_type": "sequence",
            "circumstances_file": "test.json",
            "photo_setup_file": "photo.json",
            "exposure_opt_file": "expo.json",
            "sequence_margin_min": 60,
        },
    )

    monkeypatch.setattr(
        service,
        "load_eclipse_context",
        lambda _path: _context(),
    )

    rig_config = {
        "rigs": [_rig(1, enabled=False)],
        "sequence": {"common": {}},
        "eclipse": None,
    }

    plan, _lines = compile_execution_plan_from_files(
        configs_dir=configs,
        circumstances_file="test.json",
        photo_setup_file="photo.json",
        exposure_opt_file="expo.json",
        sequence_margin_min=5,
        sequence_file="canonical.json",
        rig_config=rig_config,
    )

    assert plan["sources"] == {
        "circumstances_file": "test.json",
        "photo_setup_file": "photo.json",
        "exposure_opt_file": "expo.json",
        "sequence_file": "canonical.json",
    }

    assert plan["sequence_margin_min"] == 5

    assert plan["sequence_start_utc"] == (
        "2027-08-02T09:55:00.000Z"
    )

    assert plan["sequence_end_utc"] == (
        "2027-08-02T10:10:00.000Z"
    )


def test_service_rejects_unconfigured_active_rig_camera(
    tmp_path,
    monkeypatch,
):
    configs = _build_configs(tmp_path)

    monkeypatch.setattr(
        service,
        "load_eclipse_context",
        lambda _path: _context(),
    )

    rig_config = {
        "rigs": [
            {
                "rig_id": 1,
                "name": "RIG 1",
                "enabled": False,
                "devices": {
                    "camera": {
                        "backend": "none",
                        "manufacturer": None,
                        "model": None,
                        "serial": None,
                    },
                    "mount": None,
                    "focuser": None,
                },
                "optics": {
                    "focal_length_mm": 430,
                },
                "photo": {},
            },
        ],
        "sequence": {"common": {}},
        "eclipse": None,
    }

    with pytest.raises(
        SequencerCompileError,
        match=r"RIG 1 is not configured: camera required",
    ):
        compile_execution_plan_from_files(
            configs_dir=configs,
            circumstances_file="test.json",
            photo_setup_file="photo.json",
            exposure_opt_file="expo.json",
            sequence_margin_min=1,
            rig_config=rig_config,
        )


def test_service_rejects_unconfigured_active_rig_focal_length(
    tmp_path,
    monkeypatch,
):
    configs = _build_configs(tmp_path)

    monkeypatch.setattr(
        service,
        "load_eclipse_context",
        lambda _path: _context(),
    )

    rig_config = {
        "rigs": [
            {
                "rig_id": 1,
                "name": "RIG 1",
                "enabled": False,
                "devices": {
                    "camera": {
                        "backend": "sony",
                        "manufacturer": "Sony",
                        "model": "ILCE-7M5",
                        "serial": None,
                    },
                    "mount": None,
                    "focuser": None,
                },
                "optics": {
                    "focal_length_mm": None,
                },
                "photo": {},
            },
        ],
        "sequence": {"common": {}},
        "eclipse": None,
    }

    with pytest.raises(
        SequencerCompileError,
        match=r"RIG 1 is not configured: focal length required",
    ):
        compile_execution_plan_from_files(
            configs_dir=configs,
            circumstances_file="test.json",
            photo_setup_file="photo.json",
            exposure_opt_file="expo.json",
            sequence_margin_min=1,
            rig_config=rig_config,
        )
