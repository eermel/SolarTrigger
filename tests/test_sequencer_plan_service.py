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
):
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
        sequence_file="test_sequence.json",
        camera_timing_files={
            1: "rig1.json",
        },
        rig_config=rig_config,
    )

    assert plan["schema_version"] == 1
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

    # RIG1 is active even with enabled=false.
    assert set(plan["initial_state_required"]) == {"1"}

    assert plan["targets"]
    assert plan["events"]
    assert lines

    # Compilation only describes Sony commands.
    assert any(
        event["operation"].get("action") == "bracket_press"
        for event in plan["events"]
    )


def test_service_requires_timing_for_every_active_rig(
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
            _rig(2, enabled=True),
        ],
        "sequence": {"common": {}},
        "eclipse": None,
    }

    with pytest.raises(
        SequencerCompileError,
        match="missing calibrated camera timing profile for RIG 2",
    ):
        compile_execution_plan_from_files(
            configs_dir=configs,
            sequence_file="test_sequence.json",
            camera_timing_files={
                1: "rig1.json",
            },
            rig_config=rig_config,
        )


def test_service_uses_distinct_timing_per_rig(
    tmp_path,
    monkeypatch,
):
    configs = _build_configs(tmp_path)

    _timing(configs, "rig1.json", 275)
    _timing(configs, "rig2.json", 291)

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
        sequence_file="test_sequence.json",
        camera_timing_files={
            1: "rig1.json",
            2: "rig2.json",
        },
        rig_config=rig_config,
    )

    first_target = min(
        target["target_time"]
        for target in plan["targets"]
    )

    triggers = [
        event
        for event in plan["events"]
        if event["target_time"] == first_target
        and event["operation"].get("action") == "bracket_press"
    ]

    assert len(triggers) == 2

    by_rig = {
        event["rig_id"]: event["command_time"]
        for event in triggers
    }

    assert by_rig[1].endswith("59.725")
    assert by_rig[2].endswith("59.709")


def test_service_rejects_path_traversal(
    tmp_path,
):
    configs = _build_configs(tmp_path)

    with pytest.raises(
        SequencerCompileError,
        match="invalid Sequence filename",
    ):
        compile_execution_plan_from_files(
            configs_dir=configs,
            sequence_file="../test_sequence.json",
            camera_timing_files={},
            rig_config={
                "rigs": [_rig(1)],
            },
        )


def test_service_camera_backends_are_independent_of_rig_numbers(
    tmp_path,
    monkeypatch,
):
    configs = _build_configs(tmp_path)

    # Exposure Optimization must also know about RIG4.
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

    # Real measured D850 timing, attached here to RIG1.
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

    # Real measured Sony A7V timing, attached here to RIG4.
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
        sequence_file="test_sequence.json",
        camera_timing_files={
            1: "d850_rig1.json",
            4: "sony_rig4.json",
        },
        rig_config=rig_config,
    )

    assert set(plan["initial_state_required"]) == {"1", "4"}

    assert plan["camera_timing_files"] == {
        "1": "d850_rig1.json",
        "4": "sony_rig4.json",
    }

    # RIG1 is the Nikon D850.
    nikon_events = [
        event
        for event in plan["events"]
        if event["rig_id"] == 1
    ]

    assert nikon_events
    assert all(
        event["backend"] == "nikon-dslr"
        for event in nikon_events
    )

    assert any(
        event["operation"].get("action") == "trigger_capture"
        for event in nikon_events
    )

    assert not any(
        event["operation"].get("action") in {
            "bracket_press",
            "bracket_release",
        }
        for event in nikon_events
    )

    assert not any(
        event["operation"].get("parameter") == "capturemode"
        for event in nikon_events
    )

    # RIG4 is the Sony.
    sony_events = [
        event
        for event in plan["events"]
        if event["rig_id"] == 4
    ]

    assert sony_events
    assert all(
        event["backend"] == "sony"
        for event in sony_events
    )

    assert any(
        event["operation"].get("action") == "bracket_press"
        for event in sony_events
    )

    # Verify the measured trigger compensation on each physical backend.
    nikon_trigger = next(
        event
        for event in nikon_events
        if event["operation"].get("action") == "trigger_capture"
        and event["command_time"] is not None
    )

    nikon_target = datetime.fromisoformat(
        nikon_trigger["target_time"]
    )
    nikon_command = datetime.fromisoformat(
        nikon_trigger["command_time"]
    )

    assert (
        nikon_target - nikon_command
    ).total_seconds() == pytest.approx(
        0.285,
        abs=0.001,
    )

    sony_trigger = next(
        event
        for event in sony_events
        if event["operation"].get("action") == "bracket_press"
        and event["command_time"] is not None
    )

    sony_target = datetime.fromisoformat(
        sony_trigger["target_time"]
    )
    sony_command = datetime.fromisoformat(
        sony_trigger["command_time"]
    )

    assert (
        sony_target - sony_command
    ).total_seconds() == pytest.approx(
        0.840,
        abs=0.001,
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
        sequence_file="test_sequence.json",
        camera_timing_files={
            1: "rig1.json",
        },
        rig_config=rig_config,
    )

    assert plan["sequence_start"] == (
        "2027-08-02T09:59:00.000"
    )
    assert plan["sequence_end"] == (
        "2027-08-02T10:06:00.000"
    )

    assert plan["target_start"] == min(
        target["target_time"]
        for target in plan["targets"]
    )
    assert plan["target_end"] == max(
        target["target_time"]
        for target in plan["targets"]
    )

    absolute_commands = [
        event["command_time"]
        for event in plan["events"]
        if event["command_time"] is not None
    ]

    assert plan["command_start"] == min(
        absolute_commands
    )
    assert plan["command_end"] == max(
        absolute_commands
    )


def test_service_sequence_file_is_canonical_source(
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
            "sequence_margin_min": 1,
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
        sequence_file="canonical.json",
        camera_timing_files={
            1: "rig1.json",
        },
        rig_config=rig_config,

    )

    assert plan["sources"] == {
        "circumstances_file": "test.json",
        "photo_setup_file": "photo.json",
        "exposure_opt_file": "expo.json",
        "sequence_file": "canonical.json",
    }

    assert plan["sequence_margin_min"] == 1

    assert plan["sequence_start"] == (
        "2027-08-02T09:59:00.000"
    )

    assert plan["sequence_end"] == (
        "2027-08-02T10:06:00.000"
    )
