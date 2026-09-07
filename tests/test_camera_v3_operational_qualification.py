from copy import deepcopy
from types import SimpleNamespace

from backend import camera_characterization as module
from backend.camera_timing_contract import (
    SAFETY_POLICY,
    bracket_photo_duration_ms,
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



def test_v3_operational_qualification_cold_bracket_is_first_photo(
    monkeypatch,
):
    """Largest bracket must be first PHOTO of an independent fresh session."""
    clock = [0.0]
    camera = FakeCamera()
    camera.first_photo_by_session = {}

    profile = {
        "timing_contract": {
            "version": 3,
            "safety_policy": deepcopy(SAFETY_POLICY),
            "set_overhead_ms": 950,
            "single_overhead_ms": 650,
            "bracket_overhead_ms": 600,
            "bracket_inter_image_ms": 200,
            "supported_bracket_frames": [3, 5],
        },
        "strategy": "bracket",
        "brackets": {
            "3": {"mode": "Bracket 3"},
            "5": {"mode": "Bracket 5"},
        },
    }

    views3 = ["1/2000", "1/1000", "1/500"]
    views5 = [
        "1/4000",
        "1/2000",
        "1/1000",
        "1/500",
        "1/250",
    ]

    def speed(value):
        numerator, denominator = value.split("/", 1)
        return float(numerator) / float(denominator)

    def recipe(current_profile):
        contract = current_profile["timing_contract"]
        set_ms = contract["set_overhead_ms"]

        single_duration = single_photo_duration_ms(
            contract["single_overhead_ms"],
            1 / 500,
        )
        bracket3_duration = bracket_photo_duration_ms(
            contract["bracket_overhead_ms"],
            contract["bracket_inter_image_ms"],
            [speed(value) for value in views3],
        )
        bracket5_duration = bracket_photo_duration_ms(
            contract["bracket_overhead_ms"],
            contract["bracket_inter_image_ms"],
            [speed(value) for value in views5],
        )

        return {
            "expected_photos": 9,
            "commands": [
                {
                    "action": "SET",
                    "duration_ms": set_ms,
                    "params": {
                        "parameter": "iso",
                        "value": "100",
                    },
                },
                {
                    "action": "PHOTO",
                    "duration_ms": single_duration,
                    "params": {
                        "shutter": "1/500",
                        "frames": 1,
                        "physical_views": ["1/500"],
                        "duration_ms": single_duration,
                        "timing_contract_version": 2,
                        "validation_confirmation_grace_ms": 1000,
                    },
                },
                {
                    "action": "SET",
                    "duration_ms": set_ms,
                    "params": {
                        "parameter": "capturemode",
                        "value": "Bracket 3",
                    },
                },
                {
                    "action": "PHOTO",
                    "duration_ms": bracket3_duration,
                    "params": {
                        "shutter": "1/1000",
                        "frames": 3,
                        "physical_views": list(views3),
                        "duration_ms": bracket3_duration,
                        "timing_contract_version": 2,
                        "validation_confirmation_grace_ms": 1000,
                    },
                },
                {
                    "action": "SET",
                    "duration_ms": set_ms,
                    "params": {
                        "parameter": "capturemode",
                        "value": "Bracket 5",
                    },
                },
                {
                    "action": "PHOTO",
                    "duration_ms": bracket5_duration,
                    "params": {
                        "shutter": "1/1000",
                        "frames": 5,
                        "physical_views": list(views5),
                        "duration_ms": bracket5_duration,
                        "timing_contract_version": 2,
                        "validation_confirmation_grace_ms": 1000,
                    },
                },
                # Deterministic post-bracket state.  The cold qualification
                # must replay this after its first-photo bracket.
                {
                    "action": "SET",
                    "duration_ms": set_ms,
                    "params": {
                        "parameter": "capturemode",
                        "value": "Single Shot",
                    },
                },
                {
                    "action": "SET",
                    "duration_ms": set_ms,
                    "params": {
                        "parameter": "iso",
                        "value": "100",
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
            frames = int(params.get("frames", 1))
            session = self.camera.init_count

            self.camera.first_photo_by_session.setdefault(
                session,
                frames,
            )

            exposure_s = sum(
                speed(value)
                for value in params.get(
                    "physical_views",
                    [params["shutter"]],
                )
            )

            if frames == 1:
                overhead_ms = 500.0
            elif frames == 3:
                overhead_ms = 700.0
            elif session == 2:
                # First cold-bracket session: deliberately slower than the
                # warm bracket model, forcing an inter-image budget revision.
                overhead_ms = 1500.0
            elif session == 4:
                # Retry cold bracket after revision: now safely inside budget.
                overhead_ms = 1100.0
            else:
                overhead_ms = 900.0

            clock[0] += exposure_s + overhead_ms / 1000.0
            return SimpleNamespace(frames=frames)

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

    # Existing warm characterization:
    # 3 frames -> 700 ms overhead
    # 5 frames -> 900 ms overhead
    # raw slope = 100 ms/image, guarded inter-image = 200 ms.
    brackets = {
        3: [700.0],
        5: [900.0],
    }
    job = FakeJob()

    result = module.qualify_operational_contract_v3(
        camera,
        profile,
        set_samples,
        single_samples,
        brackets,
        job,
    )

    # Main attempt 1 starts with a single PHOTO.
    assert camera.first_photo_by_session[1] == 1

    # Independent session 2 must start directly with the largest bracket.
    assert camera.first_photo_by_session[2] == 5

    # Cold observation raises the 3->5 slope to 400 ms/image:
    # budget_ms([400]) = 500 ms.
    assert profile["timing_contract"][
        "bracket_inter_image_ms"
    ] == 500

    # Complete retry: another main fresh session followed by another
    # bracket-first fresh session.
    assert camera.first_photo_by_session[3] == 1
    assert camera.first_photo_by_session[4] == 5
    assert camera.init_count == 4
    assert camera.exit_count == 4

    assert len(result["attempts"]) == 2
    assert result["attempts"][0]["restarted"] is True
    assert result["attempts"][1]["restarted"] is False

    adjustment = next(
        item
        for item in result["adjustments"]
        if item["field"] == "bracket_model_cold_first"
    )
    assert adjustment["frames"] == 5
    assert adjustment["cold_first_photo"] is True
    assert adjustment["previous_inter_image_ms"] == 200
    assert adjustment["revised_inter_image_ms"] == 500

    final_cold = result["cold_bracket_first"]
    assert final_cold["frames"] == 5
    assert final_cold["status"] == "validated"

    assert any(
        "RUNTIME QUALIFICATION COLD BRACKET PASSED" in line
        for line in job.logs
    )
