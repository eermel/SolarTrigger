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
        "commands",
        "warnings",
        "capture_timeout_s",
        "timing_contract",
    ):
        if key in profile:
            result[key] = deepcopy(profile[key])

    result["brackets"] = {}
    for size, spec in profile.get("brackets", {}).items():
        result["brackets"][str(size)] = {
            "step_ev": spec["step_ev"],
            "mode": spec["mode"],
            "trigger": deepcopy(spec["trigger"]),
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


def publish(profile, timing, root):
    """Publish lean runtime JSON atomically; never overwrite an existing model."""
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

    if any(path.exists() for path, _ in files):
        raise RuntimeError(
            "Characterization files already exist; no overwrite performed"
        )

    written = []
    try:
        for path, document in files:
            path.parent.mkdir(parents=True, exist_ok=True)
            if (
                path.parent.resolve()
                != root.resolve() / path.parent.relative_to(root)
            ):
                raise ValueError(
                    "Camera configuration directories must not be symlinks"
                )

            import os
            import tempfile

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

            try:
                if document.get("config_type") == "camera_timing":
                    from backend.camera_timing import (
                        load_camera_timing_profile,
                    )
                    load_camera_timing_profile(temp)
                os.link(temp, path)
                written.append(path)
            finally:
                temp.unlink(missing_ok=True)

        return [
            str(path.relative_to(root))
            for path in written
        ]

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

    def find_setting(
        key,
        names,
        accept,
        critical=True,
        operator_instruction=None,
        require_set=False,
    ):
        """Discover independent GET/SET capability for one setting.

        GET is proven by reading the widget. SET is only marked true after a
        real value transition and readback. Writing the value that is already
        active is deliberately *not* considered proof: this is essential for
        bodies such as the Sony A6600, where exposure mode is readable but the
        physical mode dial cannot be changed over USB.
        """
        errors = []

        def read_value(path):
            _, node = widget(camera, path)
            return node.get_value()

        def write_and_confirm(path, value):
            write_widget(camera, path, value)
            deadline = time.monotonic() + 5.0
            for attempt in range(20):
                job.check()
                actual = read_value(path)
                if str(actual) == str(value):
                    return actual
                if time.monotonic() >= deadline or attempt == 19:
                    raise RuntimeError(
                        "readback mismatch: "
                        f"requested={value!r}, actual={actual!r}"
                    )
                time.sleep(0.25)
            raise AssertionError("unreachable")

        for operator_pass in range(2):
            candidates = [
                item
                for item in enumerate_widgets(camera)
                if item["name"] in names
            ]

            for candidate in candidates:
                path = candidate["path"]
                try:
                    current = read_value(path)
                except Exception as exc:
                    errors.append(f"GET {path}: {exc}")
                    continue

                values = list(candidate["choices"] or [current])
                if key == "capture_target":
                    values.sort(
                        key=lambda value: (
                            str(value).casefold() != "card+sdram"
                        )
                    )
                targets = [value for value in values if accept(str(value))]
                if not targets:
                    continue

                if candidate["readonly"]:
                    if not require_set:
                        # GET capability is independent from the value
                        # currently selected on the physical camera.
                        #
                        # The target is a characterized invariant.  Runtime
                        # preflight will compare the live value against it and,
                        # because SET is unavailable, ask the operator to
                        # change the physical control when necessary.
                        target = targets[0]
                        commands[key] = {
                            "path": path,
                            "value": target,
                            "get": True,
                            "set": False,
                        }
                        job.log(
                            f"VALID {key}: {path} target={target} "
                            f"current={current} "
                            "(GET=yes SET=no, readonly)"
                        )
                        return candidate

                    errors.append(
                        f"SET {path}: widget is readonly"
                    )
                    continue

                for target in targets:
                    job.check()
                    try:
                        current = read_value(path)
                        if str(current) != str(target):
                            write_and_confirm(path, target)
                            set_proved = True
                        else:
                            # Same-value writes prove nothing. Exercise a real
                            # transition and then restore the required value.
                            set_proved = False
                            alternates = [
                                value for value in values
                                if str(value) != str(target)
                            ]
                            for alternate in alternates:
                                try:
                                    write_and_confirm(path, alternate)
                                    write_and_confirm(path, target)
                                except Exception as exc:
                                    errors.append(
                                        f"SET transition {path} "
                                        f"{target!r}->{alternate!r}->{target!r}: {exc}"
                                    )
                                    try:
                                        if str(read_value(path)) != str(target):
                                            write_and_confirm(path, target)
                                    except Exception as restore_exc:
                                        errors.append(
                                            f"restore {path}={target!r}: {restore_exc}"
                                        )
                                    continue
                                set_proved = True
                                break

                        if set_proved:
                            commands[key] = {
                                "path": path,
                                "value": target,
                                "get": True,
                                "set": True,
                            }
                            job.log(
                                f"VALID {key}: {path}={target} "
                                "(GET=yes SET=yes, transition proven)"
                            )
                            return candidate

                        if not require_set:
                            try:
                                actual = read_value(path)
                            except Exception as exc:
                                errors.append(
                                    f"GET after SET proof failure "
                                    f"{path}: {exc}"
                                )
                                continue

                            commands[key] = {
                                "path": path,
                                "value": target,
                                "get": True,
                                "set": False,
                            }
                            job.log(
                                f"VALID {key}: {path} target={target} "
                                f"current={actual} "
                                "(GET=yes SET=no, transition not proven)"
                            )
                            return candidate

                        errors.append(
                            f"SET {path}: no real transition proven"
                        )

                    except Exception as exc:
                        errors.append(str(exc))
                        job.log(f"RETRY {key}: {exc}")

            if (
                operator_pass == 0
                and candidates
                and critical
                and operator_instruction
            ):
                if not job.ask(operator_instruction):
                    raise RuntimeError(
                        f"Operator refused required physical setting: {key}"
                    )
                continue
            break

        if critical:
            detail = "; ".join(errors) or "no usable GET/SET path"
            raise RuntimeError(
                f"Critical function unavailable: {key}; {detail}"
            )

        warnings.append(f"{key}: unavailable")
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

    for key, names in (
        ("self_timer", ("selftimer", "selftimerdelay")),
        ("time_lapse", ("intervalshooting", "timelapse")),
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
    aperture_probe_value = None

    current_widgets = enumerate_widgets(camera)

    for item in current_widgets:
        if item["name"] in ("batterylevel", "battery"):
            commands["battery"] = {
                "path": item["path"],
                "get": True,
                "set": False,
            }
            job.log(
                f"Battery: {item['value']}"
            )

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
                f"APERTURE GET unavailable: "
                f"{aperture_path}: {exc}"
            )
            aperture_reference = None

        if aperture_reference is not None:
            aperture_values = list(
                aperture_item["choices"] or [aperture_reference]
            )

            commands["aperture"] = {
                "path": aperture_path,
                "get": True,
                "set": False,
                "values": {
                    str(value): value
                    for value in aperture_values
                },
            }

            if aperture_item["readonly"]:
                job.log(
                    f"VALID aperture: {aperture_path} "
                    f"current={aperture_reference} "
                    "(GET=yes SET=no, readonly)"
                )

            else:
                # A writable flag alone is not proof of SET capability.
                # Exercise a real transition and restore the exact original
                # value.  Prefer adjacent aperture values when the current
                # value appears in the advertised choice list.
                indexed = {
                    str(value): index
                    for index, value in enumerate(aperture_values)
                }
                current_index = indexed.get(str(aperture_reference))

                alternates = [
                    value
                    for value in aperture_values
                    if str(value) != str(aperture_reference)
                ]

                if current_index is not None:
                    alternates.sort(
                        key=lambda value: abs(
                            indexed[str(value)] - current_index
                        )
                    )

                # SET capability does not require testing every advertised
                # aperture.  Some bodies expose a theoretical f-number list
                # even with a fully manual lens attached (for example f/0 as
                # the effective value).  Two distinct failed transitions are
                # sufficient evidence that USB aperture SET is not proven.
                aperture_probe_limit = 2
                alternates = alternates[:aperture_probe_limit]

                proof_errors = []

                for alternate in alternates:
                    job.check()

                    try:
                        # Real transition.
                        write_checked(
                            camera,
                            aperture_path,
                            alternate,
                        )

                        # Restore and prove the original aperture too.
                        write_checked(
                            camera,
                            aperture_path,
                            aperture_reference,
                        )

                    except Exception as exc:
                        proof_errors.append(
                            f"{aperture_reference!r}"
                            f"->{alternate!r}"
                            f"->{aperture_reference!r}: {exc}"
                        )

                        # Never silently leave the lens at a test value.
                        try:
                            _, node = widget(camera, aperture_path)
                            actual = node.get_value()

                            if (
                                str(actual)
                                != str(aperture_reference)
                            ):
                                write_checked(
                                    camera,
                                    aperture_path,
                                    aperture_reference,
                                )

                        except Exception as restore_exc:
                            raise RuntimeError(
                                "Cannot restore aperture after "
                                "SET qualification failure: "
                                f"{aperture_path}="
                                f"{aperture_reference!r}: "
                                f"{restore_exc}"
                            ) from restore_exc

                        continue

                    commands["aperture"]["set"] = True
                    aperture_probe_value = alternate

                    job.log(
                        f"VALID aperture: {aperture_path}="
                        f"{aperture_reference} "
                        "(GET=yes SET=yes, transition proven "
                        f"via {alternate})"
                    )
                    break

                if not commands["aperture"]["set"]:
                    detail = (
                        "; ".join(proof_errors)
                        if proof_errors
                        else "no alternate aperture value"
                    )
                    job.log(
                        f"VALID aperture: {aperture_path} "
                        f"current={aperture_reference} "
                        "(GET=yes SET=no, transition not proven; "
                        f"{detail})"
                    )

    if "battery" not in commands:
        warnings.append("battery: unavailable")

    # Discover supported 1-EV native bracket modes before timing SETs so the
    # shared SET reservation covers every drive-mode value later used by .plan.
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
                # CHARACTERIZATION RUNTIME-PATH TIMING V2
                # Runtime ProfilePlugin._apply() first calls _live_writable(),
                # which performs a full camera.get_config() before write_checked.
                # Include that real runtime cost in every SET timing sample.
                begin = time.monotonic()
                _, live_node = widget(
                    camera,
                    commands[key]["path"],
                )
                if bool(live_node.get_readonly()):
                    raise RuntimeError(
                        f"SET {key} became readonly during timing"
                    )
                write_checked(
                    camera,
                    commands[key]["path"],
                    value,
                )
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

    if commands.get("aperture", {}).get("set") is True:
        if (
            aperture_reference is None
            or aperture_probe_value is None
        ):
            raise RuntimeError(
                "Aperture SET was marked supported without "
                "a proven transition pair"
            )

        measure_set(
            "aperture",
            [
                aperture_probe_value,
                aperture_reference,
            ],
        )

        # Characterization must leave the physical lens at the exact
        # aperture that was present before the timing trials.
        write_checked(
            camera,
            commands["aperture"]["path"],
            aperture_reference,
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
        write_checked(
            camera,
            commands["capture_mode"]["path"],
            single_mode,
        )

    all_set_samples = [
        sample
        for samples in set_samples.values()
        for sample in samples
    ]
    set_overhead_ms = budget_ms(
        all_set_samples
    )

    # Probe every known capture entry point. Operator confirmation is used only
    # for the first discovery of each method/size; five timing repetitions then
    # run automatically.
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
                "value": 1,
                "release": 0,
            }
            if candidate not in trigger_candidates:
                trigger_candidates.append(candidate)

    import gphoto2 as gp

    job.log(
        "TEST POLICY: single and bracket commands are validated independently; "
        "a rejected bracket command is not retried at larger sizes"
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

        if (
            discovery
            and not job.ask(
                f"Prêt pour un test de {expected} photo(s) RAW à ISO 100 ? "
                f"Commande : {spec}. Attendez que le boîtier ait terminé "
                "toute prise précédente, puis cliquez sur OK pour démarrer "
                "et observez les déclenchements.",
                kind="start",
            )
        ):
            raise Cancelled(
                "Test cancelled by operator before capture"
            )

        job.log(
            f"TEST START: {expected} photo(s), {spec}"
        )

        # CHARACTERIZATION RUNTIME-PATH TIMING V2
        # ProfilePlugin.execute_photo() starts its guarded PHOTO deadline before
        # draining stale events. Characterization used to drain them outside the
        # stopwatch, underestimating the runtime PHOTO budget on slow USB bodies.
        operation_begin = time.monotonic()
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

        begin = time.monotonic()

        try:
            method = spec["method"]

            if method == "capture":
                file_ref = camera.capture(
                    gp.GP_CAPTURE_IMAGE
                )
                if getattr(file_ref, "name", None):
                    seen.add(
                        (
                            file_ref.folder,
                            file_ref.name,
                        )
                    )

            elif method == "trigger_capture":
                camera.trigger_capture()

            else:
                write_widget(
                    camera,
                    spec["path"],
                    spec["value"],
                )

            returned_ms = (
                time.monotonic() - begin
            ) * 1000.0

            until = (
                time.monotonic()
                + float(exposure_s)
                + 5.0
            )

            while (
                len(seen) < expected
                and time.monotonic() < until
            ):
                job.check()
                kind, data = camera.wait_for_event(100)

                if kind == gp.GP_EVENT_FILE_ADDED:
                    seen.add(
                        (
                            getattr(data, "folder", ""),
                            getattr(
                                data,
                                "name",
                                str(data),
                            ),
                        )
                    )

        except Cancelled:
            raise

        except Exception as exc:
            error = exc

        finally:
            before_release = time.monotonic()
            if "release" in spec:
                write_widget(
                    camera,
                    spec["path"],
                    spec["release"],
                )
                release_ms = (
                    time.monotonic()
                    - before_release
                ) * 1000.0

        # Files delivered after release still belong to this PHOTO.
        post_release_start = time.monotonic()
        until = (
            post_release_start
            + float(exposure_s)
            + 5.0
        )

        while (
            error is None
            and len(seen) < expected
            and time.monotonic() < until
        ):
            job.check()
            kind, data = camera.wait_for_event(100)

            if kind == gp.GP_EVENT_FILE_ADDED:
                seen.add(
                    (
                        getattr(data, "folder", ""),
                        getattr(
                            data,
                            "name",
                            str(data),
                        ),
                    )
                )

        post_release_ms = (
            time.monotonic()
            - post_release_start
        ) * 1000.0

        duration_ms = (
            time.monotonic() - operation_begin
        ) * 1000.0

        capture_confirmed = (
            len(seen) == expected
        )

        # Characterization-only pause. It is deliberately outside duration_ms.
        job.log(
            "TEST PAUSE: 2 seconds without USB events, excluded from timing; "
            f"files {len(seen)}/{expected}"
        )

        idle_ms = wait_camera_idle(
            camera,
            seen,
            check=job.check,
        )

        job.log(
            f"TEST PAUSE END: {idle_ms:.1f} ms, excluded; "
            f"files {len(seen)}/{expected}"
        )

        phases = {
            "pre_trigger_drain_ms": pre_trigger_drain_ms,
            "trigger_call_ms": returned_ms,
            "frame_wait_ms": max(
                0.0,
                (
                    before_release - begin
                ) * 1000.0
                - returned_ms,
            ),
            "release_ms": release_ms,
            "post_release_wait_ms": post_release_ms,
            "settle_ms": 0.0,
            "test_pause_ms": idle_ms,
            "total_ms": duration_ms,
        }

        if error is not None:
            observed = (
                job.ask(
                    f"Erreur USB ({error}). Attendez la fin de tous les "
                    f"déclenchements. Exactement {expected} photo(s) RAW "
                    "enregistrées sur la carte ?"
                )
                if discovery
                else None
            )
            job.log(
                "Operator observed photos after USB error: "
                f"{observed}. Method is unreliable and will not be selected."
            )
            raise RuntimeError(
                f"USB method returned an error: {error}"
            )

        if (
            discovery
            and not job.ask(
                "Attendez la fin de tous les déclenchements avant de répondre. "
                f"Exactement {expected} photo(s) RAW enregistrées sur la carte ? "
                f"Fichiers signalés par USB : {len(seen)}."
            )
        ):
            raise RuntimeError(
                "Operator reports missing/incorrect photos"
            )

        if discovery:
            validated_trials.add(trial_key)

        if (
            len(seen) != expected
            or not capture_confirmed
        ):
            if not discovery:
                raise RuntimeError(
                    "Automatic timing unavailable: "
                    f"USB confirmed {len(seen)}/{expected} files"
                )
            raise RuntimeError(
                "Discovery incomplete: "
                f"USB confirmed {len(seen)}/{expected}; "
                "no timing trials"
            )

        job.log(
            f"TEST END "
            f"{'discovery confirmed' if discovery else 'automatic timing'}: "
            f"{expected} photo(s), {duration_ms:.1f} ms "
            "(test pause excluded)"
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

    valid_single = []

    for spec in trigger_candidates:
        if (
            spec.get("path", "").rsplit("/", 1)[-1]
            == "bulb"
        ):
            job.log(
                "SKIP single bulb: held exposure is not a validated "
                "fixed-shutter capture"
            )
            continue

        samples = []

        try:
            job.log(
                f"TRIGGER TEST {spec}"
            )

            # Operator discovery is excluded from speed measurements.
            probe(
                spec,
                expected=1,
                exposure_s=_parse_speed("1/500"),
            )

            for _ in range(5):
                samples.append(
                    probe(
                        spec,
                        expected=1,
                        exposure_s=_parse_speed("1/500"),
                    )
                )

            summarize_samples(
                spec,
                1,
                samples,
            )

            valid_single.append(
                (
                    max(
                        sample[1]
                        for sample in samples
                    ),
                    deepcopy(spec),
                    samples,
                )
            )

        except (
            Cancelled,
            CameraIdleTimeout,
        ):
            raise

        except Exception as exc:
            timing_trials.append(
                {
                    "trigger": deepcopy(spec),
                    "frames": 1,
                    "status": "rejected",
                    "reason": str(exc),
                }
            )
            job.log(
                f"TRIGGER rejected: {exc}"
            )

    if not valid_single:
        raise RuntimeError(
            "No validated single trigger"
        )

    _single_peak, trigger_single, single_samples = min(
        valid_single,
        key=lambda item: item[0],
    )
    commands["trigger_single"] = trigger_single

    reference_single_s = _parse_speed("1/500")
    single_overhead_samples = [
        max(
            0.0,
            sample[1]
            - reference_single_s * 1000.0,
        )
        for sample in single_samples
    ]
    single_overhead_ms = budget_ms(
        single_overhead_samples
    )

    warnings.append(
        "Physical shutter-start latency is unmeasured; "
        "no timing correction applied"
    )

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
        "commands": commands,
        "warnings": warnings,
        "settle_idle_s": 0.0,
        "test_pause_s": 2.0,
        "brackets": {},
    }

    bracket_candidates = {}
    excluded_bracket_commands = {}

    if mode:
        for frames, mode_value in sorted(
            ordered_modes.items()
        ):
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

            for trigger_spec in trigger_candidates:
                command_id = json.dumps(
                    trigger_spec,
                    sort_keys=True,
                )

                if (
                    command_id
                    in excluded_bracket_commands
                ):
                    job.log(
                        f"SKIP BRACKET {frames}: "
                        f"{trigger_spec}; previously rejected: "
                        f"{excluded_bracket_commands[command_id]}"
                    )
                    continue

                samples = []

                try:
                    # Discovery run.
                    prepare_photo(
                        camera,
                        commands,
                        "1/500",
                        mode_value,
                    )
                    probe(
                        trigger_spec,
                        expected=frames,
                        exposure_s=reference_exposure_s,
                    )

                    # Five timing runs. SETs are intentionally outside probe().
                    for _ in range(5):
                        job.check()
                        prepare_photo(
                            camera,
                            commands,
                            "1/500",
                            mode_value,
                        )
                        samples.append(
                            probe(
                                trigger_spec,
                                expected=frames,
                                exposure_s=reference_exposure_s,
                            )
                        )

                    summarize_samples(
                        trigger_spec,
                        frames,
                        samples,
                    )

                    spec = {
                        "step_ev": 1,
                        "mode": mode_value,
                        "trigger": deepcopy(
                            trigger_spec
                        ),
                        "peak_capture_ms": max(
                            sample[1]
                            for sample in samples
                        ),
                    }

                    bracket_candidates.setdefault(
                        command_id,
                        {},
                    )[str(frames)] = {
                        "spec": spec,
                        "samples": samples,
                        "reference_views_s": (
                            reference_views_s
                        ),
                    }

                except (
                    Cancelled,
                    CameraIdleTimeout,
                ):
                    raise

                except Exception as exc:
                    excluded_bracket_commands[
                        command_id
                    ] = str(exc)

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

                    job.log(
                        f"BRACKET {frames} "
                        f"{trigger_spec} rejected: {exc}"
                    )

                finally:
                    write_checked(
                        camera,
                        commands["capture_mode"]["path"],
                        commands["capture_mode"]["value"],
                    )

    selected = choose_common_bracket_command(
        bracket_candidates,
        excluded_bracket_commands,
        ordered_modes,
    )

    bracket_overhead_samples_by_frames = {}

    if selected is not None:
        for size, item in (
            bracket_candidates[selected].items()
        ):
            profile["brackets"][size] = deepcopy(
                item["spec"]
            )

            exposure_ms = (
                sum(item["reference_views_s"])
                * 1000.0
            )
            bracket_overhead_samples_by_frames[
                int(size)
            ] = [
                max(
                    0.0,
                    sample[1] - exposure_ms,
                )
                for sample in item["samples"]
            ]

        profile["bracket_command"] = deepcopy(
            next(
                iter(
                    profile["brackets"].values()
                )
            )["trigger"]
        )
        job.log(
            "COMMON BRACKET COMMAND: "
            f"{profile['bracket_command']}"
        )

    profile["bracket_selection"] = {
        "excluded": deepcopy(
            excluded_bracket_commands
        ),
        "criterion": (
            "lowest sum of peak PHOTO durations; "
            "SET preparation excluded"
        ),
        "required_sizes": sorted(
            ordered_modes
        ),
    }

    if profile["brackets"]:
        bracket_components = (
            derive_bracket_components(
                bracket_overhead_samples_by_frames
            )
        )
        bracket_overhead_ms = budget_ms(
            [
                bracket_components[
                    "raw_bracket_overhead_ms"
                ]
            ]
        )
        bracket_inter_image_ms = budget_ms(
            [
                bracket_components[
                    "raw_bracket_inter_image_ms"
                ]
            ]
        )
    else:
        bracket_components = {
            "peak_overhead_ms_by_frames": {},
            "raw_bracket_overhead_ms": 0.0,
            "raw_bracket_inter_image_ms": 0.0,
        }
        bracket_overhead_ms = 0
        bracket_inter_image_ms = 0

        if ordered_modes:
            warnings.append(
                "No common bracket command validated across discovered sizes; "
                "sequential only"
            )

    contract = {
        "version": 3,
        "safety_policy": deepcopy(
            SAFETY_POLICY
        ),
        "set_overhead_ms": set_overhead_ms,
        "single_overhead_ms": single_overhead_ms,
        "bracket_overhead_ms": (
            bracket_overhead_ms
        ),
        "bracket_inter_image_ms": (
            bracket_inter_image_ms
        ),
        "supported_bracket_frames": sorted(
            int(size)
            for size in profile["brackets"]
        ),
    }

    profile["timing_contract"] = contract

    # Qualification must use the same logical recipe as the end-to-end IVVQ,
    # but without its diagnostic +2 s spacing.  A fresh gphoto session is used
    # on every attempt so the first capture is part of the measured contract.
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
    single_overhead_ms = contract["single_overhead_ms"]
    bracket_overhead_ms = contract["bracket_overhead_ms"]
    bracket_inter_image_ms = contract[
        "bracket_inter_image_ms"
    ]

    # Operational bracket samples may have revised the fitted components.
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

    if selected is not None:
        for size, item in (
            bracket_candidates[selected].items()
        ):
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
            "trigger_single_latency_ms": (
                "unmeasured"
            ),
        },
        "physical_latency_measured": False,
        "test_pause_s": 2.0,
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
        f"single overhead={contract['single_overhead_ms']} ms; "
        f"bracket overhead={contract['bracket_overhead_ms']} ms; "
        f"inter-image={contract['bracket_inter_image_ms']} ms"
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
    """Qualify contract-v3 budgets through the real ProfilePlugin path.

    Discovery/micro-benchmark measurements remain useful for selecting camera
    commands.  Publication, however, is allowed only after a fresh gphoto
    session has executed the same logical validation recipe used by the
    end-to-end camera IVVQ.

    The validation-only diagnostic guard and late-confirmation grace are not
    used here: every next operation starts at the production contract boundary.

    If a newly observed operation requires a larger guarded budget under the
    v3 safety policy, the affected contract value is revised and the complete
    qualification is restarted from a fresh gphoto session.
    """
    from backend.camera_timing_contract import (
        budget_ms,
        derive_bracket_components,
    )
    from backend.camera_validation import build_validation_recipe
    from plugins.camera.base import _parse_speed
    from plugins.camera.profile import CameraPreflightError

    contract = profile["timing_contract"]

    preview = build_validation_recipe(profile)

    # For bracket profiles, additionally prove the largest characterized
    # bracket as the very first PHOTO of another fresh gphoto session.
    # The normal recipe intentionally starts with singles, so it cannot prove
    # this cold-bracket condition by itself.
    preview_bracket_frames = [
        int(command["params"].get("frames", 1))
        for command in preview["commands"]
        if (
            command["action"] == "PHOTO"
            and int(command["params"].get("frames", 1)) > 1
        )
    ]
    cold_bracket_frames = (
        max(preview_bracket_frames)
        if preview_bracket_frames
        else None
    )
    complete_attempt_photos = (
        int(preview["expected_photos"])
        + int(cold_bracket_frames or 0)
    )
    cold_description = (
        f" dont {cold_bracket_frames} photo(s) pour le bracket froid "
        "déclenché comme première PHOTO d'une seconde session neuve."
        if cold_bracket_frames is not None
        else "."
    )

    if not job.ask(
        "Qualification opérationnelle finale avant publication : "
        f"{complete_attempt_photos} photo(s) RAW pour une tentative complète "
        f"({preview['expected_photos']} pour la recette opérationnelle"
        f"{cold_description} "
        "Chaque tentative principale démarre sur une session gphoto neuve. "
        "Si un budget doit être augmenté, la qualification complète "
        "redémarrera automatiquement. Cliquez sur OK pour démarrer.",
        kind="start",
    ):
        raise Cancelled(
            "Operational v3 qualification cancelled by operator"
        )

    runtime_set_samples = []
    runtime_single_overheads = []
    runtime_bracket_overheads = {
        int(frames): []
        for frames in bracket_overhead_samples_by_frames
    }
    adjustments = []
    attempts = []

    attempt = 0

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
            f"{previous} -> {revised} ms "
            f"(observed={observed:.1f} ms); "
            "restarting complete qualification"
        )

    while True:
        job.check()
        attempt += 1

        job.log(
            f"RUNTIME QUALIFICATION V3: attempt {attempt}; "
            "fresh gphoto session; production cadence; no diagnostic guard"
        )

        # A fresh CameraWorker opens a fresh gphoto session in production.
        # Reproduce that property explicitly so the first capture is measured,
        # not discarded as a warm-up.
        camera.exit()
        camera.init()

        plugin = ProfilePlugin(
            camera,
            job.log,
            profile=profile,
        )

        # Same invariant convergence performed before a scheduled production
        # sequence.  Preflight itself is outside the timed execution plan.
        #
        # A GET-only invariant (for example a physical Nikon release-mode
        # selector) must already be correct.  Never bypass it and never attempt
        # an USB SET that characterization proved unavailable: ask the operator
        # to establish the required physical state, then prove it by GET.
        while True:
            job.check()
            try:
                plugin.preflight()
                break
            except CameraPreflightError as exc:
                job.log(
                    "RUNTIME QUALIFICATION PREFLIGHT: "
                    f"operator action required: {exc}"
                )
                if not job.ask(
                    "Précontrôle de qualification caméra : "
                    f"{exc} "
                    "Corrigez ce réglage physiquement sur le boîtier, "
                    "attendez que l'appareil soit prêt, puis cliquez sur OK. "
                    "Le réglage sera relu avant toute mesure.",
                    kind="start",
                ):
                    raise Cancelled(
                        "Operational v3 qualification cancelled "
                        "during physical preflight"
                    )

        # Rebuild after every budget revision: command durations must always
        # reflect the current provisional contract.
        recipe = build_validation_recipe(profile)

        attempt_record = {
            "attempt": attempt,
            "expected_photos": recipe["expected_photos"],
            "commands_total": len(recipe["commands"]),
            "commands_completed": 0,
            "set_samples_ms": [],
            "photo_samples": [],
            "cold_bracket_first": None,
            "restarted": False,
        }
        attempt_started = time.monotonic()
        restart = False

        for command_index, command in enumerate(recipe["commands"]):
            job.check()

            action = command["action"]
            budget = float(command["duration_ms"])
            params = deepcopy(command["params"])

            if action == "SET":
                parameter = params["parameter"]
                value = params["value"]

                begin = time.monotonic()
                plugin.set_parameter(
                    parameter,
                    value,
                    fallback_parameter=params.get(
                        "fallback_parameter"
                    ),
                )
                elapsed_ms = (
                    time.monotonic() - begin
                ) * 1000.0

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

                required = budget_ms(all_set_samples)
                previous = contract["set_overhead_ms"]

                if required > previous:
                    contract["set_overhead_ms"] = required
                    profile["timing_contract"] = contract
                    revise(
                        "set_overhead_ms",
                        previous,
                        required,
                        elapsed_ms,
                        command_index,
                    )
                    restart = True
                    break

                # Production starts the following operation at the next
                # reserved contract boundary.
                time.sleep(
                    max(
                        0.0,
                        (budget - elapsed_ms) / 1000.0,
                    )
                )

            elif action == "PHOTO":
                # This grace belongs only to the external IVVQ.  Qualification
                # instead uses the explicit characterization-only observation
                # timeout below, allowing an underestimated candidate budget
                # to be measured and revised.
                params.pop(
                    "validation_confirmation_grace_ms",
                    None,
                )

                views = (
                    params.get("physical_views")
                    or [params["shutter"]]
                )
                exposure_s = sum(
                    _parse_speed(value)
                    for value in views
                )
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
                elapsed_ms = (
                    time.monotonic() - begin
                ) * 1000.0

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
                    runtime_single_overheads.append(
                        overhead_ms
                    )
                    single_overhead_samples.append(
                        overhead_ms
                    )

                    required = budget_ms(
                        single_overhead_samples
                    )
                    previous = contract[
                        "single_overhead_ms"
                    ]

                    if required > previous:
                        contract[
                            "single_overhead_ms"
                        ] = required
                        profile["timing_contract"] = contract
                        revise(
                            "single_overhead_ms",
                            previous,
                            required,
                            elapsed_ms,
                            command_index,
                        )
                        restart = True
                        break

                else:
                    samples = (
                        bracket_overhead_samples_by_frames
                        .setdefault(frames, [])
                    )
                    samples.append(overhead_ms)
                    runtime_bracket_overheads.setdefault(
                        frames, []
                    ).append(overhead_ms)

                    components = derive_bracket_components(
                        bracket_overhead_samples_by_frames
                    )
                    required_fixed = budget_ms(
                        [
                            components[
                                "raw_bracket_overhead_ms"
                            ]
                        ]
                    )
                    required_inter = budget_ms(
                        [
                            components[
                                "raw_bracket_inter_image_ms"
                            ]
                        ]
                    )

                    previous_fixed = contract[
                        "bracket_overhead_ms"
                    ]
                    previous_inter = contract[
                        "bracket_inter_image_ms"
                    ]

                    revised_fixed = max(
                        previous_fixed,
                        required_fixed,
                    )
                    revised_inter = max(
                        previous_inter,
                        required_inter,
                    )

                    if (
                        revised_fixed > previous_fixed
                        or revised_inter > previous_inter
                    ):
                        contract[
                            "bracket_overhead_ms"
                        ] = revised_fixed
                        contract[
                            "bracket_inter_image_ms"
                        ] = revised_inter
                        profile["timing_contract"] = contract

                        adjustments.append(
                            {
                                "attempt": attempt,
                                "field": "bracket_model",
                                "frames": frames,
                                "observed_ms": elapsed_ms,
                                "overhead_ms": overhead_ms,
                                "previous_bracket_overhead_ms":
                                    previous_fixed,
                                "revised_bracket_overhead_ms":
                                    revised_fixed,
                                "previous_inter_image_ms":
                                    previous_inter,
                                "revised_inter_image_ms":
                                    revised_inter,
                                "command_index": command_index,
                            }
                        )
                        job.log(
                            "RUNTIME QUALIFICATION BUDGET REVISED "
                            f"bracket model: fixed "
                            f"{previous_fixed}->{revised_fixed} ms; "
                            f"inter-image "
                            f"{previous_inter}->{revised_inter} ms; "
                            f"frames={frames}, observed={elapsed_ms:.1f} ms; "
                            "restarting complete qualification"
                        )
                        restart = True
                        break

                # Keep the exact production reservation.  There is
                # intentionally no IVVQ +2000 ms diagnostic gap here.
                time.sleep(
                    max(
                        0.0,
                        (budget - elapsed_ms) / 1000.0,
                    )
                )

            else:
                raise RuntimeError(
                    f"Unsupported qualification action: {action}"
                )

            attempt_record[
                "commands_completed"
            ] = command_index + 1

        # The main operational recipe always exercises singles before brackets.
        # Therefore, after a complete main pass, explicitly prove the largest
        # supported bracket as the very first PHOTO of another fresh gphoto
        # session.  Only the minimum SET state required immediately before that
        # bracket is replayed; no warm-up PHOTO is allowed.
        #
        # After the cold bracket, replay the deterministic final-state SET tail
        # from the same validation recipe so characterization never leaves the
        # physical camera parked in bracket mode.
        if not restart and cold_bracket_frames is not None:
            cold_targets = [
                (index, command)
                for index, command in enumerate(recipe["commands"])
                if (
                    command["action"] == "PHOTO"
                    and int(
                        command["params"].get("frames", 1)
                    ) == cold_bracket_frames
                )
            ]
            if len(cold_targets) != 1:
                raise RuntimeError(
                    "Operational v3 cold bracket target is ambiguous: "
                    f"frames={cold_bracket_frames}, "
                    f"matches={len(cold_targets)}"
                )

            cold_target_index, cold_target = cold_targets[0]
            cold_record = {
                "frames": cold_bracket_frames,
                "status": "running",
                "setup_set_samples_ms": [],
                "restore_set_samples_ms": [],
            }
            attempt_record["cold_bracket_first"] = cold_record

            job.log(
                "RUNTIME QUALIFICATION COLD BRACKET: "
                f"{cold_bracket_frames} frames; fresh gphoto session; "
                "this bracket will be the first PHOTO"
            )

            camera.exit()
            camera.init()

            cold_plugin = ProfilePlugin(
                camera,
                job.log,
                profile=profile,
            )

            while True:
                job.check()
                try:
                    cold_plugin.preflight()
                    break
                except CameraPreflightError as exc:
                    job.log(
                        "RUNTIME QUALIFICATION COLD BRACKET PREFLIGHT: "
                        f"operator action required: {exc}"
                    )
                    if not job.ask(
                        "Précontrôle bracket froid : "
                        f"{exc} "
                        "Corrigez ce réglage physiquement sur le boîtier, "
                        "attendez que l'appareil soit prêt, puis cliquez sur OK. "
                        "Le réglage sera relu avant toute mesure.",
                        kind="start",
                    ):
                        raise Cancelled(
                            "Operational v3 cold bracket qualification "
                            "cancelled during physical preflight"
                        )

            # Collapse the recipe prefix to the last SET for each parameter.
            # Those values are exactly the camera state immediately before the
            # selected bracket, without replaying unrelated earlier singles.
            last_setup_by_parameter = {}
            for original_index, command in enumerate(
                recipe["commands"][:cold_target_index]
            ):
                if command["action"] != "SET":
                    continue
                parameter = command["params"]["parameter"]
                last_setup_by_parameter[parameter] = (
                    original_index,
                    command,
                )

            cold_setup_sets = sorted(
                last_setup_by_parameter.values(),
                key=lambda item: item[0],
            )

            # The target is the largest/last bracket in the validation recipe;
            # the remaining SET commands are its deterministic final-state tail.
            cold_restore_sets = [
                (original_index, command)
                for original_index, command in enumerate(
                    recipe["commands"][cold_target_index + 1:],
                    start=cold_target_index + 1,
                )
                if command["action"] == "SET"
            ]

            def run_cold_sets(items, phase):
                nonlocal restart

                for original_index, command in items:
                    job.check()

                    params = deepcopy(command["params"])
                    parameter = params["parameter"]
                    value = params["value"]
                    budget = float(command["duration_ms"])

                    begin = time.monotonic()
                    cold_plugin.set_parameter(
                        parameter,
                        value,
                        fallback_parameter=params.get(
                            "fallback_parameter"
                        ),
                    )
                    elapsed_ms = (
                        time.monotonic() - begin
                    ) * 1000.0

                    runtime_set_samples.append(elapsed_ms)
                    all_set_samples.append(elapsed_ms)

                    sample = {
                        "parameter": parameter,
                        "value": value,
                        "elapsed_ms": elapsed_ms,
                        "budget_ms": budget,
                        "recipe_command_index": original_index,
                    }
                    cold_record[
                        f"{phase}_set_samples_ms"
                    ].append(sample)

                    required = budget_ms(all_set_samples)
                    previous = contract["set_overhead_ms"]

                    if required > previous:
                        contract["set_overhead_ms"] = required
                        profile["timing_contract"] = contract
                        revise(
                            "set_overhead_ms",
                            previous,
                            required,
                            elapsed_ms,
                            original_index,
                        )
                        cold_record["status"] = (
                            f"{phase}_set_budget_revised"
                        )
                        restart = True
                        return False

                    time.sleep(
                        max(
                            0.0,
                            (budget - elapsed_ms) / 1000.0,
                        )
                    )

                return True

            if run_cold_sets(cold_setup_sets, "setup"):
                params = deepcopy(cold_target["params"])
                params.pop(
                    "validation_confirmation_grace_ms",
                    None,
                )

                views = (
                    params.get("physical_views")
                    or [params["shutter"]]
                )
                exposure_s = sum(
                    _parse_speed(value)
                    for value in views
                )
                budget = float(cold_target["duration_ms"])

                observation_s = max(
                    15.0 + exposure_s,
                    budget / 1000.0 + 5.0,
                )

                begin = time.monotonic()
                cold_plugin.execute_photo(
                    params,
                    observation_timeout_s=observation_s,
                    check=job.check,
                )
                elapsed_ms = (
                    time.monotonic() - begin
                ) * 1000.0

                overhead_ms = max(
                    0.0,
                    elapsed_ms - exposure_s * 1000.0,
                )

                cold_record.update(
                    {
                        "elapsed_ms": elapsed_ms,
                        "exposure_ms": exposure_s * 1000.0,
                        "overhead_ms": overhead_ms,
                        "budget_ms": budget,
                    }
                )

                samples = (
                    bracket_overhead_samples_by_frames
                    .setdefault(cold_bracket_frames, [])
                )
                samples.append(overhead_ms)

                runtime_bracket_overheads.setdefault(
                    cold_bracket_frames, []
                ).append(overhead_ms)

                components = derive_bracket_components(
                    bracket_overhead_samples_by_frames
                )
                required_fixed = budget_ms(
                    [
                        components[
                            "raw_bracket_overhead_ms"
                        ]
                    ]
                )
                required_inter = budget_ms(
                    [
                        components[
                            "raw_bracket_inter_image_ms"
                        ]
                    ]
                )

                previous_fixed = contract[
                    "bracket_overhead_ms"
                ]
                previous_inter = contract[
                    "bracket_inter_image_ms"
                ]

                revised_fixed = max(
                    previous_fixed,
                    required_fixed,
                )
                revised_inter = max(
                    previous_inter,
                    required_inter,
                )

                if (
                    revised_fixed > previous_fixed
                    or revised_inter > previous_inter
                ):
                    contract[
                        "bracket_overhead_ms"
                    ] = revised_fixed
                    contract[
                        "bracket_inter_image_ms"
                    ] = revised_inter
                    profile["timing_contract"] = contract

                    adjustments.append(
                        {
                            "attempt": attempt,
                            "field": "bracket_model_cold_first",
                            "frames": cold_bracket_frames,
                            "observed_ms": elapsed_ms,
                            "overhead_ms": overhead_ms,
                            "previous_bracket_overhead_ms":
                                previous_fixed,
                            "revised_bracket_overhead_ms":
                                revised_fixed,
                            "previous_inter_image_ms":
                                previous_inter,
                            "revised_inter_image_ms":
                                revised_inter,
                            "command_index":
                                cold_target_index,
                            "cold_first_photo": True,
                        }
                    )
                    job.log(
                        "RUNTIME QUALIFICATION COLD BRACKET "
                        "BUDGET REVISED: "
                        f"fixed {previous_fixed}->{revised_fixed} ms; "
                        f"inter-image "
                        f"{previous_inter}->{revised_inter} ms; "
                        f"frames={cold_bracket_frames}, "
                        f"observed={elapsed_ms:.1f} ms; "
                        "restarting complete qualification"
                    )
                    cold_record["status"] = "budget_revised"
                    restart = True
                else:
                    # Respect the same production reservation before applying
                    # the deterministic final-state SET tail.
                    time.sleep(
                        max(
                            0.0,
                            (budget - elapsed_ms) / 1000.0,
                        )
                    )

                    if run_cold_sets(
                        cold_restore_sets,
                        "restore",
                    ):
                        cold_record["status"] = "validated"
                        job.log(
                            "RUNTIME QUALIFICATION COLD BRACKET "
                            "PASSED: "
                            f"frames={cold_bracket_frames}; "
                            f"elapsed={elapsed_ms:.1f} ms; "
                            f"overhead={overhead_ms:.1f} ms; "
                            "first PHOTO of fresh session"
                        )

        attempt_record["elapsed_ms"] = (
            time.monotonic() - attempt_started
        ) * 1000.0
        attempt_record["restarted"] = restart
        attempts.append(attempt_record)

        job.checkpoint(
            runtime_v3_qualification={
                "status": (
                    "retrying"
                    if restart
                    else "validated"
                ),
                "attempts": deepcopy(attempts),
                "adjustments": deepcopy(adjustments),
                "runtime_set_samples_ms": list(
                    runtime_set_samples
                ),
                "runtime_single_overheads_ms": list(
                    runtime_single_overheads
                ),
                "runtime_bracket_overheads_ms_by_frames": {
                    str(frames): list(samples)
                    for frames, samples
                    in runtime_bracket_overheads.items()
                },
                "timing_contract": deepcopy(contract),
            }
        )

        if restart:
            continue

        break

    job.log(
        "RUNTIME QUALIFICATION V3 PASSED: "
        f"attempts={attempt}; "
        f"SET={contract['set_overhead_ms']} ms; "
        f"single overhead={contract['single_overhead_ms']} ms; "
        f"bracket overhead={contract['bracket_overhead_ms']} ms; "
        f"inter-image={contract['bracket_inter_image_ms']} ms"
    )

    return {
        "status": "validated",
        "attempts": attempts,
        "adjustments": adjustments,
        "runtime_set_samples_ms": runtime_set_samples,
        "runtime_single_overheads_ms": runtime_single_overheads,
        "runtime_bracket_overheads_ms_by_frames": {
            str(frames): samples
            for frames, samples
            in runtime_bracket_overheads.items()
        },
        "cold_bracket_first_frames": cold_bracket_frames,
        "cold_bracket_first": (
            attempts[-1].get("cold_bracket_first")
            if attempts
            else None
        ),
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
