#!/usr/bin/env python3
"""Apply the profile-only characterization migration to a clean checkout.

Kept as a guarded migration because backend/camera_characterization.py is large;
the guards deliberately fail instead of applying against an unexpected source.
"""
from pathlib import Path
import sys

repo = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
path = repo / "backend/camera_characterization.py"
text = path.read_text(encoding="utf-8")

old = '        "timing_contract",\n    ):\n'
new = '        "timing_contract",\n        "selection",\n    ):\n'
if old not in text:
    raise SystemExit("Patch guard failed: persistent profile fields changed")
text = text.replace(old, new, 1)

start = text.index("    def find_setting(")
end = text.index("    model_for_operator =", start)
replacement = '''    selection_evidence = {}

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

        def write_and_confirm(path, value):
            started = time.monotonic()
            write_widget(camera, path, value)
            deadline = time.monotonic() + 5.0
            for attempt in range(20):
                job.check()
                actual = read_value(path)
                if str(actual) == str(value):
                    return (time.monotonic() - started) * 1000.0
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
                    alternates = [v for v in values if str(v) != str(target)]
                    if not alternates:
                        ev.failures.append("no alternate value to prove SET")
                        continue
                    for trial in range(5):
                        alternate = alternates[trial % len(alternates)]
                        try:
                            write_and_confirm(path, alternate)
                            ev.durations_ms.append(write_and_confirm(path, target))
                        except Exception as exc:
                            ev.failures.append(f"trial {trial+1}: {exc}")
                            errors.append(f"SET {cid} trial {trial+1}: {exc}")
                            try:
                                if str(read_value(path)) != str(target):
                                    write_and_confirm(path, target)
                            except Exception as restore_exc:
                                raise RuntimeError(
                                    f"Cannot restore {path}={target!r}: {restore_exc}"
                                ) from restore_exc
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
                commands[key] = {"path": candidate["path"], "value": target,
                                 "get": True, "set": not candidate["readonly"]}
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

'''
text = text[:start] + replacement + text[end:]

old_loop = '''    for key, names in (
        ("self_timer", ("selftimer", "selftimerdelay")),
        ("time_lapse", ("intervalshooting", "timelapse")),
    ):
'''
new_loop = '''    for key, names in (
        ("self_timer", ("selftimer", "selftimerdelay")),
    ):
'''
if old_loop not in text:
    raise SystemExit("Patch guard failed: self_timer/time_lapse block changed")
text = text.replace(old_loop, new_loop, 1)

needle = '        "brackets": {},\n    }\n'
repl = '        "brackets": {},\n        "selection": deepcopy(selection_evidence),\n    }\n'
if needle not in text:
    raise SystemExit("Patch guard failed: profile construction changed")
text = text.replace(needle, repl, 1)
path.write_text(text, encoding="utf-8")
print(path)
