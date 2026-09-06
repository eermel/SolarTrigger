# Camera characterization review — contract v3

This review supersedes the contract-v2 timing/qualification notes for newly
characterized cameras.

## Decisions now implemented

1. The final timing JSON is an operational configuration, not a history. Raw
   observations stay in the characterization measurement checkpoint.
2. One guarded value is used for every runtime SET.
3. A single PHOTO is modeled as exposure time plus a fixed characterized overhead.
4. A native bracket is modeled as the sum of requested exposure times plus a fixed
   overhead plus one inter-image overhead for each of the `N-1` gaps.
5. The safety rule is applied to every characterized overhead exactly as:
   `ceil50(max_observed * 1.10 + 50 ms)`.
6. The two-second quiet period remains only between characterization trials and is
   excluded from all runtime reservations.
7. The Sequencer receives explicit SET/PHOTO durations derived from the new model;
   it no longer needs the contract-v2 reference-exposure extrapolation or
   `capture_setup` reservation for v3 profiles.
8. Existing v2/legacy profiles remain readable during migration.

## What remains intentionally unchanged

- Command discovery stays conservative and only writes recognized photographic
  controls.
- RAW, manual exposure, ISO100 baseline, card destination and writable shutter
  remain required.
- Operator confirmation is used for discovery; automatic timing trials follow.
- Native bracket methods must be validated with the expected file count.
- The same native bracket trigger method must cover all discovered bracket sizes.
- Physical shutter-start latency is still not measured.
- A runtime failure does not cause old photos to be replayed; later complete groups
  provide deterministic recovery.

## Validation boundary

The software can validate arithmetic, serialization, planner coverage and recovery
semantics without a camera. It cannot prove the real USB/card cadence. After this
patch the complete pytest suite must run on the VM, followed by a real
recharacterization and a complete Pi dry-run before merging to production.
