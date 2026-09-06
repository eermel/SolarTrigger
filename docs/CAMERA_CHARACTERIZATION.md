# Local camera characterization — implementation and validation status

This feature is experimental until checked on real USB cameras. It does not
claim universal interpretation of every libgphoto2 widget. No network connection,
LLM, shell command, photo download or generated Python is used on the appliance.

## Files and identity

* `configs/camera_profiles/*.json`: validated commands, model strategy and benchmark.
* `configs/camera_timing/*.json`: paired timing data consumed by the compiler.
* `configs/camera_characterization/history.jsonl`: completion summaries, including errors.

Model profiles are shared; physical workers keep their own USB handles and state.
Generated descriptors contain no serial number. File names include a model-name
hash to avoid slug collisions. Files are published exclusively, timing first and
profile last, without overwriting existing profiles. Invalid or ambiguous profiles
are not discovered. `var/` reset does not delete these business files. Debug logs
and pending confirmation are memory-only and disappear after a process restart.

## Operator flow

Devices → Refresh → select an uncharacterized camera → Characterize.
Photos remain on the card. RAW-only, manual exposure, ISO100, writable shutter and
card destination are mandatory. The operator is not required to install an electronic
lens. No focusing commands are sent. Battery/available aperture are optional.
If no file event proves capture, the browser asks whether the expected number of
RAW photos exists. Confirmation identifiers prevent stale answers. Cancel releases
a held shutter in a `finally` block when the underlying USB call returns; a blocked
native library call cannot be interrupted safely by a Python thread.

The endpoint denies Trigger start, device refresh/rebinding and persistent reset
while characterization runs. This is not a cross-process OS-wide USB lock: other
programs must not access the body during characterization.

## What is probed

All exposed widgets are enumerated and logged. Writes are restricted to understood
photographic settings; format-card, firmware and arbitrary writable options are never
tried. Setting choices and readback are checked after mode changes.

Current command rules cover common `expprogram/autoexposuremode/exposuremode`,
`capturetarget`, `imageformat/imagequality`, `iso/iso2`, `shutterspeed/shutterspeed2/exptime`
and `capturemode/drivemode` widgets. Not all vendor value encodings are supported.
Single capture candidates are `trigger_capture`, `capture(GP_CAPTURE_IMAGE)` and
exposed `capture/bulb` toggles. Every candidate is measured five times; the fastest
validated complete operation is selected, not merely the fastest API return.
Methods returning USB errors are rejected after asking the operator about any
photos that may nevertheless have been taken.

Current bracket discovery understands combined `Continuous Bracket 1 EV N Img.`
mode choices for 3/5/7/9 views. Cameras encoding bracket configuration across several
separate widgets are **not yet characterized for bracketing**. They can still obtain
a sequential profile if all mandatory functions validate. This limitation must not
be interpreted as proof that their hardware has no bracket capability.

Transaction ISO timing alternates ISO100 with an offered ISO above 100, then restores
ISO100. All test photos and strategy comparisons use ISO100. The present benchmark
requires a central 1/500 s and nine available shutter values spanning ±4 EV. An
unavailable benchmark range is an explicit failure, not an invented substitute.

## Planning and timing limitations

The data-driven planner uses dynamic programming to cover exact exposure lists
with valid 1-EV ISO-constant brackets and singles. It never brackets across an ISO
change. The compiler produces ordinary SET/PHOTO commands; the generic executor
owns the atomic bracket trigger/release protocol. Existing Sony/Nikon executors
and measured timings are retained for regression safety, discovered from Python
modules rather than a fixed class list. New characterized models use JSON only.

The benchmark compares five real sequential nine-photo runs against five runs of
an optimized bracket/single decomposition. Time spent awaiting human confirmation
is excluded. No lower ISO or downloaded image is used. Raw measurements use a
monotonic clock; medians feed scheduling. Extra exposure time is conservatively
added to measured capture duration when compiling other shutter settings.

**A Python USB stopwatch does not measure physical shutter-start latency.** New
timing files explicitly record `physical_latency_measured: false`; zero correction
is used, not a claimed measurement of zero lag. Existing measured camera timings
are never replaced. Contact-critical scheduling must be validated on real hardware.

For bodies without capture events, an operator-validated conservative delay is
stored. Runtime frame counts are then inferred, not observed, and reported as such.
This is slower and cannot detect a later physical capture failure by itself.

Independent application features (e.g. motion-exposure limits) may require camera
sensor dimensions/pixel pitch from the existing sensor database. USB characterization
does not invent those physical specifications or silently disable those features.

## Verification before production

1. Run pytest on the development VM (including Unix-socket tests).
2. On the Pi, test a disposable/available card with enough free space and power.
3. Confirm the selected body's serial in Devices; use no other USB controller app.
4. Test both automatic event confirmation and operator confirmation/cancel.
5. Compare RAW photo counts/settings on the card against logs and generated .plan.
6. Reboot, Refresh, verify model sharing across two distinct physical bodies.
7. Verify retained profiles/history after the existing destructive reset only on
   an explicitly disposable test installation.

No hardware validation or production deployment was performed during implementation.
