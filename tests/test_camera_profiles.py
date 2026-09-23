import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from backend.camera_profiles import discover_profiles, profile_for_model, validate_profile
from backend.camera_characterization import publish, CharacterizationJob, Cancelled
from plugins.camera.profile import ProfilePlugin


@pytest.fixture
def profile():
    speeds = ["1/8000", "1/4000", "1/2000", "1/1000", "1/500", "1/250", "1/125", "1/60", "1/30"]
    return {
        "schema_version": 1, "config_type": "camera_profile", "backend": "profile-example",
        "manufacturer": "Example", "model": "Example Camera", "strategy": "sequential",
        "commands": {
            "manual_mode": {"path": "/main/mode", "value": "M"},
            "capture_target": {"path": "/main/target", "value": "card"},
            "raw": {"path": "/main/format", "value": "RAW"},
            "iso": {"path": "/main/iso", "values": {"100": "100", "200": "200"}},
            "shutter": {"path": "/main/shutter", "values": {v: v for v in speeds}},
            "capture_mode": {"path": "/main/drive", "value": "Single Shot"},
            "trigger_single": {"method": "trigger_capture"},
        },
        "planning_timing": {"single_ms": 1000},
        "brackets": {str(n): {"mode": f"bracket{n}", "step_ev": 1, "total_ms": 1200,
                              "atomic_ms": 1000, "trigger": {"method": "trigger_capture"}}
                     for n in (3, 5)},
    }


def intent(profile, isos=None):
    speeds = list(profile["commands"]["shutter"]["values"])
    return SimpleNamespace(exposure_plan=[{"shutter": s, "iso": iso} for s, iso in
                                         zip(speeds, isos or [100]*len(speeds))])


def test_model_discovery_and_duplicate_fail_closed(tmp_path, profile):
    (tmp_path / "one.json").write_text(json.dumps(profile))
    assert profile_for_model(" EXAMPLE   CAMERA ", tmp_path)["backend"] == "profile-example"
    assert profile_for_model("Different Camera", tmp_path) is None
    (tmp_path / "two.json").write_text(json.dumps(profile))
    assert discover_profiles(tmp_path) == {}


@pytest.mark.parametrize("key", ["manual_mode", "capture_target", "raw", "iso", "shutter", "trigger_single"])
def test_critical_fields_required(profile, key):
    del profile["commands"][key]
    with pytest.raises(ValueError):
        validate_profile(profile)


def test_iso100_required_and_optional_battery(profile):
    assert validate_profile(profile)
    del profile["commands"]["iso"]["values"]["100"]
    with pytest.raises(ValueError, match="100"):
        validate_profile(profile)


def test_sequential_exact_exposures(profile):
    plugin = ProfilePlugin(None, profile=profile)
    prepared = plugin.prepare_capture(intent(profile))
    photos = [o for o in prepared.token[1] if o["action"] == "trigger_capture"]
    assert [p["shutter"] for p in photos] == list(profile["commands"]["shutter"]["values"])
    assert prepared.planned_count == 9


def test_bracket_dp_exact_coverage_and_iso_boundaries(profile):
    profile["strategy"] = "bracket"
    plugin = ProfilePlugin(None, profile=profile)
    prepared = plugin.prepare_capture(intent(profile, [100]*5 + [200]*4))
    photos = [o for o in prepared.token[1] if o["action"] in ("bracket_press", "trigger_capture")]
    assert sorted(o.get("frames", 1) for o in photos) == [1, 3, 5]
    views = [v for p in photos for v in p.get("physical_views", [p.get("shutter")])]
    assert views == list(profile["commands"]["shutter"]["values"])


@pytest.mark.parametrize(
    ("plan", "expected_actions"),
    [
        (
            [
                {"shutter": "1/2000", "iso": 100},
                {"shutter": "1/1000", "iso": 100},
                {"shutter": "1/500", "iso": 100},
                {
                    "shutter": "1/250",
                    "iso": 100,
                    "sequence_group": "atmos_single",
                },
            ],
            ["bracket_press", "trigger_capture"],
        ),
        (
            [
                {
                    "shutter": "1/250",
                    "iso": 100,
                    "sequence_group": "atmos_single",
                },
                {"shutter": "1/2000", "iso": 100},
                {"shutter": "1/1000", "iso": 100},
                {"shutter": "1/500", "iso": 100},
            ],
            ["trigger_capture", "bracket_press"],
        ),
    ],
)
def test_sequence_group_keeps_atmos_single_outside_native_bracket(
    profile,
    plan,
    expected_actions,
):
    profile["strategy"] = "bracket"
    prepared = ProfilePlugin(
        None,
        profile=profile,
    ).prepare_capture(
        SimpleNamespace(exposure_plan=plan)
    )
    photos = [
        operation
        for operation in prepared.token[1]
        if operation["action"] in {"bracket_press", "trigger_capture"}
    ]

    assert [operation["action"] for operation in photos] == expected_actions
    bracket = next(
        operation
        for operation in photos
        if operation["action"] == "bracket_press"
    )
    single = next(
        operation
        for operation in photos
        if operation["action"] == "trigger_capture"
    )
    assert bracket["physical_views"] == ["1/2000", "1/1000", "1/500"]
    assert single["shutter"] == "1/250"
    assert prepared.planned_count == 4


