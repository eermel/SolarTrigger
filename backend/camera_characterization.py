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
from plugins.camera.profile import (
    write_widget,
    widget,
    ProfilePlugin,
    wait_camera_idle,
    CameraIdleTimeout,
    write_checked,
    prepare_photo,
    prime_single_config,
    write_single_config,
)

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

    def start(self, entry, root=ROOT, *, replace_existing=False):
        with self.lock:
            if self.running:
                raise RuntimeError("A characterization is already running")
            self.running, self.cancelled = True, False
            self.question = self.result = None
            self.logs.clear()
            self.job_id = uuid.uuid4().hex
            threading.Thread(
                target=self._run,
                args=(deepcopy(entry), Path(root), bool(replace_existing)),
                daemon=True,
            ).start()

    def _run(self, entry, root, replace_existing=False):
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
            summary["files"] = publish(
                profile,
                timing,
                root,
                replace_existing=replace_existing,
            )
            self.log(
                f"{'Profile replaced' if replace_existing else 'Profile installed'}: "
                f"{profile['backend']} ({profile['strategy']})"
            )
        except Exception as exc:
            summary["status"] = "FAILED"
            summary["files"] = []
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
            exact_name = node.get_name()
            result.append({"path": path, "name": exact_name.lower(),
                           "config_name": exact_name,
                           "value": node.get_value(), "choices": choices,
                           "readonly": bool(node.get_readonly())})
    walk(camera.get_config())
    return result


def _persistent_profile_document(profile):
    """Return the lean runtime profile written to configs/camera_profiles."""
    contract = profile.get("timing_contract")
    if not (
        isinstance(contract, dict)
        and contract.get("version") == 3
    ):
        return deepcopy(profile)

    result = {}
    for key in (
        "schema_version",
        "config_type",
        "backend",
        "manufacturer",
        "model",
        "characterized_at",
        "strategy",
        "capabilities",
        "commands",
        "warnings",
        "capture_timeout_s",
        "timing_contract",
        "selection",
    ):
        if key in profile:
            result[key] = deepcopy(profile[key])

    result["brackets"] = {}
    for size, spec in profile.get("brackets", {}).items():
        result["brackets"][str(size)] = {
            "step_ev": spec["step_ev"],
            "mode": spec["mode"],
            "trigger": deepcopy(spec["trigger"]),
            "shutter_requires_single_mode": bool(
                spec.get("shutter_requires_single_mode", True)
            ),
        }

    return validate_profile(result)


def _persistent_timing_document(timing):
    """Return the final non-debug timing JSON.

    Raw samples, medians, qualification traces and test pauses remain only in the
    characterization measurement checkpoint.
    """
    contract = timing.get("timing_contract")
    if not (
        isinstance(contract, dict)
        and contract.get("version") == 3
    ):
        return deepcopy(timing)

    return {
        "schema_version": 2,
        "config_type": "camera_timing",
        "backend": timing["backend"],
        "manufacturer": timing["manufacturer"],
        "model": timing["model"],
        "timing_contract": deepcopy(contract),
    }


def publish(profile, timing, root, *, replace_existing=False):
    """Publish lean runtime JSON safely.

    Initial characterization remains create-only.
    Re-characterization leaves the current profile/timing untouched during all
    camera tests, then replaces the pair only after the new documents have been
    completely generated and validated.
    """
    stored_profile = _persistent_profile_document(profile)
    stored_timing = _persistent_timing_document(timing)

    validate_profile(stored_profile)

    for key in ("manufacturer", "model", "backend"):
        if stored_timing.get(key) != stored_profile[key]:
            raise ValueError(
                f"Timing/profile identity mismatch: {key}"
            )

    slug = stored_profile["backend"][8:]
    files = [
        (
            root / "configs/camera_timing" / f"{slug}.json",
            stored_timing,
        ),
        (
            root / "configs/camera_profiles" / f"{slug}.json",
            stored_profile,
        ),
    ]

    existing = [path.exists() for path, _ in files]
    if any(existing) and not replace_existing:
        raise RuntimeError(
            "Characterization files already exist; no overwrite performed"
        )
    if replace_existing and any(existing) and not all(existing):
        raise RuntimeError(
            "Existing characterization is incomplete; refusing replacement"
        )

    import os
    import tempfile

    prepared = []
    backups = {}

    try:
        # Build and validate both replacement files before touching active data.
        for path, document in files:
            path.parent.mkdir(parents=True, exist_ok=True)
            if (
                path.parent.resolve()
                != root.resolve() / path.parent.relative_to(root)
            ):
                raise ValueError(
                    "Camera configuration directories must not be symlinks"
                )

            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=path.parent,
                suffix=".tmp",
                delete=False,
            ) as handle:
                temp = Path(handle.name)
                json.dump(
                    document,
                    handle,
                    indent=2,
                    ensure_ascii=False,
                )
                handle.flush()
                os.fsync(handle.fileno())

            if document.get("config_type") == "camera_timing":
                from backend.camera_timing import load_camera_timing_profile
                load_camera_timing_profile(temp)

            prepared.append((path, temp))

        if replace_existing:
            for path, _temp in prepared:
                backups[path] = path.read_bytes() if path.exists() else None

        installed = []
        try:
            for path, temp in prepared:
                if replace_existing:
                    os.replace(temp, path)
                else:
                    os.link(temp, path)
                    temp.unlink(missing_ok=True)
                installed.append(path)
        except Exception:
            if replace_existing:
                for path, _temp in prepared:
                    previous = backups.get(path)
                    if previous is None:
                        path.unlink(missing_ok=True)
                        continue
                    with tempfile.NamedTemporaryFile(
                        mode="wb",
                        dir=path.parent,
                        suffix=".rollback",
                        delete=False,
                    ) as handle:
                        rollback = Path(handle.name)
                        handle.write(previous)
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.replace(rollback, path)
            else:
                for path in installed:
                    path.unlink(missing_ok=True)
            raise

        return [
            str(path.relative_to(root))
            for path, _temp in prepared
        ]

    finally:
        for _path, temp in prepared:
            temp.unlink(missing_ok=True)


def choose_common_bracket_command(candidates, excluded, sizes):
    """A single exact command must pass every discovered size; no mixing."""
    required = {str(n) for n in sizes}
    eligible = {key: trials for key, trials in candidates.items()
                if key not in excluded and required and set(trials) == required}
    if not eligible:
        return None
    return min(
        eligible,
        key=lambda key: (
            # Synchronization criterion: minimize the worst measured time from
            # preparation start to the first camera file notification.
            max(
                item["spec"].get(
                    "peak_prepare_to_first_file_ms",
                    item["spec"]["peak_capture_ms"],
                )
                for item in eligible[key].values()
            ),
            max(item["spec"]["peak_capture_ms"] for item in eligible[key].values()),
            sum(item["spec"]["peak_capture_ms"] for item in eligible[key].values()),
            key,
        ),
    )


def _capture_validation_state(expected, observed, error):
    """Classify one capture from automatic USB evidence only.

    ``confirmed`` means exactly the expected number of FILE_ADDED events arrived
    and the trigger path returned without error. ``runtime_error`` means all
    expected files were accounted for but the command itself failed.
    ``incomplete`` means the automatic count is wrong and the capture is rejected
    without asking the operator.
    """
    if observed == expected:
        return "confirmed" if error is None else "runtime_error"
    return "incomplete"


def _select_bracket_candidate(entries):
    """Choose one reliable capture recipe for one bracket size only.

    Selection is based on complete operational duration, not on FILE_ADDED
    timing. FILE_ADDED is USB evidence that a frame exists; it is not an
    authoritative physical exposure-start timestamp.

    Among reliable exact-N/N candidates:
      1. lowest worst complete capture duration;
      2. lowest median complete capture duration;
      3. lowest prepare-to-first-file value only as a final tie-breaker;
      4. deterministic command id.

    Different bracket sizes may deliberately select different trigger
    primitives when that gives the shortest safe operational path.
    """
    reliable = [entry for entry in entries if entry["evidence"].reliable]
    if not reliable:
        return None
    return min(
        reliable,
        key=lambda entry: (
            entry["spec"]["peak_capture_ms"],
            entry["evidence"].median_ms,
            entry["spec"]["peak_prepare_to_first_file_ms"],
            entry["command_id"],
        ),
    )


