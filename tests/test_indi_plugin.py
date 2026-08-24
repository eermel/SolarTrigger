from copy import deepcopy

import pytest

from plugins.mount.indi_client import IndiClientError
from plugins.mount.indi_plugin import IndiMount


class StubIndiClient:
    def __init__(self, props=None):
        self.props = deepcopy(props or {})
        self.set_calls = []
        self.present_calls = []
        self.failure = None

    def ensure_device_present(self, device):
        self.present_calls.append(device)
        if self.failure:
            raise self.failure

    def get_props(self, patterns=None):
        if self.failure:
            raise self.failure
        return deepcopy(self.props)

    def set_props(self, assignments):
        if self.failure:
            raise self.failure
        self.set_calls.append(deepcopy(assignments))
        for prop, elements in assignments.items():
            self.props.setdefault(prop, {}).update(elements)


@pytest.fixture
def full_props():
    return {
        "CONNECTION": {"CONNECT": "Off", "DISCONNECT": "On"},
        "DEVICE_BAUD_RATE": {
            "BAUD_9600": {"value": "On", "label": "9600"},
            "BAUD_115200": {"value": "Off", "label": "115200"},
        },
        "DEVICE_AUTO_SEARCH": {"INDI_ENABLED": "On", "INDI_DISABLED": "Off"},
        "TELESCOPE_TRACK_MODE": {
            "TRACK_SIDEREAL": "On", "TRACK_SOLAR": "Off", "TRACK_LUNAR": "Off"
        },
        "TELESCOPE_TRACK_STATE": {"TRACK_ON": "Off", "TRACK_OFF": "On"},
        "TELESCOPE_SLEW_RATE": {
            "SLEW_GUIDE": {"value": "Off", "label": "Guide"},
            "SLEW_MAX": {"value": "On", "label": "Maximum"},
        },
        "TELESCOPE_PARK": {"PARK": "On", "UNPARK": "Off"},
        "GEOGRAPHIC_COORD": {"LAT": "1", "LONG": "2", "ELEV": "3"},
        "EQUATORIAL_EOD_COORD": {"RA": "12.5", "DEC": "-4.25"},
        "DRIVER_INFO": {"DRIVER_EXEC": "custom_driver"},
        "MOUNTINFORMATION": {
            "MOUNT_MODEL": "EQ8", "MOUNT_CONTROL": "MC001", "MOUNT_CODE": "42"
        },
    }


def mount(client, **config):
    return IndiMount(
        log_fn=lambda *_: None,
        config={"device": "Test Mount", "timeout": 0, "poll_interval": 0, **config},
        client=client,
    )


def assert_code(code, call):
    with pytest.raises(IndiClientError) as raised:
        call()
    assert raised.value.code == code
    return raised.value


def test_connect_configures_serial_baud_auto_search_and_connection(tmp_path, full_props):
    serial_port = tmp_path / "ttyUSB0"
    serial_port.touch(mode=0o600)
    client = StubIndiClient(full_props)

    plugin = mount(client, serial_port=str(serial_port), baud=115200, auto_search=True)
    plugin.connect()

    assert client.present_calls == ["Test Mount"]
    assert client.set_calls == [
        {
            "CONNECTION_MODE": {"CONNECTION_SERIAL": "On", "CONNECTION_TCP": "Off"},
            "DEVICE_PORT": {"PORT": str(serial_port)},
            "DEVICE_BAUD_RATE": {"BAUD_9600": "Off", "BAUD_115200": "On"},
            "DEVICE_AUTO_SEARCH": {"INDI_ENABLED": "Off", "INDI_DISABLED": "On"},
        },
        {"CONNECTION": {"CONNECT": "On", "DISCONNECT": "Off"}},
    ]
    assert plugin.connected is True


def test_connect_skips_optional_properties_when_they_are_absent(tmp_path):
    serial_port = tmp_path / "ttyUSB0"
    serial_port.touch(mode=0o600)
    client = StubIndiClient({"CONNECTION": {"CONNECT": "Off", "DISCONNECT": "On"}})

    mount(client, serial_port=str(serial_port)).connect()

    assert client.set_calls[0] == {
        "CONNECTION_MODE": {"CONNECTION_SERIAL": "On", "CONNECTION_TCP": "Off"},
        "DEVICE_PORT": {"PORT": str(serial_port)},
    }