def test_publication_protected_from_reset(tmp_path, profile):
    from backend.persistent_reset import reset_application_var
    timing = {"config_type": "camera_timing", "timing": {},
              **{k: profile[k] for k in ("manufacturer", "model", "backend")}}
    paths = publish(profile, timing, tmp_path)
    assert all(p.startswith("configs/") for p in paths)
    reset_application_var(tmp_path / "var")
    assert all((tmp_path / p).exists() for p in paths)
    with pytest.raises(RuntimeError, match="overwrite"):
        publish(profile, timing, tmp_path)


def test_operator_question_identity_and_cancel():
    job = CharacterizationJob()
    with pytest.raises(ValueError):
        job.respond("missing", True)
    job.cancelled = True
    with pytest.raises(Cancelled):
        job.ask("Taken?")


def test_independent_instances(profile):
    a = ProfilePlugin(None, profile=profile)
    b = ProfilePlugin(None, profile=profile)
    a.profile["strategy"] = "bracket"
    assert b.profile["strategy"] == "sequential"




class SimulatedWidget:
    def __init__(self, name, value=None, choices=(), children=()):
        self.name, self.value = name, value
        self.choices, self.children = list(choices), list(children)

    def get_name(self): return self.name
    def get_value(self): return self.value
    def set_value(self, value):
        if self.choices and value not in self.choices:
            raise ValueError("unsupported choice")
        self.value = value
    def get_choices(self): return self.choices
    def get_readonly(self): return False
    def count_children(self): return len(self.children)
    def get_children(self): return self.children
    def get_child_by_name(self, name):
        return next(child for child in self.children if child.name == name)


class SimulatedCamera:
    def __init__(self, speeds):
        self.now = 0
        self.events = []
        self.counter = 0
        self.capture_isos = []
        self.exit_count = 0
        self.init_count = 0
        self.config = SimulatedWidget("main", children=[
            SimulatedWidget("expprogram", "A", ["A", "M"]),
            SimulatedWidget("capturetarget", "RAM", ["RAM", "card"]),
            SimulatedWidget("imageformat", "JPEG", ["JPEG", "RAW"]),
            SimulatedWidget("iso", "100", ["100", "200"]),
            SimulatedWidget("shutterspeed", "1/500", speeds),
            SimulatedWidget("capturemode", "Single Shot", ["Single Shot", "Continuous Bracket 1 EV 3 Img.", "Continuous Bracket 1 EV 5 Img."]),
        ])

    def get_config(self): return self.config
    def set_config(self, config): self.now += .25
    def get_single_config(self, name):
        return self.config.get_child_by_name(name)
    def set_single_config(self, name, widget):
        assert self.config.get_child_by_name(name) is widget
        self.now += .05
    def exit(self):
        self.exit_count += 1
    def init(self):
        self.init_count += 1
    def trigger_capture(self):
        mode = self.config.get_child_by_name("capturemode").value
        n = int(mode.split()[-2]) if "Bracket" in mode else 1

        # Discovery captures historically ran only at ISO 100.  The final
        # operational V3 qualification deliberately alternates ISO 100/200
        # to exercise the exact scheduled SET -> PHOTO path.
        iso = self.config.get_child_by_name("iso").value
        assert iso in ("100", "200")
        self.capture_isos.append(iso)

        assert self.config.get_child_by_name("capturetarget").value == "card"
        assert self.config.get_child_by_name("imageformat").value == "RAW"
        for _ in range(n):
            self.counter += 1
            self.events.append(SimpleNamespace(folder="DCIM", name=f"{self.counter}.raw"))
        self.now += .02
    def capture(self, kind):
        self.trigger_capture()
        self.now += .2
        return self.events.pop(0)
    def wait_for_event(self, timeout):
        self.now += .001
        return (1, self.events.pop(0)) if self.events else (0, None)


