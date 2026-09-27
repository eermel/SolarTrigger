import sys
from types import SimpleNamespace

import pytest

sys.modules.setdefault("serial", SimpleNamespace())

from plugins.mount.onstep import OnStep


def test_direct_onstep_location_rejects_zero_zero():
    mount = OnStep()
    with pytest.raises(ValueError, match="0/0"):
        mount.set_location(0.0, 0.0)


@pytest.mark.parametrize(
    "latitude,longitude",
    [
        (float("nan"), 2.0),
        (48.0, float("inf")),
        (91.0, 2.0),
        (48.0, 181.0),
    ],
)
def test_direct_onstep_location_rejects_invalid_coordinates(latitude, longitude):
    mount = OnStep()
    with pytest.raises(ValueError):
        mount.set_location(latitude, longitude)