def test_connect_rejects_baud_missing_from_advertised_property(tmp_path, full_props):
    serial_port = tmp_path / "ttyUSB0"
    serial_port.touch(mode=0o600)
    client = StubIndiClient(full_props)

    error = assert_code(
        "PROPERTY_UNSUPPORTED",
        mount(client, serial_port=str(serial_port), baud=57600).connect,
    )

    assert "57600" in str(error)
    assert client.set_calls == []


@pytest.mark.parametrize("serial_port", [None, ""])
def test_connect_requires_serial_port_before_client_access(serial_port):
    client = StubIndiClient()
    plugin = mount(client, serial_port=serial_port)

    assert_code("SERIAL_PORT_MISSING", plugin.connect)
    assert client.present_calls == []
    assert client.set_calls == []


def test_connect_maps_missing_path_and_permission_denied(monkeypatch):
    client = StubIndiClient()
    assert_code("SERIAL_PORT_MISSING", mount(client, serial_port="/missing").connect)

    monkeypatch.setattr("plugins.mount.indi_plugin.os.path.exists", lambda _: True)
    monkeypatch.setattr("plugins.mount.indi_plugin.os.access", lambda *_: False)
    assert_code("SERIAL_PERMISSION_DENIED", mount(client, serial_port="/dev/denied").connect)


def test_status_enriches_device_and_discovers_capabilities(full_props):
    plugin = mount(StubIndiClient(full_props))

    status = plugin.status()

    assert status["connected"] is False
    assert status["ra"] == 12.5
    assert status["dec"] == -4.25
    assert status["tracking_rate"] == "sidereal"
    assert status["device"] == {
        "driver": "custom_driver", "device": "Test Mount", "model": "EQ8",
        "motor_controller": "MC001", "mount_code": "42",
        "coordinates": {"ra": 12.5, "dec": -4.25}, "parked": True,
    }
    assert status["capabilities"]["tracking"] == {
        "toggle": True, "modes": ["sidereal", "solar", "lunar"]
    }
    assert status["capabilities"]["slew_speed"]["values"] == [
        {"value": "SLEW_GUIDE", "label": "Guide"},
        {"value": "SLEW_MAX", "label": "Maximum"},
    ]
    assert status["capabilities"]["park"] is True
    assert status["capabilities"]["location"] is True


def test_status_uses_device_info_fallbacks_and_absent_capabilities():
    client = StubIndiClient({
        "DEVICE_INFO": {
            "DEVICE_MODEL": "Fallback", "MOTOR_TYPE": "Stepper", "MOUNT_TYPE": "GEM"
        }
    })

    status = mount(client).status()

    assert status["device"]["driver"] == "indi_eqmod_telescope"
    assert status["device"]["model"] == "Fallback"
    assert status["device"]["motor_controller"] == "Stepper"
    assert status["device"]["mount_code"] == "GEM"
    assert status["device"]["parked"] is None
    assert status["capabilities"]["tracking"] == {"toggle": False, "modes": []}
    assert status["capabilities"]["slew_speed"] is None


def test_tracking_capabilities_and_tracking_writes(full_props):
    client = StubIndiClient(full_props)
    plugin = mount(client)

    assert plugin.get_tracking_capabilities() == {
        "toggle": True, "modes": ["sidereal", "solar", "lunar"]
    }
    plugin.start_tracking("solar")
    assert client.set_calls[-1] == {
        "TELESCOPE_TRACK_MODE": {
            "TRACK_SIDEREAL": "Off", "TRACK_SOLAR": "On", "TRACK_LUNAR": "Off"
        },
        "TELESCOPE_TRACK_STATE": {"TRACK_ON": "On", "TRACK_OFF": "Off"},
    }
    plugin.stop_tracking()
    assert client.set_calls[-1] == {
        "TELESCOPE_TRACK_STATE": {"TRACK_ON": "Off", "TRACK_OFF": "On"}
    }