@pytest.mark.parametrize("bracket_label", ["Continuous Bracket 1 EV {} Img.", "Bracketing C 1.0 Steps {} Pictures"])
def test_full_local_characterization_without_network(monkeypatch, profile, bracket_label):
    import sys
    from backend import camera_characterization as module
    camera = SimulatedCamera(list(profile["commands"]["shutter"]["values"]))
    camera.config.get_child_by_name("capturemode").choices = ["Single Shot", bracket_label.format(3), bracket_label.format(5)]
    monkeypatch.setitem(sys.modules, "gphoto2", SimpleNamespace(GP_CAPTURE_IMAGE=2, GP_EVENT_FILE_ADDED=1, GP_EVENT_TIMEOUT=0))
    monkeypatch.setattr(module.time, "monotonic", lambda: camera.now)
    monkeypatch.setattr(module.time, "sleep", lambda seconds: setattr(camera, "now", camera.now + seconds))
    prompts = []

    def unexpected_prompt(self, message, kind="result"):
        prompts.append((kind, message))
        raise AssertionError(
            "Fully observable characterization must not ask the operator"
        )

    monkeypatch.setattr(CharacterizationJob, "ask", unexpected_prompt)
    result, timing = module.characterize(
        camera,
        {"manufacturer": "Test", "model": "Test Camera"},
        CharacterizationJob(),
    )

    # Every capture in this simulation is confirmed automatically by exact
    # FILE_ADDED counts, and every required camera state is USB-writable.
    # Characterization must therefore complete with zero operator prompts.
    assert prompts == []

    # Persistent-session characterization: one dedicated session cold-start
    # capture is measured first (excluded from candidate statistics), then
    # two single primitives receive five trials each; bracket primitives are
    # functionally checked at 3/5, then the selected primitive receives five
    # timing trials at the two available calibration sizes.  The operational
    # validation recipe then adds 12 RAWs.
    assert camera.counter == 71
    # Characterization/qualification never own the gphoto lifecycle.
    assert camera.exit_count == 0
    assert camera.init_count == 0

    # Discovery is ISO100-only, while operational qualification must prove
    # at least one real alternate-ISO transition before publication.
    assert "100" in camera.capture_isos
    assert "200" in camera.capture_isos
    for key, raw in timing["raw_timing"].items():
        if isinstance(raw, (int, float)):
            assert timing["timing"][key] >= raw
            assert timing["timing"][key] % 50 == 0
            # Operational reservations now use maximum + explicit margin.
            assert timing["timing"][key] >= raw
    assert result["strategy"] == "bracket"
    assert set(result["brackets"]) == {"3", "5"}
    assert result["commands"]["trigger_single"]["method"] == "trigger_capture"
    assert result["benchmark"]["repetitions"] == 5
    assert result["benchmark"]["guarded_optimized_ms"] < result["benchmark"]["guarded_sequential_ms"]
    assert result["benchmark"]["comparison_source"] == "operational budget model"
    assert "sequential_ms" not in result["benchmark"]
    assert len({json.dumps(v["trigger"], sort_keys=True) for v in result["brackets"].values()}) == 1
    assert "battery: unavailable" in result["warnings"]
    from backend.camera_timing import _TIMING_FIELDS
    assert set(_TIMING_FIELDS) <= timing["timing"].keys()
    assert timing["raw_timing"]["settle_idle_ms"] == 0
    assert result["settle_idle_s"] == 0
    assert timing["measurement_status"]["trigger_single_lead_ms"] == "scheduler_default"
    assert timing["timing"]["trigger_single_lead_ms"] == 0
    assert len(timing["timing_trials"]) == 4
    for trial in timing["timing_trials"]:
        assert len(trial["samples"]) == 5
        for sample in trial["samples"]:
            assert sample["test_pause_ms"] == 0
            assert sample["usb_return_ms"] >= 0
            assert sample["total_ms"] >= sample["file_complete_ms"]
    assert timing["timing_contract"]["single_usb_return_ms"] > 0
    assert timing["timing_contract"]["bracket_usb_return_ms"] > 0
    assert timing["timing_contract"]["bracket_calibration_frames"] == [3, 5]


