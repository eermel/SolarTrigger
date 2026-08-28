# DSLR vibration capability policy

This specification defines a capability-driven input for future DSLR vibration
policy. It documents discovery only: it does not enable vibration mitigation,
change capture timing, or add phase-sequencing behavior.

## Separation from motion policies

Camera-body vibration is independent of the exposure constraints imposed by
`solar_trailing` and `field_rotation`. Those policies determine motion-related
exposure limits; they must not be used to infer camera mechanisms or select a
vibration-control action. Conversely, a reported vibration capability does not
alter either motion policy or its calculations.

A consumer must therefore treat these as separate inputs:

- the configured motion policy (`solar_trailing` or `field_rotation`); and
- the camera category and capabilities returned by `camera.capabilities`.

## Camera category

`camera_type` comes from the Sensor DB entry for the configured camera. The
defined categories are `dslr`, `mirrorless`, and `astronomy`; an entry may also
omit the category. Matching uses the configured manufacturer with the model or
alias. See [Camera sensor database schema v1](sensor_db.md), including its
normalization and lookup rules.

Category is descriptive, not a substitute for a capability report. In
particular, `dslr` alone does not prove that a mirror lock, electronic
front-curtain shutter, sensor stabilization control, or any other mechanism is
available.

## Plugin-reported capabilities

The connected camera plugin is the authority for controls it can actually
provide. It reports vibration capabilities as an object whose property names
identify plugin-supported mechanisms. Boolean properties state whether the
named mechanism is controllable by that plugin. For example:

```json
{
  "mirror_lockup": true,
  "electronic_front_curtain": false
}
```

Capability names are extensible rather than inferred from manufacturer, model,
or category. Consumers must ignore names they do not understand and must not
treat an unreported name as supported. The backend passes the plugin report
through as `vibration_caps`; this operation does not activate any reported
mechanism.

## Neutral behavior

Discovery is fail-neutral:

- `vibration_caps: {}` means the connected plugin reports no controllable
  vibration-mitigation capability.
- `vibration_caps: null` means no capability report is available, for example
  when the worker has no capability getter. It must not be interpreted as
  support.
- `camera_type: null` means the configured camera could not be categorized from
  the Sensor DB. It does not suppress an otherwise available plugin report.
- For `mirrorless` and `astronomy` cameras, consumers apply no DSLR-specific
  action merely because of the category. Only an explicit capability can make
  a control eligible for a later policy decision.

Thus missing data, an empty report, an unknown camera, and a mirrorless or
astronomy category all preserve existing capture behavior. No fallback should
guess a capability or introduce a delay.

## `camera.capabilities` IPC operation

The backend camera IPC accepts one newline-terminated JSON request per Unix
socket connection. The request schema is:

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `operation` | string | yes | Must be `camera.capabilities`. |
| `params` | object | yes | Contains only `rig_id`. |
| `params.rig_id` | integer | yes | Existing configured camera RIG identifier. |
| `session_id` | non-empty string | no | Active IPC session identifier when session validation is in use. |

Request example:

```json
{"operation":"camera.capabilities","params":{"rig_id":1},"session_id":"active-session"}
```

A successful socket response uses the standard IPC envelope. Its `result`
contains the requested RIG, the normalized Sensor DB category (or `null`), and
the plugin report (or `null`):

```json
{
  "ok": true,
  "result": {
    "rig_id": 1,
    "camera_type": "dslr",
    "vibration_caps": {
      "mirror_lockup": true,
      "electronic_front_curtain": false
    }
  }
}
```

Neutral mirrorless example:

```json
{
  "ok": true,
  "result": {
    "rig_id": 2,
    "camera_type": "mirrorless",
    "vibration_caps": {}
  }
}
```

Unknown Sensor DB entry with an independent plugin report:

```json
{
  "ok": true,
  "result": {
    "rig_id": 3,
    "camera_type": null,
    "vibration_caps": {"electronic_front_curtain": true}
  }
}
```

Invalid, unknown, or unavailable RIGs retain the existing camera IPC error
semantics. Errors use `{"ok":false,"error":{"code":"...","message":"..."}}`;
the operation does not define new error codes.
