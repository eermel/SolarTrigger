import threading

from plugins.focuser.base import DIR_IN, DIR_OUT
from plugins.focuser.zwo_plugin import ZwoFocuser


class FakeEaf:
    def __init__(self, max_step=60000):
        self.max_step = max_step
        self.moves = []
        self.stops = 0

    def move_to(self, position, wait=False):
        self.moves.append((position, wait))
        return position

    def stop(self):
        self.stops += 1


def make_plugin():
    plugin = ZwoFocuser.__new__(ZwoFocuser)
    plugin.eaf = FakeEaf()
    plugin._hold_stop = threading.Event()
    plugin._hold_thread = None
    plugin.log = lambda *_args: None
    return plugin


def test_held_out_uses_one_native_move_to_upper_limit_then_stop():
    plugin = make_plugin()
    plugin._hold_stop.set()

    plugin._hold_loop(DIR_OUT, 150)

    assert plugin.eaf.moves == [(60000, False)]
    assert plugin.eaf.stops == 1


def test_held_in_uses_one_native_move_to_zero_then_stop():
    plugin = make_plugin()
    plugin._hold_stop.set()

    plugin._hold_loop(DIR_IN, 20)

    assert plugin.eaf.moves == [(0, False)]
    assert plugin.eaf.stops == 1


def test_zwo_plugin_does_not_advertise_segmented_absolute_moves():
    assert not hasattr(ZwoFocuser, "max_async_move_span")
    assert not hasattr(ZwoFocuser, "async_move_lookahead")


def test_active_focuser_registry_keeps_zwo_on_vendor_sdk_only():
    from plugins import focuser

    plugins = {item["id"] for item in focuser.available_plugins()}
    assert "zwo_eaf" in plugins
    assert "indi" not in plugins