def test_bracket_usb_return_requires_authoritative_capture_mode_readback(
    monkeypatch,
    profile,
):
    """A successful USB SET call is not enough if Sony silently ignores it."""
    import re
    import sys
    from backend import camera_characterization as module

    camera = SimulatedCamera(
        list(profile["commands"]["shutter"]["values"])
    )
    camera.config.get_child_by_name("capturemode").choices = [
        "Single Shot",
        "Continuous Bracket 1 EV 3 Img.",
        "Continuous Bracket 1 EV 5 Img.",
    ]

    monkeypatch.setitem(
        sys.modules,
        "gphoto2",
        SimpleNamespace(
            GP_CAPTURE_IMAGE=2,
            GP_EVENT_FILE_ADDED=1,
            GP_EVENT_TIMEOUT=0,
        ),
    )
    monkeypatch.setattr(
        module.time,
        "monotonic",
        lambda: camera.now,
    )
    monkeypatch.setattr(
        module.time,
        "sleep",
        lambda seconds: setattr(
            camera,
            "now",
            camera.now + seconds,
        ),
    )

    original_trigger = camera.trigger_capture
    original_set_single_config = camera.set_single_config
    busy_until = [0.0]
    last_bracket_mode = [None]
    silently_ignored_sets = []

    def trigger_capture():
        mode = camera.config.get_child_by_name("capturemode").value
        result = original_trigger()
        if "Bracket" in mode:
            last_bracket_mode[0] = mode
            # Sony-like tail: for a short period after the final bracket file,
            # set_single_config() returns success but the drive mode remains
            # physically unchanged.
            busy_until[0] = camera.now + 0.22
        return result

    def set_single_config(name, widget):
        requested = widget.get_value()
        original_set_single_config(name, widget)
        if (
            name == "capturemode"
            and requested == "Single Shot"
            and last_bracket_mode[0] is not None
            and camera.now < busy_until[0]
        ):
            widget.value = last_bracket_mode[0]
            silently_ignored_sets.append(camera.now)

    camera.trigger_capture = trigger_capture
    camera.set_single_config = set_single_config

    def unexpected_prompt(self, message, kind="result"):
        raise AssertionError(
            "Verified USB readiness must recover automatically without "
            f"operator input: kind={kind!r} message={message!r}"
        )

    monkeypatch.setattr(
        CharacterizationJob,
        "ask",
        unexpected_prompt,
    )

    job = CharacterizationJob()
    result, timing = module.characterize(
        camera,
        {"manufacturer": "Sony", "model": "Sony-like delayed drive mode"},
        job,
    )

    assert silently_ignored_sets
    assert result["strategy"] == "bracket"
    assert camera.config.get_child_by_name("capturemode").value == "Single Shot"
    assert timing["timing_contract"]["bracket_usb_return_ms"] > 0

    ready_attempt_counts = []
    for line in job.logs:
        match = re.search(r"USB SET-ready .* \((\d+) attempt\(s\)\)", line)
        if match:
            ready_attempt_counts.append(int(match.group(1)))

    assert ready_attempt_counts
    assert max(ready_attempt_counts) > 1


def test_raw_failure_prevents_all_exposures(monkeypatch, profile):
    from backend.camera_characterization import characterize
    camera = SimulatedCamera(list(profile["commands"]["shutter"]["values"]))
    camera.config.get_child_by_name("imageformat").choices = ["JPEG"]
    with pytest.raises(RuntimeError, match="raw"):
        characterize(camera, {"manufacturer": "Test", "model": "Test Camera"}, CharacterizationJob())
    assert camera.counter == 0


def test_idle_wait_restarts_on_late_events_and_collects_files(monkeypatch):
    import sys
    import plugins.camera.profile as module
    clock = [0.0]
    monkeypatch.setitem(sys.modules, "gphoto2", SimpleNamespace(GP_EVENT_TIMEOUT=0, GP_EVENT_FILE_ADDED=1))
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    pending = [(1.5, 1), (3.0, 2)]  # A file, then a non-file notification.
    class Camera:
        def wait_for_event(self, timeout):
            clock[0] += timeout / 1000
            if pending and clock[0] >= pending[0][0]:
                _, kind = pending.pop(0)
                return kind, SimpleNamespace(folder="DCIM", name="late.raw")
            return 0, None
    observed = set()
    duration = module.wait_camera_idle(Camera(), observed)
    assert 5000 <= duration < 5300
    assert observed == {("DCIM", "late.raw")}


def test_idle_wait_stops_when_events_never_cease(monkeypatch):
    import sys
    import plugins.camera.profile as module
    clock = [0.0]
    monkeypatch.setitem(sys.modules, "gphoto2", SimpleNamespace(GP_EVENT_TIMEOUT=0, GP_EVENT_FILE_ADDED=1))
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    class Camera:
        def wait_for_event(self, timeout):
            clock[0] += timeout / 1000
            return 2, None
    with pytest.raises(module.CameraIdleTimeout):
        module.wait_camera_idle(Camera(), set())
    assert 15 <= clock[0] < 15.2


