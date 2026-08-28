# Camera sensor database schema v1

The camera sensor database is a UTF-8 JSON file used by
`backend.sensor_db`. The reserved project data path is:

```text
data/camera_sensors/camera_sensors.v1.json
```

Callers pass the path explicitly to `load_sensor_db`; the loader does not
select this path automatically.

## File format

The top-level value is an object with these fields:

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `schema_version` | integer | yes | Must be exactly `1`. |
| `sensors` | array of sensor objects | yes | May be empty. |

Only schema version 1 is supported. A missing version, a boolean, or any value
other than the integer `1` is rejected.

Each object in `sensors` has these fields:

| Field | Type | Required | Description |
| --- | --- | --- | --- |
| `manufacturer` | non-empty string | yes | Sensor or camera manufacturer. Surrounding whitespace is removed when loaded. |
| `model` | non-empty string | yes | Canonical model name, unique for the manufacturer ignoring case. Surrounding whitespace is removed when loaded. |
| `sensor_width_mm` | positive finite number | yes | Physical sensor width in millimetres. |
| `sensor_height_mm` | positive finite number | yes | Physical sensor height in millimetres. |
| `width_px` | positive integer | yes | Horizontal pixel count. Booleans are not accepted as integers. |
| `height_px` | positive integer | yes | Vertical pixel count. Booleans are not accepted as integers. |
| `sources` | non-empty array of non-empty strings | yes | Provenance for the measurements. Surrounding whitespace is removed when loaded. |
| `aliases` | array of non-empty strings | no | Alternative model names for the same manufacturer; defaults to an empty array. Names must be unambiguous and unique ignoring case. |
| `pixel_pitch_um` | positive finite number | no | Pixel pitch in micrometres. `null` is treated as omitted. |
| `camera_type` | non-empty string | no | Camera category: `dslr`, `mirrorless`, or `astronomy` (case-insensitive on input). It is stored in lowercase after loading; an omitted value is normalized to `None`. |

Manufacturer and model/alias pairs identify entries. Canonical names must not
be duplicated, and aliases must not duplicate a canonical name, another alias,
or a name claimed by another entry for the same manufacturer.

When `pixel_pitch_um` is omitted or `null`, the loader derives it from the
physical width and horizontal pixel count only:

```text
pixel_pitch_um = sensor_width_mm * 1000 / width_px
```

The height fields are validated but are not used to derive pixel pitch. If an
explicit pitch is present, it is validated and retained rather than
recalculated.

## Valid entry example

```json
{
  "schema_version": 1,
  "sensors": [
    {
      "manufacturer": "Nikon",
      "model": "D850",
      "camera_type": "dslr",
      "aliases": ["Nikon D850", "D850 Body"],
      "sensor_width_mm": 35.9,
      "sensor_height_mm": 23.9,
      "width_px": 8256,
      "height_px": 5504,
      "sources": ["manufacturer specification"]
    }
  ]
}
```

## Loader and manual fallback

`load_sensor_db` validates the complete file. It raises `ValueError` for an
unreadable file, invalid JSON, an unsupported schema version, or invalid or
ambiguous entries. `lookup_model` accepts a canonical model or alias; matching
ignores surrounding whitespace and letter case, and an unknown model raises
`KeyError`.

`resolve_sensor_entry` is the camera-facing resolution API. It delegates to
`lookup_model`, so it accepts either a canonical model or an alias and returns
the same normalized entry copy. It also propagates `KeyError` when no entry
matches.

Use `make_manual_entry` when a lookup fails and the caller has trustworthy
sensor dimensions. It applies the same entry validation and normalization,
sets `sources` to `["manual"]`, and derives pitch with the same width-based
rule unless `pixel_pitch_um` is supplied explicitly.

```python
from backend.camera_model_resolution import resolve_sensor_entry
from backend.sensor_db import load_sensor_db, make_manual_entry

# The reserved path is supplied explicitly by the caller.
db = load_sensor_db("data/camera_sensors/camera_sensors.v1.json")

try:
    sensor = resolve_sensor_entry("Unknown", "Custom", db)
except KeyError:
    sensor = make_manual_entry(
        manufacturer="Unknown",
        model="Custom",
        sensor_width_mm=36.0,
        sensor_height_mm=24.0,
        width_px=6000,
        height_px=4000,
    )

# sensor["pixel_pitch_um"] == 6.0
# sensor["sources"] == ["manual"]
```
