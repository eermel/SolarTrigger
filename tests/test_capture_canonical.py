import pytest
import json
import subprocess
import sys
import types
import uuid

from plugins.camera.base import CameraPlugin
from services.camera_service import CameraService, CaptureIntent

if "gphoto2" not in sys.modules:
    sys.modules["gphoto2"] = types.SimpleNamespace(
        GP_LOG_ERROR=0,
        GP_LOG_VERBOSE=1,
        GP_LOG_DEBUG=2,
        GP_LOG_DATA=3,
        use_python_logging=lambda mapping=None: None,
        check_result=lambda *args, **kwargs: None,
    )

_argv = sys.argv
sys.argv = [sys.argv[0]]
from scripts import eclipse_trigger as trigger
sys.argv = _argv


def _minimal_capture_v2():
    return {
        "phases": {
            "partial": {"speeds": ["1/640"], "aperture": "f/8", "iso": 100},
            "diamond_ring": {
                "speeds": ["1/1250"],
                "aperture": "f/11",
                "iso": 200,
            },
            "totality": {"speeds": ["1/4", "1/2"], "aperture": "f/5.6", "iso": 400},
        },
        "exposure_correction": {"atmospheric_attenuation_enabled": True},
    }


def test_build_capture_canonical_from_minimal_v2():
    capture = _minimal_capture_v2()

    canonical = trigger.build_capture_canonical(capture)

    assert canonical == capture
    assert set(canonical["phases"]) == {"partial", "diamond_ring", "totality"}
    assert canonical["exposure_correction"]["atmospheric_attenuation_enabled"] is True


def test_prepare_capture_tokenizes_inclusive_bounds_fastest_to_slowest():
    class TokenPlugin:
        name = "token-spy"

        def prepare_capture(self, intent):
            return CameraPlugin.prepare_capture(self, intent)

    service = CameraService()
    service.plugin = TokenPlugin()
    intent = CaptureIntent(
        shutter_min="1/125",
        shutter_max="1/1000",
        step_ev=1.0,
        speeds=None,
        phase="totality",
        target_time=None,
        deadline=None,
        overflow_policy="truncate",
    )

    prepared = service.prepare_capture(intent)

    assert prepared.token == ("speeds", "1/1000", "1/125", 1.0, None)
    simulation = trigger._SimulationCameraService().prepare_capture(intent)
    assert simulation.token[1] == ["1/1000", "1/500", "1/250", "1/125"]


@pytest.mark.parametrize(
    ("phase_name", "expected_speeds", "expected_aperture", "expected_iso"),
    [
        ("partial", ["1/640"], "f/8", 100),
        ("diamond_ring", ["1/1250"], "f/11", 200),
        ("totality", ["1/4", "1/2"], "f/5.6", 400),
    ],
)
def test_each_phase_builds_intent_from_canonical_capture(
    monkeypatch,
    phase_name,
    expected_speeds,
    expected_aperture,
    expected_iso,
):
    canonical = trigger.build_capture_canonical(_minimal_capture_v2())
    canonical["exposure_correction"]["atmospheric_attenuation_enabled"] = False
    monkeypatch.setattr(trigger, "capture_canonical", canonical)

    phase = trigger.capture_phase(phase_name)
    intent = trigger._capture_intent(phase, phase_name, target_time=None)

    assert phase["aperture"] == expected_aperture
    assert phase["iso"] == expected_iso
    assert intent.speeds == expected_speeds
    assert intent.phase == phase_name


def test_capture_intent_identifies_sequencer_request(monkeypatch):
    canonical = trigger.build_capture_canonical(_minimal_capture_v2())
    canonical["exposure_correction"]["atmospheric_attenuation_enabled"] = False
    monkeypatch.setattr(trigger, "capture_canonical", canonical)

    intent = trigger._capture_intent(
        trigger.capture_phase("partial"), "partial", target_time=None
    )

    assert intent.origin == "partial"
    assert intent.request_id
    assert uuid.UUID(hex=intent.request_id).version == 4


