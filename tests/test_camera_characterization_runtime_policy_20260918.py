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


def test_capture_validation_rejects_incomplete_usb_count_automatically():
    assert characterization._capture_validation_state(5, 4, None) == "incomplete"
    assert (
        characterization._capture_validation_state(5, 0, RuntimeError("USB"))
        == "incomplete"
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


def test_bracket_selection_prefers_reliable_then_shortest_complete_capture():
    entries = [
        _entry("capture", 1200, 2200, 2000),
        _entry("bulb", 1000, 2400, 2100),
        _entry("fast-but-unreliable", 100, 200, 150, reliable=False),
    ]

    # FILE_ADDED may arrive earlier for bulb, but complete operational
    # duration is longer. The faster complete capture path must win.
    assert (
        characterization._select_bracket_candidate(entries)["command_id"]
        == "capture"
    )


def test_bracket_sizes_can_select_different_capture_primitives():
    bracket3 = [
        _entry("capture", 900, 1700, 1600),
        _entry("bulb", 800, 1800, 1700),
    ]
    bracket9 = [
        _entry("capture", 1600, 4200, 4000),
        _entry("bulb", 1100, 3900, 3700),
    ]

    assert (
        characterization._select_bracket_candidate(bracket3)["command_id"]
        == "capture"
    )
    assert (
        characterization._select_bracket_candidate(bracket9)["command_id"]
        == "bulb"
    )


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




def test_operational_qualification_keeps_one_persistent_camera_session():
    source = inspect.getsource(characterization.qualify_operational_contract_v3)
    assert "quiesce_before_session_reopen" not in source
    assert "fresh gphoto session" not in source
    assert "persistent camera session" in source
    assert "RUNTIME QUALIFICATION COMMAND" in source


def test_operational_qualification_prompts_only_for_physical_preflight():
    source = inspect.getsource(characterization.qualify_operational_contract_v3)
    assert "except CameraPhysicalPreflightError as exc:" in source
    assert "except CameraPreflightError as exc:" not in source
    assert "physically saved on the card" not in source
    assert "Operator physical-card check" not in source
    assert "_ensure_camera_storage" not in source

def test_final_operational_qualification_starts_without_global_go_prompt():
    source = inspect.getsource(characterization.qualify_operational_contract_v3)
    assert "Final operational qualification before publication" not in source
    assert "Final operational qualification starts automatically" in source



def test_sony_bracket_selection_rejects_slow_bulb_usb_tail():
    """Sony a7V: complete USB-ready duration must dominate FILE_ADDED timing."""

    entries = [
        # Real characterization order of magnitude:
        # /main/actions/capture BRK5 total ~= 1263.7 ms.
        _entry(
            "capture",
            1200.0,
            1263.7,
            1263.7,
        ),
        # /main/actions/bulb can expose FILE_ADDED competitively but leaves
        # the body unavailable for SET for ~1.7 s afterwards:
        # total ~= 2941.5 ms.
        _entry(
            "bulb",
            1000.0,
            2941.5,
            2941.5,
        ),
    ]

    selected = characterization._select_bracket_candidate(entries)

    assert selected["command_id"] == "capture"
    assert selected["spec"]["peak_capture_ms"] == 1263.7
