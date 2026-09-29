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


def test_bracket_timing_calibration_retries_rejected_samples_but_requires_five_valid():
    source = inspect.getsource(characterization.characterize)
    assert "BRACKET TIMING RETRY" in source
    assert "BRACKET TIMING PASS" in source
    assert "max_retry_failures = target_trials" in source
    assert "while len(samples) < target_trials:" in source
    assert "could not collect {target_trials} valid samples" in source


def test_bracket_timing_retry_always_restores_single_mode():
    source = inspect.getsource(characterization.characterize)
    marker = "# Always converge to the production baseline after a"
    assert marker in source
    tail = source[source.index(marker):]
    assert 'runtime_set(\n                        "capture_mode",' in tail


def test_characterization_recovers_poisoned_exploratory_session_before_final_qualification():
    source = inspect.getsource(characterization.characterize)

    assert "exploratory_usb_failure = False" in source
    assert "exploratory_usb_failure = True" in source
    recovery = source.index("if exploratory_usb_failure:")
    qualification = source.index(
        "operational_qualification = qualify_operational_contract_v3("
    )
    assert recovery < qualification
    recovery_block = source[recovery:qualification]
    assert "camera.exit()" in recovery_block
    assert "direct_nodes.clear()" in recovery_block
    assert "camera.init()" in recovery_block
    assert "converge_characterized_preflight()" in recovery_block



def test_single_trigger_selection_prefers_capture_inside_50ms_tie_band():
    from backend.camera_candidate_optimizer import CandidateEvidence

    trigger = CandidateEvidence(
        candidate_id='trigger',
        recipe={'method': 'trigger_capture'},
        expected_trials=5,
        durations_ms=[1440.0, 1445.0, 1450.0, 1451.9, 1447.0],
        functional_ok=True,
    )
    capture = CandidateEvidence(
        candidate_id='capture',
        recipe={'method': 'capture'},
        expected_trials=5,
        durations_ms=[1470.0, 1475.0, 1480.0, 1497.3, 1473.8],
        functional_ok=True,
    )

    selected = characterization._select_single_trigger_candidate([trigger, capture])

    assert selected is capture


def test_single_trigger_selection_keeps_materially_faster_trigger_capture():
    from backend.camera_candidate_optimizer import CandidateEvidence

    trigger = CandidateEvidence(
        candidate_id='trigger',
        recipe={'method': 'trigger_capture'},
        expected_trials=5,
        durations_ms=[1090.0, 1110.0, 1175.0, 1247.0, 1120.0],
        functional_ok=True,
    )
    capture = CandidateEvidence(
        candidate_id='capture',
        recipe={'method': 'capture'},
        expected_trials=5,
        durations_ms=[1125.0, 1175.0, 1190.0, 1238.0, 1320.0],
        functional_ok=True,
    )

    selected = characterization._select_single_trigger_candidate([trigger, capture])

    assert selected is trigger


def test_single_trigger_selection_ignores_unreliable_capture_candidate():
    from backend.camera_candidate_optimizer import CandidateEvidence

    trigger = CandidateEvidence(
        candidate_id='trigger',
        recipe={'method': 'trigger_capture'},
        expected_trials=5,
        durations_ms=[1000.0] * 5,
        functional_ok=True,
    )
    capture = CandidateEvidence(
        candidate_id='capture',
        recipe={'method': 'capture'},
        expected_trials=5,
        durations_ms=[990.0] * 4,
        functional_ok=True,
    )

    selected = characterization._select_single_trigger_candidate([trigger, capture])

    assert selected is trigger

def test_single_rearm_search_finds_lowest_stable_50ms_candidate_and_reverifies():
    calls = []
    recoveries = []

    def probe(delay_ms):
        calls.append(delay_ms)
        if delay_ms < 600:
            raise RuntimeError("device busy")
        return {"delay_ms": delay_ms, "photos": 10}

    def recover(delay_ms, exc):
        recoveries.append((delay_ms, str(exc)))

    result = characterization._search_single_rearm_ms(
        probe,
        recover,
        start_ms=1200,
    )

    assert result["minimum_stable_ms"] == 600
    assert calls.count(600) == 2
    assert all(value % 50 == 0 for value in calls)
    assert recoveries
    assert any(not item["passed"] for item in result["tested"])
    assert result["tested"][-1]["verification"] is True
    assert result["tested"][-1]["passed"] is True


