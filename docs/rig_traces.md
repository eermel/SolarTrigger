# RIG trace log

The RIG trace log is intended for offline diagnosis of camera and motion-control
operations. It is stored at `rig_traces.jsonl` in the repository/application
root (for example, `<PROJECT_REPO_ABSOLUTE>/rig_traces.jsonl`). The file is
append-only; each line is one independent JSON object (JSONL), so a partial or
malformed final line can be discarded without losing earlier events.

The log may contain camera and device identity or error text. Treat it as
operational data and do not add credentials, IPC session IDs, prepared-capture
tokens, or other secrets. In particular, `token_id` and `session_id` are not
written by the prepared-capture tracer.

## Reading the file

Examples of useful offline commands are:

```sh
# Pretty-print every complete record
jq -c . rig_traces.jsonl

# Select failures for RIG 2
jq -c 'select(.rig_id == 2 and .status != "success")' rig_traces.jsonl

# Select camera execution events in chronological file order
jq -c 'select(.kind == "camera.trigger_prepared" or .kind == "camera.shoot_speed_list")' rig_traces.jsonl
```

Writers in one process serialize their appends, and every completed line is a
complete JSON record. File order is append order, not a guaranteed global time
order across processes; use `start_utc` when correlating concurrent work.

## Common fields

Fields are omitted when they are unavailable unless noted otherwise.

| Field | Meaning |
| --- | --- |
| `kind` | Event schema/discriminator, such as `camera.trigger_prepared`, `camera.shoot_speed_list`, `camera.read_info`, `camera.test_photo`, `focuser.stop`, or `mount.stop`. |
| `rig_id` | Integer RIG attribution. See the legacy caveat below. |
| `timestamp` | UTC ISO-8601 time at which `backend.rig_trace` appended the record. Present on trigger, speed-list, and STOP records. The manual camera-action writer does not currently add it; use `start_utc`/`end_utc` there. |
| `start_utc` | A timezone-aware ISO-8601 UTC wall-clock time sampled immediately before the traced operation. |
| `end_utc` | A timezone-aware ISO-8601 UTC wall-clock time sampled immediately after it returns or raises. |
| `duration_ms` | `(end_utc - start_utc)` in milliseconds. This is observed wall-clock elapsed time, not exposure time. |
| `status` | `success`, `error`, or (for camera execution only) `expired`. |
| `code` | Stable error code when one is available, for example `EXPIRED`, `CAMERA_BUSY`, `CAMERA_UNAVAILABLE`, or `DEVICE_NOT_CONFIGURED`. |
| `message` | Sanitized error description used by camera execution and STOP events. Manual camera actions currently use `error` for their error text instead. |

All times are wall-clock observations and can be affected by a system-clock
adjustment. Numeric durations and latencies are floating-point milliseconds.

## OP-001 camera timing

`camera.trigger_prepared` and `camera.shoot_speed_list` use these definitions:

- `start_utc`: the time immediately before dispatch to the per-RIG camera
  worker.
- `end_utc`: the time immediately after the worker succeeds or fails.
- `duration_ms = (end_utc - start_utc) * 1000`.
- `target_time`: the logical intended capture time retained from
  `prepare_capture`. It is an ISO-8601 value for `trigger_prepared`; the legacy
  speed-list path sets it to `null`.
- `latency_ms = (start_utc - target_time) * 1000`. Positive values mean worker
  dispatch started late, negative values mean it started early, and zero means
  the sampled instants coincide. It is `null` when there is no target time.
- `deadline`: the optional UTC deadline supplied to the execution request. It
  is recorded only when explicitly supplied; it is not the same as
  `target_time` and is not used to calculate `latency_ms`.

The timing window surrounds worker dispatch. It does not measure the earlier
preparation request, queuing before the IPC handler starts, or later response
transport.

## Camera execution events

### `camera.trigger_prepared`

This event combines the retained logical preparation context with the outcome
of consuming the prepared token:

- `phase`, `target_time`, and `request_id` identify the logical capture;
- `exposures_s`, `planned_count`, and `plugin_name` describe the prepared plan;
- `plan_version` identifies the plan-policy version;
- `iso_applied`, `corrections`, and `warnings` contain optional policy
  augmentation (and may be `null`);
- `deadline` appears only when the trigger request supplied one;
- `frames` and `planned` are copied from a successful worker result when
  returned.

Prepared-token and IPC-session values are deliberately excluded. A success
record can look like:

```json
{"rig_id":3,"phase":"C2","target_time":"2026-08-12T17:59:59.900000","request_id":"capture-42","exposures_s":[0.25,0.5],"planned_count":2,"plugin_name":"sony","iso_applied":null,"corrections":null,"warnings":null,"plan_version":"v1","start_utc":"2026-08-12T18:00:00+00:00","end_utc":"2026-08-12T18:00:00.005000+00:00","duration_ms":5.0,"latency_ms":100.0,"status":"success","frames":2,"planned":2,"kind":"camera.trigger_prepared","timestamp":"2026-08-12T18:00:00.006000+00:00"}
```

