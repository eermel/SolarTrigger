"""Operational qualification for characterized camera timing contracts.

The routines in this module execute the final runtime-equivalent qualification.
They receive all mutable characterization state explicitly and do not own
publication, process supervision, or device discovery.
"""
from __future__ import annotations

from copy import deepcopy
import time

from backend.camera_characterization_types import Cancelled
from plugins.camera.profile import ProfilePlugin


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
    *,
    profile_plugin_cls=ProfilePlugin,
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
        plugin = profile_plugin_cls(camera, job.log, profile=profile)
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
        f"single rearm={contract.get('single_rearm_ms', 'n/a')} ms; "
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



def qualify_operational_contract(
    camera,
    profile,
    timing,
    set_samples,
    setup_samples,
    job,
    *,
    profile_plugin_cls=ProfilePlugin,
):
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
    plugin = profile_plugin_cls(camera, job.log, profile=profile)
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
    estimated = profile_plugin_cls(None, profile=profile).prepare_capture(SimpleNamespace(exposure_plan=exposures))
    profile["benchmark"]["guarded_optimized_ms"] = round(estimated.estimated_total_s * 1000)
    profile["benchmark"]["guarded_sequential_ms"] = sum(
        contract["iso_ms"] + contract["single"]["setup_ms"] +
        photo_budget_ms(contract["single"], _parse_speed(v["shutter"])) for v in exposures)
    profile["strategy"] = "bracket" if any(o["action"] == "bracket_press" for o in estimated.token[1]) else "sequential"
    # Raw estimates remain diagnostics. Runtime uses the explicit contract only.
    job.log(f"BUDGET POLICY: maximum observed + 10% + 50 ms, rounded upwards to 50 ms")
