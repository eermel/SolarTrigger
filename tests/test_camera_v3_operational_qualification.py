from copy import deepcopy
from types import SimpleNamespace

from backend import camera_characterization as module
from backend.camera_timing_contract import (
    SAFETY_POLICY,
    budget_ms,
    single_photo_duration_ms,
)


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
        "bracket_overhead_ms": 0,
        "bracket_inter_image_ms": 0,
        "supported_bracket_frames": [],
    }


def test_v3_operational_qualification_includes_fresh_session_first_capture(
    monkeypatch,
):
    clock = [0.0]
    camera = FakeCamera()

    profile = {
        "timing_contract": _contract(),
        "strategy": "sequential",
    }

    def recipe(current_profile):
        overhead = current_profile[
            "timing_contract"
        ]["single_overhead_ms"]
        return {
            "expected_photos": 1,
            "commands": [
                {
                    "action": "SET",
                    "duration_ms": current_profile[
                        "timing_contract"
                    ]["set_overhead_ms"],
                    "params": {
                        "parameter": "iso",
                        "value": "100",
                    },
                },
                {
                    "action": "PHOTO",
                    "duration_ms": single_photo_duration_ms(
                        overhead,
                        1 / 500,
                    ),
                    "params": {
                        "shutter": "1/500",
                        "frames": 1,
                        "physical_views": ["1/500"],
                        "duration_ms":
                            single_photo_duration_ms(
                                overhead,
                                1 / 500,
                            ),
                        "timing_contract_version": 2,
                        "validation_confirmation_grace_ms": 1000,
                    },
                },
            ],
        }

    class FakePlugin:
        def __init__(self, camera_obj, log_fn, profile=None):
            self.camera = camera_obj
            self.profile = profile

        def preflight(self):
            return {"ok": True}

        def set_parameter(
            self,
            parameter,
            value,
            fallback_parameter=None,
        ):
            clock[0] += 0.100
            return True

        def execute_photo(
            self,
            params,
            *,
            observation_timeout_s=None,
            check=None,
        ):
            # First PHOTO of first fresh session is deliberately much slower
            # than the warm micro-benchmark.  Second fresh-session attempt
            # must then pass with the revised guarded budget.
            if self.camera.init_count == 1:
                clock[0] += 1.200
            else:
                clock[0] += 0.550
            return SimpleNamespace(frames=1)

    from backend import camera_validation

    monkeypatch.setattr(
        camera_validation,
        "build_validation_recipe",
        recipe,
    )
    monkeypatch.setattr(
        module,
        "ProfilePlugin",
        FakePlugin,
    )
    monkeypatch.setattr(
        module.time,
        "monotonic",
        lambda: clock[0],
    )
    monkeypatch.setattr(
        module.time,
        "sleep",
        lambda seconds: clock.__setitem__(
            0,
            clock[0] + max(0.0, seconds),
        ),
    )

    set_samples = [300.0]
    single_samples = [500.0]
    brackets = {}
    job = FakeJob()

    result = module.qualify_operational_contract_v3(
        camera,
        profile,
        set_samples,
        single_samples,
        brackets,
        job,
    )

    # First fresh-session PHOTO:
    # 1200 ms elapsed - 2 ms exposure = 1198 ms overhead.
    expected = budget_ms([500.0, 1198.0])

    assert expected == 1400
    assert profile["timing_contract"][
        "single_overhead_ms"
    ] == expected

    # One failed/revised attempt, then one complete fresh-session attempt.
    assert camera.init_count == 2
    assert camera.exit_count == 2
    assert len(result["attempts"]) == 2
    assert result["attempts"][0]["restarted"] is True
    assert result["attempts"][1]["restarted"] is False

    adjustment = result["adjustments"][0]
    assert adjustment["field"] == "single_overhead_ms"
    assert adjustment["previous_ms"] == 650
    assert adjustment["revised_ms"] == 1400

    assert any(
        "RUNTIME QUALIFICATION V3 PASSED" in line
        for line in job.logs
    )