def characterize(camera, entry, job):
    """Discover commands and build the simplified timing contract v3.

    Runtime budgets are deliberately reduced to four values:
      * one maximum SET reservation shared by every runtime SET;
      * one fixed overhead for a single PHOTO;
      * one fixed bracket overhead;
      * one per-gap bracket inter-image overhead.

    Raw observations stay in the characterization checkpoint and in this
    function's returned diagnostic object. publish() strips them from the
    persistent runtime JSON.
    """
    from backend.camera_timing_contract import (
        SAFETY_POLICY,
        budget_ms,
        bracket_photo_duration_ms,
        derive_bracket_components,
        single_photo_duration_ms,
    )
    from plugins.camera.base import _parse_speed
    from types import SimpleNamespace

    commands = {}
    warnings = []
    timing_trials = []
    set_samples = {}

    job.checkpoint(
        manufacturer=entry["manufacturer"],
        model=entry["model"],
        timing_trials=timing_trials,
        commands=commands,
    )

    initial = enumerate_widgets(camera)
    for item in initial:
        job.log(
            f"DISCOVER {item['path']} "
            f"readonly={item['readonly']} "
            f"choices={item['choices']}"
        )

    selection_evidence = {}

    def find_setting(key, names, accept, critical=True,
                     operator_instruction=None, require_set=False):
        """Qualify every safe candidate; select reliability first, speed second."""
        from backend.camera_candidate_optimizer import (
            CandidateEvidence, compact_selection, select_best,
        )
        errors, evidence, candidate_by_id = [], [], {}

        def read_value(path):
            _, node = widget(camera, path)
            return node.get_value()

        def direct_spec(candidate):
            return {
                "path": candidate["path"],
                "name": candidate["config_name"],
                "writer": "single_config",
            }

        def write_and_confirm(candidate, node, value):
            # Timed portion is SET-only. Readback happens afterwards and is
            # characterization evidence, never part of the runtime path.
            started = time.monotonic()
            write_single_config(camera, direct_spec(candidate), node, value)
            elapsed_ms = (time.monotonic() - started) * 1000.0
            deadline = time.monotonic() + 5.0
            for attempt in range(20):
                job.check()
                actual = read_value(candidate["path"])
                if str(actual) == str(value):
                    return elapsed_ms
                if time.monotonic() >= deadline or attempt == 19:
                    raise RuntimeError(
                        f"readback mismatch: requested={value!r}, actual={actual!r}"
                    )
                time.sleep(0.25)
            raise AssertionError("unreachable")

        for operator_pass in range(2):
            candidates = [item for item in enumerate_widgets(camera)
                          if item["name"] in names]
            for candidate in candidates:
                path = candidate["path"]
                try:
                    original = read_value(path)
                except Exception as exc:
                    errors.append(f"GET {path}: {exc}")
                    continue
                values = list(candidate["choices"] or [original])
                if key == "capture_target":
                    values.sort(key=lambda v: str(v).casefold() != "card+sdram")
                for target in [v for v in values if accept(str(v))]:
                    cid = f"{path}={target!r}"
                    if cid in candidate_by_id:
                        continue
                    ev = CandidateEvidence(cid, {"path": path, "value": target}, 5)
                    evidence.append(ev)
                    candidate_by_id[cid] = (candidate, target, ev)
                    if candidate["readonly"]:
                        if require_set:
                            ev.failures.append("widget is readonly")
                            continue
                        ev.functional_ok = True
                        ev.expected_trials = 1
                        ev.durations_ms.append(0.0)
                        job.log(f"CANDIDATE {key}: {cid} qualified GET-only")
                        continue
                    # Qualify the exact production primitive:
                    # get_single_config is done once before timing, then each
                    # trial is one set_single_config followed by an untimed
                    # readback. New runtime profiles therefore never need a
                    # configuration GET to perform a SET.
                    try:
                        node = prime_single_config(camera, direct_spec(candidate))
                    except Exception as exc:
                        ev.failures.append(f"direct writer prime: {exc}")
                        errors.append(f"DIRECT SET {cid}: {exc}")
                        continue
                    for trial in range(5):
                        try:
                            ev.durations_ms.append(
                                write_and_confirm(candidate, node, target)
                            )
                        except Exception as exc:
                            ev.failures.append(f"trial {trial+1}: {exc}")
                            errors.append(f"SET {cid} trial {trial+1}: {exc}")
                            break
                    ev.functional_ok = (len(ev.durations_ms) == 5
                                        and str(read_value(path)) == str(target))
                    job.log(f"CANDIDATE {key}: {cid} reliable={ev.reliable} "
                            f"trials={len(ev.durations_ms)}/5 "
                            f"peak_ms={ev.peak_ms if ev.durations_ms else None}")
            writable = []
            for ev in evidence:
                candidate = candidate_by_id[ev.candidate_id][0]
                if ev.reliable and not candidate["readonly"]:
                    writable.append(ev)
            selectable = writable or ([ev for ev in evidence if ev.reliable]
                                      if not require_set else [])
            if selectable:
                selected = select_best(selectable)
                candidate, target, _ = candidate_by_id[selected.candidate_id]
                commands[key] = {
                    "path": candidate["path"],
                    "name": candidate["config_name"],
                    "value": target,
                    "get": True,
                    "set": not candidate["readonly"],
                }
                if not candidate["readonly"]:
                    commands[key]["writer"] = "single_config"
                selection_evidence[key] = compact_selection(key, evidence, selected)
                job.log(f"SELECT {key}: {selected.candidate_id}")
                return candidate
            if (operator_pass == 0 and candidates and critical
                    and operator_instruction):
                if not job.ask(operator_instruction):
                    raise RuntimeError(
                        f"Operator refused required physical setting: {key}"
                    )
                evidence.clear()
                candidate_by_id.clear()
                continue
            break
        if critical:
            raise RuntimeError(
                f"Critical function unavailable: {key}; "
                + ("; ".join(errors) or "no qualified candidate")
            )
        warnings.append(f"{key}: unavailable")
        selection_evidence[key] = {
            "action": key,
            "policy": "correctness_then_reliability_then_peak_then_median",
            "candidate_count": len(evidence),
            "qualified_count": sum(1 for ev in evidence if ev.reliable),
            "selected": None,
        }
        return None

    model_for_operator = str(entry.get("model") or "appareil photo")
    model_for_operator = re.sub(
        r"\s*\(PC Control\)\s*$", "", model_for_operator
    ).replace("Alpha-A", "A")

    find_setting(
        "manual_mode",
        ("expprogram", "autoexposuremode", "exposuremode"),
        lambda value: value.casefold() in ("m", "manual"),
        operator_instruction=(
            f"Mettre le {model_for_operator} en mode manuel (M), puis confirmer."
        ),
    )
    find_setting(
        "capture_target",
        ("capturetarget",),
        lambda value: value.casefold()
        in ("card+sdram", "card", "memory card", "sd card"),
    )
    find_setting(
        "raw",
        ("imageformat", "imagequality", "imagequality2"),
        lambda value: (
            (
                "raw" in value.casefold()
                or "nef" in value.casefold()
            )
            and not any(
                token in value.casefold()
                for token in ("+", "jpeg", "jpg")
            )
        ),
    )

    if any(
        item["name"] in ("whitebalance", "whitebalance2", "wb")
        for item in enumerate_widgets(camera)
    ):
        find_setting(
            "white_balance",
            ("whitebalance", "whitebalance2", "wb"),
            lambda value: value.casefold() in (
                "daylight", "direct sunlight", "sunlight", "5200k", "5000k"
            ),
            False,
            require_set=True,
        )

    for key, names in (
        ("self_timer", ("selftimer", "selftimerdelay")),
    ):
        if any(
            item["name"] in names
            for item in enumerate_widgets(camera)
        ):
            find_setting(
                key,
                names,
                lambda value: value.casefold()
                in ("off", "0", "disabled"),
                False,
            )

    iso = find_setting(
        "iso",
        ("iso", "iso2"),
        lambda value: value == "100",
        require_set=True,
    )
    commands["iso"]["values"] = {
        str(value): value
        for value in iso["choices"]
        if str(value).isdigit()
    }

    mode = find_setting(
        "capture_mode",
        ("capturemode", "drivemode"),
        lambda value: value.casefold()
        in ("single shot", "single", "single frame"),
        False,
    )

    shutter = find_setting(
        "shutter",
        ("shutterspeed", "shutterspeed2", "exptime"),
        lambda value: value == "1/500",
        require_set=True,
    )

    speeds = {}
    for value in shutter["choices"]:
        try:
            if 0 < _parse_speed(value) <= 30:
                speeds[str(value)] = value
        except (ValueError, ZeroDivisionError):
            pass

    if "1/500" not in speeds:
        raise RuntimeError(
            "Reference shutter 1/500 is unavailable"
        )

    commands["shutter"]["values"] = speeds

    aperture_reference = None

    current_widgets = enumerate_widgets(camera)

    for item in current_widgets:
        if item["name"] in ("batterylevel", "battery"):
            commands["battery"] = {
                "path": item["path"],
                "get": True,
                "set": False,
            }
            job.log(f"Battery: {item['value']}")

    aperture_item = next(
        (
            item
            for item in current_widgets
            if item["name"] in ("f-number", "aperture")
        ),
        None,
    )

    if aperture_item is not None:
        aperture_path = aperture_item["path"]
        try:
            _, aperture_node = widget(camera, aperture_path)
            aperture_reference = aperture_node.get_value()
        except Exception as exc:
            job.log(
                f"APERTURE GET unavailable: {aperture_path}: {exc}"
            )
            aperture_reference = None

        if aperture_reference is not None:
            aperture_values = list(
                aperture_item["choices"] or [aperture_reference]
            )
            aperture_spec = {
                "path": aperture_path,
                "name": aperture_item["config_name"],
                "get": True,
                # Lens-dependent capability: never freeze SET=no into a body
                # profile merely because the characterization lens is manual.
                "set": True,
                "runtime_optional": True,
                "values": {
                    str(value): value
                    for value in aperture_values
                },
            }
            # Prove only that the direct writer can be addressed.  Do not move
            # the aperture during characterization; the mounted lens may differ
            # on eclipse day.  Runtime SET failure is warning-only.
            try:
                prime_single_config(camera, aperture_spec)
                aperture_spec["writer"] = "single_config"
                writer_note = "single_config available"
            except Exception as exc:
                writer_note = f"single_config unavailable: {exc}"

            commands["aperture"] = aperture_spec
            job.log(
                f"VALID aperture: {aperture_path} current={aperture_reference} "
                f"(GET=yes SET=runtime-optional; {writer_note})"
            )

    auxiliary_capabilities = {
        "clock": {"local_sync_supported": False, "probe_error": "not_run"},
        "shutter": {"control_detected": False, "probe_error": "not_run"},
    }
    try:
        from backend.camera_auxiliary_capabilities import (
            characterize_auxiliary_capabilities,
        )
        auxiliary_capabilities, auxiliary_commands = (
            characterize_auxiliary_capabilities(camera, job)
        )
        commands.update(auxiliary_commands)

        shutter_mode_spec = commands.get("shutter_mode")
        if (
            isinstance(shutter_mode_spec, dict)
            and shutter_mode_spec.get("set") is True
            and "value" in shutter_mode_spec
        ):
            shutter_item = next(
                (
                    item
                    for item in enumerate_widgets(camera)
                    if item["path"] == shutter_mode_spec.get("path")
                ),
                None,
            )
            if shutter_item is not None and not shutter_item["readonly"]:
                direct_spec = {
                    "path": shutter_item["path"],
                    "name": shutter_item["config_name"],
                    "writer": "single_config",
                }
                original = None
                try:
                    _, live_node = widget(camera, shutter_item["path"])
                    original = live_node.get_value()
                    target = shutter_mode_spec["value"]
                    alternate = next(
                        (
                            value
                            for value in shutter_item.get("choices", [])
                            if str(value) != str(target)
                        ),
                        None,
                    )
                    if alternate is None and str(original) != str(target):
                        alternate = original
                    if alternate is None:
                        raise RuntimeError(
                            "no alternate shutter-mode value for direct transition proof"
                        )

                    direct_node = prime_single_config(camera, direct_spec)
                    write_single_config(camera, direct_spec, direct_node, alternate)
                    _, verify_node = widget(camera, shutter_item["path"])
                    if str(verify_node.get_value()) != str(alternate):
                        raise RuntimeError(
                            "direct shutter-mode alternate readback mismatch"
                        )

                    write_single_config(camera, direct_spec, direct_node, target)
                    _, verify_node = widget(camera, shutter_item["path"])
                    if str(verify_node.get_value()) != str(target):
                        raise RuntimeError(
                            "direct shutter-mode target readback mismatch"
                        )

                    if str(original) != str(target):
                        write_single_config(
                            camera, direct_spec, direct_node, original
                        )
                        _, verify_node = widget(camera, shutter_item["path"])
                        if str(verify_node.get_value()) != str(original):
                            raise RuntimeError(
                                "direct shutter-mode restore readback mismatch"
                            )

                    shutter_mode_spec["name"] = shutter_item["config_name"]
                    shutter_mode_spec["writer"] = "single_config"
                    job.log(
                        "OPTIONAL SHUTTER: direct single_config writer proven "
                        f"at {shutter_item['path']}"
                    )
                except Exception as exc:
                    # Direct promotion is optional. Preserve the already-proven
                    # legacy preflight writer, but never leave a test value active.
                    try:
                        if original is not None:
                            _, restore_node = widget(camera, shutter_item["path"])
                            if str(restore_node.get_value()) != str(original):
                                write_checked(
                                    camera,
                                    shutter_item["path"],
                                    original,
                                )
                    except Exception as restore_exc:
                        raise RuntimeError(
                            "Cannot restore shutter mode after direct SET probe: "
                            f"{restore_exc}"
                        ) from restore_exc
                    job.log(
                        "OPTIONAL SHUTTER: direct single_config writer not proven; "
                        f"legacy preflight writer retained: {exc}"
                    )

        job.checkpoint(
            auxiliary_capabilities=auxiliary_capabilities,
            commands=commands,
        )
    except Cancelled:
        raise
    except Exception as exc:
        auxiliary_capabilities = {
            "clock": {
                "local_sync_supported": False,
                "probe_error": str(exc),
            },
            "shutter": {
                "control_detected": False,
                "probe_error": str(exc),
            },
        }
        job.log(
            f"OPTIONAL CAPABILITIES probe failed non-fatally: {exc}"
        )

    if "battery" not in commands:
        warnings.append("battery: unavailable")

    # Discover supported 1-EV native bracket modes before timing SETs so the
    # shared SET reservation covers every drive-mode value later used by reactive execution.
    ordered_modes = {}
    if mode and commands["capture_mode"].get("set") is not False:
        for value in mode["choices"]:
            match = re.fullmatch(
                r"(?:Continuous Bracket 1(?:\.0)? EV|"
                r"Bracketing C 1(?:\.0)? Steps) "
                r"(\d+) (?:Img\.|Pictures)",
                str(value),
            )
            if not match:
                continue

            frames = int(match.group(1))
            if frames not in (3, 5, 7, 9):
                continue

            ordered_modes.setdefault(
                frames,
                value,
            )

    direct_nodes = {}

    def prime_runtime_spec(spec):
        if spec.get("writer") != "single_config":
            return None
        name = spec["name"]
        if name not in direct_nodes:
            direct_nodes[name] = prime_single_config(camera, spec)
        return direct_nodes[name]

    def runtime_set(key, value):
        spec = commands[key]
        if spec.get("writer") == "single_config":
            node = prime_runtime_spec(spec)
            write_single_config(camera, spec, node, value)
        else:
            # Compatibility only for initialization-only optional commands.
            write_widget(camera, spec["path"], value)

    def characterization_read(key):
        """Fresh GET used only by characterization/preflight evidence."""
        spec = commands[key]
        _, node = widget(camera, spec["path"])
        return node.get_value()

    def converge_characterized_preflight():
        """Re-establish invariant state after opening a fresh gphoto session.

        This deliberately mirrors production preflight semantics. GETs and
        readback are allowed here because this is characterization, not timed
        execution. Writable settings use the characterized direct writer.
        """
        for key in (
            "manual_mode",
            "capture_target",
            "raw",
            "white_balance",
            "shutter_mode",
            "capture_mode",
            "self_timer",
            "time_lapse",
        ):
            spec = commands.get(key)
            if not isinstance(spec, dict) or "value" not in spec:
                continue
            target = spec["value"]
            actual = characterization_read(key)
            if str(actual) == str(target):
                continue
            if spec.get("set") is False:
                if not job.ask(
                    "Fresh-session camera preflight: "
                    f"{key} must be {target!r}, current value is {actual!r}. "
                    "Correct this setting physically on the camera, wait until "
                    "the camera is ready, then click OK. The setting will be "
                    "read again before any capture.",
                    kind="start",
                ):
                    raise Cancelled(
                        f"Fresh-session physical preflight cancelled for {key}"
                    )
                actual = characterization_read(key)
                if str(actual) != str(target):
                    raise RuntimeError(
                        f"Fresh-session invariant {key} still incorrect after "
                        f"operator confirmation: expected={target!r}, "
                        f"actual={actual!r}"
                    )
                continue
            runtime_set(key, target)
            actual = characterization_read(key)
            if str(actual) != str(target):
                raise RuntimeError(
                    f"Fresh-session invariant readback mismatch for {key}: "
                    f"expected={target!r}, actual={actual!r}"
                )

    def measure_set(key, values):
        values = list(values)
        if not values:
            raise RuntimeError(
                f"Cannot measure SET {key}: no values"
            )

        samples = []
        for _ in range(5):
            for value in values:
                job.check()
                # Production timing measures one SolarTrigger SET call only.
                # The CameraWidget was primed before this loop, so SolarTrigger
                # performs no get_config()/get_single_config() here. Any internal
                # PTP traffic performed by libgphoto2 is part of the measured SET.
                prime_runtime_spec(commands[key])
                begin = time.monotonic()
                runtime_set(key, value)
                samples.append(
                    (time.monotonic() - begin) * 1000.0
                )

        set_samples[key] = samples
        job.log(
            f"TIMING SET {key}: "
            f"max={max(samples):.1f} ms "
            f"median={statistics.median(samples):.1f} ms "
            f"({len(samples)} operations)"
        )
        return samples

    # ISO alternates only during timing, then returns to ISO100.
    iso_other = next(
        (
            value
            for key, value
            in commands["iso"]["values"].items()
            if int(key) > 100
        ),
        None,
    )
    if iso_other is None:
        raise RuntimeError(
            "Cannot measure a real ISO transition: "
            "no alternate standard ISO"
        )

    measure_set(
        "iso",
        [
            iso_other,
            commands["iso"]["values"]["100"],
        ],
    )

    shutter_other = next(
        (
            value
            for key, value in speeds.items()
            if key != "1/500"
        ),
        None,
    )
    if shutter_other is None:
        raise RuntimeError(
            "Cannot measure shutter transitions"
        )

    measure_set(
        "shutter",
        [
            shutter_other,
            speeds["1/500"],
        ],
    )

    if mode and commands["capture_mode"].get("set") is not False:
        single_mode = commands["capture_mode"]["value"]
        mode_values = [
            ordered_modes[frames]
            for frames in sorted(ordered_modes)
        ]

        if not mode_values:
            # No native bracket mode: still time the capture-mode SET used by
            # sequential plans. Prefer one real transition when available.
            alternate = next(
                (
                    value
                    for value in mode["choices"]
                    if str(value) != str(single_mode)
                ),
                None,
            )
            if alternate is not None:
                mode_values.append(alternate)

        mode_values.append(single_mode)
        measure_set(
            "capture_mode",
            mode_values,
        )
        runtime_set("capture_mode", single_mode)

    # Determine whether a shutter transition can be made while each native
    # bracket mode is already active. This decides whether runtime needs the
    # historical Single->shutter->Bracket round-trip. GET/readback is allowed
    # here because this is characterization, never timed execution.
    bracket_prepare_policy = {}
    if ordered_modes and "capture_mode" in commands:
        for frames, mode_value in sorted(ordered_modes.items()):
            requires_single = True
            try:
                runtime_set("capture_mode", mode_value)
                runtime_set("shutter", shutter_other)
                _, verify_node = widget(camera, commands["shutter"]["path"])
                first_ok = str(verify_node.get_value()) == str(shutter_other)
                runtime_set("shutter", speeds["1/500"])
                _, verify_node = widget(camera, commands["shutter"]["path"])
                restore_ok = str(verify_node.get_value()) == str(speeds["1/500"])
                requires_single = not (first_ok and restore_ok)
            except Exception as exc:
                requires_single = True
                job.log(
                    f"BRACKET PREP {frames}: in-bracket shutter SET not proven: {exc}"
                )
            finally:
                runtime_set("capture_mode", single_mode)
                runtime_set("shutter", speeds["1/500"])
            bracket_prepare_policy[frames] = requires_single
            job.log(
                f"BRACKET PREP {frames}: shutter_requires_single_mode="
                f"{requires_single}"
            )

    # Characterize the dependency matrix of every dynamic Direct-SET. Runtime
    # keeps a local known-state cache and may skip redundant SETs, so that cache
    # is safe only when characterization proves which settings survive another
    # setting's transition. On any ambiguous probe, fail conservative: mark
    # every other dynamic setting invalidated instead of assuming persistence.
    dependency_baseline = {
        "iso": commands["iso"]["values"]["100"],
        "shutter": speeds["1/500"],
    }
    dependency_alternates = {
        "iso": [iso_other],
        "shutter": [shutter_other],
    }

    if (
        "capture_mode" in commands
        and commands["capture_mode"].get("set") is not False
    ):
        dependency_baseline["capture_mode"] = single_mode
        dependency_alternates["capture_mode"] = list(dict.fromkeys(
            ordered_modes[frames]
            for frames in sorted(ordered_modes)
        ))

    def restore_dependency_baseline():
        # Mode first: some cameras accept shutter changes only in Single Shot.
        if "capture_mode" in dependency_baseline:
            runtime_set("capture_mode", dependency_baseline["capture_mode"])
        runtime_set("iso", dependency_baseline["iso"])
        runtime_set("shutter", dependency_baseline["shutter"])

    def observe_dependency_peers(source_key, peer_keys, invalidates):
        for dep_key in peer_keys:
            actual = characterization_read(dep_key)
            if str(actual) != str(dependency_baseline[dep_key]):
                invalidates.add(dep_key)

    for source_key, alternates in dependency_alternates.items():
        peer_keys = [
            key for key in dependency_baseline
            if key != source_key
        ]
        invalidates = set()
        tested = []
        try:
            for alternate in alternates:
                if alternate is None:
                    continue
                restore_dependency_baseline()

                # Prove both directions. A camera may preserve peers when
                # entering a mode but reset them when returning to the baseline.
                runtime_set(source_key, alternate)
                observe_dependency_peers(source_key, peer_keys, invalidates)
                runtime_set(source_key, dependency_baseline[source_key])
                observe_dependency_peers(source_key, peer_keys, invalidates)
                tested.append(alternate)
        except Exception as exc:
            # Unknown dependency means runtime must not trust cached peers.
            invalidates.update(peer_keys)
            warnings.append(
                f"{source_key} dependency probe inconclusive; "
                "conservative invalidation enabled"
            )
            job.log(
                f"SET DEPENDENCIES {source_key}: probe failed: {exc}; "
                f"conservative invalidates={sorted(invalidates)}"
            )
        finally:
            restore_dependency_baseline()

        commands[source_key]["invalidates"] = sorted(invalidates)
        job.log(
            f"SET DEPENDENCIES {source_key}: tested={tested}; "
            f"invalidates={commands[source_key]['invalidates']}"
        )

    all_set_samples = [
        sample
        for samples in set_samples.values()
        for sample in samples
    ]
    set_overhead_ms = budget_ms(
        all_set_samples
    )

    def timed_runtime_prepare(shutter, bracket_mode=None):
        """Measure the exact SET preamble emitted by contract-v3 runtime.

        The characterization deliberately performs all SETs even when their
        current values already match. This measures the conservative cold/cache-
        invalidated path that Trigger must reserve before the requested PHOTO
        instant.
        """
        started = time.monotonic()

        runtime_set(
            "iso",
            commands["iso"]["values"]["100"],
        )

        capture_mode_writable = (
            "capture_mode" in commands
            and commands["capture_mode"].get("set") is not False
        )

        if bracket_mode is None:
            if capture_mode_writable:
                runtime_set(
                    "capture_mode",
                    commands["capture_mode"]["value"],
                )
            runtime_set(
                "shutter",
                commands["shutter"]["values"][str(shutter)],
            )
        else:
            if not capture_mode_writable:
                raise RuntimeError(
                    "Bracket mode requires a writable capture_mode"
                )
            frames = next(
                (
                    size
                    for size, value in ordered_modes.items()
                    if str(value) == str(bracket_mode)
                ),
                None,
            )
            requires_single = bracket_prepare_policy.get(frames, True)
            if requires_single:
                runtime_set(
                    "capture_mode",
                    commands["capture_mode"]["value"],
                )
                runtime_set(
                    "shutter",
                    commands["shutter"]["values"][str(shutter)],
                )
                runtime_set("capture_mode", bracket_mode)
            else:
                runtime_set("capture_mode", bracket_mode)
                runtime_set(
                    "shutter",
                    commands["shutter"]["values"][str(shutter)],
                )

        return (time.monotonic() - started) * 1000.0

    # Probe every known capture entry point. Each eligible primitive gets one
    # fresh-session cold trial followed by five warm timing repetitions.
    # Operator input is requested only when automatic USB evidence is incomplete.
    trigger_candidates = [
        {"method": "trigger_capture"},
        {"method": "capture"},
    ]

    for item in enumerate_widgets(camera):
        if (
            item["name"] in ("capture", "bulb")
            and not item["readonly"]
        ):
            candidate = {
                "method": "widget",
                "path": item["path"],
                "name": item["config_name"],
                "writer": "single_config",
                "value": 1,
                "release": 0,
            }
            if candidate not in trigger_candidates:
                trigger_candidates.append(candidate)

    import gphoto2 as gp

    job.log(
        "TEST POLICY: bracket primitives are tested smallest-to-largest; "
        "rejected methods are pruned from larger sizes"
    )
    job.log(
        "TIMING MODEL V3: SET=max guarded command; "
        "PHOTO=exposure(s)+fixed/inter-image overheads"
    )

    validated_trials = set()

    def probe(
        spec,
        expected=1,
        exposure_s=0.002,
        ready_set=None,
    ):
        job.check()
        trial_key = (
            spec["method"],
            spec.get("path"),
            spec.get("value"),
            spec.get("release"),
            expected,
        )
        discovery = trial_key not in validated_trials

        job.log(f"TEST START: {expected} photo(s), {spec}")

        if spec.get("method") == "widget":
            prime_runtime_spec(spec)

        operation_begin = time.monotonic()

        # Runtime also starts from a clean event boundary.  This drain is part
        # of PHOTO duration because it is executed on the real timed path.
        drain_begin = operation_begin
        for _ in range(100):
            kind, _data = camera.wait_for_event(1)
            if kind == gp.GP_EVENT_TIMEOUT:
                break
        pre_trigger_drain_ms = (time.monotonic() - drain_begin) * 1000.0

        seen = set()
        error = None
        returned_ms = 0.0
        release_ms = 0.0
        first_file_ms = None
        last_file_at = None

        begin = time.monotonic()

        def remember_file(data):
            nonlocal first_file_ms, last_file_at
            now = time.monotonic()
            if first_file_ms is None:
                first_file_ms = (now - begin) * 1000.0
            key = (
                getattr(data, "folder", ""),
                getattr(data, "name", str(data)),
            )
            before = len(seen)
            seen.add(key)
            if len(seen) != before:
                last_file_at = now

        try:
            method = spec["method"]

            if method == "capture":
                file_ref = camera.capture(gp.GP_CAPTURE_IMAGE)
                if getattr(file_ref, "name", None):
                    remember_file(file_ref)

            elif method == "trigger_capture":
                camera.trigger_capture()

            else:
                node = prime_runtime_spec(spec)
                write_single_config(camera, spec, node, spec["value"])

            returned_ms = (time.monotonic() - begin) * 1000.0

            until = time.monotonic() + float(exposure_s) + 5.0
            while len(seen) < expected and time.monotonic() < until:
                job.check()
                kind, data = camera.wait_for_event(100)
                if kind == gp.GP_EVENT_FILE_ADDED:
                    remember_file(data)

        except Cancelled:
            raise
        except Exception as exc:
            error = exc
        finally:
            before_release = time.monotonic()
            if "release" in spec:
                node = prime_runtime_spec(spec)
                write_single_config(camera, spec, node, spec["release"])
                release_ms = (time.monotonic() - before_release) * 1000.0

        # Files delivered after release still belong to this PHOTO.
        post_release_start = time.monotonic()
        until = post_release_start + float(exposure_s) + 5.0
        while (
            error is None
            and len(seen) < expected
            and time.monotonic() < until
        ):
            job.check()
            kind, data = camera.wait_for_event(100)
            if kind == gp.GP_EVENT_FILE_ADDED:
                remember_file(data)

        post_release_ms = (time.monotonic() - post_release_start) * 1000.0
        file_complete_ms = (time.monotonic() - operation_begin) * 1000.0

        validation_state = _capture_validation_state(
            expected,
            len(seen),
            error,
        )

        if validation_state == "runtime_error":
            job.log(
                f"AUTO REJECT: USB reported {len(seen)}/{expected} file(s) "
                f"but the command returned an error: {error}"
            )
            raise RuntimeError(
                f"USB method returned an error after {len(seen)}/{expected} "
                f"confirmed files: {error}"
            )

        if validation_state != "confirmed":
            job.log(
                f"AUTO REJECT: USB confirmed {len(seen)}/{expected} file(s); "
                "capture count is incomplete"
            )
            if error is not None:
                failure = RuntimeError(
                    "USB method error with incomplete confirmation "
                    f"({len(seen)}/{expected}): {error}"
                )
            else:
                failure = RuntimeError(
                    "Automatic capture confirmation incomplete: "
                    f"USB confirmed {len(seen)}/{expected}"
                )
            failure.observed_frames = len(seen)
            failure.expected_frames = expected
            raise failure

        # Exact N/N proves the physical capture count.  It does NOT prove that
        # Sony is ready for the next command.  Measure the missing tail by
        # repeatedly attempting one representative Direct-SET until the body
        # accepts it. For a bracket this is deliberately the real transition
        # back to Single Shot -- the exact operation that exposed Sony -2
        # Bad parameters. Singles use a harmless same-value ISO100 SET.
        ready_origin = last_file_at or time.monotonic()
        ready_deadline = time.monotonic() + 5.0
        ready_attempts = 0
        last_ready_error = None
        while True:
            job.check()
            ready_attempts += 1
            try:
                ready_key, ready_value = ready_set or (
                    "iso",
                    commands["iso"]["values"]["100"],
                )
                runtime_set(ready_key, ready_value)
                break
            except Cancelled:
                raise
            except Exception as exc:
                last_ready_error = exc
                # A failed single-config transaction can poison the cached
                # CameraWidget. Re-prime only during characterization retry.
                name = commands[ready_key].get("name")
                if name:
                    direct_nodes.pop(name, None)
                if time.monotonic() >= ready_deadline:
                    raise RuntimeError(
                        "Camera did not return to USB SET-ready state within "
                        f"5 s after PHOTO: {last_ready_error}"
                    ) from exc
                time.sleep(0.01)

        usb_return_ms = max(
            0.0,
            (time.monotonic() - ready_origin) * 1000.0,
        )
        duration_ms = (time.monotonic() - operation_begin) * 1000.0

        if discovery:
            validated_trials.add(trial_key)

        phases = {
            "pre_trigger_drain_ms": pre_trigger_drain_ms,
            "trigger_call_ms": returned_ms,
            # FILE_ADDED is not physical shutter-open telemetry.  This value is
            # retained only as USB evidence and never used as shutter latency.
            "first_file_ms": (
                float(first_file_ms)
                if first_file_ms is not None
                else float(file_complete_ms)
            ),
            "frame_wait_ms": max(
                0.0,
                (before_release - begin) * 1000.0 - returned_ms,
            ),
            "release_ms": release_ms,
            "post_release_wait_ms": post_release_ms,
            "file_complete_ms": file_complete_ms,
            "usb_return_ms": usb_return_ms,
            "usb_ready_attempts": ready_attempts,
            "settle_ms": 0.0,
            "test_pause_ms": 0.0,
            "total_ms": duration_ms,
        }

        job.log(
            f"AUTO CONFIRM: USB reported exactly {len(seen)}/{expected} "
            f"file(s); USB SET-ready after {usb_return_ms:.1f} ms "
            f"({ready_attempts} attempt(s))"
        )
        job.log(
            f"TEST END {'discovery confirmed' if discovery else 'automatic timing'}: "
            f"{expected} photo(s), file_complete={file_complete_ms:.1f} ms, "
            f"usb_ready_total={duration_ms:.1f} ms"
        )

        return (
            returned_ms,
            duration_ms,
            "events",
            phases,
        )

    def summarize_samples(
        spec,
        frames,
        samples,
    ):
        phases = {
            key: statistics.median(
                sample[3][key]
                for sample in samples
            )
            for key in samples[0][3]
        }

        timing_trials.append(
            {
                "trigger": deepcopy(spec),
                "frames": frames,
                "samples": [
                    deepcopy(sample[3])
                    for sample in samples
                ],
                "median": phases,
                "status": "validated",
            }
        )

        job.checkpoint(
            timing_trials=timing_trials,
            set_trials=set_samples,
        )

        return phases

    # --- Session cold-start measurement --------------------------------
    # The very first physical capture of a freshly opened camera session
    # can be measurably slower than subsequent ones (observed on Nikon D850/
    # gphoto2: several seconds vs ~1 second afterwards). Because trigger
    # candidates below are tested one after another, only the FIRST
    # candidate's FIRST trial ever experiences this cold state -- and
    # select_best() ranks candidates by peak_ms, so it systematically
    # discards exactly that candidate in favour of one tested later on an
    # already-warmed-up camera. The real cost of "first shot of a live
    # session" was therefore never characterized or budgeted. When a worker
    # really starts cold (notably a temporary Validation worker, or a Trigger
    # worker created after restart), that missing budget can make the scheduler
    # skip a later command
    # (see execution_plan budget_overrun_ms handling).
    #
    # Fix: measure it once, explicitly, before any candidate competes, using
    # the first candidate as a neutral reference. This trial is excluded
    # from every candidate's own statistics (evidence/peak_ms/median_ms), so
    # it no longer biases trigger selection; its value is kept separately as
    # contract["session_first_photo_overhead_ms"].
    job.log(
        "COLD START: measuring the first physical capture of this session "
        "before any trigger candidate is scored"
    )
    cold_start_overhead_ms = None
    try:
        cold_start_reference_s = _parse_speed("1/500")

        # Reproduce the real v3 preamble before the first PHOTO.  Capability
        # and dependency probing above may have left the camera in a native
        # bracket mode.  Trigger never fires a PHOTO from that accidental
        # state: it first applies ISO / capture-mode / shutter SETs.  Without
        # this normalization the cold-start probe can fire trigger_capture()
        # while BRK5 is still active, defeating the smaller-to-larger bracket
        # pruning invariant tested below.
        cold_start_prepare_ms = timed_runtime_prepare("1/500")

        cold_start_sample = probe(
            trigger_candidates[0],
            expected=1,
            exposure_s=cold_start_reference_s,
        )

        # Use the complete PHOTO blocking interval through USB SET-ready.
        # file_complete_ms alone is insufficient: on Sony in particular the
        # body can report the last FILE_ADDED and still reject the next SET.
        # single_overhead_ms is defined with the same complete-PHOTO semantics.
        cold_start_overhead_ms = max(
            0.0,
            cold_start_sample[3]["total_ms"]
            - cold_start_reference_s * 1000.0,
        )
        job.log(
            f"COLD START measured: {cold_start_overhead_ms:.1f} ms overhead "
            f"(prepare={cold_start_prepare_ms:.1f} ms, "
            f"file_complete={cold_start_sample[3]['file_complete_ms']:.1f} ms, "
            f"usb_ready_total={cold_start_sample[3]['total_ms']:.1f} ms); "
            "excluded from candidate peak/median statistics"
        )
    except (Cancelled, CameraIdleTimeout):
        raise
    except Exception as exc:
        job.log(
            "COLD START measurement failed; no dedicated session-first-photo "
            f"floor will be recorded (falls back to single_overhead_ms): {exc}"
        )
    # ---------------------------------------------------------------------

    from backend.camera_candidate_optimizer import (
        CandidateEvidence,
        compact_selection,
        select_best,
    )
    valid_single = []
    single_evidence = []

    for spec in trigger_candidates:
        if spec.get("path", "").rsplit("/", 1)[-1] == "bulb":
            job.log(
                "SKIP single bulb: held exposure is not a validated "
                "fixed-shutter capture"
            )
            continue

        samples = []
        try:
            job.log(f"TRIGGER TEST {spec}: 5 persistent-session trials")
            for repetition in range(5):
                job.check()
                prepare_ms = timed_runtime_prepare("1/500")
                sample = probe(
                    spec,
                    expected=1,
                    exposure_s=_parse_speed("1/500"),
                )
                samples.append((*sample, prepare_ms))
                job.log(
                    f"TRIGGER PASS {repetition + 1}/5: {spec}; "
                    f"capture_ms={sample[1]:.1f}"
                )

            summarize_samples(spec, 1, samples)
            evidence = CandidateEvidence(
                candidate_id=json.dumps(spec, sort_keys=True),
                recipe=deepcopy(spec),
                expected_trials=5,
                durations_ms=[sample[1] for sample in samples],
                functional_ok=True,
            )
            single_evidence.append(evidence)
            valid_single.append((evidence, deepcopy(spec), samples))

        except (Cancelled, CameraIdleTimeout):
            raise
        except Exception as exc:
            rejected = CandidateEvidence(
                candidate_id=json.dumps(spec, sort_keys=True),
                recipe=deepcopy(spec),
                expected_trials=5,
                functional_ok=False,
            )
            rejected.failures.append(str(exc))
            single_evidence.append(rejected)
            timing_trials.append(
                {
                    "trigger": deepcopy(spec),
                    "frames": 1,
                    "status": "rejected",
                    "reason": str(exc),
                }
            )
            job.log(f"TRIGGER rejected: {exc}")

    if not valid_single:
        raise RuntimeError(
            "No validated single trigger"
        )

    selected_single = select_best(single_evidence)
    _ev, trigger_single, single_samples = next(
        item for item in valid_single
        if item[0].candidate_id == selected_single.candidate_id
    )
    commands["trigger_single"] = trigger_single
    selection_evidence["trigger_single"] = compact_selection(
        "trigger_single", single_evidence, selected_single
    )
    job.log(
        f"SELECT trigger_single: {selected_single.candidate_id}; "
        f"peak={selected_single.peak_ms:.1f} ms; "
        f"median={selected_single.median_ms:.1f} ms"
    )

    reference_single_s = _parse_speed("1/500")
    single_core_overhead_samples = [
        max(
            0.0,
            sample[3]["file_complete_ms"]
            - reference_single_s * 1000.0,
        )
        for sample in single_samples
    ]
    single_usb_return_samples = [
        sample[3]["usb_return_ms"]
        for sample in single_samples
    ]
    single_core_overhead_ms = budget_ms(single_core_overhead_samples)
    single_usb_return_ms = budget_ms(single_usb_return_samples)
    # Complete PHOTO duration through the point where the next USB SET is safe.
    single_overhead_ms = single_core_overhead_ms + single_usb_return_ms
    # Compatibility/debug name consumed by qualification evidence.
    single_overhead_samples = [
        core + usb
        for core, usb in zip(
            single_core_overhead_samples,
            single_usb_return_samples,
        )
    ]

    model_key = (
        f"{entry['manufacturer']} "
        f"{entry['model']}"
    )
    slug = re.sub(
        r"[^a-z0-9]+",
        "_",
        model_key.casefold(),
    ).strip("_")[:80]
    slug += (
        "_"
        + hashlib.sha256(
            model_key.encode()
        ).hexdigest()[:8]
    )

    profile = {
        "schema_version": 1,
        "config_type": "camera_profile",
        "backend": f"profile-{slug}",
        "manufacturer": entry["manufacturer"],
        "model": entry["model"],
        "characterized_at": (
            datetime.now(timezone.utc).isoformat()
        ),
        "strategy": "sequential",
        "capabilities": auxiliary_capabilities,
        "commands": commands,
        "warnings": warnings,
        "settle_idle_s": 0.0,
        "test_pause_s": 0.0,
        "brackets": {},
        "selection": deepcopy(selection_evidence),
    }

    # Native bracket qualification uses the same persistent gphoto session as
    # Trigger.  All advertised sizes are functionally checked, while timing is
    # identified from BRK3 and BRK7 only (five trials each) when available.
    selected_bracket_items = {}
    bracket_rejections = {str(frames): {} for frames in sorted(ordered_modes)}
    selected_candidate_ids = {}
    bracket_overhead_samples_by_frames = {}
    bracket_usb_return_samples = []
    bracket_calibration_frames = []

    # Functional discovery is per bracket size.  Timing calibration requires
    # one common trigger primitive across the two calibration sizes so that
    # trigger overhead cancels correctly when deriving the inter-frame slope.
    #
    # Preferred timing pair is BRK3 + BRK7.  If that exact pair is unavailable,
    # use the widest supported pair that has one common reliable primitive.
    # Other bracket sizes remain usable even when they need another primitive.
    bracket_candidates_by_frames = {
        frames: []
        for frames in sorted(ordered_modes)
    }

    if mode and ordered_modes:
        for trigger_spec in trigger_candidates:
            command_id = json.dumps(
                trigger_spec,
                sort_keys=True,
            )

            for frames, mode_value in sorted(
                ordered_modes.items()
            ):
                size = str(frames)
                reference_views_s = [
                    reference_single_s * (2 ** ev)
                    for ev in range(
                        -(frames // 2),
                        frames // 2 + 1,
                    )
                ]
                reference_exposure_s = sum(
                    reference_views_s
                )

                try:
                    # One functional N/N trial for this exact
                    # (bracket size, trigger primitive).
                    prepare_ms = timed_runtime_prepare(
                        "1/500",
                        mode_value,
                    )
                    sample = probe(
                        trigger_spec,
                        expected=frames,
                        exposure_s=reference_exposure_s,
                        ready_set=(
                            "capture_mode",
                            commands["capture_mode"]["value"],
                        ),
                    )
                    packed = (
                        *sample,
                        prepare_ms,
                    )

                    spec = {
                        "step_ev": 1,
                        "mode": mode_value,
                        "trigger": deepcopy(
                            trigger_spec
                        ),
                        "shutter_requires_single_mode": bool(
                            bracket_prepare_policy.get(
                                frames,
                                True,
                            )
                        ),
                        "peak_capture_ms": sample[1],
                        "peak_first_file_ms": (
                            sample[3]["first_file_ms"]
                        ),
                        "peak_prepare_to_first_file_ms": (
                            prepare_ms
                            + sample[3]["first_file_ms"]
                        ),
                    }

                    evidence = CandidateEvidence(
                        candidate_id=command_id,
                        recipe=deepcopy(trigger_spec),
                        expected_trials=1,
                        durations_ms=[sample[1]],
                        functional_ok=True,
                    )

                    bracket_candidates_by_frames[
                        frames
                    ].append(
                        {
                            "command_id": command_id,
                            "evidence": evidence,
                            "spec": spec,
                            "trigger": deepcopy(
                                trigger_spec
                            ),
                            "samples": [packed],
                            "reference_views_s":
                                reference_views_s,
                            "mode": mode_value,
                        }
                    )

                    job.log(
                        f"BRACKET FUNCTIONAL PASS "
                        f"{frames}: {trigger_spec}; "
                        f"capture_ms={sample[1]:.1f}"
                    )

                except (
                    Cancelled,
                    CameraIdleTimeout,
                ):
                    raise

                except Exception as exc:
                    rejected = CandidateEvidence(
                        candidate_id=command_id,
                        recipe=deepcopy(trigger_spec),
                        expected_trials=1,
                        functional_ok=False,
                    )
                    rejected.failures.append(
                        str(exc)
                    )

                    bracket_rejections[
                        size
                    ][command_id] = str(exc)

                    timing_trials.append(
                        {
                            "trigger": deepcopy(
                                trigger_spec
                            ),
                            "frames": frames,
                            "status": "rejected",
                            "reason": str(exc),
                        }
                    )

                    # Preserve the existing conservative pruning policy:
                    # a primitive that already fails a smaller native
                    # bracket is not exercised again at larger sizes.
                    job.log(
                        f"BRACKET PRUNE "
                        f"{trigger_spec}: failed at "
                        f"{frames} frames and will not "
                        "be retested at larger frame counts"
                    )
                    break

                finally:
                    runtime_set(
                        "capture_mode",
                        commands[
                            "capture_mode"
                        ]["value"],
                    )

        supported_sizes = [
            frames
            for frames in sorted(ordered_modes)
            if bracket_candidates_by_frames[
                frames
            ]
        ]

        def candidate_ids(frames):
            return {
                item["command_id"]
                for item
                in bracket_candidates_by_frames[
                    frames
                ]
            }

        # Find a calibration pair with one common primitive.
        candidate_pairs = []

        for index, first in enumerate(
            supported_sizes
        ):
            for second in supported_sizes[
                index + 1:
            ]:
                common = (
                    candidate_ids(first)
                    & candidate_ids(second)
                )
                if common:
                    candidate_pairs.append(
                        (first, second)
                    )

        candidate_pairs.sort(
            key=lambda pair: (
                0
                if pair == (3, 7)
                else 1,
                -(pair[1] - pair[0]),
                pair[0],
                pair[1],
            )
        )

        calibration_trigger_id = None

        if candidate_pairs:
            bracket_calibration_frames = list(
                candidate_pairs[0]
            )

            common_ids = (
                candidate_ids(
                    bracket_calibration_frames[0]
                )
                & candidate_ids(
                    bracket_calibration_frames[1]
                )
            )

            combined_candidates = []

            for command_id in sorted(common_ids):
                entries = [
                    next(
                        item
                        for item
                        in bracket_candidates_by_frames[
                            frames
                        ]
                        if item["command_id"]
                        == command_id
                    )
                    for frames
                    in bracket_calibration_frames
                ]

                evidence = CandidateEvidence(
                    candidate_id=command_id,
                    recipe=deepcopy(
                        entries[0]["trigger"]
                    ),
                    expected_trials=len(
                        entries
                    ),
                    durations_ms=[
                        item["samples"][0][1]
                        for item in entries
                    ],
                    functional_ok=True,
                )

                combined_candidates.append(
                    {
                        "command_id":
                            command_id,
                        "evidence":
                            evidence,
                        "spec": {
                            "peak_prepare_to_first_file_ms":
                                max(
                                    item[
                                        "samples"
                                    ][0][4]
                                    + item[
                                        "samples"
                                    ][0][3][
                                        "first_file_ms"
                                    ]
                                    for item
                                    in entries
                                ),
                            "peak_capture_ms":
                                max(
                                    item[
                                        "samples"
                                    ][0][1]
                                    for item
                                    in entries
                                ),
                        },
                    }
                )

            selected_calibration = (
                _select_bracket_candidate(
                    combined_candidates
                )
            )

            if selected_calibration is None:
                raise RuntimeError(
                    "Internal error selecting "
                    "bracket calibration primitive"
                )

            calibration_trigger_id = (
                selected_calibration[
                    "command_id"
                ]
            )

        elif supported_sizes:
            # Inter-frame cannot be identified from only one
            # native size.  Keep that bracket functional and
            # publish a minimal guarded inter-frame term.
            bracket_calibration_frames = [
                supported_sizes[0]
            ]

            selected_calibration = (
                _select_bracket_candidate(
                    bracket_candidates_by_frames[
                        supported_sizes[0]
                    ]
                )
            )

            if selected_calibration is not None:
                calibration_trigger_id = (
                    selected_calibration[
                        "command_id"
                    ]
                )

        # Select each supported bracket independently, except
        # calibration sizes which MUST use the common calibration
        # primitive so the timing slope remains mathematically valid.
        for frames in supported_sizes:
            size = str(frames)
            candidates = (
                bracket_candidates_by_frames[
                    frames
                ]
            )

            if (
                frames
                in bracket_calibration_frames
                and calibration_trigger_id
                is not None
            ):
                selected_item = next(
                    item
                    for item in candidates
                    if item["command_id"]
                    == calibration_trigger_id
                )
            else:
                selected_item = (
                    _select_bracket_candidate(
                        candidates
                    )
                )

            if selected_item is None:
                continue

            samples = selected_item[
                "samples"
            ]

            target_trials = (
                5
                if frames
                in bracket_calibration_frames
                else 1
            )

            # Functional trial above is trial 1/5 for
            # calibration sizes.
            while len(samples) < target_trials:
                job.check()

                prepare_ms = (
                    timed_runtime_prepare(
                        "1/500",
                        selected_item["mode"],
                    )
                )

                sample = probe(
                    selected_item["trigger"],
                    expected=frames,
                    exposure_s=sum(
                        selected_item[
                            "reference_views_s"
                        ]
                    ),
                    ready_set=(
                        "capture_mode",
                        commands[
                            "capture_mode"
                        ]["value"],
                    ),
                )

                samples.append(
                    (
                        *sample,
                        prepare_ms,
                    )
                )

                runtime_set(
                    "capture_mode",
                    commands[
                        "capture_mode"
                    ]["value"],
                )

            # Timing-trial history contains only the sizes
            # that actually identify the timing model.
            if (
                frames
                in bracket_calibration_frames
            ):
                summarize_samples(
                    selected_item["trigger"],
                    frames,
                    samples,
                )

            bracket_spec = {
                "step_ev": 1,
                "mode":
                    selected_item["mode"],
                "trigger": deepcopy(
                    selected_item["trigger"]
                ),
                "shutter_requires_single_mode":
                    bool(
                        bracket_prepare_policy.get(
                            frames,
                            True,
                        )
                    ),
                "peak_capture_ms":
                    max(
                        sample[1]
                        for sample in samples
                    ),
                "peak_first_file_ms":
                    max(
                        sample[3][
                            "first_file_ms"
                        ]
                        for sample in samples
                    ),
                "peak_prepare_to_first_file_ms":
                    max(
                        sample[4]
                        + sample[3][
                            "first_file_ms"
                        ]
                        for sample in samples
                    ),
            }

            profile["brackets"][
                size
            ] = bracket_spec

            selected_candidate_ids[
                size
            ] = selected_item[
                "command_id"
            ]

            selected_bracket_items[
                size
            ] = {
                "command_id":
                    selected_item[
                        "command_id"
                    ],
                "evidence":
                    selected_item[
                        "evidence"
                    ],
                "spec":
                    bracket_spec,
                "samples":
                    samples,
                "reference_views_s":
                    selected_item[
                        "reference_views_s"
                    ],
            }

            bracket_usb_return_samples.extend(
                sample[3][
                    "usb_return_ms"
                ]
                for sample in samples
            )

            if (
                frames
                in bracket_calibration_frames
            ):
                exposure_ms = (
                    sum(
                        selected_item[
                            "reference_views_s"
                        ]
                    )
                    * 1000.0
                )

                bracket_overhead_samples_by_frames[
                    frames
                ] = [
                    max(
                        0.0,
                        sample[3][
                            "file_complete_ms"
                        ]
                        - exposure_ms,
                    )
                    for sample in samples
                ]

            selection_evidence[
                f"native_bracket_{size}"
            ] = compact_selection(
                f"native_bracket_{size}",
                [
                    item["evidence"]
                    for item in candidates
                ],
                selected_item[
                    "evidence"
                ],
            )

            job.log(
                f"SELECT BRACKET "
                f"{frames}: "
                f"{selected_item['command_id']}; "
                f"trials={len(samples)}; "
                f"timing_model="
                f"{'yes' if frames in bracket_calibration_frames else 'functional-only'}"
            )

        selected_values = [
            value
            for value
            in selected_candidate_ids.values()
            if value is not None
        ]

        common_selected = (
            selected_values[0]
            if selected_values
            and len(
                set(selected_values)
            ) == 1
            else None
        )

        selection_evidence["native_bracket"] = {
            "action":
                "native_bracket",
            "policy": (
                "per-size reliable primitive; "
                "one common primitive required "
                "only across timing calibration sizes"
            ),
            "required_sizes":
                sorted(ordered_modes),
            "selected_candidate_id":
                common_selected,
            "selected_candidate_ids_by_frames":
                deepcopy(
                    selected_candidate_ids
                ),
        }

        profile["selection"] = deepcopy(
            selection_evidence
        )

        profile[
            "bracket_selection"
        ] = {
            "criterion": (
                "per-size exact N/N capability; "
                "BRK3/BRK7 use one common primitive "
                "for timing when available"
            ),
            "required_sizes":
                sorted(ordered_modes),
            "timing_calibration_frames":
                list(
                    bracket_calibration_frames
                ),
            "selected_by_frames":
                deepcopy(
                    selected_candidate_ids
                ),
            "excluded_by_frames":
                deepcopy(
                    bracket_rejections
                ),
        }

    if profile["brackets"]:
        bracket_components = derive_bracket_components(
            bracket_overhead_samples_by_frames
        )
        bracket_core_overhead_ms = budget_ms(
            [bracket_components["raw_bracket_overhead_ms"]]
        )
        bracket_inter_image_ms = (
            budget_ms(
                [bracket_components["raw_bracket_inter_image_ms"]]
            )
            if len(bracket_calibration_frames) >= 2
            else budget_ms([0.0])
        )
        bracket_usb_return_ms = budget_ms(
            bracket_usb_return_samples
        )
        bracket_overhead_ms = (
            bracket_core_overhead_ms + bracket_usb_return_ms
        )
    else:
        bracket_components = {
            "peak_overhead_ms_by_frames": {},
            "raw_bracket_overhead_ms": 0.0,
            "raw_bracket_inter_image_ms": 0.0,
        }
        bracket_core_overhead_ms = 0
        bracket_usb_return_ms = 0
        bracket_overhead_ms = 0
        bracket_inter_image_ms = 0
        if ordered_modes:
            warnings.append(
                "No common native bracket capture primitive validated; "
                "sequential only"
            )

    missing_bracket_sizes = sorted(
        frames
        for frames in ordered_modes
        if str(frames) not in profile["brackets"]
    )
    if missing_bracket_sizes and profile["brackets"]:
        warnings.append(
            "Native bracket unavailable for characterized frame counts: "
            + ", ".join(str(value) for value in missing_bracket_sizes)
        )

    prepare_lead_samples = [
        sample[4]
        for sample in single_samples
        if len(sample) > 4
    ]
    for item in selected_bracket_items.values():
        prepare_lead_samples.extend(
            sample[4]
            for sample in item["samples"]
            if len(sample) > 4
        )

    prepare_lead_ms = (
        budget_ms(prepare_lead_samples)
        if prepare_lead_samples
        else 0
    )

    contract = {
        "version": 3,
        "safety_policy": deepcopy(SAFETY_POLICY),
        "set_overhead_ms": set_overhead_ms,
        "single_overhead_ms": single_overhead_ms,
        "single_usb_return_ms": single_usb_return_ms,
        # Dedicated floor for the very first PHOTO of a fresh session (see
        # the "Session cold-start measurement" block above). Never lower
        # than single_overhead_ms: if the cold trial happened to be faster
        # than the steady-state budget (noise, or measurement unavailable),
        # single_overhead_ms already covers it.
        "session_first_photo_overhead_ms": (
            max(single_overhead_ms, budget_ms([cold_start_overhead_ms]))
            if cold_start_overhead_ms is not None
            else single_overhead_ms
        ),
        "prepare_lead_ms": prepare_lead_ms,
        "bracket_overhead_ms": bracket_overhead_ms,
        "bracket_inter_image_ms": bracket_inter_image_ms,
        "bracket_usb_return_ms": bracket_usb_return_ms,
        "supported_bracket_frames": sorted(
            int(size) for size in profile["brackets"]
        ),
        "bracket_calibration_frames": list(bracket_calibration_frames),
        # gphoto2 does not expose an authoritative physical shutter-open event.
        # Never substitute command return or FILE_ADDED for this measurement.
        "physical_trigger_latency": {
            "status": "unmeasured",
            "compensation_ms": 0.0,
            "jitter_ms": None,
        },
    }

    profile["timing_contract"] = contract

    # Qualification exercises representative reactive SET/PHOTO groups in
    # the already-open persistent camera session.  It never owns camera.init()
    # or camera.exit(); Trigger/CameraService own that lifecycle.
    profile["strategy"] = (
        "bracket"
        if profile["brackets"]
        else "sequential"
    )

    operational_qualification = qualify_operational_contract_v3(
        camera,
        profile,
        all_set_samples,
        single_overhead_samples,
        bracket_overhead_samples_by_frames,
        job,
    )

    contract = profile["timing_contract"]
    set_overhead_ms = contract["set_overhead_ms"]

    # Runtime v3 is reactive: SET/PHOTO guard budgets are limits, not
    # reservations that must be consumed.  prepare_lead_ms is therefore the
    # independently measured full runtime SET preamble (already protected by
    # the v3 safety policy), and must not be inflated to N * set_overhead_ms.
    #
    # set_overhead_ms remains a per-command overrun/deadline guard only.
    contract["prepare_lead_ms"] = int(
        contract.get("prepare_lead_ms", 0) or 0
    )

    single_overhead_ms = contract["single_overhead_ms"]
    bracket_overhead_ms = contract["bracket_overhead_ms"]
    bracket_inter_image_ms = contract[
        "bracket_inter_image_ms"
    ]

    # Bracket timing remains identified exclusively from the characterized
    # calibration sizes (normally BRK3 and BRK7). Qualification validates the
    # model but does not turn BRK5/9 into redundant timing benchmarks.
    if profile["brackets"]:
        bracket_components = derive_bracket_components(
            bracket_overhead_samples_by_frames
        )

    job.checkpoint(
        operational_qualification=operational_qualification,
        timing_contract=contract,
    )

    # Classify sequential vs bracket from the same exact 9-view 1-EV plan used
    # by the Sequencer optimizer. This is a calculation; it takes no photographs.
    from math import log2

    test_speeds = []
    for ev in range(-4, 5):
        target = reference_single_s * (2 ** ev)
        closest = min(
            speeds,
            key=lambda value: abs(
                log2(
                    _parse_speed(value)
                    / target
                )
            ),
        )

        if (
            abs(
                log2(
                    _parse_speed(closest)
                    / target
                )
            )
            > .12
        ):
            raise RuntimeError(
                "Nine-view comparison range unavailable at 1 EV"
            )

        test_speeds.append(
            closest
        )

    profile["strategy"] = (
        "bracket"
        if profile["brackets"]
        else "sequential"
    )

    benchmark_exposures = [
        {
            "shutter": speed,
            "iso": 100,
        }
        for speed in test_speeds
    ]

    estimated = ProfilePlugin(
        None,
        profile=profile,
    ).prepare_capture(
        SimpleNamespace(
            exposure_plan=benchmark_exposures
        )
    )

    if not any(
        operation["action"] == "bracket_press"
        for operation in estimated.token[1]
    ):
        profile["strategy"] = "sequential"

    single_set_count = (
        3
        if "capture_mode" in commands
        else 2
    )

    guarded_sequential_ms = sum(
        (
            single_set_count
            * set_overhead_ms
            + single_photo_duration_ms(
                single_overhead_ms,
                _parse_speed(
                    exposure["shutter"]
                ),
            )
        )
        for exposure in benchmark_exposures
    )

    profile["benchmark"] = {
        "iso": 100,
        "step_ev": 1,
        "repetitions": 5,
        "speeds": test_speeds,
        "comparison_source": (
            "operational budget model"
        ),
        "guarded_optimized_ms": round(
            estimated.estimated_total_s
            * 1000.0
        ),
        "guarded_sequential_ms": round(
            guarded_sequential_ms
        ),
    }

    if (
        profile["brackets"]
        and profile["strategy"] == "sequential"
    ):
        warnings.append(
            "Native bracket validated but not faster under guarded v3 budgets; "
            "sequential strategy selected"
        )

    # Diagnostic legacy-shaped values are returned/checkpointed only so existing
    # developer tooling remains useful. publish() removes them from final JSON.
    raw_set_iso = max(
        set_samples["iso"]
    )
    raw_set_shutter = max(
        set_samples["shutter"]
    )
    raw_set_capturemode = (
        max(set_samples["capture_mode"])
        if "capture_mode" in set_samples
        else 0.0
    )
    raw_single_total = max(
        sample[1]
        for sample in single_samples
    )

    raw_bracket_atomic = {}
    release_samples = []

    for size, item in selected_bracket_items.items():
        raw_bracket_atomic[size] = max(
            sample[1]
            for sample in item["samples"]
        )
        release_samples.extend(
            sample[3]["release_ms"]
            for sample in item["samples"]
        )

    raw_timing = {
        "set_iso_ms": raw_set_iso,
        "set_shutter_ms": raw_set_shutter,
        "set_capturemode_ms": (
            raw_set_capturemode
        ),
        "trigger_single_duration_ms": (
            raw_single_total
        ),
        "settle_idle_ms": 0.0,
        "bracket_press_latency_ms": 0.0,
        "bracket_release_ms": (
            max(release_samples)
            if release_samples
            else 0.0
        ),
        "trigger_single_latency_ms": 0.0,
        "bracket_atomic_ms_by_frames": (
            raw_bracket_atomic
        ),
    }

    # Legacy-shaped guarded diagnostics, never persisted by contract v3.
    guarded_timing = {
        "set_iso_ms": budget_ms(
            [raw_set_iso]
        ),
        "set_shutter_ms": budget_ms(
            [raw_set_shutter]
        ),
        "set_capturemode_ms": (
            budget_ms([raw_set_capturemode])
            if raw_set_capturemode > 0
            else 0
        ),
        "trigger_single_duration_ms": (
            budget_ms([raw_single_total])
        ),
        "settle_idle_ms": 0,
        "bracket_press_latency_ms": 0,
        "bracket_release_ms": (
            budget_ms(release_samples)
            if release_samples
            else 0
        ),
        "trigger_single_latency_ms": 0,
        "bracket_atomic_ms_by_frames": {
            size: budget_ms([value])
            for size, value
            in raw_bracket_atomic.items()
        },
    }

    timing = {
        "schema_version": 2,
        "config_type": "camera_timing",
        "backend": profile["backend"],
        "manufacturer": entry["manufacturer"],
        "model": entry["model"],
        "timing_contract": deepcopy(
            contract
        ),
        # Debug/developer evidence below. publish() strips it.
        "timing": guarded_timing,
        "raw_timing": raw_timing,
        "timing_trials": timing_trials,
        "set_trials": deepcopy(
            set_samples
        ),
        "raw_components": {
            "set_max_ms": max(
                all_set_samples
            ),
            "single_overhead_samples_ms": (
                single_overhead_samples
            ),
            "single_core_overhead_samples_ms": (
                single_core_overhead_samples
            ),
            "single_usb_return_samples_ms": (
                single_usb_return_samples
            ),
            "bracket_usb_return_samples_ms": (
                bracket_usb_return_samples
            ),
            "bracket_overhead_samples_ms_by_frames": {
                str(frames): samples
                for frames, samples
                in bracket_overhead_samples_by_frames.items()
            },
            **deepcopy(
                bracket_components
            ),
        },
        "measurement_status": {
            "set_overhead_ms": "measured",
            "single_overhead_ms": "measured",
            "prepare_lead_ms": "measured_full_runtime_set_preamble",
            "bracket_overhead_ms": (
                "derived_from_measured_brackets"
                if profile["brackets"]
                else "not_applicable"
            ),
            "bracket_inter_image_ms": (
                "derived_from_measured_brackets"
                if profile["brackets"]
                else "not_applicable"
            ),
            "trigger_single_latency_ms": "unmeasured_physical",
            "single_usb_return_ms": "measured",
            "bracket_usb_return_ms": (
                "measured" if profile["brackets"] else "not_applicable"
            ),
        },
        "physical_latency_measured": False,
        "test_pause_s": 0.0,
    }

    job.checkpoint(
        profile_draft=profile,
        timing_debug=timing,
        timing_contract=contract,
        set_trials=set_samples,
        timing_trials=timing_trials,
    )

    job.log(
        "BUDGET POLICY: maximum observed -> +10% -> +50 ms "
        "-> round upwards to 50 ms"
    )
    job.log(
        "TIMING CONTRACT V3: "
        f"SET={contract['set_overhead_ms']} ms; "
        f"prepare lead={contract.get('prepare_lead_ms', 0)} ms; "
        f"single overhead={contract['single_overhead_ms']} ms; "
        f"single USB return={contract.get('single_usb_return_ms', 0)} ms; "
        f"bracket overhead={contract['bracket_overhead_ms']} ms; "
        f"inter-image={contract['bracket_inter_image_ms']} ms; "
        f"bracket USB return={contract.get('bracket_usb_return_ms', 0)} ms; "
        "physical trigger latency=UNMEASURED"
    )
    job.log(
        f"RESULT {profile['strategy']}: "
        f"{profile['benchmark']}"
    )

    return (
        validate_profile(profile),
        timing,
    )


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



def qualify_operational_contract_v3(
    camera,
    profile,
    all_set_samples,
    single_overhead_samples,
    bracket_overhead_samples_by_frames,
    job,
):
    """Validate contract-v3 through the real plugin in one persistent session.

    Camera lifecycle belongs to Trigger/CameraService.  Characterization must
    therefore never emulate normal eclipse execution with repeated exit()/init().
    Budget revisions restart the logical validation recipe only; the physical
    gphoto session stays up exactly as it does from partial phase through both
    Diamond Rings and totality.
    """
    from backend.camera_timing_contract import budget_ms
    from backend.camera_validation import build_validation_recipe
    from plugins.camera.base import _parse_speed
    from plugins.camera.profile import CameraPhysicalPreflightError

    contract = profile["timing_contract"]
    preview = build_validation_recipe(profile)
    job.log(
        "Final operational qualification starts automatically in the current "
        "persistent camera session: "
        f"{preview['expected_photos']} RAW photo(s) per complete attempt. "
        "No camera.exit()/camera.init() is performed."
    )

    runtime_set_samples = []
    runtime_single_overheads = []
    runtime_bracket_overheads = {}
    adjustments = []
    attempts = []
    attempt = 0

    def initialize_plugin():
        plugin = ProfilePlugin(camera, job.log, profile=profile)
        while True:
            job.check()
            try:
                plugin.preflight()
                return plugin
            except CameraPhysicalPreflightError as exc:
                job.log(
                    "RUNTIME QUALIFICATION CAMERA INITIALIZATION: "
                    f"operator action required: {exc}"
                )
                if not job.ask(
                    "Camera initialization: "
                    f"{exc} Correct this physical setting, wait until the "
                    "camera is ready, then click OK.",
                    kind="start",
                ):
                    raise Cancelled(
                        "Operational qualification cancelled during physical "
                        "camera initialization"
                    )

    def revise(field, previous, revised, observed, command_index):
        adjustments.append(
            {
                "attempt": attempt,
                "field": field,
                "observed_ms": observed,
                "previous_ms": previous,
                "revised_ms": revised,
                "command_index": command_index,
            }
        )
        job.log(
            f"RUNTIME QUALIFICATION BUDGET REVISED {field}: "
            f"{previous} -> {revised} ms (observed={observed:.1f} ms); "
            "restarting logical qualification in the same camera session"
        )

    while True:
        job.check()
        attempt += 1
        plugin = initialize_plugin()
        recipe = build_validation_recipe(profile)
        attempt_record = {
            "attempt": attempt,
            "expected_photos": recipe["expected_photos"],
            "commands_total": len(recipe["commands"]),
            "commands_completed": 0,
            "set_samples_ms": [],
            "photo_samples": [],
            "restarted": False,
            "persistent_session": True,
        }
        attempt_started = time.monotonic()
        restart = False

        for command_index, command in enumerate(recipe["commands"]):
            job.check()
            action = command["action"]
            budget = float(command["duration_ms"])
            params = deepcopy(command["params"])

            job.log(
                "RUNTIME QUALIFICATION COMMAND "
                f"{command_index + 1}/{len(recipe['commands'])}: "
                f"{action} {params}"
            )

            if action == "SET":
                parameter = params["parameter"]
                value = params["value"]
                begin = time.monotonic()
                plugin.set_parameter(
                    parameter,
                    value,
                    fallback_parameter=params.get("fallback_parameter"),
                )
                elapsed_ms = (time.monotonic() - begin) * 1000.0
                runtime_set_samples.append(elapsed_ms)
                all_set_samples.append(elapsed_ms)
                attempt_record["set_samples_ms"].append(
                    {
                        "parameter": parameter,
                        "value": value,
                        "elapsed_ms": elapsed_ms,
                        "budget_ms": budget,
                    }
                )
                if elapsed_ms > budget:
                    previous = contract["set_overhead_ms"]
                    revised = max(previous, budget_ms([elapsed_ms]))
                    contract["set_overhead_ms"] = revised
                    profile["timing_contract"] = contract
                    revise(
                        "set_overhead_ms",
                        previous,
                        revised,
                        elapsed_ms,
                        command_index,
                    )
                    restart = True
                    break

            elif action == "PHOTO":
                params.pop("validation_confirmation_grace_ms", None)
                views = params.get("physical_views") or [params["shutter"]]
                exposure_s = sum(_parse_speed(value) for value in views)
                frames = int(params.get("frames", 1))
                observation_s = max(
                    15.0 + exposure_s,
                    budget / 1000.0 + 5.0,
                )
                begin = time.monotonic()
                plugin.execute_photo(
                    params,
                    observation_timeout_s=observation_s,
                    check=job.check,
                )
                elapsed_ms = (time.monotonic() - begin) * 1000.0
                overhead_ms = max(
                    0.0,
                    elapsed_ms - exposure_s * 1000.0,
                )
                attempt_record["photo_samples"].append(
                    {
                        "frames": frames,
                        "views": list(views),
                        "elapsed_ms": elapsed_ms,
                        "exposure_ms": exposure_s * 1000.0,
                        "overhead_ms": overhead_ms,
                        "budget_ms": budget,
                    }
                )

                if frames == 1:
                    runtime_single_overheads.append(overhead_ms)
                else:
                    runtime_bracket_overheads.setdefault(frames, []).append(
                        overhead_ms
                    )

                if elapsed_ms > budget:
                    excess_ms = elapsed_ms - budget
                    if frames == 1:
                        field = "single_overhead_ms"
                    else:
                        # A BRK3/7-derived inter-frame slope is preserved.  Any
                        # unexpected validation overrun is conservatively folded
                        # into the fixed bracket component.
                        field = "bracket_overhead_ms"
                    previous = contract[field]
                    revised = previous + budget_ms([excess_ms])
                    contract[field] = revised
                    profile["timing_contract"] = contract
                    revise(
                        field,
                        previous,
                        revised,
                        elapsed_ms,
                        command_index,
                    )
                    restart = True
                    break

            else:
                raise RuntimeError(
                    f"Unsupported qualification action: {action}"
                )

            attempt_record["commands_completed"] = command_index + 1

        attempt_record["elapsed_ms"] = (
            time.monotonic() - attempt_started
        ) * 1000.0
        attempt_record["restarted"] = restart
        attempts.append(attempt_record)

        job.checkpoint(
            runtime_v3_qualification={
                "status": "retrying" if restart else "validated",
                "attempts": deepcopy(attempts),
                "adjustments": deepcopy(adjustments),
                "runtime_set_samples_ms": list(runtime_set_samples),
                "runtime_single_overheads_ms": list(
                    runtime_single_overheads
                ),
                "runtime_bracket_overheads_ms_by_frames": {
                    str(frames): list(samples)
                    for frames, samples in runtime_bracket_overheads.items()
                },
                "timing_contract": deepcopy(contract),
                "persistent_camera_session": True,
            }
        )

        if restart:
            continue
        break

    job.log(
        "RUNTIME QUALIFICATION V3 PASSED: persistent camera session; "
        f"attempts={attempt}; SET={contract['set_overhead_ms']} ms; "
        f"single overhead={contract['single_overhead_ms']} ms; "
        f"single USB return={contract.get('single_usb_return_ms', 0)} ms; "
        f"bracket overhead={contract['bracket_overhead_ms']} ms; "
        f"inter-image={contract['bracket_inter_image_ms']} ms; "
        f"bracket USB return={contract.get('bracket_usb_return_ms', 0)} ms"
    )

    return {
        "status": "validated",
        "attempts": attempts,
        "adjustments": adjustments,
        "runtime_set_samples_ms": runtime_set_samples,
        "runtime_single_overheads_ms": runtime_single_overheads,
        "runtime_bracket_overheads_ms_by_frames": {
            str(frames): samples
            for frames, samples in runtime_bracket_overheads.items()
        },
        "persistent_camera_session": True,
        "cold_bracket_first_frames": None,
        "cold_bracket_first": None,
    }



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