def test_runtime_capture_never_uses_characterization_pause(monkeypatch, profile):
    import sys
    import plugins.camera.profile as module
    monkeypatch.setitem(sys.modules, "gphoto2", SimpleNamespace(GP_EVENT_TIMEOUT=0, GP_EVENT_FILE_ADDED=1))
    def forbidden(*args, **kwargs):
        raise AssertionError("Test pause must never run in Trigger")
    monkeypatch.setattr(module, "wait_camera_idle", forbidden)
    monkeypatch.setattr(module.time, "sleep", forbidden)
    events = []
    camera = SimpleNamespace(wait_for_event=lambda timeout: (1, events.pop(0)) if events else (0, None))
    profile["settle_idle_s"] = 2.0  # Older metadata must not impose a test pause.
    plugin = module.ProfilePlugin(camera, profile=profile)
    def trigger(spec):
        events.append(SimpleNamespace(folder="DCIM", name="photo.raw"))
    monkeypatch.setattr(plugin, "_trigger", trigger)
    result = plugin.execute_photo({"frames": 1, "shutter": "1/500"})
    assert result.frames == 1




def test_bracket_failure_prunes_primitive_from_larger_sizes_and_matrix_stays_sorted(
    monkeypatch,
    profile,
):
    import sys
    from backend import camera_characterization as module

    camera = SimulatedCamera(
        list(profile["commands"]["shutter"]["values"])
    )
    camera.config.get_child_by_name("capturemode").choices = [
        "Single Shot",
        "Continuous Bracket 1 EV 5 Img.",
        "Continuous Bracket 1 EV 3 Img.",
    ]
    monkeypatch.setitem(
        sys.modules,
        "gphoto2",
        SimpleNamespace(
            GP_CAPTURE_IMAGE=2,
            GP_EVENT_FILE_ADDED=1,
            GP_EVENT_TIMEOUT=0,
        ),
    )
    monkeypatch.setattr(module.time, "monotonic", lambda: camera.now)
    monkeypatch.setattr(
        module.time,
        "sleep",
        lambda seconds: setattr(camera, "now", camera.now + seconds),
    )

    original_trigger = camera.trigger_capture
    primitive_calls = []

    def trigger_capture():
        mode = camera.config.get_child_by_name("capturemode").value
        primitive_calls.append(("trigger_capture", mode))
        if mode == "Continuous Bracket 1 EV 3 Img.":
            raise RuntimeError("simulated trigger_capture failure for bracket 3")
        return original_trigger()

    def capture(kind):
        mode = camera.config.get_child_by_name("capturemode").value
        primitive_calls.append(("capture", mode))
        # Deliberately bypass the monkeypatched trigger_capture: this is a
        # distinct gphoto primitive and must remain independently testable.
        original_trigger()
        camera.now += .2
        return camera.events.pop(0)

    camera.trigger_capture = trigger_capture
    camera.capture = capture

    def ask(self, message, kind="result"):
        raise AssertionError(
            "Automatic capture failure must not request operator confirmation: "
            f"kind={kind!r} message={message!r}"
        )

    monkeypatch.setattr(CharacterizationJob, "ask", ask)

    result, timing = module.characterize(
        camera,
        {"manufacturer": "Test", "model": "Camera"},
        CharacterizationJob(),
    )

    bracket_calls = [
        (method, mode)
        for method, mode in primitive_calls
        if "Bracket" in mode
    ]

    # The discovery matrix itself is ordered by bracket size. Later
    # operational qualification deliberately reuses selected brackets, so the
    # complete primitive call history is not globally ordered by frame count.
    bracket_trials = [
        trial
        for trial in timing["timing_trials"]
        if trial["frames"] > 1
    ]
    trial_frames = [trial["frames"] for trial in bracket_trials]
    assert trial_frames == sorted(trial_frames)

    # A primitive that fails at bracket 3 is structurally pruned from
    # larger brackets. This avoids repeating a known-incompatible trigger at
    # 5/7/9 frames while independent primitives remain fully testable.
    assert any(
        method == "trigger_capture" and "3 Img." in mode
        for method, mode in bracket_calls
    )
    assert not any(
        method == "trigger_capture" and "5 Img." in mode
        for method, mode in bracket_calls
    )

    rejected_3 = [
        trial
        for trial in bracket_trials
        if (
            trial["frames"] == 3
            and trial["trigger"]["method"] == "trigger_capture"
            and trial["status"] == "rejected"
        )
    ]
    retried_5 = [
        trial
        for trial in bracket_trials
        if (
            trial["frames"] == 5
            and trial["trigger"]["method"] == "trigger_capture"
        )
    ]

    assert len(rejected_3) == 1
    assert retried_5 == []

    # Failure at 3 frames globally excludes this primitive only from larger
    # native brackets; the other capture primitive remains available.
    assert (
        "trigger_capture",
        "Continuous Bracket 1 EV 5 Img.",
    ) not in bracket_calls

    rejected = [
        trial
        for trial in timing["timing_trials"]
        if trial["frames"] == 3
        and trial["trigger"]["method"] == "trigger_capture"
        and trial["status"] == "rejected"
    ]
    assert len(rejected) == 1
    assert "samples" not in rejected[0]

    assert result["commands"]["trigger_single"]["method"] == "trigger_capture"
    assert result["brackets"]["3"]["trigger"]["method"] == "capture"
    assert result["brackets"]["5"]["trigger"]["method"] == "capture"
    selected = result["selection"]["native_bracket"][
        "selected_candidate_ids_by_frames"
    ]
    assert selected["3"] == selected["5"]


