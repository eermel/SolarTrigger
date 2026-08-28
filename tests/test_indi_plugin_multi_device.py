from copy import deepcopy

from plugins.mount.indi_client import IndiSubprocessClient
from plugins.mount.indi_plugin import IndiMount


INDI_STDOUT = """\
D1.CONNECTION.CONNECT=On
D1.CONNECTION.DISCONNECT=Off
D1.EQUATORIAL_EOD_COORD.RA=1.25
D1.EQUATORIAL_EOD_COORD.DEC=-2.5
D1.TELESCOPE_TRACK_STATE.TRACK_ON=On
D1.TELESCOPE_TRACK_STATE.TRACK_OFF=Off
D1.TELESCOPE_TRACK_MODE.TRACK_SIDEREAL=On
D1.TELESCOPE_TRACK_MODE.TRACK_SOLAR=Off
D1.TELESCOPE_SLEW_RATE.SLEW_GUIDE=Off
D1.TELESCOPE_SLEW_RATE.SLEW_MAX=On
D2.CONNECTION.CONNECT=On
D2.CONNECTION.DISCONNECT=Off
D2.EQUATORIAL_EOD_COORD.RA=18.75
D2.EQUATORIAL_EOD_COORD.DEC=42.0
D2.TELESCOPE_TRACK_STATE.TRACK_ON=Off
D2.TELESCOPE_TRACK_STATE.TRACK_OFF=On
D2.TELESCOPE_TRACK_MODE.TRACK_SIDEREAL=Off
D2.TELESCOPE_TRACK_MODE.TRACK_SOLAR=On
D2.TELESCOPE_SLEW_RATE.SLEW_GUIDE=On
D2.TELESCOPE_SLEW_RATE.SLEW_MAX=Off
"""


class FakeIndiClient:
    def __init__(self, device, stdout, assignments):
        self.device = device
        self.stdout = stdout
        self.assignments = assignments
        self.get_calls = []

    def get_props(self, patterns=None):
        self.get_calls.append(deepcopy(patterns))
        all_devices = IndiSubprocessClient._parse_props(self.stdout)
        return deepcopy(all_devices.get(self.device, {}))

    def set_props(self, assignments):
        self.assignments.append((self.device, deepcopy(assignments)))


def make_mount(device, client):
    return IndiMount(
        log_fn=lambda *_: None,
        config={"device": device, "timeout": 0, "poll_interval": 0},
        client=client,
    )


def test_status_is_isolated_by_indi_device_name():
    assignments = []
    client_d1 = FakeIndiClient("D1", INDI_STDOUT, assignments)
    client_d2 = FakeIndiClient("D2", INDI_STDOUT, assignments)
    mount_d1 = make_mount("D1", client_d1)
    mount_d2 = make_mount("D2", client_d2)

    status_d1 = mount_d1.status()
    status_d2 = mount_d2.status()

    assert status_d1["device"]["device"] == "D1"
    assert status_d1["device"]["coordinates"] == {"ra": 1.25, "dec": -2.5}
    assert status_d1["tracking"] is True
    assert status_d1["tracking_rate"] == "sidereal"
    assert status_d2["device"]["device"] == "D2"
    assert status_d2["device"]["coordinates"] == {"ra": 18.75, "dec": 42.0}
    assert status_d2["tracking"] is False
    assert status_d2["tracking_rate"] == "solar"
    assert client_d1.get_calls == [None]
    assert client_d2.get_calls == [None]
    assert assignments == []


def test_d1_move_stop_and_slew_rate_do_not_target_d2():
    assignments = []
    client_d1 = FakeIndiClient("D1", INDI_STDOUT, assignments)
    client_d2 = FakeIndiClient("D2", INDI_STDOUT, assignments)
    mount_d1 = make_mount("D1", client_d1)
    mount_d2 = make_mount("D2", client_d2)
    status_d2_before = mount_d2.status()

    mount_d1.move("north")
    mount_d1.stop()
    mount_d1.set_speed("SLEW_GUIDE")

    assert assignments == [
        (
            "D1",
            {"TELESCOPE_MOTION_NS": {"MOTION_NORTH": "On", "MOTION_SOUTH": "Off"}},
        ),
        (
            "D1",
            {
                "TELESCOPE_MOTION_NS": {
                    "MOTION_NORTH": "Off",
                    "MOTION_SOUTH": "Off",
                },
                "TELESCOPE_MOTION_WE": {
                    "MOTION_EAST": "Off",
                    "MOTION_WEST": "Off",
                },
                "TELESCOPE_ABORT_MOTION": {"ABORT": "On"},
            },
        ),
        (
            "D1",
            {"TELESCOPE_SLEW_RATE": {"SLEW_GUIDE": "On", "SLEW_MAX": "Off"}},
        ),
    ]
    assert mount_d1.status()["move_rate"] == "SLEW_GUIDE"
    assert mount_d2.status() == status_d2_before
    assert mount_d2.status()["move_rate"] is None
    assert all(device == "D1" for device, _ in assignments)