### `camera.shoot_speed_list`

This is the legacy direct speed-list execution path. `speeds` is the ordered
list of shutter-speed strings and `photo_num_start` is the starting photo
number. `phase` and `target_time` are currently `null`, so `latency_ms` is also
`null`. `deadline` is present only when supplied. Successful results may add
`frames` and `planned`.

```json
{"rig_id":3,"speeds":["1/1000","1/500"],"photo_num_start":7,"phase":null,"target_time":null,"deadline":"2026-08-12T18:00:01","start_utc":"2026-08-12T18:00:00+00:00","end_utc":"2026-08-12T18:00:00.005000+00:00","duration_ms":5.0,"latency_ms":null,"status":"success","frames":2,"planned":2,"kind":"camera.shoot_speed_list","timestamp":"2026-08-12T18:00:00.006000+00:00"}
```

### Expired versus error

For the two camera execution kinds, `status: "expired"` is reserved for
`code: "EXPIRED"`: the worker job missed its deadline and was rejected rather
than completing normally. It is operationally distinct from
`status: "error"`, which means dispatch or camera execution failed for another
reason. Known IPC errors preserve their public `code` and `message`; unexpected
exceptions are recorded as `INTERNAL_ERROR` with the sanitized message
`camera operation failed`.

```json
{"rig_id":3,"speeds":["1/250"],"photo_num_start":4,"phase":null,"target_time":null,"start_utc":"2026-08-12T18:00:00+00:00","end_utc":"2026-08-12T18:00:00.005000+00:00","duration_ms":5.0,"latency_ms":null,"status":"expired","code":"EXPIRED","message":"camera worker job expired","kind":"camera.shoot_speed_list","timestamp":"2026-08-12T18:00:00.006000+00:00"}
```

Do not infer whether any frames were produced from `expired` or `error` alone;
use camera-side evidence when partial execution matters.

## Manual camera actions

`camera.read_info` traces an operator-requested camera information read.
`camera.test_photo` traces an operator-requested diagnostic exposure. Both may
include the configured identity fields `serial` and
`fallback_physical_path`. Successful reads may add `model` and `battery`;
successful test photos may add `frames`, `planned`, and `detail`.

Manual-action errors use `status: "error"` and an `error` string. Busy errors
also carry `code: "CAMERA_BUSY"`; test-photo availability failures carry
`code: "CAMERA_UNAVAILABLE"`. These records currently have no append-time
`timestamp` field.

```json
{"rig_id":1,"serial":"CAMERA-1","start_utc":"2026-08-12T17:55:00+00:00","end_utc":"2026-08-12T17:55:00.020000+00:00","duration_ms":20.0,"status":"success","model":"Sony ILCE-7M5","battery":"81%","kind":"camera.read_info"}
{"rig_id":1,"serial":"CAMERA-1","start_utc":"2026-08-12T17:56:00+00:00","end_utc":"2026-08-12T17:56:00.130000+00:00","duration_ms":130.0,"status":"success","frames":1,"planned":1,"detail":"single","kind":"camera.test_photo"}
```

## Focuser and mount STOP events

`focuser.stop` covers both focuser STOP and jog STOP routes. `mount.stop`
covers both slew STOP and tracking STOP routes. The fields `device_type`
(`focuser` or `mount`) and `action: "stop"` identify this shared schema. An HTTP
or device failure has `status: "error"` plus `code` and `message`.

```json
{"rig_id":2,"device_type":"focuser","action":"stop","start_utc":"2026-08-12T18:01:00+00:00","end_utc":"2026-08-12T18:01:00.003000+00:00","duration_ms":3.0,"status":"success","kind":"focuser.stop","timestamp":"2026-08-12T18:01:00.004000+00:00"}
{"rig_id":2,"device_type":"mount","action":"stop","start_utc":"2026-08-12T18:02:00+00:00","end_utc":"2026-08-12T18:02:00.002000+00:00","duration_ms":2.0,"status":"error","code":"DEVICE_NOT_CONFIGURED","message":"mount is not configured for rig 2","kind":"mount.stop","timestamp":"2026-08-12T18:02:00.003000+00:00"}
```

## Legacy unscoped routes

The old mono-RIG focuser and mount STOP endpoints do not carry a RIG identifier
in their URL. Their trace records are intentionally attributed to `rig_id: 1`.
This is a compatibility convention, not proof that a caller explicitly chose
RIG 1. Prefer the `/api/rigs/<rig_id>/...` endpoints and treat `rig_id: 1`
STOP records as potentially legacy/unscoped during offline analysis.
