from services.mount_service import MountService


class StateStoreRejectingDevices:
    def snapshot(self, section):
        assert section != "devices"
        return None


class DummyMountPlugin:
    def __init__(self):
        self.connected = False

    def connect(self):
        self.connected = True

    def disconnect(self):
        self.connected = False

    def status(self):
        return {"moving": False, "move_rate": None}

    def get_slew_speed_capabilities(self):
        return None


def test_selected_plugin_overrides_state_store_device_selection():
    loaded_plugins = []

    def load_mount(plugin_id, *_args, **_kwargs):
        loaded_plugins.append(plugin_id)
        return DummyMountPlugin()

    service = MountService(
        StateStoreRejectingDevices(),
        plugin_loader=load_mount,
        selected_plugin="indi",
    )

    try:
        status = service.status()

        assert status["plugin"] == "indi"
        assert loaded_plugins == ["indi"]
    finally:
        service.close()
