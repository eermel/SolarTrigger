"""Local characterization. No network, shell execution or generated Python.

Only explicitly understood photographic settings are written. Enumeration is
exhaustive; probing arbitrary writable widgets (format card, firmware, etc.) is
deliberately forbidden. All trigger alternatives are tested with RAW/card/ISO100.
"""
from __future__ import annotations

from collections import deque
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import statistics
import threading
import time
import uuid

from backend.camera_profiles import validate_profile
from plugins.camera.profile import write_widget, widget, ProfilePlugin, wait_camera_idle, CameraIdleTimeout, write_checked, prepare_photo

ROOT = Path(__file__).resolve().parents[1]


class Cancelled(RuntimeError):
    pass


class CharacterizationJob:
    def __init__(self):
        self.lock = threading.RLock()
        self.condition = threading.Condition(self.lock)
        self.logs = deque(maxlen=2000)
        self.running = False
        self.cancelled = False
        self.question = None
        self.answer = None
        self.result = None
        self.job_id = None
        self.measurement_path = None
        self.measurement_state = {}

    def checkpoint(self, **data):
        """Keep measured evidence even if qualification fails; never a plugin.

        Snapshots contain measurements, not reboot-persistent debug logs. They
        are not automatically reused against a different physical configuration.
        """
        self.measurement_state.update(data)
        if self.measurement_path is None:
            return
        import os
        import tempfile
        temporary = None
        try:
            path = self.measurement_path
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as handle:
                temporary = Path(handle.name)
                json.dump(self.measurement_state, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except (OSError, TypeError, ValueError) as exc:
            self.log(f"Measurement checkpoint unavailable: {exc}")
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def log(self, message):
        with self.lock:
            self.logs.append(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {message}")

    def check(self):
        if self.cancelled:
            raise Cancelled("Characterization cancelled")

    def ask(self, message, kind="result"):
        with self.condition:
            self.check()
            self.question = {"id": uuid.uuid4().hex, "message": message, "kind": kind}
            self.answer = None
            until = time.monotonic() + 600
            while self.answer is None:
                self.check()
                remaining = until - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError("Operator confirmation timed out")
                self.condition.wait(min(1, remaining))
            answer = self.answer
            self.question = None
            return answer

    def respond(self, question_id, answer):
        with self.condition:
            if type(answer) is not bool or not self.question or self.question["id"] != question_id:
                raise ValueError("Stale or invalid operator confirmation")
            self.answer = answer
            self.condition.notify_all()

    def snapshot(self):
        with self.lock:
            return deepcopy({"running": self.running, "job_id": self.job_id,
                             "logs": list(self.logs), "question": self.question, "result": self.result})

    def start(self, entry, root=ROOT):
        with self.lock:
            if self.running:
                raise RuntimeError("A characterization is already running")
            self.running, self.cancelled = True, False
            self.question = self.result = None
            self.logs.clear()
            self.job_id = uuid.uuid4().hex
            threading.Thread(target=self._run, args=(deepcopy(entry), Path(root)), daemon=True).start()

    def _run(self, entry, root):
        camera = None
        self.measurement_state = {}
        self.measurement_path = root / "configs/camera_characterization/measurements" / f"{self.job_id or uuid.uuid4().hex}.json"
        summary = {"date_utc": datetime.now(timezone.utc).isoformat(),
                   "manufacturer": entry["manufacturer"], "model": entry["model"],
                   "status": "FAILED", "files": [], "schema_version": 1}
        try:
            import gphoto2 as gp
            camera = gp.Camera()
            ports = gp.PortInfoList()
            ports.load()
            camera.set_port_info(ports[ports.lookup_path(entry["transport_locator"])])
            camera.init()
            profile, timing = characterize(camera, entry, self)
            self.check()
            summary.update(status="PARTIAL" if profile["warnings"] else "SUCCESS",
                           strategy=profile["strategy"], warnings=profile["warnings"])
            summary["files"] = publish(profile, timing, root)
            self.log(f"Profile installed: {profile['backend']} ({profile['strategy']})")
        except Exception as exc:
            summary["error"] = str(exc)
            self.log(f"FAILED: {exc}")
        finally:
            self.checkpoint(outcome=summary)
            if camera is not None:
                try:
                    camera.exit()
                except Exception as exc:
                    self.log(f"Camera close: {exc}")
            try:
                history = root / "configs/camera_characterization/history.jsonl"
                history.parent.mkdir(parents=True, exist_ok=True)
                with history.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(summary, ensure_ascii=False) + "\n")
                    handle.flush()
                    import os
                    os.fsync(handle.fileno())
            except OSError as exc:
                summary["history_error"] = str(exc)
                self.log(f"History write failed: {exc}")
            with self.condition:
                self.result = summary
                self.running = False
                self.question = None
                self.condition.notify_all()


def enumerate_widgets(camera):
    result = []
    def walk(node, parent=""):
        path = f"{parent}/{node.get_name()}"
        if node.count_children():
            for child in node.get_children():
                walk(child, path)
        else:
            try:
                choices = list(node.get_choices())
            except Exception:
                choices = []
            result.append({"path": path, "name": node.get_name().lower(),
                           "value": node.get_value(), "choices": choices,
                           "readonly": bool(node.get_readonly())})
    walk(camera.get_config())
    return result


def publish(profile, timing, root):
    """Timing first, profile last as activation marker; never overwrite a model."""
    validate_profile(profile)
    for key in ("manufacturer", "model", "backend"):
        if timing.get(key) != profile[key]:
            raise ValueError(f"Timing/profile identity mismatch: {key}")
    slug = profile["backend"][8:]
    files = [(root / "configs/camera_timing" / f"{slug}.json", timing),
             (root / "configs/camera_profiles" / f"{slug}.json", profile)]
    if any(path.exists() for path, _ in files):
        raise RuntimeError("Characterization files already exist; no overwrite performed")
    written = []
    try:
        for path, document in files:
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.parent.resolve() != root.resolve() / path.parent.relative_to(root):
                raise ValueError("Camera configuration directories must not be symlinks")
            # Exclusive creation and atomic hard-link publication prevent partial JSON
            # from becoming discoverable, including after power loss.
            import os
            import tempfile
            with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, suffix=".tmp", delete=False) as handle:
                temp = Path(handle.name)
                json.dump(document, handle, indent=2, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                if document.get("config_type") == "camera_timing":
                    from backend.camera_timing import load_camera_timing_profile
                    load_camera_timing_profile(temp)
                os.link(temp, path)
                written.append(path)
            finally:
                temp.unlink(missing_ok=True)
        return [str(p.relative_to(root)) for p in written]
    except Exception:
        for path in written:
            path.unlink(missing_ok=True)
        raise


def choose_common_bracket_command(candidates, excluded, sizes):
    """A single exact command must pass every discovered size; no mixing."""
    required = {str(n) for n in sizes}
    eligible = {key: trials for key, trials in candidates.items()
                if key not in excluded and required and set(trials) == required}
    if not eligible:
        return None
    return min(eligible, key=lambda key: (
        sum(item["spec"]["peak_capture_ms"] for item in eligible[key].values()), key))


def characterize(camera, entry, job):
    commands, warnings, measurements = {}, [], {}
    timing_trials = []
    job.checkpoint(manufacturer=entry["manufacturer"], model=entry["model"],
                   timing_trials=timing_trials, commands=commands)
    set_samples = {}
    setup_samples = {"1": []}
    initial = enumerate_widgets(camera)
    for item in initial:
        job.log(f"DISCOVER {item['path']} readonly={item['readonly']} choices={item['choices']}")

    def find_setting(key, names, accept, critical=True):
        # Re-enumerate after each mode change: writeability and choices can change.
        candidates = [w for w in enumerate_widgets(camera) if w["name"] in names and not w["readonly"]]
        errors = []
        for candidate in candidates:
            values = candidate["choices"] or [candidate["value"]]
            if key == "capture_target":
                values = sorted(values, key=lambda v: str(v).casefold() != "card+sdram")
            for value in values:
                if not accept(str(value)):
                    continue
                job.check()
                try:
                    write_widget(camera, candidate["path"], value)
                    deadline = time.monotonic() + 5.0
                    for attempt in range(20):
                        job.check()
                        _, node = widget(camera, candidate["path"])
                        actual = node.get_value()
                        if str(actual) == str(value):
                            break
                        if time.monotonic() >= deadline or attempt == 19:
                            raise RuntimeError(f"readback mismatch: requested={value!r}, actual={actual!r}")
                        job.log(f"WAIT {key}: requested={value!r}, actual={actual!r}")
                        time.sleep(0.25)
                    commands[key] = {"path": candidate["path"], "value": value}
                    job.log(f"VALID {key}: {candidate['path']}={value}")
                    return candidate
                except Exception as exc:
                    errors.append(str(exc))
                    job.log(f"RETRY {key}: {exc}")
        if critical:
            raise RuntimeError(f"Critical function unavailable: {key}; {'; '.join(errors)}")
        warnings.append(f"{key}: unavailable")
        return None

    find_setting("manual_mode", ("expprogram", "autoexposuremode", "exposuremode"),
                 lambda s: s.casefold() in ("m", "manual"))
    find_setting("capture_target", ("capturetarget",),
                 lambda s: s.casefold() in ("card+sdram", "card", "memory card", "sd card"))
    find_setting("raw", ("imageformat", "imagequality", "imagequality2"),
                 lambda s: ("raw" in s.casefold() or "nef" in s.casefold()) and
                 not any(v in s.casefold() for v in ("+", "jpeg", "jpg")))
    for key, names in (("self_timer", ("selftimer", "selftimerdelay")),
                       ("time_lapse", ("intervalshooting", "timelapse"))):
        if any(w["name"] in names for w in enumerate_widgets(camera)):
            find_setting(key, names, lambda s: s.casefold() in ("off", "0", "disabled"), False)
    iso = find_setting("iso", ("iso", "iso2"), lambda s: s == "100")
    commands["iso"]["values"] = {str(v): v for v in iso["choices"] if str(v).isdigit()}
    mode = find_setting("capture_mode", ("capturemode", "drivemode"),
                        lambda s: s.casefold() in ("single shot", "single", "single frame"), False)
    shutter = find_setting("shutter", ("shutterspeed", "shutterspeed2", "exptime"), lambda s: s == "1/500")
    from plugins.camera.base import _parse_speed
    speeds = {}
    for value in shutter["choices"]:
        try:
            if 0 < _parse_speed(value) <= 30:
                speeds[str(value)] = value
        except (ValueError, ZeroDivisionError):
            pass
    commands["shutter"]["values"] = speeds
    for item in enumerate_widgets(camera):
        if item["name"] in ("batterylevel", "battery"):
            commands["battery"] = {"path": item["path"]}
            job.log(f"Battery: {item['value']}")
        if item["name"] in ("f-number", "aperture") and not item["readonly"]:
            commands["aperture"] = {"path": item["path"], "values": {str(v): v for v in item["choices"]}}
    if "battery" not in commands:
        warnings.append("battery: unavailable")

    def measure_set(key, values):
        samples = []
        for _ in range(5):
            for value in values:
                job.check()
                begin = time.monotonic()
                write_checked(camera, commands[key]["path"], value)
                samples.append((time.monotonic() - begin)*1000)
        set_samples[key] = samples
        result = statistics.median(samples)
        job.log(f"TIMING {key}: median {result:.1f} ms ({len(samples)} operations)")
        return result

    # ISO alternates only during transaction timing, then returns to ISO100.
    # Every exposure/strategy comparison uses ISO100, never base/expanded ISO.
    iso_other = next((v for k, v in commands["iso"]["values"].items() if int(k) > 100), None)
    if iso_other is None:
        raise RuntimeError("Cannot measure a real ISO transition: no alternate standard ISO")
    measurements["set_iso_ms"] = measure_set("iso", [iso_other, commands["iso"]["values"]["100"]])
    other = next((v for k, v in speeds.items() if k != "1/500"), None)
    if other is None:
        raise RuntimeError("Cannot measure shutter transitions")
    measurements["set_shutter_ms"] = measure_set("shutter", [other, speeds["1/500"]])
    # Initial modes, battery, timer and timelapse are never timed separately.
    # Preparation is the exact sequence later used by the profile executor.
    for speed in [other, speeds["1/500"]] * 5:
        begin = time.monotonic()
        prepare_photo(camera, commands, speed)
        setup_samples["1"].append((time.monotonic() - begin) * 1000)

    # Probe all known capture entry points, not just the first successful one.
    trigger_candidates = [{"method": "trigger_capture"}, {"method": "capture"}]
    for item in enumerate_widgets(camera):
        if item["name"] in ("capture", "bulb") and not item["readonly"]:
            candidate = {"method": "widget", "path": item["path"], "value": 1, "release": 0}
            if candidate not in trigger_candidates:
                trigger_candidates.append(candidate)
    import gphoto2 as gp
    job.log("TEST POLICY: single and bracket commands validated independently; rejected bracket commands are never retried at larger sizes")
    job.log("PHOTO SAVING: redundant comparison removed (up to 45 singles and 45 bracket frames); each tested method uses one discovery plus five timing trials")
    job.log("QUALIFICATION: five passes per selected block; budget revisions restart that block until validation or operator cancellation")
    validated_trials = set()
    def probe(spec, expected=1, exposure_s=0.002):
        job.check()
        trial_key = (spec['method'], spec.get('path'), spec.get('value'), spec.get('release'), expected)
        discovery = trial_key not in validated_trials
        if discovery and not job.ask(
            f"Prêt pour un test de {expected} photo(s) RAW à ISO 100 ? "
            f"Commande : {spec}. Attendez que le boîtier ait terminé toute prise précédente, "
            "puis cliquez sur OK pour démarrer et observez les déclenchements.", kind="start"
        ):
            raise Cancelled("Test cancelled by operator before capture")
        job.log(f"TEST START: {expected} photo(s), {spec}")
        # Discard old events before starting a trial.
        for _ in range(100):
            kind, _data = camera.wait_for_event(1)
            if kind == gp.GP_EVENT_TIMEOUT:
                break
        seen = set()
        error = None
        returned = release_ms = 0.0
        begin = time.monotonic()
        try:
            method = spec["method"]
            if method == "capture":
                file = camera.capture(gp.GP_CAPTURE_IMAGE)
                if getattr(file, "name", None):
                    seen.add((file.folder, file.name))
            elif method == "trigger_capture":
                camera.trigger_capture()
            else:
                write_widget(camera, spec["path"], spec["value"])
            returned = (time.monotonic()-begin)*1000
            until = time.monotonic() + exposure_s + 5
            while len(seen) < expected and time.monotonic() < until:
                job.check()
                kind, data = camera.wait_for_event(100)
                if kind == gp.GP_EVENT_FILE_ADDED:
                    seen.add((getattr(data, "folder", ""), getattr(data, "name", str(data))))
        except Cancelled:
            raise
        except Exception as exc:
            error = exc
        finally:
            before_release = time.monotonic()
            if "release" in spec:
                write_widget(camera, spec["path"], spec["release"])
                release_ms = (time.monotonic() - before_release) * 1000
        # Files delivered after release are still part of capture completion.
        post_release_start = time.monotonic()
        until = post_release_start + exposure_s + 5
        while error is None and len(seen) < expected and time.monotonic() < until:
            job.check()
            kind, data = camera.wait_for_event(100)
            if kind == gp.GP_EVENT_FILE_ADDED:
                seen.add((getattr(data, "folder", ""), getattr(data, "name", str(data))))
        post_release_ms = (time.monotonic() - post_release_start) * 1000
        duration = (time.monotonic()-begin)*1000
        capture_confirmed = len(seen) == expected
        job.log(f"TEST PAUSE: 2 seconds without USB events, excluded from timing; files {len(seen)}/{expected}")
        idle_ms = wait_camera_idle(camera, seen, check=job.check)
        job.log(f"TEST PAUSE END: {idle_ms:.1f} ms, excluded; files {len(seen)}/{expected}")
        phases = {
            "trigger_call_ms": returned,
            "frame_wait_ms": max(0.0, (before_release - begin) * 1000 - returned),
            "release_ms": release_ms,
            "post_release_wait_ms": post_release_ms,
            "settle_ms": 0.0,
            "test_pause_ms": idle_ms,
            "total_ms": duration,
        }
        if error is not None:
            taken = job.ask(f"Erreur USB ({error}). Attendez la fin de tous les déclenchements. Exactement {expected} photo(s) RAW enregistrées sur la carte ?") if discovery else None
            job.log(f"Operator observed photos after USB error: {taken}. Method is unreliable and will not be selected.")
            raise RuntimeError(f"USB method returned an error: {error}")
        if discovery and not job.ask(
            f"Attendez la fin de tous les déclenchements avant de répondre. "
            f"Exactement {expected} photo(s) RAW enregistrées sur la carte ? "
            f"Fichiers signalés par USB : {len(seen)}."
        ):
            raise RuntimeError("Operator reports missing/incorrect photos")
        if discovery:
            validated_trials.add(trial_key)
        if len(seen) != expected or not capture_confirmed:
            if not discovery:
                raise RuntimeError(f"Automatic timing unavailable: USB confirmed {len(seen)}/{expected} files")
            raise RuntimeError(f"Discovery incomplete: USB confirmed {len(seen)}/{expected}; no timing trials")
        job.log(f"TEST END {'discovery confirmed' if discovery else 'automatic timing'}: {expected} photo(s), {duration:.1f} ms (test pause excluded)")
        return returned, duration, "events", phases

    def summarize_samples(spec, frames, samples):
        phases = {key: statistics.median(s[3][key] for s in samples) for key in samples[0][3]}
        timing_trials.append({"trigger": deepcopy(spec), "frames": frames,
                              "samples": [deepcopy(s[3]) for s in samples],
                              "median": phases, "status": "validated"})
        job.checkpoint(set_trials=set_samples, setup_trials=setup_samples)
        return phases

    valid = []
    for spec in trigger_candidates:
        if spec.get("path", "").rsplit("/", 1)[-1] == "bulb":
            job.log("SKIP single bulb: held exposure is not a validated fixed-shutter capture")
            continue
        samples = []
        try:
            job.log(f"TRIGGER TEST {spec}")
            probe(spec)  # Operator discovery is excluded from speed measurements.
            for _ in range(5):
                samples.append(probe(spec))
            summarize_samples(spec, 1, samples)
            valid.append((max(s[1] for s in samples), spec, samples))
        except (Cancelled, CameraIdleTimeout):
            raise
        except Exception as exc:
            timing_trials.append({"trigger": deepcopy(spec), "frames": 1, "status": "rejected", "reason": str(exc)})
            job.log(f"TRIGGER rejected: {exc}")
    if not valid:
        raise RuntimeError("No validated single trigger")
    duration, trigger, samples = min(valid, key=lambda v: v[0])
    commands["trigger_single"] = trigger
    measurements["trigger_single_duration_ms"] = statistics.median(
        s[3]["trigger_call_ms"] + s[3]["frame_wait_ms"] + s[3]["release_ms"] + s[3]["post_release_wait_ms"] for s in samples)
    measurements["settle_idle_ms"] = statistics.median(s[3]["settle_ms"] for s in samples)
    measurements.setdefault("set_capturemode_ms", 0)
    measurements["bracket_press_latency_ms"] = 0
    measurements["bracket_release_ms"] = 0
    # Zero means no physical-latency correction, NOT a measured zero latency.
    measurements["trigger_single_latency_ms"] = 0
    warnings.append("Physical shutter-start latency is unmeasured; no timing correction applied")
    if any(s[2] == "operator" for s in samples):
        trigger["completion"] = "operator_validated_delay"
        trigger["wait_ms"] = max(s[1] for s in samples)
        warnings.append("Single capture completion requires conservative delay (operator validated)")
    model_key = f"{entry['manufacturer']} {entry['model']}"
    slug = re.sub(r"[^a-z0-9]+", "_", model_key.casefold()).strip("_")[:80]
    slug += "_" + hashlib.sha256(model_key.encode()).hexdigest()[:8]
    profile = {"schema_version": 1, "config_type": "camera_profile", "backend": f"profile-{slug}",
               "manufacturer": entry["manufacturer"], "model": entry["model"],
               "characterized_at": datetime.now(timezone.utc).isoformat(),
               "strategy": "sequential", "commands": commands, "warnings": warnings,
               "settle_idle_s": 0.0, "test_pause_s": 2.0,
               "planning_timing": {"single_ms": duration + measurements["set_shutter_ms"], "single_atomic_ms": duration},
               "brackets": {}}

    # Brackets are accepted only with an understood 1-EV mode and a complete
    # nine-view benchmark. Other representations remain explicitly unsupported.
    bracket_candidates = {}
    excluded_bracket_commands = {}
    ordered_modes = {}
    if mode:
        for value in mode["choices"]:
            match = re.fullmatch(r"(?:Continuous Bracket 1(?:\.0)? EV|Bracketing C 1(?:\.0)? Steps) (\d+) (?:Img\.|Pictures)", str(value))
            if not match:
                continue
            n = int(match[1])
            if n not in (3, 5, 7, 9):
                continue
            ordered_modes.setdefault(n, value)
        for n, value in sorted(ordered_modes.items()):
            for trigger_spec in trigger_candidates:
                command_id = json.dumps(trigger_spec, sort_keys=True)
                if command_id in excluded_bracket_commands:
                    job.log(f"SKIP BRACKET {n}: {trigger_spec}; previously rejected: {excluded_bracket_commands[command_id]}")
                    continue
                try:
                    times = []
                    candidate_setups = []
                    for repetition in range(6):
                        job.check()
                        begin = time.monotonic()
                        prepare_photo(camera, commands, "1/500", value)
                        preparation_ms = (time.monotonic()-begin)*1000
                        measured = probe(trigger_spec, n, 0.1)
                        if repetition:
                            candidate_setups.append(preparation_ms)
                            times.append((preparation_ms + measured[1], measured))
                    spec = {"step_ev": 1, "mode": value, "total_ms": max(t[0] for t in times),
                            "atomic_ms": statistics.median(t[1][1] for t in times),
                            "trigger": deepcopy(trigger_spec)}
                    spec["measured_phases"] = summarize_samples(trigger_spec, n, [t[1] for t in times])
                    if any(t[1][2] == "operator" for t in times):
                        spec["trigger"].update(completion="operator_validated_delay", wait_ms=max(t[1][1] for t in times))
                    spec["peak_capture_ms"] = max(t[1][1] for t in times)
                    bracket_candidates.setdefault(command_id, {})[str(n)] = {
                        "spec": spec, "setup_samples": candidate_setups}

                except (Cancelled, CameraIdleTimeout):
                    raise
                except Exception as exc:
                    excluded_bracket_commands[command_id] = str(exc)
                    timing_trials.append({"trigger": deepcopy(trigger_spec), "frames": n, "status": "rejected", "reason": str(exc)})
                    job.log(f"BRACKET {n} {trigger_spec} rejected: {exc}")
                finally:
                    write_checked(camera, commands["capture_mode"]["path"], commands["capture_mode"]["value"])
    selected = choose_common_bracket_command(bracket_candidates, excluded_bracket_commands, ordered_modes)
    if selected is not None:
        for size, item in bracket_candidates[selected].items():
            profile["brackets"][size] = item["spec"]
            setup_samples[size] = item["setup_samples"]
        profile["bracket_command"] = deepcopy(next(iter(profile["brackets"].values()))["trigger"])
        job.log(f"COMMON BRACKET COMMAND: {profile['bracket_command']}")
    profile["bracket_selection"] = {"excluded": excluded_bracket_commands,
                                    "criterion": "lowest sum of peak capture durations, preparation excluded",
                                    "required_sizes": sorted(ordered_modes)}
    # Compare an actual nine-view sequence against optimized brackets covering
    # those same exposures; the planner can combine smaller validated groups.
    from math import log2
    test_speeds = []
    for ev in range(-4, 5):
        target = (1/500) * 2**ev
        closest = min(speeds, key=lambda v: abs(log2(_parse_speed(v)/target)))
        if abs(log2(_parse_speed(closest)/target)) > .12:
            raise RuntimeError("Nine-view comparison range unavailable at 1 EV")
        test_speeds.append(closest)
    # The old comparison shot another 45 singles and 45 bracketed frames.
    # Selection now uses the existing measurements; only sustained validation
    # takes more photographs. Do not label estimated costs as measured times.
    profile["benchmark"] = {"iso": 100, "step_ev": 1, "repetitions": 5,
                            "speeds": test_speeds, "comparison_source": "operational budget model"}
    if not profile["brackets"]:
        warnings.append("No common bracket command validated across discovered sizes; sequential only")
    measurements["bracket_atomic_ms_by_frames"] = {size: spec["atomic_ms"] for size, spec in profile["brackets"].items()}
    for spec in profile["brackets"].values():
        phases = spec["measured_phases"]
        measurements["bracket_release_ms"] = max(measurements["bracket_release_ms"], phases["release_ms"])
        measurements["settle_idle_ms"] = max(measurements["settle_idle_ms"], phases["settle_ms"])
    from math import ceil
    def conservative(value):
        return int(ceil(value / 50.0) * 50)
    raw_measurements = deepcopy(measurements)
    profile["raw_timings"] = deepcopy({"planning_timing": profile["planning_timing"], "brackets": profile["brackets"], "benchmark": profile["benchmark"]})
    for key, value in measurements.items():
        measurements[key] = {k: conservative(v) for k, v in value.items()} if isinstance(value, dict) else conservative(value)
    for key in profile["planning_timing"]:
        profile["planning_timing"][key] = conservative(profile["planning_timing"][key])
    for spec in profile["brackets"].values():
        for key in ("atomic_ms", "total_ms"):
            spec[key] = conservative(spec[key])
    for key in ("sequential_ms", "bracket_ms"):
        if key in profile["benchmark"]:
            profile["benchmark"][key] = conservative(profile["benchmark"][key])
    profile["timing_rounding_ms"] = 50
    timing = {"schema_version": 1, "config_type": "camera_timing", "backend": profile["backend"],
              "manufacturer": entry["manufacturer"], "model": entry["model"], "timing": measurements,
              "raw_timing": raw_measurements, "timing_rounding_ms": 50,
              "timing_trials": timing_trials,
              "measurement_status": {
                  "set_iso_ms": "measured",
                  "set_shutter_ms": "measured",
                  "set_capturemode_ms": "measured" if mode else "unavailable",
                  "trigger_single_duration_ms": "measured",
                  "trigger_single_latency_ms": "unmeasured",
                  "bracket_press_latency_ms": "unmeasured" if profile["brackets"] else "not_applicable",
                  "bracket_release_ms": "measured" if profile["brackets"] else "not_applicable",
                  "settle_idle_ms": "not_applicable",
                  "bracket_atomic_ms_by_frames": "measured" if profile["brackets"] else "not_applicable",
              },
              "timing_semantics": {
                  "set_iso_ms": "USB write duration; excludes readback validation",
                  "set_shutter_ms": "USB write duration; excludes readback validation",
                  "set_capturemode_ms": "USB write duration; zero if unavailable",
                  "trigger_single_latency_ms": "Physical exposure-start latency unmeasured; zero disables compensation",
                  "bracket_press_latency_ms": "Physical bracket-start latency unmeasured; zero disables compensation",
                  "trigger_single_duration_ms": "Trigger call + frame waits + optional release; excludes test pause",
                  "bracket_release_ms": "Maximum median release-call duration across selected brackets; zero if none",
                  "settle_idle_ms": "Zero: no additional runtime stabilization imposed; test pause is excluded",
                  "bracket_atomic_ms_by_frames": "Capture block INCLUDING release and file confirmation, EXCLUDING test pause",
              },
              "physical_latency_measured": False}
    job.checkpoint(profile_draft=profile, timing_draft=timing,
                   set_trials=set_samples, setup_trials=setup_samples)
    qualify_operational_contract(camera, profile, timing, set_samples, setup_samples, job)
    job.checkpoint()
    job.log(f"RESULT {profile['strategy']}: {profile['benchmark']}")
    return validate_profile(profile), timing


class QualificationOverrun(RuntimeError):
    def __init__(self, field, observed_ms, budget):
        self.field, self.observed_ms, self.budget = field, observed_ms, budget
        super().__init__(f"{field}: {observed_ms:.1f} ms exceeds {budget} ms")


def check_qualification_margins(maxima, contract, block):
    from backend.camera_timing_contract import budget_ms
    for field, observed in maxima.items():
        owner = contract if field == "iso_ms" else block
        required = budget_ms([observed])
        if required > owner[field]:
            error = QualificationOverrun(field, observed, owner[field])
            error.args = (f"Insufficient margin for {field}: observed {observed:.1f} ms, "
                          f"budget {owner[field]} ms, required {required} ms",)
            raise error


def refine_qualification(run, contract, block, job):
    """Retry a complete block only after a successfully completed operation.

    USB exceptions, missing files, cancellation and readback failures propagate.
    Budgets are revised until all five repetitions pass in one run or the
    operator cancels. USB failures are never retried here.
    """
    from backend.camera_timing_contract import budget_ms
    overruns = {"iso_ms": 0, "setup_ms": 0, "duration_ms": 0}
    adjustments = []
    attempt = 0
    while True:
        job.check()
        attempt += 1
        job.log(f"QUALIFICATION budget validation: attempt {attempt}; retry until validated or cancelled")
        try:
            result = run()
        except QualificationOverrun as exc:
            if exc.field not in overruns:
                raise ValueError(f"Unknown qualification budget: {exc.field}") from exc
            overruns[exc.field] += 1
            owner = contract if exc.field == "iso_ms" else block
            previous = owner[exc.field]
            owner[exc.field] = max(previous, budget_ms([exc.observed_ms]))
            adjustments.append({"attempt": attempt, "field": exc.field,
                                "field_revision": overruns[exc.field],
                                "observed_ms": exc.observed_ms,
                                "previous_ms": previous, "revised_ms": owner[exc.field]})
            job.log(f"BUDGET REVISED {exc.field}: {previous} -> {owner[exc.field]} ms "
                    f"(observed {exc.observed_ms:.1f} ms, revision "
                    f"{overruns[exc.field]}); restarting all five repetitions")
        else:
            result.update(attempts=attempt, budget_adjustments=adjustments)
            return result


def qualify_operational_contract(camera, profile, timing, set_samples, setup_samples, job):
    """Publish budgets only after executing the actual protocol without test pauses.

    File confirmation remains our conservative completion criterion. It is not
    presented as a measured minimum USB-ready delay or physical shutter latency.
    """
    from backend.camera_timing_contract import POLICY, budget_ms, photo_budget_ms
    from plugins.camera.base import _parse_speed
    trials = timing["timing_trials"]
    def selected_trial(frames, trigger):
        return next(t for t in trials if t["status"] == "validated" and
                    t["frames"] == frames and t["trigger"] == trigger)
    def block(frames, trigger):
        trial = selected_trial(frames, trigger)
        return {"setup_ms": budget_ms(setup_samples[str(frames)]),
                "duration_ms": budget_ms(s["total_ms"] for s in trial["samples"]),
                "reference_exposure_s": .002 * sum(2**ev for ev in range(-(frames//2), frames//2+1)),
                "completion": "file_confirmation_and_release"}
    contract = {"version": 2, "policy": deepcopy(POLICY),
                "iso_ms": budget_ms(set_samples["iso"]),
                "single": block(1, profile["commands"]["trigger_single"]),
                "brackets": {n: block(int(n), spec["trigger"]) for n, spec in profile["brackets"].items()},
                "physical_latency": {"status": "unmeasured", "compensation_ms": 0},
                "sustained": {"status": "pending"}}
    job.checkpoint(qualification_contract=contract)
    # Do not use the offline planner until this check completes.
    plugin = ProfilePlugin(camera, job.log, profile=profile)
    plugin.profile["timing_contract"] = contract
    job.log("QUALIFICATION: automatic execution with guarded budgets; NO 2-second test pauses")
    runs = []
    for size, capture_block in {"1": contract["single"], **contract["brackets"]}.items():
        n = int(size)
        spec = profile["brackets"].get(size)
        def run_block():
            started = time.monotonic()
            maximum_actual = 0
            maxima = {"iso_ms": 0.0, "setup_ms": 0.0, "duration_ms": 0.0}
            for repetition in range(5):
                # Exercise the next SET immediately at the reserved boundary, using
                # the same checked write + capture implementation as the runtime.
                job.log(f"QUALIFICATION {n} photo(s): repetition {repetition + 1}/5")
                centres = profile["benchmark"]["speeds"] if n == 1 else ["1/500"]
                for centre in centres:
                    job.check()
                    views_s = [_parse_speed(centre) * 2**ev for ev in range(-(n//2), n//2+1)]
                    operations = [("capture_setup", {"shutter": centre, "frames": n}, capture_block["setup_ms"])]
                    operations.insert(0, ("iso", "100", contract["iso_ms"]))
                    for parameter, value, budget in operations:
                        begin = time.monotonic()
                        plugin.set_parameter(parameter, value)
                        elapsed = (time.monotonic() - begin) * 1000
                        field = "iso_ms" if parameter == "iso" else "setup_ms"
                        maxima[field] = max(maxima[field], elapsed)
                        if elapsed > budget:
                            raise QualificationOverrun(field, elapsed, budget)
                        time.sleep(max(0, (budget - elapsed) / 1000))
                    budget = photo_budget_ms(capture_block, sum(views_s))
                    observation_s = max(15.0 + sum(views_s), budget / 1000 + 5)
                    job.log(f"QUALIFICATION PHOTO: frames={n}, shutter={centre}, budget={budget} ms, "
                            f"observation_limit={observation_s:.3f} s")
                    begin = time.monotonic()
                    plugin.execute_photo({"shutter": centre, "frames": n,
                                          "physical_views": [str(v) for v in views_s],
                                          "duration_ms": budget, "timing_contract_version": 2},
                                     observation_timeout_s=observation_s,
                                     check=job.check)
                    elapsed = (time.monotonic() - begin) * 1000
                    maximum_actual = max(maximum_actual, elapsed)
                    excess_exposure_ms = max(0, sum(views_s) - capture_block["reference_exposure_s"]) * 1000
                    maxima["duration_ms"] = max(maxima["duration_ms"], max(0, elapsed - excess_exposure_ms))
                    if elapsed > budget:
                        raise QualificationOverrun("duration_ms", elapsed, budget)
                    time.sleep(max(0, (budget - elapsed) / 1000))
            check_qualification_margins(maxima, contract, capture_block)
            return {"frames": n, "repetitions": 5, "maximum_photo_ms": maximum_actual,
                    "validated_maxima_ms": maxima,
                    "elapsed_ms": (time.monotonic()-started)*1000}
        result = refine_qualification(run_block, contract, capture_block, job)
        runs.append(result)
        job.checkpoint(qualification_runs=runs)
        job.log(f"QUALIFICATION {n} photo(s): passed after {result['attempts']} attempt(s)")
    contract["sustained"] = {"status": "validated", "runs": runs,
                             "scope": "single RIG, listed exposures, five repetitions; no multi-RIG qualification"}
    profile["timing_contract"] = contract
    profile["timing_policy"] = deepcopy(POLICY)
    timing["timing_contract"] = deepcopy(contract)
    timing["timing_policy"] = deepcopy(POLICY)
    timing["set_trials"] = deepcopy(set_samples)
    timing["setup_trials"] = deepcopy(setup_samples)
    values = timing["timing"]
    values["set_iso_ms"] = contract["iso_ms"]
    values["set_shutter_ms"] = budget_ms(set_samples["shutter"])
    values["set_capturemode_ms"] = 0
    values["trigger_single_duration_ms"] = contract["single"]["duration_ms"]
    values["bracket_atomic_ms_by_frames"] = {n: b["duration_ms"] for n, b in contract["brackets"].items()}
    release_samples = [sample["release_ms"] for n, spec in profile["brackets"].items()
                       for sample in selected_trial(int(n), spec["trigger"])["samples"]]
    values["bracket_release_ms"] = budget_ms(release_samples) if any(release_samples) else 0
    timing["measurement_status"]["set_capturemode_ms"] = "included_in_setup_not_separately_timed"
    timing["timing_semantics"].update({
        "set_iso_ms": "Guarded maximum checked ISO write, including readback",
        "set_shutter_ms": "Guarded maximum checked shutter write, including readback",
        "set_capturemode_ms": "Not separately timed; transitions included in timing_contract setup budgets",
        "trigger_single_duration_ms": "Guarded maximum trigger + file confirmation + release; excludes test pause",
    })
    profile["planning_timing"] = {"single_ms": contract["single"]["setup_ms"] + contract["single"]["duration_ms"],
                                   "single_atomic_ms": contract["single"]["duration_ms"]}
    for n, b in contract["brackets"].items():
        profile["brackets"][n].update(atomic_ms=b["duration_ms"], total_ms=b["setup_ms"] + b["duration_ms"])
    from types import SimpleNamespace
    exposures = [{"shutter": speed, "iso": 100} for speed in profile["benchmark"]["speeds"]]
    estimated = ProfilePlugin(None, profile=profile).prepare_capture(SimpleNamespace(exposure_plan=exposures))
    profile["benchmark"]["guarded_optimized_ms"] = round(estimated.estimated_total_s * 1000)
    profile["benchmark"]["guarded_sequential_ms"] = sum(
        contract["iso_ms"] + contract["single"]["setup_ms"] +
        photo_budget_ms(contract["single"], _parse_speed(v["shutter"])) for v in exposures)
    profile["strategy"] = "bracket" if any(o["action"] == "bracket_press" for o in estimated.token[1]) else "sequential"
    # Raw estimates remain diagnostics. Runtime uses the explicit contract only.
    job.log(f"BUDGET POLICY: maximum observed + 10% + 50 ms, rounded upwards to 50 ms")


JOB = CharacterizationJob()