def test_legacy_configuration_is_canonicalized_before_capture_engine(monkeypatch):
    legacy = {
        "partial": {
            "speeds": ["1/1000", "1/500"],
            "aperture": "f/8",
            "iso": 100,
        },
        "diamond_ring": {"speeds": ["1/2000"]},
        "totality": {"speeds": ["1/8", "1/4"]},
    }
    canonical = trigger.build_legacy_capture_canonical({}, legacy)
    monkeypatch.setattr(trigger, "capture_canonical", canonical)

    phase = trigger.capture_phase("partial")
    intent = trigger._capture_intent(phase, "partial", target_time=None)

    assert set(canonical) == {"phases", "exposure_correction"}
    assert phase is canonical["phases"]["partial"]
    assert intent.speeds == ["1/1000", "1/500"]


def test_dry_run_uses_same_canonical_phase_parameters_as_real_run(tmp_path):
    circumstances = {
        "_date": "2026-08-12",
        "TSTART": "18:00:00",
        "C1": "19:00:00",
        "C2": "20:00:00",
        "TMAX": "20:01:00",
        "C3": "20:02:00",
        "C4": "21:00:00",
        "TEND": "22:00:00",
    }
    capture = _minimal_capture_v2()
    capture["exposure_correction"]["atmospheric_attenuation_enabled"] = False
    circumstances_path = tmp_path / "circumstances.json"
    capture_path = tmp_path / "capture.json"
    circumstances_path.write_text(json.dumps(circumstances), encoding="utf-8")
    capture_path.write_text(json.dumps(capture), encoding="utf-8")

    probe = """
import json
import sys
import types

sys.modules["gphoto2"] = types.SimpleNamespace(
    GP_LOG_ERROR=0,
    GP_LOG_VERBOSE=1,
    GP_LOG_DEBUG=2,
    GP_LOG_DATA=3,
    use_python_logging=lambda mapping=None: None,
    check_result=lambda *args, **kwargs: None,
)
sys.argv = ["eclipse_trigger.py", "--file", sys.argv[1], "--camera", sys.argv[2], *sys.argv[3:]]
from scripts import eclipse_trigger as trigger

phase_inputs = {
    "partial": (trigger.TSTART, trigger.C1),
    "diamond_ring": (trigger.C2, trigger.C2),
    "totality": (trigger.TMAX, trigger.C3),
}
observed = {}
service = trigger._SimulationCameraService()
for name, (target, deadline) in phase_inputs.items():
    phase = trigger.capture_phase(name)
    apply = {"aperture": phase.get("aperture", "f/8"), "iso": str(phase.get("iso", "100"))}
    service.apply_phase_settings(**apply)
    intent = trigger._capture_intent(phase, name, target, deadline)
    prepared = service.prepare_capture(intent)
    prepared_intent, prepared_speeds = prepared.token
    observed[name] = {
        "apply": apply,
        "prepare": {
            "shutter_min": prepared_intent.shutter_min,
            "shutter_max": prepared_intent.shutter_max,
            "step_ev": prepared_intent.step_ev,
            "speeds": prepared_intent.speeds,
            "prepared_speeds": prepared_speeds,
            "phase": prepared_intent.phase,
            "overflow_policy": prepared_intent.overflow_policy,
        },
    }
print("PARITY_PROBE=" + json.dumps({
    "canonical": trigger.capture_canonical,
    "phases": observed,
    "timeline": {key: value.isoformat() for key, value in trigger._timeline.items()},
}, sort_keys=True))
"""

    def run_probe(*extra_args):
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                probe,
                str(circumstances_path),
                str(capture_path),
                *extra_args,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        line = next(
            item
            for item in result.stdout.splitlines()
            if item.startswith("PARITY_PROBE=")
        )
        return json.loads(line.removeprefix("PARITY_PROBE="))

    real = run_probe()
    dry_run = run_probe("--dry-run", "--dry-run-delay", "0")

    assert dry_run["canonical"] == real["canonical"]
    assert dry_run["phases"] == real["phases"]
    assert dry_run["timeline"] != real["timeline"]


