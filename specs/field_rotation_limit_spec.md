# Field-rotation exposure limit

## Configuration

Each rig may define `rigs[i].photo.field_rotation_radius_deg`. The value is
the angular distance, in degrees, from the optical axis to the image location
at which field-rotation motion is limited. It must be in the range
`0 <= field_rotation_radius_deg < 90`.

The calculation also uses:

- `rigs[i].optics.focal_length_mm` (`f_mm`), the focal length in millimetres;
- the resolved camera sensor's `pixel_pitch_um` (`p_um`), in micrometres per
  pixel; and
- `rigs[i].photo.motion_tolerance_px` (`d_px`), the permitted motion in pixels.

For configured radius `r_deg`, the projected radius on the sensor and its
conversion to pixels are:

```text
r_mm = f_mm * tan(radians(r_deg))
r_px = r_mm * 1000 / p_um
```

## Exposure ceiling

At the capture intent's target instant, the system computes the Sun's apparent
right ascension and declination and the Greenwich sidereal angle. Together
with the configured reference-site longitude, these give the local hour angle.
The reference-site latitude, solar declination, and local hour angle determine
the instantaneous field-rotation rate `omega_deg_s` in degrees per second.

The exposure ceiling is:

```text
omega_rad_s = abs(omega_deg_s) * pi / 180
t_max_s = d_px / (omega_rad_s * r_px)
```

Thus, `t_max_s` is the maximum exposure duration for which tangential motion at
the configured field radius remains within `motion_tolerance_px`. A zero field
radius or zero instantaneous rotation rate imposes no field-rotation ceiling.

## Applicability and deterministic inputs

The field-rotation limit is selected only when anti-trailing is enabled and the
rig has an alt-az mount whose tracking mode is `solar` or `sidereal`. It is not
selected for an equatorial mount, tracking-off mount, or an unrecognized mount
configuration.

Geometry is evaluated from `eclipse.reference_site.lat` and
`eclipse.reference_site.lon` in the rig policy snapshot. The calculation does
not substitute a live GPS position. Solar coordinates and sidereal angle are
derived from the capture intent's `target_time`, normalized to UTC; therefore,
identical policy snapshots and UTC target instants produce deterministic
inputs to the field-rotation calculation.