def test_single_rearm_search_accepts_zero_only_after_real_probe():
    calls = []

    result = characterization._search_single_rearm_ms(
        lambda delay_ms: calls.append(delay_ms) or {"ok": True},
        lambda _delay_ms, _exc: pytest.fail("recovery should not be needed"),
        start_ms=1200,
    )

    assert result["minimum_stable_ms"] == 0
    assert 0 in calls
    assert calls.count(0) == 2


def test_single_rearm_search_expands_upper_bound_after_failure():
    recoveries = []

    def probe(delay_ms):
        if delay_ms < 1500:
            raise RuntimeError("too fast")
        return {"ok": True}

    result = characterization._search_single_rearm_ms(
        probe,
        lambda delay_ms, _exc: recoveries.append(delay_ms),
        start_ms=1000,
        max_ms=2000,
    )

    assert result["minimum_stable_ms"] == 1500
    assert 1000 in recoveries
    assert 1250 in recoveries
    assert any(
        item["delay_ms"] == 1500 and item["passed"]
        for item in result["tested"]
    )


def test_operational_ready_timing_excludes_characterization_readback():
    timings = characterization._operational_ready_timings(
        10.0,
        11.0,
        11.25,
        12.05,
    )

    assert timings["usb_return_ms"] == pytest.approx(250.0)
    assert timings["runtime_total_ms"] == pytest.approx(1250.0)
    assert timings["verification_ms"] == pytest.approx(800.0)
    assert timings["wall_ms"] == pytest.approx(2050.0)


def test_characterization_ready_probe_excludes_final_readback_from_runtime_tail():
    source = inspect.getsource(characterization.characterize)

    assert "successful_ready_started = attempt_started" in source
    assert "USB SET-ready runtime tail=" in source
    assert "readback verification=" in source


def test_operational_ready_timing_rejects_non_monotonic_timestamps():
    with pytest.raises(ValueError, match="not monotonic"):
        characterization._operational_ready_timings(
            10.0,
            11.0,
            10.5,
            12.0,
        )


def test_rearm_baseline_readback_waits_for_delayed_sony_convergence():
    values = iter(["1/1000", "1/1000", "1/500"])
    sleeps = []
    checks = []

    result = characterization._settle_characterization_readback(
        lambda: next(values),
        "1/500",
        check=lambda: checks.append(True),
        sleep_fn=lambda delay: sleeps.append(delay),
        delays_s=(0.10, 0.25, 0.50),
    )

    assert result == "1/500"
    assert sleeps == [0.10, 0.25]
    assert len(checks) == 3


def test_rearm_baseline_readback_does_not_delay_when_already_converged():
    sleeps = []

    result = characterization._settle_characterization_readback(
        lambda: "1/500",
        "1/500",
        sleep_fn=lambda delay: sleeps.append(delay),
        delays_s=(0.10, 0.25, 0.50),
    )

    assert result == "1/500"
    assert sleeps == []


def test_rearm_baseline_readback_returns_last_stale_value_after_bounded_settle():
    values = iter(["1/1000", "1/1000", "1/1000"])

    result = characterization._settle_characterization_readback(
        lambda: next(values),
        "1/500",
        sleep_fn=lambda _delay: None,
        delays_s=(0.10, 0.25),
    )

    assert result == "1/1000"


def test_characterization_single_rearm_probe_uses_real_set_then_immediate_trigger():
    source = inspect.getsource(characterization.characterize)
    marker = "# Critical point: no FILE_ADDED wait"
    assert marker in source
    assert "_settle_characterization_readback(" in source
    assert 'runtime_set("shutter", second_value)' in source
    tail = source[source.index(marker):]
    assert "camera.trigger_capture()" in tail
    assert "_confirm_rearm_files(2, timeout_s=6.0)" in tail
    assert "SINGLE REARM RESULT" in source
    assert "SINGLE REARM SUSTAINED" in source
    assert "SINGLE_REARM_SUSTAINED_FRAMES" in source



