import threading

import pytest

from backend.state_store import StateStore
from services.mount_service import MountService


class BlockingHomePlugin:
    def __init__(self, home_releases):
        self.connected = False
        self.moving = False
        self.calls = []
        self.home_started = [threading.Event() for _ in home_releases]
        self.home_finished = [threading.Event() for _ in home_releases]
        self._home_releases = home_releases
        self._home_call = 0

    def connect(self):
        self.connected = True

    def disconnect(self):
        self.connected = False

    def move(self, direction):
        self.calls.append(("move", direction))
        self.moving = True

    def stop(self):
        self.calls.append(("stop", None))
        self.moving = False

    def go_home(self):
        call = self._home_call
        self._home_call += 1
        self.calls.append(("go_home", call))
        self.home_started[call].set()
        self._home_releases[call].wait()
        self.home_finished[call].set()

    def status(self):
        return {"moving": self.moving, "move_rate": None}

    def get_slew_speed_capabilities(self):
        return None


def make_service(tmp_path, home_count=1):
    state_store = StateStore(tmp_path / "state.json")
    state_store.update_section(
        "devices", {"mount": {"plugin": "fake", "active": True}}
    )
    releases = [threading.Event() for _ in range(home_count)]
    plugin = BlockingHomePlugin(releases)
    service = MountService(
        state_store,
        log_fn=lambda _message: None,
        plugin_loader=lambda *_args, **_kwargs: plugin,
    )
    return service, plugin, releases


def test_home_can_be_cancelled_immediately_and_slew_can_restart(tmp_path):
    service, plugin, releases = make_service(tmp_path)
    try:
        assert service.home_start()["homing"] is True
        assert plugin.home_started[0].wait(timeout=1)
        assert service.status()["homing"] is True
        with pytest.raises(RuntimeError, match="mount is homing"):
            service.start_slew("east")

        assert service.stop()["homing"] is False
        assert service.start_slew("east")["direction"] == "east"
        assert plugin.home_started[0].is_set()
    finally:
        releases[0].set()
        service.close()


def test_home_stops_manual_motion_before_starting_worker(tmp_path):
    service, plugin, releases = make_service(tmp_path)
    try:
        service.start_slew("north")

        status = service.home_start()

        assert plugin.home_started[0].wait(timeout=1)
        assert status["homing"] is True
        assert status["moving"] is False
        assert status["direction"] is None
        assert plugin.calls[:3] == [
            ("move", "north"),
            ("stop", None),
            ("go_home", 0),
        ]
    finally:
        releases[0].set()
        service.close()


def test_obsolete_home_worker_cannot_clear_new_homing_state_or_stop(tmp_path):
    service, plugin, releases = make_service(tmp_path, home_count=2)
    try:
        service.home_start()
        assert plugin.home_started[0].wait(timeout=1)
        service.stop()
        service.home_start()
        assert plugin.home_started[1].wait(timeout=1)

        releases[0].set()
        assert plugin.home_finished[0].wait(timeout=1)

        assert service.status()["homing"] is True
        assert plugin.calls.count(("stop", None)) == 1
    finally:
        releases[0].set()
        releases[1].set()
        service.close()
