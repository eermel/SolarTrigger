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


def test_status_refuses_zero_zero_gps_location(tmp_path):
    state_store = StateStore(tmp_path / "state.json")
    state_store.update_section(
        "devices", {"mount": {"plugin": "fake", "active": True}}
    )
    state_store.update_section(
        "gps", {"lat": 0.0, "lon": 0.0, "alt": 0.0}
    )
    plugin = LocationMountPlugin()
    logs = []
    service = MountService(
        state_store,
        log_fn=logs.append,
        plugin_loader=lambda *_args, **_kwargs: plugin,
    )

    status = service.status()

    assert status["connected"] is True
    assert plugin.location_calls == []
    assert any("refusing invalid mount location 0/0" in line for line in logs)

    service.close()


def test_manual_location_rejects_zero_zero_before_driver_write(tmp_path):
    state_store = StateStore(tmp_path / "state.json")
    state_store.update_section(
        "devices", {"mount": {"plugin": "fake", "active": True}}
    )
    plugin = LocationMountPlugin()
    service = MountService(
        state_store,
        plugin_loader=lambda *_args, **_kwargs: plugin,
    )

    try:
        service.set_location(0.0, 0.0, 0.0)
    except ValueError as exc:
        assert "0/0" in str(exc)
    else:
        raise AssertionError("zero/zero mount location must be rejected")

    assert plugin.location_calls == []
    service.close()


def test_manual_location_rejects_out_of_range_values(tmp_path):
    state_store = StateStore(tmp_path / "state.json")
    state_store.update_section(
        "devices", {"mount": {"plugin": "fake", "active": True}}
    )
    plugin = LocationMountPlugin()
    service = MountService(
        state_store,
        plugin_loader=lambda *_args, **_kwargs: plugin,
    )

    for location in (
        (91.0, 2.0, 10.0),
        (48.0, 181.0, 10.0),
        (48.0, 2.0, 20000.0),
    ):
        try:
            service.set_location(*location)
        except ValueError:
            pass
        else:
            raise AssertionError(f"invalid mount location accepted: {location!r}")

    assert plugin.location_calls == []
    service.close()


class SyncMountPlugin(LocationMountPlugin):
    def __init__(self):
        super().__init__()
        self.sync_calls = []

    def sync_site_time(self, lat, lon, elev, utc_iso, utc_offset_hours):
        self.sync_calls.append((lat, lon, elev, utc_iso, utc_offset_hours))
        return {
            "latitude": lat,
            "longitude": lon,
            "elevation": elev,
            "utc": utc_iso,
            "utc_offset_hours": utc_offset_hours,
        }


def test_mount_site_time_sync_validates_and_delegates(tmp_path):
    state_store = StateStore(tmp_path / "state.json")
    state_store.update_section(
        "devices", {"mount": {"plugin": "fake", "active": True}}
    )
    plugin = SyncMountPlugin()
    service = MountService(
        state_store,
        plugin_loader=lambda *_args, **_kwargs: plugin,
    )

    result = service.sync_site_time(
        48.87379,
        2.37972,
        78.0,
        "2026-09-27T02:02:31",
        2.0,
    )

    assert plugin.sync_calls == [
        (48.87379, 2.37972, 78.0, "2026-09-27T02:02:31", 2.0)
    ]
    assert result["synchronization"]["utc"] == "2026-09-27T02:02:31"
    service.close()
