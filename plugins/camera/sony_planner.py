"""Compatibility facade for the pure Sony exposure planner.

The canonical implementation now lives in
:mod:`backend.sony_exposure_planner` so runtime and preview can share exactly
the same planning rules without importing a hardware plugin.
"""

from backend import sony_exposure_planner as _impl

globals().update({
    name: getattr(_impl, name)
    for name in dir(_impl)
    if not name.startswith("__")
})
