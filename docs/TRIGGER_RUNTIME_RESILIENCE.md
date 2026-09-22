# Trigger runtime resilience contract

## START / preflight

Before a RIG enters timed capture, the characterized camera is connected and checked. Characterized commands carry independent `get` and `set` capabilities. The camera profile performs authoritative readback and sends SET only when a value must change.

## Live phase execution

The live eclipse trigger is driven by `scripts/eclipse_trigger.py` and `backend.phase_trigger.PhaseRuntime`. The current eclipse phase and configured photographic policy are the runtime authority. No intermediate execution-plan file is generated or consumed by the live trigger. PHOTO is never blindly replayed after an ambiguous transport failure because the shutter may already have fired.

## USB failure / battery replacement

The camera worker invalidates a stale USB handle on transport failure. A later operation can reconnect to the configured physical camera identity. Camera photographic settings are reconciled through the characterized profile before capture when required.

## Camera Validation

Camera Validation uses a relative in-memory diagnostic recipe. It preflights the real camera endpoint first, then dispatches SET/PHOTO operations through Camera IPC and the real camera worker from a monotonic anchor. Validation artifacts are the run log and JSON report; there is no intermediate scheduler file.
