from services.mount_service import MountService


class FakeStateStore:
    def snapshot(self, key=None):
        if key == "gps":
            return {}
        return {}


class FakeMountPlugin:
    def __init__(self):
        self.connected = False
        self.connect_calls = 0
        self.stop_calls = 0

    def connect(self):
        self.connect_calls += 1
        self.connected = True

    def disconnect(self):
        self.connected = False

    def stop(self):
        assert self.connected is True
        self.stop_calls += 1

    def status(self):
        return {
            "moving": False,
        }

    def get_slew_speed_capabilities(self):
        return None

    def get_tracking_capabilities(self):
        return None


def test_emergency_stop_reconnects_and_sends_physical_stop():
    plugin = FakeMountPlugin()

    service = MountService(
        FakeStateStore(),
        log_fn=lambda _message: None,
        config={},
        selected_plugin="indi",
        plugin_loader=lambda *_args, **_kwargs: plugin,
    )

    result = service.emergency_stop()

    assert plugin.connect_calls == 1
    assert plugin.stop_calls == 1
    assert result["connected"] is True
    assert result["moving"] is False
    assert result["homing"] is False