def test_single_capture_failure_does_not_suppress_bracket_capture(
    monkeypatch,
    profile,
):
    import sys
    from backend import camera_characterization as module

    camera = SimulatedCamera(
        list(profile["commands"]["shutter"]["values"])
    )
    camera.config.get_child_by_name("capturemode").choices = [
        "Single Shot",
        "Continuous Bracket 1 EV 3 Img.",
        "Continuous Bracket 1 EV 5 Img.",
    ]
    monkeypatch.setitem(
        sys.modules,
        "gphoto2",
        SimpleNamespace(
            GP_CAPTURE_IMAGE=2,
            GP_EVENT_FILE_ADDED=1,
            GP_EVENT_TIMEOUT=0,
        ),
    )
    monkeypatch.setattr(module.time, "monotonic", lambda: camera.now)
    monkeypatch.setattr(
        module.time,
        "sleep",
        lambda seconds: setattr(camera, "now", camera.now + seconds),
    )

    original_trigger = camera.trigger_capture
    capture_modes = []

    def capture(kind):
        mode = camera.config.get_child_by_name("capturemode").value
        capture_modes.append(mode)
        original_trigger()
        if mode == "Single Shot":
            # A physical file exists and will be drained automatically, but the
            # capture primitive itself reports an error. That is an automatic
            # runtime rejection, not an operator question.
            raise RuntimeError("simulated single capture USB error")
        camera.now += .2
        return camera.events.pop(0)

    camera.capture = capture

    prompts = []

    def unexpected_prompt(self, message, kind="result"):
        prompts.append((kind, message))
        raise AssertionError(
            "Exact file evidence or runtime error must be resolved automatically"
        )

    monkeypatch.setattr(CharacterizationJob, "ask", unexpected_prompt)

    result, timing = module.characterize(
        camera,
        {"manufacturer": "Test", "model": "Camera"},
        CharacterizationJob(),
    )

    assert prompts == []
    assert "Single Shot" in capture_modes
    assert "Continuous Bracket 1 EV 3 Img." in capture_modes
    assert "Continuous Bracket 1 EV 5 Img." in capture_modes

    rejected_single_capture = [
        trial
        for trial in timing["timing_trials"]
        if trial["frames"] == 1
        and trial["trigger"]["method"] == "capture"
        and trial["status"] == "rejected"
    ]
    assert len(rejected_single_capture) == 1
    assert result["commands"]["trigger_single"]["method"] == "trigger_capture"



