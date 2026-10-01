"""Capture-probe execution used by camera characterization.

The method body intentionally mirrors the original in-function probe. It owns
only capture-event evidence and USB SET-ready measurement; orchestration stays
in camera_characterization.py.
"""
from __future__ import annotations

import time

from backend.camera_characterization_selection import _capture_validation_state
from backend.camera_characterization_single import (
    _operational_ready_timings,
    _settle_characterization_readback,
)
from backend.camera_characterization_types import Cancelled
from plugins.camera.profile import write_single_config


class CharacterizationCaptureProbe:
    def __init__(self, camera, job, gp, commands, runtime_ops):
        self.camera = camera
        self.job = job
        self.gp = gp
        self.commands = commands
        self.runtime_ops = runtime_ops
        self.validated_trials = set()

    def probe(
        self,
        spec,
        expected=1,
        exposure_s=0.002,
        ready_set=None,
    ):
        camera = self.camera
        job = self.job
        gp = self.gp
        commands = self.commands
        runtime_set = self.runtime_ops.set
        characterization_read = self.runtime_ops.read
        prime_runtime_spec = self.runtime_ops.prime_runtime_spec
        direct_nodes = self.runtime_ops.direct_nodes
        validated_trials = self.validated_trials
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
        successful_ready_started = None
        successful_ready_verified = None
        while True:
            job.check()
            ready_attempts += 1
            attempt_started = time.monotonic()
            try:
                ready_key, ready_value = ready_set or (
                    "iso",
                    commands["iso"]["values"]["100"],
                )
                runtime_set(ready_key, ready_value)
                # set_single_config() returning successfully is not sufficient
                # evidence that the camera actually applied the setting. Sony
                # bodies can transiently accept the USB transaction while still
                # keeping the previous drive mode after a bracket. Characterization
                # is allowed to perform an authoritative GET here; runtime is not.
                #
                # IMPORTANT: the GET is validation instrumentation only. It must
                # never be charged to the runtime tail. The operational tail ends
                # when the first SET that is later proven valid was *started*;
                # runtime performs that SET but does not perform this GET.
                actual_ready_value = _settle_characterization_readback(
                    lambda: characterization_read(ready_key),
                    ready_value,
                    check=job.check,
                )
                if str(actual_ready_value) != str(ready_value):
                    raise RuntimeError(
                        "SET returned without applying the requested value "
                        "after settled readback: "
                        f"{ready_key} requested={ready_value!r}, "
                        f"actual={actual_ready_value!r}"
                    )
                successful_ready_started = attempt_started
                successful_ready_verified = time.monotonic()
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
    
        assert successful_ready_started is not None
        assert successful_ready_verified is not None
    
        ready_timings = _operational_ready_timings(
            operation_begin,
            ready_origin,
            successful_ready_started,
            successful_ready_verified,
        )
        usb_return_ms = ready_timings["usb_return_ms"]
        # Runtime duration ends exactly where the next proven-valid SET may
        # begin. The SET itself is budgeted separately by set_overhead_ms.
        duration_ms = ready_timings["runtime_total_ms"]
        readiness_verification_ms = ready_timings["verification_ms"]
        characterization_wall_ms = ready_timings["wall_ms"]
    
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
            "usb_ready_verification_ms": readiness_verification_ms,
            "characterization_wall_ms": characterization_wall_ms,
            "settle_ms": 0.0,
            "test_pause_ms": 0.0,
            "total_ms": duration_ms,
        }
    
        job.log(
            f"AUTO CONFIRM: USB reported exactly {len(seen)}/{expected} "
            f"file(s); USB SET-ready runtime tail={usb_return_ms:.1f} ms "
            f"({ready_attempts} attempt(s)); "
            f"readback verification={readiness_verification_ms:.1f} ms"
        )
        job.log(
            f"TEST END {'discovery confirmed' if discovery else 'automatic timing'}: "
            f"{expected} photo(s), file_complete={file_complete_ms:.1f} ms, "
            f"runtime_ready_total={duration_ms:.1f} ms, "
            f"characterization_wall={characterization_wall_ms:.1f} ms"
        )
    
        return (
            returned_ms,
            duration_ms,
            "events",
            phases,
        )
    
    
