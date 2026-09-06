# Local camera characterization — timing contract v3

This feature characterizes a camera locally through libgphoto2. It does not use
the network, an LLM, shell-generated code, or image downloads. Only understood
photographic controls are written.

## Persistent outputs

Two runtime files are published for a characterized model:

- `configs/camera_profiles/<model>.json`: validated commands, bracket modes and
  the selected strategy.
- `configs/camera_timing/<model>.json`: the compact timing contract consumed by
  the Sequencer.

The timing JSON is deliberately **not a measurement history**. For contract v3
it contains only the model identity and final guarded budgets:

- `set_overhead_ms`
- `single_overhead_ms`
- `bracket_overhead_ms`
- `bracket_inter_image_ms`
- `supported_bracket_frames`
- `safety_policy`

Raw trials and diagnostic breakdowns stay in
`configs/camera_characterization/measurements/<job_id>.json`. The completion
summary remains in `configs/camera_characterization/history.jsonl`.

## Safety policy

Every characterized overhead is converted to a runtime reservation with:

    budget = ceil50(max_observed_ms * 1.10 + 50 ms)

The order is significant: maximum observed, +10%, +50 ms, then round upward to
the next 50 ms multiple.

## SET timing

The characterization times the SET transactions that the generated plan can use:
ISO, shutter speed and capture/drive mode when available. Values are alternated so
a real transition is measured. The Sequencer uses **one common SET reservation**:

    set_overhead_ms = safe(max(all measured SET samples))

This intentionally trades a little cadence for a much simpler and deterministic
scheduler.

## Single PHOTO timing

A short known reference exposure is used only to isolate camera/USB overhead.
For every valid trigger method, five automatic trials are measured after one
operator-confirmed discovery trial.

For each trial:

    raw_single_overhead = total_capture_time - reference_exposure_time

The final runtime model is:

    T_single = exposure_time + single_overhead_ms

The user-selected exposure is therefore never extrapolated from a characterized
reference duration.

## Native bracket timing

Only bracket modes that are explicitly understood and validated are used. The
same trigger command must work for every discovered supported bracket size; methods
are never mixed across sizes.

For each validated bracket trial:

    raw_overhead_N = measured_total - sum(reference_exposures)

The maxima across 3/5/7/9-view sizes are decomposed into a fixed component and an
inter-image component. The generated runtime model is exactly:

    T_bracket =
        sum(exposure_times)
        + bracket_overhead_ms
        + (N - 1) * bracket_inter_image_ms

Both characterized components receive the same safety policy independently.

If only one bracket size is available, the inter-image contribution cannot be
identified independently. The observed overhead is therefore assigned to the fixed
component; the inter-image value starts from zero and still receives the normal
50 ms safety guard.

## Test-only two-second pause

After a characterization trial, the code waits for two continuous seconds without
USB events so delayed notifications cannot contaminate the next trial. This is
strictly a **test separation pause**:

- it is outside the measured PHOTO duration;
- it is absent from the compact timing JSON;
- it is absent from the generated `.plan`;
- it is never executed by Trigger.

File confirmation and any required shutter release remain part of the measured
capture operation.

## Sequencer and `.plan`

Contract v3 emits explicit physical SET commands rather than the old
`capture_setup` macro. Every SET receives `set_overhead_ms`.

PHOTO reservations are computed from the actual exposure list requested by the
Sequencer using the single or bracket formula above. The existing execution-plan
transport guard (`timing_contract_version=2`) is retained only as an envelope
understood by the current Trigger/runtime; `camera_timing_model_version=3` marks
the new camera timing model. No v2 reference-exposure arithmetic is used.

Each optimized group is self-contained (ISO, capture mode if needed, shutter,
bracket mode if needed, PHOTO). If a USB operation fails or a command is missed,
the next complete group is the recovery point; past photographs are never replayed.

Legacy Sony/Nikon timing files and contract-v2 profiles remain readable. They are
not silently converted. A camera must be recharacterized and its `.plan`
regenerated to use contract v3.

## Physical shutter latency

A Python/libgphoto2 stopwatch does not measure the physical instant at which the
shutter begins exposing. Contract v3 does not claim otherwise. Physical
shutter-start latency remains unmeasured and no correction is invented.

## Production validation

Software tests validate the model and scheduler, not the real camera cadence.
Before production use, run the full test suite on the development VM and perform
a complete Pi dry-run with the intended bodies, memory cards, USB topology,
exposure list and number of RIGs.