def test_characterization_preserves_single_shot_target_for_readonly_capture_mode(
    monkeypatch,
    profile,
):
    """Readonly drive mode keeps required target, not current physical value."""
    import sys
    from backend import camera_characterization as module

    camera = SimulatedCamera(
        list(profile["commands"]["shutter"]["values"])
    )

    drive = camera.config.get_child_by_name("capturemode")

    # Nikon D850-like case: physical release mode is CH/Burst while
    # gphoto2 exposes the widget as GET-only.
    drive.value = "Burst"
    drive.choices = [
        "Single Shot",
        "Burst",
    ]
    drive.get_readonly = lambda: True

    def forbidden_set(value):
        raise AssertionError(
            "GET-only capture_mode must never receive a SET"
        )

    drive.set_value = forbidden_set

    monkeypatch.setitem(
        sys.modules,
        "gphoto2",
        SimpleNamespace(
            GP_CAPTURE_IMAGE=2,
            GP_EVENT_FILE_ADDED=1,
            GP_EVENT_TIMEOUT=0,
        ),
    )

    monkeypatch.setattr(
        module.time,
        "monotonic",
        lambda: camera.now,
    )

    monkeypatch.setattr(
        module.time,
        "sleep",
        lambda seconds: setattr(
            camera,
            "now",
            camera.now + seconds,
        ),
    )

    physical_preflight_prompts = []

    def confirm_d850(self, message, kind="result"):
        if kind == "start":
            # In this simulated D850 all photographic settings except the
            # physical release-mode selector are USB-writable. Therefore any
            # operator action requested here is the physical Single Shot change.
            physical_preflight_prompts.append(message)

            # Simulate the human moving the physical release-mode selector.
            # Direct assignment is deliberate: forbidden_set() below proves
            # that the backend itself never issued an USB SET.
            drive.value = "Single Shot"

        return True

    monkeypatch.setattr(
        CharacterizationJob,
        "ask",
        confirm_d850,
    )

    result, timing = module.characterize(
        camera,
        {
            "manufacturer": "Nikon",
            "model": "Nikon DSC D850",
        },
        CharacterizationJob(),
    )

    spec = result["commands"]["capture_mode"]

    # This is the regression:
    # old behaviour could store "Burst" because it was the current value.
    assert spec["value"] == "Single Shot"
    assert spec["get"] is True
    assert spec["set"] is False

    # The backend never altered the GET-only selector.  The simulated
    # operator did so only after the qualification preflight requested it.
    assert physical_preflight_prompts
    assert drive.value == "Single Shot"

    # GET-only drive mode means no native USB bracket manipulation.
    assert result["strategy"] == "sequential"
    assert timing is not None

    # Characterization and qualification use the already-open camera session.
    assert camera.exit_count == 0
    assert camera.init_count == 0


def test_single_shot_operator_instruction_is_unambiguous(profile):
    plugin = ProfilePlugin(None, profile=profile)

    message = plugin._manual_instruction(
        "capture_mode",
        "Single Shot",
        "Burst",
    )

    assert "single-shot release mode" in message
    assert "Single Shot" in message
    assert "Burst" in message
    assert "S / Single Shot" not in message


def test_equivalent_shutter_spelling_resolves_to_characterized_value(profile):
    profile["commands"]["shutter"]["values"]["5/10"] = "5/10"

    plugin = ProfilePlugin(None, profile=profile)
    capture_intent = SimpleNamespace(
        exposure_plan=[
            {"shutter": "1/2", "iso": 100},
        ]
    )

    prepared = plugin.prepare_capture(capture_intent)

    photos = [
        operation
        for operation in prepared.token[1]
        if operation["action"] == "trigger_capture"
    ]

    assert len(photos) == 1
    assert photos[0]["shutter"] == "5/10"


def test_genuinely_unsupported_shutter_still_fails_closed(profile):
    plugin = ProfilePlugin(None, profile=profile)
    capture_intent = SimpleNamespace(
        exposure_plan=[
            {"shutter": "1/3", "iso": 100},
        ]
    )

    with pytest.raises(
        ValueError,
        match=r"Unsupported profile shutter: 1/3",
    ):
        plugin.prepare_capture(capture_intent)



def test_single_bracket_size_keeps_positive_interframe_guard(
    monkeypatch,
    profile,
):
    """One native bracket remains valid with a guarded inter-frame term."""
    import sys
    from backend import camera_characterization as module

    camera = SimulatedCamera(
        list(profile["commands"]["shutter"]["values"])
    )

    camera.config.get_child_by_name(
        "capturemode"
    ).choices = [
        "Single Shot",
        "Continuous Bracket 1 EV 3 Img.",
    ]

    monkeypatch.setitem(
        sys.modules,
        "gphoto2",
        SimpleNamespace(
            GP_CAPTURE_IMAGE=2,
            GP_EVENT_FILE_ADDED=1,
            GP_EVENT_TIMEOUT=0,
        ),
    )

    monkeypatch.setattr(
        module.time,
        "monotonic",
        lambda: camera.now,
    )

    monkeypatch.setattr(
        module.time,
        "sleep",
        lambda seconds: setattr(
            camera,
            "now",
            camera.now + seconds,
        ),
    )

    monkeypatch.setattr(
        CharacterizationJob,
        "ask",
        lambda *args, **kwargs: (
            (_ for _ in ()).throw(
                AssertionError(
                    "No operator prompt expected"
                )
            )
        ),
    )

    result, timing = module.characterize(
        camera,
        {
            "manufacturer": "Test",
            "model": "Single Bracket Camera",
        },
        CharacterizationJob(),
    )

    assert set(result["brackets"]) == {"3"}

    contract = timing["timing_contract"]

    assert contract[
        "supported_bracket_frames"
    ] == [3]

    assert contract[
        "bracket_calibration_frames"
    ] == [3]

    # Inter-frame cannot be identified from one size, but
    # the operational contract still needs a positive guard.
    assert contract[
        "bracket_inter_image_ms"
    ] == 50


