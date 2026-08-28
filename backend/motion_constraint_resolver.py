"""Resolve the motion constraint implied by a rig policy snapshot."""

from __future__ import annotations


def resolve_motion_constraint(policy: dict) -> str:
    """Return the exposure motion constraint for *policy*.

    Missing mount information falls back to ``"fixed_trailing"`` because the
    system cannot establish that tracking is available.  When a mount is
    present but its geometry or tracking mode is incomplete or unknown, the
    conservative result is ``"none"``: no geometry-specific constraint is
    selected without a recognized combination.
    """

    photo = policy.get("photo")
    if not isinstance(photo, dict) or photo.get("anti_trailing_enabled") is not True:
        return "none"

    devices = policy.get("devices")
    mount = devices.get("mount") if isinstance(devices, dict) else None
    if not isinstance(mount, dict) or mount.get("control") == "none":
        return "fixed_trailing"

    tracking = mount.get("tracking")
    if tracking == "off":
        return "fixed_trailing"

    geometry = mount.get("geometry")
    if tracking in {"sidereal", "solar"}:
        if geometry == "equatorial":
            return "none"
        if geometry == "altaz":
            return "field_rotation"

    return "none"


__all__ = ["resolve_motion_constraint"]
