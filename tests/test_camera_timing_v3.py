from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from backend.camera_characterization import (
    _persistent_profile_document,
    _persistent_timing_document,
)
from backend.camera_timing_contract import (
    SAFETY_POLICY,
    bracket_photo_duration_ms,
    budget_ms,
    derive_bracket_components,
    single_photo_duration_ms,
)
from backend.camera_timing import load_camera_timing_profile
from plugins.camera.profile import ProfilePlugin


def contract(**overrides):
    result = {
        "version": 3,
        "safety_policy": deepcopy(SAFETY_POLICY),
        "set_overhead_ms": 950,
        "single_overhead_ms": 650,
        "bracket_overhead_ms": 2100,
        "bracket_inter_image_ms": 150,
        "supported_bracket_frames": [3],
    }
    result.update(overrides)
    return result


def profile():
    speeds = ["1/1000", "1/500", "1/250"]
    return {
        "schema_version": 1,
        "config_type": "camera_profile",
        "backend": "profile-v3-example",
        "manufacturer": "Example",
        "model": "Example Camera",
        "strategy": "bracket",
        "commands": {
            "manual_mode": {"path": "/main/mode", "value": "M"},
            "capture_target": {"path": "/main/target", "value": "card"},
            "raw": {"path": "/main/format", "value": "RAW"},
            "iso": {
                "path": "/main/iso",
                "values": {"100": "100", "200": "200"},
            },
            "shutter": {
                "path": "/main/shutter",
                "values": {value: value for value in speeds},
            },
            "capture_mode": {
                "path": "/main/drive",
                "value": "Single Shot",
            },
            "trigger_single": {"method": "trigger_capture"},
        },
        "brackets": {
            "3": {
                "step_ev": 1,
                "mode": "Continuous Bracket 1 EV 3 Img.",
                "trigger": {"method": "trigger_capture"},
                "peak_capture_ms": 9999,  # debug-only metadata
            }
        },
        "timing_contract": contract(),
        "benchmark": {"debug": True},
        "raw_timings": {"debug": True},
    }


def test_agreed_budget_policy_order():
    assert budget_ms([804]) == 950
    assert budget_ms([300]) == 400


def test_simple_duration_is_exposure_plus_fixed_overhead():
    assert single_photo_duration_ms(650, 4.0) == 4650
    assert single_photo_duration_ms(650, 1 / 500) == 652


def test_bracket_duration_is_sum_plus_fixed_plus_inter_image():
    exposures = [1 / 1000, 1 / 500, 1 / 250]
    expected = sum(exposures) * 1000 + 2100 + 2 * 150
    assert bracket_photo_duration_ms(2100, 150, exposures) == pytest.approx(expected)


def test_bracket_component_derivation_covers_all_measured_peaks():
    derived = derive_bracket_components({
        3: [2200, 2250],
        5: [2500, 2550],
        7: [2850, 2800],
    })
    fixed = derived["raw_bracket_overhead_ms"]
    inter = derived["raw_bracket_inter_image_ms"]
    for raw_frames, peak in derived["peak_overhead_ms_by_frames"].items():
        frames = int(raw_frames)
        assert fixed + (frames - 1) * inter >= peak


def test_v3_profile_planner_emits_discrete_set_commands():
    data = profile()
    prepared = ProfilePlugin(None, profile=data).prepare_capture(
        SimpleNamespace(
            exposure_plan=[
                {"shutter": "1/1000", "iso": 100},
                {"shutter": "1/500", "iso": 100},
                {"shutter": "1/250", "iso": 100},
            ]
        )
    )
    operations = prepared.token[1]
    assert [item["action"] for item in operations] == [
        "set", "set", "set", "set", "bracket_press"
    ]
    assert [item.get("parameter") for item in operations[:-1]] == [
        "iso", "capturemode", "shutterspeed", "capturemode"
    ]
    assert all(item["duration_ms"] == 950 for item in operations[:-1])
    photo = operations[-1]
    assert photo["camera_timing_model_version"] == 3
    assert photo["duration_ms"] == pytest.approx(
        bracket_photo_duration_ms(
            2100,
            150,
            [1 / 1000, 1 / 500, 1 / 250],
        )
    )


def test_persistent_documents_strip_debug_history(tmp_path):
    rich_profile = profile()
    rich_timing = {
        "schema_version": 2,
        "config_type": "camera_timing",
        "backend": rich_profile["backend"],
        "manufacturer": rich_profile["manufacturer"],
        "model": rich_profile["model"],
        "timing_contract": deepcopy(rich_profile["timing_contract"]),
        "raw_timing": {"x": 1},
        "timing_trials": [{"x": 1}],
        "set_trials": {"iso": [1, 2, 3]},
    }

    stored_profile = _persistent_profile_document(rich_profile)
    stored_timing = _persistent_timing_document(rich_timing)

    assert "benchmark" not in stored_profile
    assert "raw_timings" not in stored_profile
    assert "peak_capture_ms" not in stored_profile["brackets"]["3"]

    assert set(stored_timing) == {
        "schema_version",
        "config_type",
        "backend",
        "manufacturer",
        "model",
        "timing_contract",
    }
    assert "raw_timing" not in stored_timing
    assert "timing_trials" not in stored_timing

    path = tmp_path / "timing.json"
    path.write_text(json.dumps(stored_timing), encoding="utf-8")
    loaded = load_camera_timing_profile(path)
    assert loaded.backend == rich_profile["backend"]
