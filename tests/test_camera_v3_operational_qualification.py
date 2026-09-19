from copy import deepcopy
from types import SimpleNamespace

from backend import camera_characterization as module
from backend.camera_timing_contract import SAFETY_POLICY, single_photo_duration_ms


class FakeJob:
    def __init__(self):
        self.logs = []
        self.snapshots = []

    def ask(self, message, kind="result"):
        assert kind == "start"
        return True

    def check(self):
        return None

    def log(self, message):
        self.logs.append(message)

    def checkpoint(self, **data):
        self.snapshots.append(deepcopy(data))


class FakeCamera:
    def __init__(self):
        self.exit_count = 0
        self.init_count = 0

    def exit(self):
        self.exit_count += 1

    def init(self):
        self.init_count += 1


def _contract(single=650):
    return {
        "version": 3,
        "safety_policy": deepcopy(SAFETY_POLICY),
        "set_overhead_ms": 950,
        "single_overhead_ms": single,
        "single_usb_return_ms": 150,
        "bracket_overhead_ms": 0,
        "bracket_inter_image_ms": 0,
        "bracket_usb_return_ms": 0,
        "supported_bracket_frames": [],
        "bracket_calibration_frames": [],
        "physical_trigger_latency": {
            "status": "unmeasured",
            "compensation_ms": 0.0,
            "jitter_ms": None,
        },
    }


def test_v3_operational_qualification_restarts_logically_without_reopening_camera(
    monkeypatch,
):
    clock = [0.0]
    camera = FakeCamera()
    profile = {
        "timing_contract": _contract(),
        "strategy": "sequential",
    }

    def recipe(current_profile):
        overhead = current_profile["timing_contract"]["single_overhead_ms"]
        duration = single_photo_duration_ms(overhead, 1 / 500)
        return {
            "expected_photos": 1,
            "commands": [
                {
                    "action": "SET",
                    "duration_ms": current_profile["timing_contract"]["set_overhead_ms"],
                    "params": {"parameter": "iso", "value": "100"},
                },
                {
                    "action": "PHOTO",
                    "duration_ms": duration,
                    "params": {
                        "shutter": "1/500",
                        "frames": 1,
                        "physical_views": ["1/500"],
                        "duration_ms": duration,
                        "timing_contract_version": 2,
                    },
                },
            ],
        }

    plugin_generation = [0]

    class FakePlugin:
        def __init__(self, camera_obj, log_fn, profile=None):
            self.camera = camera_obj
            self.profile = profile
            plugin_generation[0] += 1
            self.generation = plugin_generation[0]

        def preflight(self):
            return {"ok": True}

        def set_parameter(self, parameter, value, fallback_parameter=None):
            clock[0] += 0.100
            return True

        def execute_photo(self, params, *, observation_timeout_s=None, check=None):
            clock[0] += 1.200 if self.generation == 1 else 0.550
            return SimpleNamespace(frames=1)

    from backend import camera_validation

    monkeypatch.setattr(camera_validation, "build_validation_recipe", recipe)
    monkeypatch.setattr(module, "ProfilePlugin", FakePlugin)
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])

    result = module.qualify_operational_contract_v3(
        camera,
        profile,
        [300.0],
        [500.0],
        {},
        FakeJob(),
    )

    assert len(result["attempts"]) == 2
    assert result["attempts"][0]["restarted"] is True
    assert result["attempts"][1]["restarted"] is False
    assert result["persistent_camera_session"] is True
    assert camera.exit_count == 0
    assert camera.init_count == 0
    assert profile["timing_contract"]["single_overhead_ms"] > 650


def test_v3_operational_qualification_logs_command_before_hardware_call(monkeypatch):
    clock = [0.0]
    camera = FakeCamera()
    profile = {"timing_contract": _contract(), "strategy": "sequential"}

    def recipe(current_profile):
        return {
            "expected_photos": 0,
            "commands": [
                {
                    "action": "SET",
                    "duration_ms": current_profile["timing_contract"]["set_overhead_ms"],
                    "params": {"parameter": "iso", "value": "100"},
                }
            ],
        }

    job = FakeJob()

    class FakePlugin:
        def __init__(self, camera_obj, log_fn, profile=None):
            self.log_fn = log_fn
        def preflight(self):
            return {"ok": True}
        def set_parameter(self, parameter, value, fallback_parameter=None):
            assert any("RUNTIME QUALIFICATION COMMAND 1/1: SET" in line for line in job.logs)
            clock[0] += 0.010
            return True

    from backend import camera_validation
    monkeypatch.setattr(camera_validation, "build_validation_recipe", recipe)
    monkeypatch.setattr(module, "ProfilePlugin", FakePlugin)
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])

    result = module.qualify_operational_contract_v3(
        camera, profile, [10.0], [100.0], {}, job
    )
    assert result["status"] == "validated"
    assert camera.exit_count == 0
    assert camera.init_count == 0