def test_tracking_mode_supports_short_indi_element_names():
    client = StubIndiClient({
        "TELESCOPE_TRACK_MODE": {
            "SIDEREAL": "Off", "SOLAR": "On", "LUNAR": "Off"
        },
        "TELESCOPE_TRACK_STATE": {"TRACK_ON": "On", "TRACK_OFF": "Off"},
    })
    plugin = mount(client)

    assert plugin.status()["tracking_rate"] == "solar"
    assert plugin.get_tracking_capabilities() == {
        "toggle": True, "modes": ["sidereal", "solar", "lunar"]
    }
    plugin.set_tracking_mode("lunar")
    assert client.set_calls[-1] == {
        "TELESCOPE_TRACK_MODE": {
            "SIDEREAL": "Off", "SOLAR": "Off", "LUNAR": "On"
        }
    }


def test_tracking_mode_rejects_absent_mode_property():
    assert_code(
        "PROPERTY_UNSUPPORTED",
        lambda: mount(StubIndiClient()).set_tracking_mode("solar"),
    )


def test_tracking_timeout_is_structured(full_props):
    client = StubIndiClient(full_props)
    client.set_props = lambda assignments: client.set_calls.append(deepcopy(assignments))

    error = assert_code("CONNECTION_FAILED", lambda: mount(client).start_tracking("solar"))
    assert "did not start" in str(error)


@pytest.mark.parametrize(
    ("direction", "expected"),
    [
        ("north", {"TELESCOPE_MOTION_NS": {"MOTION_NORTH": "On", "MOTION_SOUTH": "Off"}}),
        ("south", {"TELESCOPE_MOTION_NS": {"MOTION_SOUTH": "On", "MOTION_NORTH": "Off"}}),
        ("east", {"TELESCOPE_MOTION_WE": {"MOTION_EAST": "On", "MOTION_WEST": "Off"}}),
        ("west", {"TELESCOPE_MOTION_WE": {"MOTION_WEST": "On", "MOTION_EAST": "Off"}}),
    ],
)
def test_move_and_stop_map_to_indi_switches(direction, expected):
    client = StubIndiClient()
    plugin = mount(client)

    plugin.move(direction)
    assert client.set_calls[-1] == expected
    plugin.stop()
    assert client.set_calls[-1]["TELESCOPE_ABORT_MOTION"] == {"ABORT": "On"}
    assert all(
        state == "Off"
        for prop in ("TELESCOPE_MOTION_NS", "TELESCOPE_MOTION_WE")
        for state in client.set_calls[-1][prop].values()
    )


def test_speed_selects_value_updates_rate_and_rejects_unknown(full_props):
    client = StubIndiClient(full_props)
    plugin = mount(client)

    plugin.set_speed("Maximum")
    assert client.set_calls[-1] == {
        "TELESCOPE_SLEW_RATE": {"SLEW_GUIDE": "Off", "SLEW_MAX": "On"}
    }
    assert plugin.status()["move_rate"] == "Maximum"
    assert_code("PROPERTY_UNSUPPORTED", lambda: plugin.set_speed("missing"))


def test_slew_capability_discovery_preserves_element_names_and_labels(full_props):
    plugin = mount(StubIndiClient(full_props))

    assert plugin.get_slew_speed_capabilities() == {
        "kind": "discrete",
        "unit": None,
        "min": None,
        "max": None,
        "step": None,
        "values": [
            {"value": "SLEW_GUIDE", "label": "Guide"},
            {"value": "SLEW_MAX", "label": "Maximum"},
        ],
    }
    assert mount(StubIndiClient()).get_slew_speed_capabilities() is None


def test_location_push_and_unsupported_property(full_props):
    client = StubIndiClient(full_props)
    mount(client).set_location(48.5, 2.25, 120)
    assert client.set_calls[-1] == {
        "GEOGRAPHIC_COORD": {"LAT": 48.5, "LONG": 2.25, "ELEV": 120}
    }

    assert_code("PROPERTY_UNSUPPORTED", lambda: mount(StubIndiClient()).set_location(1, 2, 3))


def test_raw_client_exception_is_mapped_without_traceback_text():
    client = StubIndiClient()
    client.failure = RuntimeError("transport broke")

    error = assert_code("CONNECTION_FAILED", mount(client).status)
    assert "Traceback" not in str(error)
    assert "transport broke" in str(error)


def test_structured_client_error_keeps_its_code():
    client = StubIndiClient()
    client.failure = IndiClientError("CONNECTION_LOST", "server disconnected")

    error = assert_code("CONNECTION_LOST", mount(client).status)
    assert str(error) == "server disconnected"
