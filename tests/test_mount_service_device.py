from backend.state_store import StateStore
from services.mount_service import MountService


class LocationMountPlugin:
    def __init__(self):
        self.connected = False
        self.location_calls = []

    def connect(self):
        self.connected = True

    def disconnect(self):
        self.connected = False

    def set_location(self, latitude, longitude, elevation):
        self.location_calls.append((latitude, longitude, elevation))

    def status(self):
        return {
            "connected": self.connected,
            "moving": False,
            "move_rate": None,
            "device": {"name": "Test mount", "port": "/dev/test"},
        }

    def get_slew_speed_capabilities(self):
        return None


def test_status_passes_through_device_and_pushes_gps_once(tmp_path):
    state_store = StateStore(tmp_path / "state.json")
    state_store.update_section(
        "devices", {"mount": {"plugin": "fake", "active": True}}
    )
    state_store.update_section(
        "gps", {"lat": 48.8566, "lon": 2.3522, "alt": 35.0}
    )
    plugins = []

    def load_mount(*_args, **_kwargs):
        plugin = LocationMountPlugin()
        plugins.append(plugin)
        return plugin

    service = MountService(state_store, plugin_loader=load_mount)

    first_status = service.status()
    second_status = service.status()

    assert first_status["device"] == {
        "name": "Test mount",
        "port": "/dev/test",
    }
    assert second_status["device"] == first_status["device"]
    assert plugins[0].location_calls == [(48.8566, 2.3522, 35.0)]

    service.close()
    service.status()

    assert len(plugins) == 2
    assert plugins[1].location_calls == [(48.8566, 2.3522, 35.0)]

    service.close()


def test_status_passes_through_read_only_mount_telemetry(tmp_path):
    state_store = StateStore(tmp_path / "state.json")
    state_store.update_section(
        "devices", {"mount": {"plugin": "fake", "active": True}}
    )

    class TelemetryMountPlugin(LocationMountPlugin):
        def status(self):
            return {
                "connected": self.connected,
                "moving": False,
                "move_rate": 0.25,
                "raw": "nNpeEW264",
                "general_error": 4,
                "ra": "12:34:56",
                "dec": "+45*00:00",
                "sidereal_time": "10:11:12",
                "product": "On-Step",
                "firmware": "4.24",
                "park_status": "not_parked",
            }

    plugin = TelemetryMountPlugin()
    service = MountService(
        state_store,
        plugin_loader=lambda *_args, **_kwargs: plugin,
    )

    status = service.status()

    for field, expected in {
        "raw": "nNpeEW264",
        "general_error": 4,
        "ra": "12:34:56",
        "dec": "+45*00:00",
        "sidereal_time": "10:11:12",
        "product": "On-Step",
        "firmware": "4.24",
        "park_status": "not_parked",
    }.items():
        assert status[field] == expected

    service.close()


def test_status_connects_without_gps_location(tmp_path):
    state_store = StateStore(tmp_path / "state.json")
    state_store.update_section(
        "devices", {"mount": {"plugin": "fake", "active": True}}
    )
    plugin = LocationMountPlugin()
    service = MountService(
        state_store,
        plugin_loader=lambda *_args, **_kwargs: plugin,
    )

    status = service.status()

    assert status["connected"] is True
    assert status["device"] == {
        "name": "Test mount",
        "port": "/dev/test",
    }
    assert plugin.location_calls == []

    service.close()