def test_failed_bracket9_does_not_discard_valid_smaller_brackets(
    monkeypatch,
    profile,
):
    """BRK9 failure must not force BRK3/5/7 back to sequential."""
    import sys
    from backend import camera_characterization as module

    camera = SimulatedCamera(
        list(profile["commands"]["shutter"]["values"])
    )

    camera.config.get_child_by_name(
        "capturemode"
    ).choices = [
        "Single Shot",
        "Continuous Bracket 1 EV 3 Img.",
        "Continuous Bracket 1 EV 5 Img.",
        "Continuous Bracket 1 EV 7 Img.",
        "Continuous Bracket 1 EV 9 Img.",
    ]

    monkeypatch.setitem(
        sys.modules,
        "gphoto2",
        SimpleNamespace(
            GP_CAPTURE_IMAGE=2,
            GP_EVENT_FILE_ADDED=1,
            GP_EVENT_TIMEOUT=0,
        ),
    )

    monkeypatch.setattr(
        module.time,
        "monotonic",
        lambda: camera.now,
    )

    monkeypatch.setattr(
        module.time,
        "sleep",
        lambda seconds: setattr(
            camera,
            "now",
            camera.now + seconds,
        ),
    )

    original_trigger = camera.trigger_capture

    def trigger_without_brk9():
        mode = camera.config.get_child_by_name(
            "capturemode"
        ).value

        if "9 Img." in mode:
            raise RuntimeError(
                "simulated BRK9 unsupported"
            )

        return original_trigger()

    camera.trigger_capture = (
        trigger_without_brk9
    )

    monkeypatch.setattr(
        CharacterizationJob,
        "ask",
        lambda *args, **kwargs: (
            (_ for _ in ()).throw(
                AssertionError(
                    "No operator prompt expected"
                )
            )
        ),
    )

    result, timing = module.characterize(
        camera,
        {
            "manufacturer": "Test",
            "model": "Partial Bracket Camera",
        },
        CharacterizationJob(),
    )

    assert result["strategy"] == "bracket"

    assert set(
        result["brackets"]
    ) == {
        "3",
        "5",
        "7",
    }

    contract = timing["timing_contract"]

    assert contract[
        "supported_bracket_frames"
    ] == [
        3,
        5,
        7,
    ]

    assert contract[
        "bracket_calibration_frames"
    ] == [
        3,
        7,
    ]

    assert "9" not in result["brackets"]


def test_publication_accepts_only_canonical_shared_camera_symlinks(tmp_path, profile):
    timing = {
        "config_type": "camera_timing",
        "timing": {},
        **{key: profile[key] for key in ("manufacturer", "model", "backend")},
    }
    shared_profiles = tmp_path / "var/generated/camera_profiles"
    shared_timing = tmp_path / "var/generated/camera_timing"
    shared_profiles.mkdir(parents=True)
    shared_timing.mkdir(parents=True)
    configs = tmp_path / "configs"
    configs.mkdir()
    (configs / "camera_profiles").symlink_to(shared_profiles, target_is_directory=True)
    (configs / "camera_timing").symlink_to(shared_timing, target_is_directory=True)

    paths = publish(profile, timing, tmp_path)

    assert all((tmp_path / relative).exists() for relative in paths)
    assert any(shared_profiles.glob("*.json"))
    assert any(shared_timing.glob("*.json"))


def test_publication_rejects_camera_symlink_outside_shared_var(tmp_path, profile):
    timing = {
        "config_type": "camera_timing",
        "timing": {},
        **{key: profile[key] for key in ("manufacturer", "model", "backend")},
    }
    outside_profiles = tmp_path / "outside-profiles"
    outside_timing = tmp_path / "outside-timing"
    outside_profiles.mkdir()
    outside_timing.mkdir()
    configs = tmp_path / "configs"
    configs.mkdir()
    (configs / "camera_profiles").symlink_to(outside_profiles, target_is_directory=True)
    (configs / "camera_timing").symlink_to(outside_timing, target_is_directory=True)

    with pytest.raises(ValueError, match="shared persistent data"):
        publish(profile, timing, tmp_path)