def test_sustained_rearm_qualification_keeps_first_passing_guarded_value():
    calls = []

    result = characterization._qualify_guarded_single_rearm_ms(
        lambda delay_ms: calls.append(delay_ms) or {
            "bursts": 3,
            "frames_per_burst": 15,
            "total_frames": 45,
        },
        lambda _delay_ms, _exc: pytest.fail("recovery should not be needed"),
        start_ms=50,
    )

    assert result["stable_ms"] == 50
    assert calls == [50]
    assert result["tested"] == [
        {
            "delay_ms": 50,
            "passed": True,
            "detail": {
                "bursts": 3,
                "frames_per_burst": 15,
                "total_frames": 45,
            },
        }
    ]


def test_sustained_rearm_qualification_increases_by_50ms_after_failure():
    calls = []
    recoveries = []

    def probe(delay_ms):
        calls.append(delay_ms)
        if delay_ms < 150:
            raise RuntimeError("buffer pressure")
        return {"bursts": 3, "frames_per_burst": 15}

    result = characterization._qualify_guarded_single_rearm_ms(
        probe,
        lambda delay_ms, exc: recoveries.append((delay_ms, str(exc))),
        start_ms=50,
        max_ms=500,
    )

    assert result["stable_ms"] == 150
    assert calls == [50, 100, 150]
    assert [item[0] for item in recoveries] == [50, 100]
    assert [item["passed"] for item in result["tested"]] == [
        False,
        False,
        True,
    ]


def test_sustained_rearm_probe_has_no_file_wait_inside_15_frame_burst():
    source = inspect.getsource(characterization.characterize)
    start = source.index("def _probe_sustained_single_rearm")
    end = source.index(
        'job.log(\n                "SINGLE REARM SUSTAINED: validating guarded delay',
        start,
    )
    block = source[start:end]

    loop_start = block.index(
        "for frame in range(SINGLE_REARM_SUSTAINED_FRAMES):"
    )
    confirm = block.index("_confirm_rearm_files(", loop_start)
    loop_block = block[loop_start:confirm]

    baseline_block = block[:loop_start]
    assert "_settle_characterization_readback(" in baseline_block
    assert "camera.trigger_capture()" in loop_block
    assert 'runtime_set(\n                                "shutter",' in loop_block
    assert "wait_for_event" not in loop_block
    assert "characterization_read" not in loop_block
    assert "SINGLE_REARM_SUSTAINED_FRAMES" in block
    assert "SINGLE_REARM_SUSTAINED_REPETITIONS" in block



def test_exposure_rearm_probe_covers_reference_regimes_without_mid_pair_observation():
    source = inspect.getsource(characterization.characterize)
    start = source.index("def _probe_exposure_single_rearm")
    end = source.index(
        'job.log(\n                    "SINGLE REARM EXPOSURE: validating guarded delay',
        start,
    )
    block = source[start:end]

    assert "SINGLE_REARM_EXPOSURE_REPETITIONS" in block
    assert "camera.trigger_capture()" in block
    assert 'runtime_set("shutter", second_value)' in block
    assert "_confirm_rearm_files(" in block

    baseline = block[:block.index("first_begin = time.monotonic()")]
    assert "_settle_characterization_readback(" in baseline

    critical = block[
        block.index("# Critical multi-exposure transition"):
        block.index("confirmed = _confirm_rearm_files(")
    ]
    assert "wait_for_event" not in critical
    assert "characterization_read" not in critical

    assert characterization.SINGLE_REARM_EXPOSURE_REGIMES == (
        ("fast", "1/1000", "1/500"),
        ("medium", "1/30", "1/15"),
        ("long", "1", "2"),
        ("very_long", "2", "4"),
    )
