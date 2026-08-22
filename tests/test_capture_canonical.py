import pytest
import json
import subprocess
import sys
import types

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