def test_v2_initialization_ignores_photo_fields_from_circumstances(tmp_path):
    circumstances = {
        "_date": "2026-08-12",
        "TSTART": "18:00:00",
        "C1": "19:00:00",
        "C2": "20:00:00",
        "TMAX": "20:01:00",
        "C3": "20:02:00",
        "C4": "21:00:00",
        "TEND": "22:00:00",
        "shutterspeed_partial": "CIRCUMSTANCES_MUST_NOT_BE_USED",
        "shutterspeed_diamondring": "CIRCUMSTANCES_MUST_NOT_BE_USED",
        "speeds_partial": ["CIRCUMSTANCES_MUST_NOT_BE_USED"],
        "speeds_diamond_ring": ["CIRCUMSTANCES_MUST_NOT_BE_USED"],
        "totality": {"speeds": ["CIRCUMSTANCES_MUST_NOT_BE_USED"]},
    }
    capture = _minimal_capture_v2()
    circumstances_path = tmp_path / "circumstances.json"
    capture_path = tmp_path / "capture.json"
    circumstances_path.write_text(json.dumps(circumstances), encoding="utf-8")
    capture_path.write_text(json.dumps(capture), encoding="utf-8")

    probe = """
import json
import sys
import types

sys.modules["gphoto2"] = types.SimpleNamespace(
    GP_LOG_ERROR=0,
    GP_LOG_VERBOSE=1,
    GP_LOG_DEBUG=2,
    GP_LOG_DATA=3,
    use_python_logging=lambda mapping=None: None,
    check_result=lambda *args, **kwargs: None,
)
sys.argv = ["eclipse_trigger.py", "--file", sys.argv[1], "--camera", sys.argv[2]]
from scripts import eclipse_trigger as trigger
print("CAPTURE_SELECTION=" + json.dumps({
    "partial": trigger.speeds_partial,
    "diamond_ring": trigger.speeds_diamond_ring,
    "totality": trigger.shutter_speeds,
}))
"""
    result = subprocess.run(
        [sys.executable, "-c", probe, str(circumstances_path), str(capture_path)],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "Stratégie photo dérivée de capture v2" in result.stdout
    assert "CIRCUMSTANCES_MUST_NOT_BE_USED" not in result.stdout
    selected_line = next(
        line for line in result.stdout.splitlines() if line.startswith("CAPTURE_SELECTION=")
    )
    assert json.loads(selected_line.removeprefix("CAPTURE_SELECTION=")) == {
        "partial": ["1/640"],
        "diamond_ring": ["1/1250"],
        "totality": ["1/4", "1/2"],
    }


def test_diamond_ring_duration_is_shared_by_audio_and_photo_windows(tmp_path):
    circumstances = {
        "_date": "2026-08-12",
        "TSTART": "18:00:00",
        "C1": "19:00:00",
        "C2": "20:00:00",
        "TMAX": "20:01:00",
        "C3": "20:02:00",
        "C4": "21:00:00",
        "TEND": "22:00:00",
        "duree_diamond_ring": 41,
    }
    capture = _minimal_capture_v2()
    capture["phases"]["partial"]["interval_s"] = 180
    capture["phases"]["diamond_ring"].update({
        "interval_s": 4,
        "duration_s": 17,
    })
    capture["phases"]["totality"]["interval_s"] = 1
    circumstances_path = tmp_path / "circumstances.json"
    capture_path = tmp_path / "capture.json"
    circumstances_path.write_text(json.dumps(circumstances), encoding="utf-8")
    capture_path.write_text(json.dumps(capture), encoding="utf-8")

    probe = """
import json
import sys
import types

sys.modules["gphoto2"] = types.SimpleNamespace(
    GP_LOG_ERROR=0,
    GP_LOG_VERBOSE=1,
    GP_LOG_DEBUG=2,
    GP_LOG_DATA=3,
    use_python_logging=lambda mapping=None: None,
    check_result=lambda *args, **kwargs: None,
)
sys.argv = ["eclipse_trigger.py", "--file", sys.argv[1], "--camera", sys.argv[2]]
from scripts import eclipse_trigger as trigger
alerts = {sound: when for when, sound in trigger.alertes_sons}
print("DIAMOND_RING=" + json.dumps({
    "photo_duration_s": trigger.diamond_ring_duration_s,
    "before_c2_s": (trigger.C2 - alerts["filters_off.wav"]).total_seconds(),
    "after_c3_s": (alerts["filters_on.wav"] - trigger.C3).total_seconds(),
}))
"""
    result = subprocess.run(
        [sys.executable, "-c", probe, str(circumstances_path), str(capture_path)],
        check=True,
        capture_output=True,
        text=True,
    )

    selected_line = next(
        line for line in result.stdout.splitlines()
        if line.startswith("DIAMOND_RING=")
    )
    assert json.loads(selected_line.removeprefix("DIAMOND_RING=")) == {
        "photo_duration_s": 17,
        "before_c2_s": 17.0,
        "after_c3_s": 17.0,
    }


def _probe_legacy_capture_selection(tmp_path, circumstances):
    circumstances = {
        "_date": "2026-08-12",
        "TSTART": "18:00:00",
        "C1": "19:00:00",
        "C2": "20:00:00",
        "TMAX": "20:01:00",
        "C3": "20:02:00",
        "C4": "21:00:00",
        "TEND": "22:00:00",
        **circumstances,
    }
    circumstances_path = tmp_path / "circumstances.json"
    circumstances_path.write_text(json.dumps(circumstances), encoding="utf-8")
    probe = """
import json
import sys
import types

sys.modules["gphoto2"] = types.SimpleNamespace(
    GP_LOG_ERROR=0,
    GP_LOG_VERBOSE=1,
    GP_LOG_DEBUG=2,
    GP_LOG_DATA=3,
    use_python_logging=lambda mapping=None: None,
    check_result=lambda *args, **kwargs: None,
)
sys.argv = ["eclipse_trigger.py", "--file", sys.argv[1]]
from scripts import eclipse_trigger as trigger
print("CAPTURE_SELECTION=" + json.dumps({
    "partial": trigger.speeds_partial,
    "diamond_ring": trigger.speeds_diamond_ring,
}))
"""
    result = subprocess.run(
        [sys.executable, "-c", probe, str(circumstances_path)],
        check=True,
        capture_output=True,
        text=True,
    )
    selected_line = next(
        line for line in result.stdout.splitlines()
        if line.startswith("CAPTURE_SELECTION=")
    )
    return json.loads(selected_line.removeprefix("CAPTURE_SELECTION="))


def test_legacy_circumstances_top_level_shutterspeeds_are_honored(tmp_path):
    selected = _probe_legacy_capture_selection(tmp_path, {
        "shutterspeed_partial": "1/321",
        "shutterspeed_diamondring": "1/654",
    })

    assert selected == {
        "partial": ["1/321"],
        "diamond_ring": ["1/654"],
    }


def test_legacy_circumstances_per_phase_speeds_are_honored(tmp_path):
    selected = _probe_legacy_capture_selection(tmp_path, {
        "shutterspeed_partial": "1/321",
        "shutterspeed_diamondring": "1/654",
        "partial": {"speeds": ["1/800", "1/400"]},
        "diamond_ring": {"speeds": ["1/1600", "1/1250"]},
    })

    assert selected == {
        "partial": ["1/800", "1/400"],
        "diamond_ring": ["1/1600", "1/1250"],
    }


def _minimal_v2_without_correction():
    return {
        "schema_version": 2,
        "kind": "capture_execution",
        "name": "minimal",
        "phases": {
            "partial": {},
            "diamond_ring": {},
            "totality": {},
        },
    }


def test_capture_v2_accepts_missing_exposure_correction():
    capture = _minimal_v2_without_correction()

    canonical = trigger.build_capture_canonical(capture)

    assert canonical["exposure_correction"] == {}


def test_capture_v2_rejects_missing_phase():
    capture = _minimal_v2_without_correction()
    del capture["phases"]["diamond_ring"]

    with pytest.raises(ValueError, match=r"phases\.diamond_ring"):
        trigger.build_capture_canonical(capture)


def test_capture_v2_rejects_non_dict_phase():
    capture = _minimal_v2_without_correction()
    capture["phases"]["totality"] = "invalid"

    with pytest.raises(ValueError, match=r"phases\.totality"):
        trigger.build_capture_canonical(capture)


def test_capture_v2_rejects_non_dict_exposure_correction():
    capture = _minimal_v2_without_correction()
    capture["exposure_correction"] = True

    with pytest.raises(ValueError, match="exposure_correction"):
        trigger.build_capture_canonical(capture)


def test_capture_v2_rejects_non_boolean_atmospheric_attenuation():
    capture = _minimal_v2_without_correction()
    capture["exposure_correction"] = {
        "atmospheric_attenuation_enabled": "yes"
    }

    with pytest.raises(
        ValueError,
        match="atmospheric_attenuation_enabled",
    ):
        trigger.build_capture_canonical(capture)


def test_capture_v2_normalizes_transitional_atmospheric_key():
    capture = _minimal_v2_without_correction()
    capture["exposure_correction"] = {"atmospheric": True}

    canonical = trigger.build_capture_canonical(capture)

    assert canonical["exposure_correction"] == {
        "atmospheric_attenuation_enabled": True
    }
