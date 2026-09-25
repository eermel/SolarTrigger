from datetime import datetime, timezone

from services.focuser_service import FocuserService


class FakeStateStore:
    def __init__(self):
        self.data = {
            "devices": {
                "focuser": {
                    "active": True,
                    "plugin": "fake",
                }
            },
            "focuser_settings": {
                "mode": "slow",
                "slow_step": 20,
                "fast_step": 150,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
        }

    def snapshot(self, section):
        value = self.data.get(section)
        return dict(value) if isinstance(value, dict) else value

    def update_section(self, section, value, persist=False):
        self.data[section] = dict(value)


class FakePlugin:
    def __init__(self):
        self.connected = False
        self.position = 100
        self.moving = False
        self.report_motion_on_move = True
        self.reset_positions = []

    def connect(self):
        self.connected = True

    def disconnect(self):
        self.connected = False

    def status(self):
        return {"moving": self.moving, "holding": False}

    def get_position(self):
        return self.position

    def set_current_position(self, value):
        self.position = int(value)
        self.reset_positions.append(int(value))

    def move_to(self, position, wait=False):
        self.moving = self.report_motion_on_move and not wait

    def stop(self):
        self.moving = False


def make_service():
    store = FakeStateStore()
    plugin = FakePlugin()
    service = FocuserService(
        store,
        log_fn=lambda *_: None,
        plugin_loader=lambda *_args, **_kwargs: plugin,
    )
    return service, plugin


def test_successful_home_resets_eaf_counter_to_zero():
    service, plugin = make_service()

    started = service.home()
    assert started["motion_command"] == "home"
    assert started["moving"] is True

    plugin.position = 0
    plugin.moving = False
    completed = service.status()

    assert completed["position"] == 0
    assert completed["motion_command"] is None
    assert completed["target_position"] is None
    assert plugin.reset_positions == [0]


def test_cancelled_home_does_not_reset_eaf_counter():
    service, plugin = make_service()

    service.home()
    plugin.position = 42
    stopped = service.stop()

    assert stopped["position"] == 42
    assert stopped["motion_command"] is None
    assert plugin.reset_positions == []


def test_go_to_zero_does_not_reset_eaf_counter():
    service, plugin = make_service()

    service.move_to(0)
    plugin.position = 0
    plugin.moving = False
    completed = service.status()

    assert completed["position"] == 0
    assert completed["motion_command"] is None
    assert plugin.reset_positions == []


def test_home_that_finishes_with_nonzero_counter_resets_reference_to_zero():
    service, plugin = make_service()

    service.home()
    plugin.position = 7333
    plugin.moving = False
    completed = service.status()

    assert completed["position"] == 0
    assert completed["motion_command"] is None
    assert completed["target_position"] is None
    assert plugin.reset_positions == [0]


def test_home_does_not_finish_before_motion_has_actually_started():
    service, plugin = make_service()
    plugin.report_motion_on_move = False

    started = service.home()

    assert started["position"] == 100
    assert started["moving"] is False
    assert started["motion_command"] == "home"
    assert started["target_position"] == 0
    assert plugin.reset_positions == []

    plugin.moving = True
    moving = service.status()
    assert moving["motion_command"] == "home"

    plugin.position = 7333
    plugin.moving = False
    completed = service.status()

    assert completed["position"] == 0
    assert completed["motion_command"] is None
    assert plugin.reset_positions == [0]
