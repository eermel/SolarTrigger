# Trigger runtime resilience contract

## START / preflight

Before the timed execution plan starts, each characterized camera is connected
and checked. Characterized commands carry independent `get` and `set`
capabilities. Trigger performs GET first and sends SET only when the value must
change. A GET-only invariant is valid when already correct. If it is wrong,
START fails with an operator-actionable message. Example: a Sony A6600 whose
exposure-mode dial is not `M` reports `Mettre le Sony A6600 en mode manuel (M).`
The timed RIG scheduler is not started.

## Absolute execution

The `.plan` UTC timeline is authoritative. Commands whose timestamp is past are
permanently discarded. PHOTO is never retried or replayed. A late START reduces
past SET history to one effective desired state and preflights that state once;
it does not replay command history.

## USB failure / battery replacement

A camera exception is operation-scoped and never terminates the RIG scheduler.
The camera worker invalidates only the stale USB handle. The next GET/SET/PHOTO
reconnects automatically to the configured physical camera identity/serial.
Camera photographic settings are deliberately considered persistent across a
normal battery replacement.

If a SET fails, the result may be ambiguous. Runtime may reconcile it ASAP:
first GET the parameter; if the body already holds the desired value, no SET is
resent. Otherwise retry the latest desired SET. A newer SET for the same
parameter supersedes the older failed request. If recovery consumes the target
slot, the PHOTO is lost rather than fired late.

## Stateful plan reduction

Characterized `profile-*` backends participate in compile-time state reduction.
Stable ISO/capture-mode/shutter/aperture values are not resent for every capture.
The Sony/profile bracket centre-shutter transaction remains protected where the
physical bracket protocol requires it.
