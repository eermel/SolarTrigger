import importlib.util
import sys
from types import ModuleType

import pytest


if importlib.util.find_spec("serial") is None:
    sys.modules.setdefault("serial", ModuleType("serial"))

from plugins.mount import onstep, onstep_plugin


class OnStepStub:
    def __init__(self, **_kwargs):
        self.selected_rates = []
        self.start_tracking_calls = []

    def select_tracking_rate(self, rate):
        self.selected_rates.append(rate)

    def start_tracking(self, rate):
        self.start_tracking_calls.append(rate)


@pytest.fixture
def mount(monkeypatch):
    monkeypatch.setattr(onstep_plugin, "OnStep", OnStepStub)
    return onstep_plugin.OnStepMount(log_fn=lambda _message: None)


def test_tracking_capabilities_expose_only_supported_contract_modes(mount):
    assert mount.get_tracking_capabilities() == {
        "modes": ["solar", "sidereal"],
        "toggle": True,
    }


@pytest.mark.parametrize(
    ("mode", "expected_rate"),
    [
        ("solar", onstep_plugin.TrackingRate.SOLAR),
        ("sidereal", onstep_plugin.TrackingRate.SIDEREAL),
    ],
)
def test_set_tracking_mode_selects_rate_without_starting_tracking(
    mount, mode, expected_rate
):
    mount.set_tracking_mode(mode)

    assert mount.mount.selected_rates == [expected_rate]
    assert mount.mount.start_tracking_calls == []


def test_set_tracking_mode_rejects_unknown_mode(mount):
    with pytest.raises(ValueError):
        mount.set_tracking_mode("lunar")

    assert mount.mount.selected_rates == []
    assert mount.mount.start_tracking_calls == []


def test_go_home_passes_optional_is_cancelled_to_onstep(monkeypatch):
    received = []

    def capture_go_home(_self, **kwargs):
        received.append(kwargs["is_cancelled"])

    monkeypatch.setattr(onstep.OnStep, "go_home", capture_go_home)
    mount = onstep_plugin.OnStepMount(log_fn=lambda _message: None)
    callback = lambda: False

    mount.go_home()
    mount.go_home(is_cancelled=callback)

    assert received == [None, callback]
