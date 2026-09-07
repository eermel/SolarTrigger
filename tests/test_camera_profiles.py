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


def test_compile_profile_audit_to_photo_units(monkeypatch, profile):
    from datetime import datetime, timezone
    from backend import camera_profiles
    from backend.sequencer_compiler import (
        audit_materialized_capture, _split_totality_single_photos,
        schedule_audited_capture, CameraTimingProfile,
    )
    profile["strategy"] = "bracket"
    monkeypatch.setattr(camera_profiles, "discover_profiles", lambda: {profile["backend"]: profile})
    target = SimpleNamespace(phase="TOTALITY", target_time=datetime.now(timezone.utc),
                             deadline=None, phase_window="phase_2", sequence_index=0)
    capture = SimpleNamespace(backend=profile["backend"], rig_id=1, target=target,
                              aperture=None, final_exposure_plan=tuple(intent(profile).exposure_plan))
    audited = audit_materialized_capture(capture)
    units = _split_totality_single_photos(audited)
    assert sum(u.planned_count for u in units) == 9
    timing = CameraTimingProfile(backend=profile["backend"], set_iso_ms=100,
                                 set_capturemode_ms=100, set_shutter_ms=100,
                                 trigger_single_duration_ms=500,
                                 bracket_atomic_ms_by_frames={3: 1000, 5: 1000})
    for unit in units:
        scheduled = schedule_audited_capture(unit, timing)
        assert all(s.command_time is not None for s in scheduled)
        assert scheduled[-1].duration_ms > 0


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
    confirmations = []
    qualification_starts = []

    def confirm(self, message, kind="result"):
        if (
            kind == "start"
            and "Qualification opérationnelle finale" in message
        ):
            qualification_starts.append((kind, camera.counter))
            return True

        confirmations.append((kind, camera.counter))
        if kind == "start":
            assert (
                len(confirmations) == 1
                or confirmations[-2][0] == "result"
            )
        else:
            assert confirmations[-2][0] == "start"
            assert camera.counter > confirmations[-2][1]
        return True

    monkeypatch.setattr(CharacterizationJob, "ask", confirm)
    result, timing = module.characterize(
        camera,
        {"manufacturer": "Test", "model": "Test Camera"},
        CharacterizationJob(),
    )

    assert confirmations[0] == ("start", 0)
    assert confirmations[-1][0] == "result"
    assert len(confirmations) == 12
    assert len(qualification_starts) == 1

    # Historical discovery/timing = 108 physical images.
    # Main operational recipe = 4 singles + bracket 3 + bracket 5 = 12.
    # Cold-bracket proof adds one bracket-5 as the first PHOTO of a second
    # fresh session: 108 + 12 + 5 = 125 physical images.
    assert camera.counter == 125
    assert camera.exit_count == 2
    assert camera.init_count == 2

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
    assert timing["physical_latency_measured"] is False
    assert "battery: unavailable" in result["warnings"]
    from backend.camera_timing import _TIMING_FIELDS
    assert set(_TIMING_FIELDS) <= timing["timing"].keys()
    assert timing["raw_timing"]["settle_idle_ms"] == 0
    assert result["settle_idle_s"] == 0
    assert timing["measurement_status"]["trigger_single_latency_ms"] == "unmeasured"
    assert timing["timing"]["trigger_single_latency_ms"] == 0
    assert len(timing["timing_trials"]) == 6
    for trial in timing["timing_trials"]:
        assert len(trial["samples"]) == 5
        for sample in trial["samples"]:
            assert sample["test_pause_ms"] >= 2000
            assert sample["total_ms"] < sample["test_pause_ms"]
            summed = sum(sample[k] for k in ("pre_trigger_drain_ms", "trigger_call_ms", "frame_wait_ms", "release_ms", "post_release_wait_ms", "settle_ms"))
            assert summed == pytest.approx(sample["total_ms"], abs=0.01)


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


