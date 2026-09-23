# OnStep direct backend assessment

## Current integration

SolarTrigger already contains a direct OnStep/LX200 backend:

- `plugins/mount/onstep.py`: low-level serial protocol driver.
- `plugins/mount/onstep_plugin.py`: `MountPlugin` adapter.
- `plugins/mount/__init__.py`: the `onstep` backend is registered.
- `backend/mount_worker_runtime.py`: worker bindings accept every pilotable
  backend except `none` and `external`; there is no INDI-only restriction.

The direct backend is therefore already usable by the multi-RIG runtime when a
RIG is bound to `backend: onstep`.

## SolarTrigger feature coverage

The direct adapter currently provides:

- connection / disconnect / ping;
- live status;
- tracking start / stop;
- solar and sidereal tracking-mode selection;
- manual N/S/E/W movement;
- discrete slew rates;
- stop / emergency stop through the low-level driver;
- Home return and GPS-assisted recentering.

The low-level driver also knows the lunar rate, but the plugin deliberately
advertises only solar and sidereal mode selection today.

The low-level driver intentionally does not implement GoTo. SolarTrigger does
not need GoTo for the existing eclipse manual-control workflow.

## Identity

OnStep exposes product and firmware strings through the LX200-compatible
`:GVP#` and `:GVN#` queries. Device discovery now uses the product string as
the displayed model when available.

The protocol used by this driver does not currently expose a trustworthy mount
serial number. SolarTrigger therefore keeps the stable
`/dev/serial/by-id/...` path as the physical binding identity and does **not**
present the USB adapter identity as a mount serial number.

## Direct backend versus INDI

For an OnStep mount dedicated to SolarTrigger, the direct backend removes the
INDI server/driver layer from the command path and avoids depending on INDI
property naming for the supported operations. That makes it a good candidate
for the preferred OnStep backend.

It must **not** be used concurrently with an INDI driver connected to the same
serial controller. Only one backend may own the serial port.

INDI remains useful for other mount families and as a compatibility backend.

## Required hardware qualification before changing the default

Do not automatically migrate existing OnStep bindings yet. On the actual Pi and
mount, qualify the direct backend for:

1. repeated connect/disconnect and status reads;
2. every manual direction and every advertised slew rate;
3. STOP while moving;
4. solar tracking start/stop and mode restoration;
5. Home from several axis positions, including cancellation;
6. recovery after USB disconnect/reconnect;
7. latency comparison with the INDI path.

After those checks pass, changing an OnStep RIG from `backend: indi` to
`backend: onstep` can be done without changing the mount worker architecture.
