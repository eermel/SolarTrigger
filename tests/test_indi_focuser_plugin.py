from copy import deepcopy

from plugins.focuser.indi_plugin import IndiFocuser


class FakeClient:
    def __init__(self):
        self.props = {
            "CONNECTION": {
                "CONNECT": "Off",
                "DISCONNECT": "On",
            },
            "ABS_FOCUS_POSITION": {
                "FOCUS_ABSOLUTE_POSITION": "100",
            },
            "FOCUS_MAX": {
                "FOCUS_MAX_VALUE": "10000",
            },
            "FOCUS_ABORT_MOTION": {
                "ABORT": "Off",
            },
            "FOCUS_SYNC": {
                "FOCUS_SYNC_VALUE": "100",
            },
        }
        self.monitor_started = False
        self.monitor_stopped = False
        self.assignments = []

    def ensure_device_present(self, device_name):
        assert device_name == "ZWO EAF"

    def get_props(self, patterns=None):
        return deepcopy(self.props)

    def set_props(self, assignments):
        self.assignments.append(deepcopy(assignments))
        for prop, values in assignments.items():
            self.props.setdefault(prop, {}).update(
                {name: str(value) for name, value in values.items()}
            )
        if "ABS_FOCUS_POSITION" in assignments:
            value = assignments["ABS_FOCUS_POSITION"]["FOCUS_ABSOLUTE_POSITION"]
            self.props["ABS_FOCUS_POSITION"]["FOCUS_ABSOLUTE_POSITION"] = str(value)
        if "FOCUS_SYNC" in assignments:
            value = assignments["FOCUS_SYNC"]["FOCUS_SYNC_VALUE"]
            self.props["ABS_FOCUS_POSITION"]["FOCUS_ABSOLUTE_POSITION"] = str(value)

    def start_monitor(self):
        self.monitor_started = True

    def stop_monitor(self):
        self.monitor_stopped = True


def make_focuser():
    client = FakeClient()
    focuser = IndiFocuser(
        log_fn=lambda *_args: None,
        config={
            "device_name": "ZWO EAF",
            "timeout": 0.01,
            "poll_interval": 0,
        },
        client=client,
    )
    return focuser, client


def test_indi_focuser_connect_move_sync_stop_disconnect():
    focuser, client = make_focuser()

    focuser.connect()
    assert focuser.connected is True
    assert client.monitor_started is True
    assert focuser.get_position() == 100

    focuser.move_to(321, wait=True)
    assert focuser.get_position() == 321

    focuser.set_current_position(0)
    assert focuser.get_position() == 0

    focuser.stop()
    assert any(
        "FOCUS_ABORT_MOTION" in assignment
        for assignment in client.assignments
    )

    focuser.disconnect()
    assert focuser.connected is False
    assert client.monitor_stopped is True


def test_indi_focuser_exposes_standard_status_contract():
    focuser, _client = make_focuser()
    focuser.connect()

    status = focuser.status()

    assert status["connected"] is True
    assert status["position"] == 100
    assert status["max_step"] == 10000
    assert status["step_coarse"] == 150
    assert status["step_fine"] == 20