def test_budgeted_plan_has_self_contained_groups_and_exact_reservations(profile):
    from backend.camera_timing_contract import POLICY
    from backend.sequencer_compiler import _set_operation_duration_ms, CameraTimingProfile
    profile['brackets'] = {}
    profile['timing_contract'] = {
        'version': 2, 'policy': POLICY, 'iso_ms': 400,
        'single': {'setup_ms': 700, 'duration_ms': 1000, 'reference_exposure_s': .002},
        'brackets': {}, 'sustained': {'status': 'validated'},
    }
    plan = SimpleNamespace(exposure_plan=[{'shutter': '1/500', 'iso': 100}] * 2)
    prepared = ProfilePlugin(None, profile=profile).prepare_capture(plan)
    ops = prepared.token[1]
    assert [o.get('parameter', o['action']) for o in ops] == [
        'iso', 'capture_setup', 'trigger_capture', 'iso', 'capture_setup', 'trigger_capture']
    assert prepared.estimated_total_s == pytest.approx(sum(o['duration_ms'] for o in ops)/1000)
    assert ops[2]['duration_ms'] == 1000  # The reference exposure is included once.
    assert _set_operation_duration_ms(ops[1], CameraTimingProfile(backend='profile-example')) == 700
    profile['timing_contract']['sustained']['status'] = 'pending'
    with pytest.raises(ValueError, match='qualification'):
        ProfilePlugin(None, profile=profile)


@pytest.mark.parametrize('reject_single', [False, True])
def test_command_exclusion_is_bracket_only_and_sizes_are_sorted(monkeypatch, profile, reject_single):
    import sys
    from backend import camera_characterization as module
    camera = SimulatedCamera(list(profile['commands']['shutter']['values']))
    camera.config.get_child_by_name('capturemode').choices = [
        'Single Shot', 'Continuous Bracket 1 EV 5 Img.', 'Continuous Bracket 1 EV 3 Img.']
    monkeypatch.setitem(sys.modules, 'gphoto2', SimpleNamespace(GP_CAPTURE_IMAGE=2, GP_EVENT_FILE_ADDED=1, GP_EVENT_TIMEOUT=0))
    monkeypatch.setattr(module.time, 'monotonic', lambda: camera.now)
    monkeypatch.setattr(module.time, 'sleep', lambda seconds: setattr(camera, 'now', camera.now + seconds))
    starts, current = [], ['']
    def ask(self, message, kind='result'):
        if kind == 'start':
            starts.append(message)
            current[0] = message
            return True
        if reject_single:
            return not ('1 photo(s)' in current[0] and "'method': 'capture'" in current[0])
        return not ('3 photo(s)' in current[0] and "'method': 'trigger_capture'" in current[0])
    monkeypatch.setattr(CharacterizationJob, 'ask', ask)
    result, timing = module.characterize(camera, {'manufacturer': 'Test', 'model': 'Camera'}, CharacterizationJob())
    bracket_starts = [m for m in starts if '1 photo(s)' not in m]
    assert '3 photo(s)' in bracket_starts[0]
    if not reject_single:
        assert not any('5 photo(s)' in m and "'method': 'trigger_capture'" in m for m in starts)
        rejected = [t for t in timing['timing_trials'] if t['frames'] > 1 and t['trigger']['method'] == 'trigger_capture']
        assert len(rejected) == 1 and rejected[0]['status'] == 'rejected' and 'samples' not in rejected[0]
        assert result['commands']['trigger_single']['method'] == 'trigger_capture'
        assert result['bracket_command']['method'] == 'capture'
    else:
        assert any('5 photo(s)' in m and "'method': 'capture'" in m for m in starts)
    assert len({json.dumps(v['trigger'], sort_keys=True) for v in result['brackets'].values()}) == 1



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
        if (
            kind == "start"
            and "Corrigez ce réglage physiquement" in message
        ):
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

    # Qualification still exercised a fresh camera session.
    assert camera.exit_count == 1
    assert camera.init_count == 1


def test_single_shot_operator_instruction_is_unambiguous(profile):
    plugin = ProfilePlugin(None, profile=profile)

    message = plugin._manual_instruction(
        "capture_mode",
        "Single Shot",
        "Burst",
    )

    assert "mode de déclenchement vue par vue" in message
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
