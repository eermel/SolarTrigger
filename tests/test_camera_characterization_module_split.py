from pathlib import Path

import backend.camera_characterization as characterization
import backend.camera_characterization_persistence as persistence
import backend.camera_characterization_selection as selection
import backend.camera_characterization_single as single
import backend.camera_characterization_types as characterization_types
import backend.camera_characterization_qualification as qualification
import backend.camera_characterization_settings as settings
import backend.camera_characterization_runtime_ops as runtime_ops
import backend.camera_characterization_capture as capture


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "backend" / "camera_characterization.py").read_text(
    encoding="utf-8"
)


def test_characterization_persistence_is_split_without_api_breakage():
    assert characterization.publish is persistence.publish
    assert (
        characterization._persistent_profile_document
        is persistence._persistent_profile_document
    )
    assert (
        characterization._persistent_timing_document
        is persistence._persistent_timing_document
    )
    assert "def publish(" not in SOURCE
    assert "def _persistent_profile_document(" not in SOURCE


def test_characterization_candidate_selection_is_split_without_api_breakage():
    assert (
        characterization.choose_common_bracket_command
        is selection.choose_common_bracket_command
    )
    assert (
        characterization._capture_validation_state
        is selection._capture_validation_state
    )
    assert (
        characterization._select_common_bracket_calibration_candidate
        is selection._select_common_bracket_calibration_candidate
    )
    assert (
        characterization._select_bracket_candidate
        is selection._select_bracket_candidate
    )
    assert "def choose_common_bracket_command(" not in SOURCE
    assert "def _select_bracket_candidate(" not in SOURCE



def test_single_rearm_helpers_are_split_without_api_breakage():
    assert characterization.Cancelled is characterization_types.Cancelled
    assert (
        characterization._select_single_trigger_candidate
        is single._select_single_trigger_candidate
    )
    assert (
        characterization._search_single_rearm_ms
        is single._search_single_rearm_ms
    )
    assert (
        characterization._qualify_guarded_single_rearm_ms
        is single._qualify_guarded_single_rearm_ms
    )
    assert characterization.SINGLE_REARM_STEP_MS == single.SINGLE_REARM_STEP_MS
    assert "def _search_single_rearm_ms(" not in SOURCE
    assert "class Cancelled(RuntimeError):" not in SOURCE



def test_operational_qualification_is_split_without_api_breakage():
    assert characterization.QualificationOverrun is qualification.QualificationOverrun
    assert (
        characterization.check_qualification_margins
        is qualification.check_qualification_margins
    )
    assert (
        characterization.refine_qualification
        is qualification.refine_qualification
    )
    assert (
        characterization._qualify_operational_contract_v3_impl
        is qualification.qualify_operational_contract_v3
    )
    assert (
        characterization._qualify_operational_contract_impl
        is qualification.qualify_operational_contract
    )
    assert callable(characterization.qualify_operational_contract_v3)
    assert callable(characterization.qualify_operational_contract)
    assert "class QualificationOverrun(RuntimeError):" not in SOURCE
    assert "_qualify_operational_contract_v3_impl(" in SOURCE
    assert "_qualify_operational_contract_impl(" in SOURCE
    assert "RUNTIME QUALIFICATION COMMAND" not in SOURCE



def test_setting_discovery_is_split_out_of_characterize():
    assert characterization.enumerate_widgets is settings.enumerate_widgets
    assert characterization.find_setting is settings.find_setting
    assert "    def find_setting(" not in SOURCE
    characterize_source = SOURCE[SOURCE.index("def characterize("):]
    assert (
        "find_setting(camera, job, initial, commands, warnings, "
        "selection_evidence, "
    ) in characterize_source



def test_runtime_set_operations_are_owned_by_dedicated_object():
    assert (
        characterization.CharacterizationRuntimeOps
        is runtime_ops.CharacterizationRuntimeOps
    )
    assert "    def prime_runtime_spec(" not in SOURCE
    assert "    def runtime_set(" not in SOURCE
    assert "    def characterization_read(" not in SOURCE
    assert "    def converge_characterized_preflight(" not in SOURCE
    assert "    def measure_set(" not in SOURCE
    assert "runtime_ops = CharacterizationRuntimeOps(" in SOURCE



def test_capture_probe_is_split_without_timing_policy_changes():
    assert (
        characterization.CharacterizationCaptureProbe
        is capture.CharacterizationCaptureProbe
    )
    assert "    def probe(" not in SOURCE
    assert "capture_probe = CharacterizationCaptureProbe(" in SOURCE
    assert "probe = capture_probe.probe" in SOURCE
    assert "validated_trials = set()" not in SOURCE



class _RegressionWidget:
    def __init__(
        self,
        name,
        *,
        value=None,
        choices=(),
        readonly=False,
        children=(),
    ):
        self._name = name
        self._value = value
        self._choices = tuple(choices)
        self._readonly = bool(readonly)
        self._children = tuple(children)

    def get_name(self):
        return self._name

    def count_children(self):
        return len(self._children)

    def get_children(self):
        return self._children

    def get_choices(self):
        return self._choices

    def get_value(self):
        return self._value

    def get_readonly(self):
        return self._readonly


class _SecondPassCamera:
    def __init__(self):
        self._leaf = _RegressionWidget(
            "expprogram",
            value="M",
            choices=("M",),
            readonly=True,
        )
        self._root = _RegressionWidget("main", children=(self._leaf,))

    def get_config(self):
        return self._root

    def get_single_config(self, name):
        assert name == "expprogram"
        return self._leaf


class _SecondPassJob:
    def __init__(self):
        self.asked = 0

    def checkpoint(self, **_fields):
        return None

    def check(self):
        return None

    def log(self, _message):
        return None

    def ask(self, _message):
        self.asked += 1
        return True


def test_find_setting_refreshes_widgets_after_operator_action():
    camera = _SecondPassCamera()
    job = _SecondPassJob()
    initial = [{
        "path": "/main/expprogram",
        "name": "expprogram",
        "config_name": "expprogram",
        "value": "AUTO",
        "choices": ("AUTO",),
        "readonly": True,
    }]
    commands = {}
    warnings = []
    evidence = {}

    selected = settings.find_setting(
        camera,
        job,
        initial,
        commands,
        warnings,
        evidence,
        "manual_mode",
        ("expprogram",),
        lambda value: value == "M",
        critical=True,
        operator_instruction="Set manual mode",
        require_set=False,
    )

    assert job.asked == 1
    assert selected["config_name"] == "expprogram"
    assert commands["manual_mode"]["value"] == "M"
    assert warnings == []
    assert evidence["manual_mode"]["selected"] is not None
