"""Safe camera-setting discovery and direct-writer qualification."""
from __future__ import annotations

import time

from plugins.camera.profile import prime_single_config, write_single_config


def find_setting(
    camera,
    job,
    initial,
    commands,
    warnings,
    selection_evidence,
    key,
    names,
    accept,
    critical=True,
    operator_instruction=None,
    require_set=False,
):
    """Qualify every safe candidate; select reliability first, speed second."""
    from backend.camera_candidate_optimizer import (
        CandidateEvidence, compact_selection, select_best,
    )
    errors, evidence, candidate_by_id = [], [], {}

    def read_value(candidate):
        name = candidate["config_name"]
        getter = getattr(camera, "get_single_config", None)
        if not callable(getter):
            raise RuntimeError("gphoto2 get_single_config is unavailable")
        job.checkpoint(
            phase="setting_read_single_config",
            setting=key,
            path=candidate["path"],
        )
        return getter(name).get_value()

    def direct_spec(candidate):
        return {
            "path": candidate["path"],
            "name": candidate["config_name"],
            "writer": "single_config",
        }

    def write_and_confirm(candidate, node, value):
        # Persist the exact native operation before entering libgphoto2.
        # If the isolated worker crashes, the parent-side checkpoint still
        # identifies the setting/primitive that was in flight.
        job.checkpoint(
            phase="setting_set_single_config",
            setting=key,
            path=candidate["path"],
            target=str(value),
        )
        # Timed portion is SET-only. Readback happens afterwards and is
        # characterization evidence, never part of the runtime path.
        started = time.monotonic()
        write_single_config(camera, direct_spec(candidate), node, value)
        elapsed_ms = (time.monotonic() - started) * 1000.0
        deadline = time.monotonic() + 5.0
        for attempt in range(20):
            job.check()
            actual = read_value(candidate)
            if str(actual) == str(value):
                return elapsed_ms
            if time.monotonic() >= deadline or attempt == 19:
                raise RuntimeError(
                    f"readback mismatch: requested={value!r}, actual={actual!r}"
                )
            time.sleep(0.25)
        raise AssertionError("unreachable")

    for operator_pass in range(2):
        # Reuse the initial discovery tree. Rebuilding the complete gphoto2
        # configuration tree between individual SETs is both unnecessary
        # and unsafe on cameras whose native driver invalidates parts of
        # that tree after a setting change. Only an operator intervention
        # justifies refreshing discovery on the second pass.
        source = initial if operator_pass == 0 else enumerate_widgets(camera)
        candidates = [item for item in source if item["name"] in names]
        for candidate in candidates:
            path = candidate["path"]
            try:
                original = read_value(candidate)
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
                if candidate["readonly"] and not require_set:
                    ev.functional_ok = True
                    ev.expected_trials = 1
                    ev.durations_ms.append(0.0)
                    job.log(f"CANDIDATE {key}: {cid} qualified GET-only")
                    continue
                # Some PTP drivers (notably Sony) expose a setting as
                # readonly in the full configuration tree while
                # get_single_config/set_single_config can still write it.
                # For require_set actions, prove the production primitive
                # instead of trusting that advisory metadata.
                # Qualify the exact production primitive:
                # get_single_config is done once before timing, then each
                # trial is one set_single_config followed by an untimed
                # readback. New runtime profiles therefore never need a
                # configuration GET to perform a SET.
                try:
                    job.checkpoint(
                        phase="setting_get_single_config",
                        setting=key,
                        path=candidate["path"],
                        target=str(target),
                    )
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
                                    and str(read_value(candidate)) == str(target))
                job.log(f"CANDIDATE {key}: {cid} reliable={ev.reliable} "
                        f"trials={len(ev.durations_ms)}/5 "
                        f"peak_ms={ev.peak_ms if ev.durations_ms else None}")
        writable = []
        for ev in evidence:
            candidate = candidate_by_id[ev.candidate_id][0]
            # A reliable multi-trial result proves the direct writer even
            # when the full-tree widget advertised readonly=True.
            direct_set_proven = ev.reliable and ev.expected_trials > 1
            if direct_set_proven:
                writable.append(ev)
        selectable = writable or ([ev for ev in evidence if ev.reliable]
                                  if not require_set else [])
        if selectable:
            selected = select_best(selectable)
            candidate, target, selected_ev = candidate_by_id[selected.candidate_id]
            direct_set_proven = (
                selected_ev.reliable and selected_ev.expected_trials > 1
            )
            commands[key] = {
                "path": candidate["path"],
                "name": candidate["config_name"],
                "value": target,
                "get": True,
                "set": direct_set_proven,
            }
            if direct_set_proven:
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

