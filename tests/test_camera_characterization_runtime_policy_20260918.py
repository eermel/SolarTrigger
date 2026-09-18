from types import SimpleNamespace
import inspect

import pytest

import backend.camera_characterization as characterization
import plugins.camera.profile as profile_module
from backend.camera_timing_contract import SAFETY_POLICY
from plugins.camera.profile import ProfilePlugin


def _profile_with_timing_contract():
    return {
        "schema_version": 1,
        "config_type": "camera_profile",
        "backend": "profile-runtime-write-test",
        "manufacturer": "Example",
        "model": "Example Camera",
        "strategy": "sequential",
        "commands": {
            "manual_mode": {"path": "/main/mode", "value": "M"},
            "capture_target": {"path": "/main/target", "value": "card"},
            "raw": {"path": "/main/format", "value": "RAW"},
            "iso": {
                "path": "/main/iso",
                "value": "100",
                "values": {"100": "100", "200": "200"},
            },
            "shutter": {
                "path": "/main/shutter",
                "value": "1/500",
                "values": {"1/500": "1/500"},
            },
            "trigger_single": {"method": "trigger_capture"},
        },
        "brackets": {},
        "timing_contract": {
            "version": 3,
            "safety_policy": dict(SAFETY_POLICY),
            "set_overhead_ms": 1000,
            "single_overhead_ms": 1000,
            "prepare_lead_ms": 2000,
            "bracket_overhead_ms": 0,
            "bracket_inter_image_ms": 0,
            "supported_bracket_frames": [],
        },
    }


def test_capture_validation_is_automatic_for_exact_file_count():
    assert characterization._capture_validation_state(5, 5, None) == "confirmed"


def test_capture_validation_does_not_prompt_when_files_are_exact_but_command_failed():
    assert (
        characterization._capture_validation_state(5, 5, RuntimeError("USB"))
        == "runtime_error"
    )


def test_capture_validation_requests_human_evidence_only_when_usb_count_is_incomplete():
    assert characterization._capture_validation_state(5, 4, None) == "ambiguous"
    assert (
        characterization._capture_validation_state(5, 0, RuntimeError("USB"))
        == "ambiguous"
    )


def _entry(candidate_id, prepare_first, capture_peak, median, reliable=True):
    evidence = SimpleNamespace(
        reliable=reliable,
        median_ms=median,
        candidate_id=candidate_id,
    )
    return {
        "command_id": candidate_id,
        "evidence": evidence,
        "spec": {
            "peak_prepare_to_first_file_ms": prepare_first,
            "peak_capture_ms": capture_peak,
        },
    }


def test_bracket_selection_prefers_reliable_then_fastest_synchronization_path():
    entries = [
        _entry("capture", 1200, 2200, 2000),
        _entry("bulb", 1000, 2400, 2100),
        _entry("fast-but-unreliable", 100, 200, 150, reliable=False),
    ]
    assert characterization._select_bracket_candidate(entries)["command_id"] == "bulb"


def test_bracket_sizes_can_select_different_capture_primitives():
    bracket3 = [
        _entry("capture", 800, 1800, 1700),
        _entry("bulb", 900, 1700, 1600),
    ]
    bracket9 = [
        _entry("capture", 1600, 4200, 4000),
        _entry("bulb", 1100, 3900, 3700),
    ]
    assert characterization._select_bracket_candidate(bracket3)["command_id"] == "capture"
    assert characterization._select_bracket_candidate(bracket9)["command_id"] == "bulb"


def test_runtime_apply_uses_legacy_write_only_for_legacy_profile(monkeypatch):
    plugin = ProfilePlugin(
        None,
        log_fn=lambda _message: None,
        profile=_profile_with_timing_contract(),
    )
    plugin._writable_cache.add("iso")

    calls = []

    def fake_write_widget(camera, path, value):
        calls.append(("write_widget", path, value))

    monkeypatch.setattr(profile_module, "write_widget", fake_write_widget)

    # Profiles characterized before the direct-writer metadata remain usable.
    assert plugin._apply("iso", "200") is True
    assert calls == [("write_widget", "/main/iso", "200")]


def test_characterization_source_has_no_per_capture_go_prompt():
    source = inspect.getsource(characterization.characterize)
    assert "Ready for a test of" not in source
    assert "rejection at one size never suppresses another" not in source
    assert "rejected methods are pruned from larger sizes" in source
    assert "selected_candidate_ids_by_frames" in source


def test_final_operational_qualification_starts_without_global_go_prompt():
    source = inspect.getsource(characterization.qualify_operational_contract_v3)
    assert "Final operational qualification before publication" not in source
    assert "Final operational qualification starts automatically" in source
